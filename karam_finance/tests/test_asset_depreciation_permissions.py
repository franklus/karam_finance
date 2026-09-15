"""Asset report totals respect native source GL permissions before aggregation."""

from typing import Any, override

import frappe
from frappe.desk.query_report import run
from frappe.tests import IntegrationTestCase


class TestAssetDepreciationPermissions(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "asset_permission_" + frappe.generate_hash(length=8)
        self.previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, self.previous_user)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        self.company = self.token
        self.asset = self.token + " Asset"
        self.expense = self.token + " Expense"
        self.accumulated = self.token + " Accumulated"
        self.allowed = self.token + " Allowed"
        self.denied = self.token + " Denied"
        self._insert(
            "Company",
            self.company,
            default_currency="USD",
            depreciation_expense_account=self.expense,
            accumulated_depreciation_account=self.accumulated,
        )
        for account, root_type in (
            (self.expense, "Expense"),
            (self.accumulated, "Asset"),
        ):
            self._insert(
                "Account",
                account,
                account_name=account,
                company=self.company,
                account_currency="USD",
                root_type=root_type,
                is_group=0,
            )
        self._insert("Asset Category", self.token, asset_category_name=self.token)
        self._insert(
            "Asset",
            self.asset,
            asset_name=self.asset,
            company=self.company,
            asset_category=self.token,
            purchase_date="2025-01-01",
            docstatus=1,
            status="Partially Depreciated",
            net_purchase_amount=5000,
            opening_accumulated_depreciation=0,
        )
        for label, department, amount in (
            ("A", self.allowed, 100),
            ("B", self.denied, 900),
        ):
            self._insert(
                "Department", department, department_name=department, is_group=0
            )
            for suffix, account, debit, credit, date in (
                ("expense", self.expense, amount, 0, "2026-01-15"),
                ("accumulated", self.accumulated, 0, amount, "2026-01-15"),
                ("opening", self.accumulated, 0, amount / 5, "2025-12-31"),
            ):
                self._insert(
                    "GL Entry",
                    self.token + label + suffix,
                    company=self.company,
                    account=account,
                    account_currency="USD",
                    department=department,
                    posting_date=date,
                    debit=debit,
                    credit=credit,
                    docstatus=1,
                    against_voucher_type="Asset",
                    against_voucher=self.asset,
                    voucher_type="Journal Entry",
                    voucher_no=self.token + label,
                    is_cancelled=0,
                    is_opening="No",
                )
        self.user = frappe.get_doc(
            {
                "doctype": "User",
                "email": self.token + "@example.com",
                "first_name": "Asset permission",
                "send_welcome_email": 0,
                "roles": [{"role": "Accounts User"}],
            }
        ).insert()
        self.permission = frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": self.user.name,
                "allow": "Department",
                "for_value": self.allowed,
                "apply_to_all_doctypes": 0,
                "applicable_for": "GL Entry",
            }
        ).insert()
        frappe.set_user(str(self.user.name))

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        # Stored posting fixtures isolate report reads from voucher creation policies.
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()

    def _report_row(self) -> dict[str, Any]:
        result = run(
            "Asset Depreciation Ledger Summary (Karam)",
            filters={
                "company": self.company,
                "from_date": "2026-01-01",
                "to_date": "2026-01-31",
            },
            ignore_prepared_report=True,
        )
        return next(row for row in result["result"] if row.get("asset") == self.asset)

    def test_report_excludes_restricted_movements_from_all_gl_totals(self) -> None:
        assert frappe.has_permission("Asset", "read", doc=self.asset)
        assert set(
            frappe.get_list(
                "GL Entry",
                filters={"company": self.company},
                pluck="name",
                limit=0,
            )
        ) == {
            self.token + "A" + suffix
            for suffix in ("expense", "accumulated", "opening")
        }
        row = self._report_row()
        assert row["depreciation_amount"] == 100
        assert row["opening_accumulated_depreciation"] == 20
        assert row["accumulated_depreciation"] == 120
        assert row["value_after_depreciation"] == 4880

    def test_location_permission_hides_asset_metadata_from_report(self) -> None:
        frappe.set_user("Administrator")
        for location in (self.allowed, self.denied):
            self._insert("Location", location, location_name=location, is_group=0)
        frappe.db.set_value("Asset", self.asset, "location", self.allowed)
        denied_asset = self.token + " Restricted Asset"
        self._insert(
            "Asset",
            denied_asset,
            asset_name=denied_asset,
            company=self.company,
            asset_category=self.token,
            location=self.denied,
            purchase_date="2025-01-01",
            docstatus=1,
            status="Partially Depreciated",
            net_purchase_amount=9000,
            opening_accumulated_depreciation=900,
        )
        frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": self.user.name,
                "allow": "Location",
                "for_value": self.allowed,
                "apply_to_all_doctypes": 0,
                "applicable_for": "Asset",
            }
        ).insert()
        frappe.clear_cache(user=self.user.name)
        frappe.set_user(str(self.user.name))
        assert frappe.has_permission("Asset", "read", doc=self.asset)
        assert not frappe.has_permission("Asset", "read", doc=denied_asset)
        result = run(
            "Asset Depreciation Ledger Summary (Karam)",
            filters={
                "company": self.company,
                "from_date": "2026-01-01",
                "to_date": "2026-01-31",
            },
            ignore_prepared_report=True,
        )
        names = {row.get("asset") for row in result["result"] if row.get("asset")}
        assert names == {self.asset}

    def test_unrestricted_reader_retains_complete_asset_totals(self) -> None:
        frappe.set_user("Administrator")
        self.permission.delete()
        frappe.clear_cache(user=self.user.name)
        frappe.set_user(str(self.user.name))
        row = self._report_row()
        assert row["depreciation_amount"] == 1000
        assert row["opening_accumulated_depreciation"] == 200
        assert row["accumulated_depreciation"] == 1200
        assert row["value_after_depreciation"] == 3800

    def test_account_restriction_excludes_accumulated_account_movements(self) -> None:
        frappe.set_user("Administrator")
        frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": self.user.name,
                "allow": "Account",
                "for_value": self.expense,
                "apply_to_all_doctypes": 0,
                "applicable_for": "GL Entry",
            }
        ).insert()
        frappe.clear_cache(user=self.user.name)
        frappe.set_user(str(self.user.name))
        row = self._report_row()
        assert row["depreciation_amount"] == 100
        assert row["opening_accumulated_depreciation"] == 0
        assert row["accumulated_depreciation"] == 0

    def test_restriction_scoped_to_another_doctype_does_not_reduce_totals(self) -> None:
        frappe.set_user("Administrator")
        self.permission.set("applicable_for", "Journal Entry")
        self.permission.save()
        frappe.clear_cache(user=self.user.name)
        frappe.set_user(str(self.user.name))
        assert self._report_row()["depreciation_amount"] == 1000
