"""Native Item Price pre-check visibility through the whitelisted endpoint."""

from typing import Any, override

import frappe
import frappe.share
from frappe.tests import IntegrationTestCase
from karam_finance.karam_general.utils.item_price_on_rate_mismatch import (
    create_item_price_for_rate_mismatch,
    get_item_price_mismatch_context_api,
)


class TestItemPricePrecheckPermissions(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "price_permission_" + frappe.generate_hash(length=8)
        previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, previous_user)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        self.item = self.token + " Item"
        self.allowed = self.token + " Allowed"
        self.denied = self.token + " Denied"
        self._insert(
            "Item", self.item, item_code=self.item, item_name=self.item, stock_uom="Nos"
        )
        for price_list, rate in ((self.allowed, 100), (self.denied, 900)):
            self._insert(
                "Price List",
                price_list,
                price_list_name=price_list,
                currency="USD",
                selling=1,
                enabled=1,
            )
            self._insert(
                "Item Price",
                price_list,
                item_code=self.item,
                item_name=self.item,
                price_list=price_list,
                currency="USD",
                uom="Nos",
                selling=1,
                price_list_rate=rate,
                valid_from="2026-01-01",
                packing_unit=0,
            )
        settings = frappe.get_single("Stock Settings")
        settings.set(
            "ka_item_price_mismatch_doctypes",
            [
                {
                    "doctype_name": "Sales Order",
                    "enabled": 1,
                    "update_item_price": 1,
                    "throw_exception": 0,
                }
            ],
        )
        settings.save()
        self.user = frappe.get_doc(
            {
                "doctype": "User",
                "email": self.token + "@example.com",
                "first_name": "Price permission",
                "send_welcome_email": 0,
                "roles": [{"role": "Sales User"}, {"role": "Sales Master Manager"}],
            }
        ).insert()
        self.permission = frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": self.user.name,
                "allow": "Price List",
                "for_value": self.allowed,
                "apply_to_all_doctypes": 0,
                "applicable_for": "Item Price",
            }
        ).insert()
        frappe.set_user(str(self.user.name))

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()

    def _precheck(self, price_list: str, **kwargs: Any) -> dict[str, Any]:
        return get_item_price_mismatch_context_api(
            item_code=self.item,
            price_list=price_list,
            currency="USD",
            stock_uom="Nos",
            doctype="Sales Order",
            transaction_date="2026-01-01",
            **kwargs,
        )

    def test_precheck_rejects_unreadable_price_without_exposing_name_or_rate(
        self,
    ) -> None:
        assert frappe.has_permission("Item Price", "read")
        assert not frappe.has_permission("Item Price", "read", doc=self.denied)
        with self.assertRaises(frappe.PermissionError) as error:
            self._precheck(self.denied)
        assert self.denied not in str(error.exception)
        assert "900" not in str(error.exception)

    def test_matching_rate_does_not_confirm_a_restricted_price(self) -> None:
        with self.assertRaises(frappe.PermissionError):
            self._precheck(self.denied, price_list_rate=900)

    def test_creation_cannot_confirm_or_update_restricted_existing_price(self) -> None:
        assert frappe.has_permission("Item Price", "write")
        assert not frappe.has_permission("Item Price", "read", doc=self.denied)
        for rate in (900, 100):
            with (
                self.subTest(rate=rate),
                self.assertRaises(frappe.PermissionError) as error,
            ):
                self._create(self.denied, rate)
            assert self.denied not in str(error.exception)
            assert "900" not in str(error.exception)
        frappe.set_user("Administrator")
        assert frappe.db.get_value("Item Price", self.denied, "price_list_rate") == 900

    def test_creation_reuses_readable_existing_price(self) -> None:
        result = self._create(self.allowed, 100)
        assert result["reused"] is True
        assert result["created"] is False
        assert result["item_price_name"] == self.allowed
        assert result["price_list_rate"] == 100

    def _create(self, price_list: str, rate: int) -> dict[str, Any]:
        return create_item_price_for_rate_mismatch(
            item_code=self.item,
            price_list=price_list,
            currency="USD",
            stock_uom="Nos",
            doctype="Sales Order",
            transaction_date="2026-01-01",
            conversion_factor=1,
            price_list_rate=rate,
            rate=rate,
        )

    def test_readable_price_and_matching_rate_preserve_response_contract(self) -> None:
        assert self._precheck(self.allowed) == {
            "enabled": True,
            "valid_from": "2026-01-01",
            "item_price_name": self.allowed,
            "item_price_rate": 100,
            "update_item_price": True,
            "throw_exception": False,
        }
        assert self._precheck(self.allowed, price_list_rate=100) == {
            "enabled": False,
            "price_exists": True,
        }

    def test_missing_price_preserves_existing_response(self) -> None:
        assert self._precheck(self.allowed, batch_no="nonexistent-batch") == {
            "enabled": True,
            "valid_from": "2026-01-01",
            "update_item_price": True,
            "throw_exception": False,
        }

    def test_customer_and_supplier_restrictions_apply_to_selected_prices(self) -> None:
        frappe.set_user("Administrator")
        self.permission.delete()
        for doctype, fieldname in (("Customer", "customer"), ("Supplier", "supplier")):
            with self.subTest(doctype=doctype):
                allowed_party = self.token + doctype + " A"
                denied_party = self.token + doctype + " B"
                for party in (allowed_party, denied_party):
                    self._insert(doctype, party, **{fieldname + "_name": party})
                frappe.db.set_value(
                    "Item Price",
                    self.denied,
                    {
                        "customer": None,
                        "supplier": None,
                        fieldname: denied_party,
                    },
                )
                permission = frappe.get_doc(
                    {
                        "doctype": "User Permission",
                        "user": self.user.name,
                        "allow": doctype,
                        "for_value": allowed_party,
                        "apply_to_all_doctypes": 0,
                        "applicable_for": "Item Price",
                    }
                ).insert()
                frappe.clear_cache(user=self.user.name)
                frappe.set_user(str(self.user.name))
                assert not frappe.has_permission("Item Price", "read", doc=self.denied)
                with self.assertRaises(frappe.PermissionError):
                    self._precheck(self.denied, **{fieldname: denied_party})
                frappe.set_user("Administrator")
                permission.delete()

    def test_duplicate_blocking_does_not_disclose_unreadable_price(self) -> None:
        frappe.set_user("Administrator")
        settings = frappe.get_single("Stock Settings")
        for row in settings.get("ka_item_price_mismatch_doctypes"):
            if row.doctype_name == "Sales Order":
                row.update_item_price = 0
                row.throw_exception = 1
        settings.save()
        frappe.set_user(str(self.user.name))
        with self.assertRaises(frappe.PermissionError):
            self._precheck(self.denied)

    def test_native_explicit_share_allows_reading_the_shared_price(self) -> None:
        frappe.set_user("Administrator")
        frappe.share.add("Item Price", self.denied, user=str(self.user.name), read=1)
        frappe.set_user(str(self.user.name))
        assert frappe.has_permission("Item Price", "read", doc=self.denied)
        assert self._precheck(self.denied)["item_price_rate"] == 900
