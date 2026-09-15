"""Tests for item price creation on transaction rate mismatch."""

from __future__ import annotations

import importlib
from datetime import date
from types import SimpleNamespace
from typing import TYPE_CHECKING, override
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

import frappe
import pytest
from frappe.query_builder.builder import MariaDB

if TYPE_CHECKING:
    from types import ModuleType

MODULE_NAME = "karam_finance.karam_general.utils.item_price_on_rate_mismatch"


def _load_module() -> ModuleType:
    return importlib.import_module(MODULE_NAME)


def _raise_item_price_validation(message: str, *args: object, **_: object) -> None:
    exception = (
        args[0] if args and isinstance(args[0], type) else frappe.ValidationError
    )
    raise exception(message)


class TestItemPriceHelperContracts(TestCase):
    """Pure helper and permission paths without requiring a Frappe site."""

    @override
    def setUp(self) -> None:
        self.module = _load_module()
        self.frappe = MagicMock()
        self.frappe._dict.side_effect = frappe._dict
        self.frappe.ValidationError = frappe.ValidationError
        self.frappe.PermissionError = frappe.PermissionError
        self.frappe.throw.side_effect = _raise_item_price_validation
        self.enterContext(patch.object(self.module, "frappe", self.frappe))
        self.enterContext(patch.object(self.module, "_", side_effect=str))

    def test_context_returns_pending_date_without_querying_prices(self) -> None:
        with (
            patch.object(
                self.module,
                "get_rate_mismatch_settings",
                return_value=frappe._dict(enabled=1),
            ),
            patch.object(self.module, "resolve_valid_from", return_value=None),
            patch.object(self.module, "find_item_price_with_matching_rate") as matching,
            patch.object(
                self.module, "find_item_price_with_same_valid_from"
            ) as same_date,
        ):
            assert self.module.get_item_price_mismatch_context(
                item_code="ITEM",
                price_list="Retail",
                currency="USD",
                stock_uom="Nos",
                doctype="Sales Invoice",
            ) == {"enabled": True, "valid_from": None}
        matching.assert_not_called()
        same_date.assert_not_called()

    def test_creation_endpoint_rejects_unreadable_existing_prices_before_policy(
        self,
    ) -> None:
        def can_read(*_args: object, **kwargs: object) -> bool:
            return not kwargs.get("doc")

        self.frappe.has_permission.side_effect = can_read
        existing = {
            "name": "RESTRICTED-PRICE",
            "price_list_rate": 900,
            "item_name": "Restricted item name",
            "packing_unit": 0,
        }
        for rate, update, throw in ((900, 1, 0), (100, 0, 1), (100, 1, 0), (100, 0, 0)):
            with (
                self.subTest(rate=rate, update=update, throw=throw),
                patch.object(self.module, "_lock_item_price_scope"),
                patch.object(
                    self.module,
                    "get_rate_mismatch_settings",
                    return_value=frappe._dict(
                        enabled=1, update_item_price=update, throw_exception=throw
                    ),
                ),
                patch.object(
                    self.module,
                    "find_item_price_with_same_valid_from",
                    return_value=existing,
                ),
                pytest.raises(frappe.PermissionError) as error,
            ):
                self.module.create_item_price_for_rate_mismatch(
                    item_code="ITEM",
                    price_list="Restricted",
                    currency="USD",
                    stock_uom="Nos",
                    conversion_factor=1,
                    price_list_rate=rate,
                    rate=rate,
                    doctype="Sales Invoice",
                    posting_date="2026-01-01",
                )
            assert "RESTRICTED-PRICE" not in str(error.value)
            assert "900" not in str(error.value)
            assert "Restricted item name" not in str(error.value)
        self.frappe.get_doc.assert_not_called()

    def test_creation_endpoint_reuses_readable_existing_price(self) -> None:
        self.frappe.has_permission.return_value = True
        with (
            patch.object(self.module, "_lock_item_price_scope"),
            patch.object(
                self.module,
                "get_rate_mismatch_settings",
                return_value=frappe._dict(
                    enabled=1, update_item_price=1, throw_exception=0
                ),
            ),
            patch.object(
                self.module,
                "find_item_price_with_same_valid_from",
                return_value={
                    "name": "READABLE-PRICE",
                    "price_list_rate": 100,
                    "item_name": "Item",
                    "packing_unit": 0,
                },
            ),
        ):
            result = self.module.create_item_price_for_rate_mismatch(
                item_code="ITEM",
                price_list="Retail",
                currency="USD",
                stock_uom="Nos",
                conversion_factor=1,
                price_list_rate=100,
                rate=100,
                doctype="Sales Invoice",
                posting_date="2026-01-01",
            )
        assert result == {
            "created": False,
            "reused": True,
            "item_price_name": "READABLE-PRICE",
            "price_list_rate": 100,
            "valid_from": date(2026, 1, 1),
        }
        self.frappe.get_doc.assert_not_called()

    def test_item_price_lookup_wrappers_build_their_complete_scope(self) -> None:
        same_date_result = {
            "name": "IP-SAME",
            "price_list_rate": 10,
            "item_name": None,
            "packing_unit": None,
        }
        matching_rate_result = {
            "name": "IP-RATE",
            "price_list_rate": 11,
            "item_name": None,
            "packing_unit": None,
        }
        with patch.object(
            self.module,
            "_find_applicable_item_price",
            side_effect=[same_date_result, matching_rate_result],
        ) as find_applicable:
            assert (
                self.module.find_item_price_with_same_valid_from(
                    item_code="ITEM",
                    price_list="Retail",
                    currency="USD",
                    stock_uom="Nos",
                    valid_from=date(2026, 1, 1),
                    customer="CUSTOMER",
                    supplier=None,
                    batch_no="BATCH",
                    qty=12,
                    for_update=True,
                )
                == same_date_result
            )
            assert (
                self.module.find_item_price_with_matching_rate(
                    item_code="ITEM",
                    price_list="Retail",
                    currency="USD",
                    stock_uom="Nos",
                    price_list_rate=11,
                    customer="CUSTOMER",
                    supplier=None,
                    batch_no="BATCH",
                    qty=12,
                    valid_from=date(2026, 1, 1),
                )
                == matching_rate_result
            )

        scope = self.module.ItemPriceScope(
            item_code="ITEM",
            price_list="Retail",
            currency="USD",
            stock_uom="Nos",
            customer="CUSTOMER",
            supplier=None,
            batch_no="BATCH",
        )
        assert find_applicable.call_args_list == [
            call(
                scope=scope,
                valid_from=date(2026, 1, 1),
                quantity=12,
                for_update=True,
            ),
            call(
                scope=scope,
                valid_from=date(2026, 1, 1),
                price_list_rate=11.0,
                quantity=12,
            ),
        ]

    def test_api_and_create_wrapper_reject_missing_item_price_permissions(self) -> None:
        self.frappe.has_permission.side_effect = [True, False]
        with pytest.raises(frappe.PermissionError, match="Item Price records"):
            self.module.get_item_price_mismatch_context_api(
                item_code="ITEM",
                price_list="Retail",
                currency="USD",
                stock_uom="Nos",
                doctype="Sales Invoice",
            )
        assert self.frappe.has_permission.call_args_list == [
            (("Sales Invoice", "read"), {}),
            (("Item Price", "read"), {}),
        ]
        self.frappe.has_permission.reset_mock()
        self.frappe.has_permission.side_effect = [True, False]
        with pytest.raises(frappe.PermissionError, match="write Item Price"):
            self.module.create_item_price_for_rate_mismatch(
                item_code="ITEM",
                price_list="Retail",
                currency="USD",
                stock_uom="Nos",
                conversion_factor=1,
                price_list_rate=10,
                rate=10,
                doctype="Sales Invoice",
                posting_date="2026-01-01",
            )
        assert self.frappe.has_permission.call_args_list == [
            (("Sales Invoice", "write"), {}),
            (("Item Price", "write"), {}),
        ]
        self.frappe.get_doc.assert_not_called()

    def test_context_api_dispatches_after_authorisation_and_support_checks(
        self,
    ) -> None:
        self.frappe.has_permission.return_value = True
        expected_context = {"enabled": True, "valid_from": "2026-01-01"}
        with patch.object(
            self.module,
            "get_item_price_mismatch_context",
            return_value=expected_context,
        ) as get_context:
            assert (
                self.module.get_item_price_mismatch_context_api(
                    item_code="ITEM",
                    price_list="Retail",
                    currency="USD",
                    stock_uom="Nos",
                    doctype="Sales Invoice",
                    price_list_rate=10,
                    posting_date="2026-01-01",
                    customer="CUSTOMER",
                    batch_no="BATCH",
                    qty=12,
                )
                == expected_context
            )

        get_context.assert_called_once_with(
            item_code="ITEM",
            price_list="Retail",
            currency="USD",
            stock_uom="Nos",
            doctype="Sales Invoice",
            price_list_rate=10,
            transaction_date=None,
            posting_date="2026-01-01",
            customer="CUSTOMER",
            supplier=None,
            batch_no="BATCH",
            qty=12,
        )

    def test_custom_field_wrappers_dispatch_in_the_required_order(self) -> None:
        with patch.object(frappe, "logger", return_value=MagicMock()):
            general_custom_fields = importlib.import_module(
                "karam_finance.karam_general.utils.custom_fields"
            )
            letter_custom_fields = importlib.import_module(
                "karam_finance.letter_reconciliation.utils.custom_fields"
            )

        calls = MagicMock()
        general = calls.general
        sync = calls.sync
        letter = calls.letter
        with (
            patch.object(
                general_custom_fields,
                "ensure_custom_fields_from_dir",
                general,
            ),
            patch.object(
                general_custom_fields,
                "sync_stock_settings_rate_mismatch_rows_for_migrate",
                sync,
            ),
            patch.object(
                letter_custom_fields,
                "ensure_custom_fields_from_dir",
                letter,
            ),
        ):
            general_custom_fields.ensure_custom_fields_general()
            letter_custom_fields.ensure_custom_fields_letter()

        assert calls.mock_calls == [
            call.general("karam_general", "custom_fields", label="karam_general"),
            call.sync(),
            call.letter(
                "letter_reconciliation", "custom_fields", label="letter_reconciliation"
            ),
        ]

    def test_settings_and_selection_helpers_handle_absence_and_scope(self) -> None:
        self.frappe.get_single.return_value = SimpleNamespace(
            ka_item_price_mismatch_doctypes=[]
        )
        assert self.module.get_rate_mismatch_settings("Sales Invoice") == {
            "enabled": 0,
            "update_item_price": 0,
            "throw_exception": 1,
        }
        assert (
            self.module._select_applicable_item_price(
                [
                    {
                        "name": "PACK",
                        "packing_unit": 6,
                        "price_list_rate": 1,
                        "item_name": None,
                    }
                ],
                5,
            )
            is None
        )
        assert (
            self.module._select_applicable_item_price(
                [
                    {
                        "name": "GENERIC",
                        "packing_unit": 0,
                        "price_list_rate": 1,
                        "item_name": None,
                    }
                ],
                5,
            )["name"]
            == "GENERIC"
        )
        assert self.module._normalise_scope_value("  CUSTOMER  ") == "CUSTOMER"
        assert self.module._normalise_scope_value(None) == ""
        assert self.module._get_row_value({"enabled": 1}, "enabled") == 1
        assert self.module._get_row_value(SimpleNamespace(), "enabled", 0) == 0

    def test_party_display_and_missing_existing_item_price_are_explicit(self) -> None:
        self.frappe.db.get_value.side_effect = ["Customer Name", "Supplier Name"]
        assert self.module._get_party_display_name(customer="CUST") == "Customer Name"
        assert self.module._get_party_display_name(supplier="SUP") == "Supplier Name"
        assert self.module._get_party_display_name() is None
        request = self.module.RateMismatchCreateRequest(
            item_code="ITEM",
            price_list="Retail",
            currency="USD",
            stock_uom="Nos",
            conversion_factor=1,
            price_list_rate=10,
            rate=10,
            doctype="Sales Invoice",
        )
        self.frappe.db.exists.return_value = False
        with pytest.raises(frappe.ValidationError, match="no longer exists"):
            self.module._apply_existing_item_price_policy(
                existing_item_price={
                    "name": "MISSING",
                    "price_list_rate": 1,
                    "item_name": None,
                    "packing_unit": None,
                },
                settings=frappe._dict(throw_exception=0, update_item_price=1),
                request=request,
                valid_from=date(2026, 1, 1),
                effective_rate=10,
            )

    def test_new_item_price_preserves_customer_scope_and_optional_batch(self) -> None:
        request = self.module.RateMismatchCreateRequest(
            item_code="ITEM",
            price_list="Retail",
            currency="USD",
            stock_uom="Nos",
            conversion_factor=1,
            price_list_rate=10,
            rate=10,
            doctype="Sales Invoice",
            customer="CUSTOMER",
            batch_no="BATCH",
        )
        item_price = MagicMock(name="ItemPrice")
        item_price.name = "IP-CUSTOMER"
        self.frappe.get_doc.return_value = item_price

        result = self.module._create_new_item_price_for_rate_mismatch(
            request=request,
            valid_from=date(2026, 1, 1),
            effective_rate=10,
            packing_unit=6,
        )

        self.frappe.get_doc.assert_called_once_with(
            {
                "doctype": "Item Price",
                "item_code": "ITEM",
                "price_list": "Retail",
                "currency": "USD",
                "uom": "Nos",
                "price_list_rate": 10,
                "valid_from": date(2026, 1, 1),
                "packing_unit": 6,
                "customer": "CUSTOMER",
                "batch_no": "BATCH",
            }
        )
        item_price.insert.assert_called_once_with()
        assert result == {
            "created": True,
            "item_price_name": "IP-CUSTOMER",
            "price_list_rate": 10,
            "valid_from": date(2026, 1, 1),
        }

    def test_validation_failure_paths_and_disabled_settings(self) -> None:
        request = self.module.RateMismatchCreateRequest(
            item_code="ITEM",
            price_list="Retail",
            currency="USD",
            stock_uom="Nos",
            conversion_factor=1,
            price_list_rate=10,
            rate=10,
            doctype="Sales Invoice",
        )
        self.frappe.has_permission.return_value = False
        with pytest.raises(frappe.PermissionError):
            self.module._validate_rate_mismatch_create_request(request)
        self.frappe.has_permission.side_effect = [True, False]
        with pytest.raises(frappe.PermissionError):
            self.module._validate_rate_mismatch_create_request(request)
        self.frappe.has_permission.side_effect = None
        self.frappe.has_permission.return_value = True
        blank = self.module.RateMismatchCreateRequest(
            item_code="",
            price_list="Retail",
            currency="USD",
            stock_uom="Nos",
            conversion_factor=1,
            price_list_rate=10,
            rate=10,
            doctype="Sales Invoice",
        )
        with pytest.raises(frappe.ValidationError, match="Missing required"):
            self.module._validate_rate_mismatch_create_request(blank)
        zero = self.module.RateMismatchCreateRequest(
            item_code="ITEM",
            price_list="Retail",
            currency="USD",
            stock_uom="Nos",
            conversion_factor=1,
            price_list_rate=0,
            rate=10,
            doctype="Sales Invoice",
        )
        with pytest.raises(frappe.ValidationError, match="price list rate"):
            self.module._validate_rate_mismatch_create_request(zero)
        with (
            patch.object(
                self.module,
                "get_rate_mismatch_settings",
                return_value=frappe._dict(enabled=0),
            ),
            pytest.raises(
                frappe.ValidationError,
                match="Enable the current doctype",
            ),
        ):
            self.module._get_enabled_rate_mismatch_settings("Sales Invoice")
        with pytest.raises(frappe.ValidationError, match="document date"):
            self.module._resolve_required_valid_from(request)
        with patch.object(
            self.module,
            "get_rate_mismatch_settings",
            return_value=frappe._dict(enabled=1),
        ):
            assert self.module.is_rate_mismatch_enabled("Sales Invoice") is True
        with pytest.raises(frappe.ValidationError, match="Unsupported doctype"):
            self.module._ensure_supported_doctype("Unsupported")

    def test_context_existing_price_and_duplicate_policy_branches(self) -> None:
        valid_from = date(2026, 1, 1)
        settings = frappe._dict(throw_exception=0, update_item_price=0)
        result = self.module._build_item_price_mismatch_context(
            existing_item_price={
                "name": "IP-1",
                "price_list_rate": 5,
                "item_name": "Item",
                "packing_unit": None,
            },
            settings=settings,
            item_code="ITEM",
            valid_from=valid_from,
        )
        assert result["item_price_name"] == "IP-1"
        assert (
            self.module._build_item_price_mismatch_context(
                existing_item_price=None,
                settings=settings,
                item_code="ITEM",
                valid_from=valid_from,
            )["update_item_price"]
            is False
        )
        request = self.module.RateMismatchCreateRequest(
            item_code="ITEM",
            price_list="Retail",
            currency="USD",
            stock_uom="Nos",
            conversion_factor=1,
            price_list_rate=10,
            rate=10,
            doctype="Sales Invoice",
        )
        with (
            patch.object(
                self.module,
                "_throw_duplicate_valid_from_error",
                side_effect=frappe.ValidationError("duplicate"),
            ),
            pytest.raises(frappe.ValidationError),
        ):
            self.module._apply_existing_item_price_policy(
                existing_item_price={
                    "name": "IP-1",
                    "price_list_rate": 5,
                    "item_name": None,
                    "packing_unit": None,
                },
                settings=frappe._dict(throw_exception=1, update_item_price=0),
                request=request,
                valid_from=valid_from,
                effective_rate=10,
            )

    def test_scope_query_applies_every_optional_constraint(self) -> None:
        with (
            patch.object(self.module, "frappe", frappe),
            patch.object(frappe, "qb", MariaDB),
            patch.object(frappe, "db", SimpleNamespace(db_type="mariadb")),
        ):
            query = self.module._build_item_price_scope_query(
                scope=self.module.ItemPriceScope(
                    item_code="I",
                    price_list="P",
                    currency="USD",
                    stock_uom="Nos",
                    customer="C",
                    supplier="S",
                    batch_no="B",
                    packing_unit=0,
                ),
                valid_from=date(2026, 1, 1),
                price_list_rate=0,
                for_update=True,
                limit=2,
            )
        sql = str(query)
        for value in (
            "`item_code`='I'",
            "`price_list`='P'",
            "`currency`='USD'",
            "`uom`='Nos'",
            "IFNULL(`customer`,'')='C'",
            "IFNULL(`supplier`,'')='S'",
            "IFNULL(`batch_no`,'')='B'",
            "`packing_unit`=0",
            "`price_list_rate`=0",
            "`valid_from`='2026-01-01'",
            "ORDER BY IFNULL(`packing_unit`,0) DESC,`name`",
            "LIMIT 2",
            "FOR UPDATE",
        ):
            assert value in sql

        with (
            patch.object(self.module, "frappe", frappe),
            patch.object(frappe, "qb", MariaDB),
            patch.object(frappe, "db", SimpleNamespace(db_type="mariadb")),
        ):
            default_query = self.module._build_item_price_scope_query(
                scope=self.module.ItemPriceScope(
                    item_code="I",
                    price_list="P",
                    currency="USD",
                    stock_uom="Nos",
                )
            )
            unbounded_query = self.module._build_item_price_scope_query(
                scope=self.module.ItemPriceScope(
                    item_code="I",
                    price_list="P",
                    currency="USD",
                    stock_uom="Nos",
                ),
                limit=None,
            )
        assert "LIMIT 1" in str(default_query)
        assert "LIMIT" not in str(unbounded_query)

    def test_applicable_item_price_enforces_candidate_bound_and_forwards_quantity(
        self,
    ) -> None:
        query = MagicMock()
        scope = self.module.ItemPriceScope(
            item_code="I", price_list="P", currency="USD", stock_uom="Nos"
        )
        with patch.object(
            self.module,
            "_build_item_price_scope_query",
            return_value=query,
        ) as build_query:
            query.run.return_value = [{}] * (
                self.module.MAX_ITEM_PRICE_SCOPE_CANDIDATES + 1
            )
            with (
                patch.object(self.module, "_select_applicable_item_price") as select,
                pytest.raises(frappe.ValidationError, match="Too many"),
            ):
                self.module._find_applicable_item_price(scope=scope, quantity=4)
            select.assert_not_called()
            build_query.assert_called_once_with(
                scope=scope,
                valid_from=None,
                price_list_rate=None,
                for_update=False,
                limit=self.module.MAX_ITEM_PRICE_SCOPE_CANDIDATES + 1,
            )
            candidate = {
                "name": "IP-1",
                "price_list_rate": 1,
                "item_name": None,
                "packing_unit": None,
            }
            query.run.return_value = [candidate]
            build_query.reset_mock()
            with patch.object(
                self.module, "_select_applicable_item_price", return_value=candidate
            ) as select:
                assert (
                    self.module._find_applicable_item_price(scope=scope, quantity=6)
                    == candidate
                )
            select.assert_called_once_with([candidate], 6)
            build_query.assert_called_once_with(
                scope=scope,
                valid_from=None,
                price_list_rate=None,
                for_update=False,
                limit=self.module.MAX_ITEM_PRICE_SCOPE_CANDIDATES + 1,
            )
