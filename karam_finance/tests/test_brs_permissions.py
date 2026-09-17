"""Restricted-user behaviour through the native Bank Reconciliation report API."""

from typing import Any, override
from unittest.mock import patch

import frappe
import frappe.share
from frappe.desk.query_report import run
from frappe.tests import IntegrationTestCase
from frappe.tests.classes.context_managers import patch_hooks

REPORT = "Bank Reconciliation Statement (Karam)"


class TestBRSPermissions(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "brs_permission_" + frappe.generate_hash(length=8)
        self.previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, self.previous_user)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        self.company = self.token + " Allowed"
        self.denied_company = self.token + " Denied"
        self.account = self.company + " Bank"
        self.denied_account = self.denied_company + " Bank"
        for company, account in (
            (self.company, self.account),
            (self.denied_company, self.denied_account),
        ):
            self._insert("Company", company, default_currency="USD")
            self._insert(
                "Account",
                account,
                company=company,
                account_name="Bank",
                account_currency="USD",
                account_type="Bank",
                root_type="Asset",
                report_type="Balance Sheet",
                is_group=0,
                lft=1,
                rgt=2,
            )
        self.user = frappe.get_doc(
            {
                "doctype": "User",
                "email": self.token + "@example.com",
                "first_name": "BRS permission",
                "send_welcome_email": 0,
                "roles": [{"role": "Accounts User"}],
            }
        ).insert()
        self._allow("Company", self.company)

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()

    def _allow(self, doctype: str, name: str) -> None:
        frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": self.user.name,
                "allow": doctype,
                "for_value": name,
                "apply_to_all_doctypes": 1,
            }
        ).insert()
        frappe.clear_cache(user=self.user.name)

    def _report(self, **overrides: Any) -> dict[str, Any]:
        filters = {
            "company": self.company,
            "account": self.account,
            "report_date": "2026-01-31",
        }
        filters.update(overrides)
        return run(REPORT, filters=filters, ignore_prepared_report=True)

    def test_denied_company_is_rejected_without_client_filter_metadata(self) -> None:
        frappe.set_user(str(self.user.name))
        with self.assertRaises(frappe.PermissionError):
            self._report(company=self.denied_company, account=self.denied_account)

    def test_denied_account_is_rejected_within_an_allowed_company(self) -> None:
        other = self.token + " Other bank"
        self._insert(
            "Account",
            other,
            company=self.company,
            account_name="Other bank",
            account_currency="USD",
            is_group=0,
            lft=3,
            rgt=4,
        )
        self._allow("Account", self.account)
        frappe.set_user(str(self.user.name))
        with self.assertRaises(frappe.PermissionError):
            self._report(account=other)

    def test_account_must_belong_to_the_selected_company(self) -> None:
        with self.assertRaises(frappe.ValidationError):
            self._report(account=self.denied_account)

    def test_owned_and_shared_payments_are_included_without_other_owners(self) -> None:
        permission = frappe.get_doc(
            {
                "doctype": "Custom DocPerm",
                "parent": "Payment Entry",
                "role": "Accounts User",
                "read": 1,
                "if_owner": 1,
            }
        ).insert()
        self.addCleanup(frappe.clear_cache, doctype="Payment Entry")
        assert permission.name
        frappe.clear_cache(doctype="Payment Entry")
        for label, amount, owner in (
            ("owned", 20, self.user.name),
            ("denied", 180, "Administrator"),
            ("shared", 7, "Administrator"),
        ):
            self._insert(
                "Payment Entry",
                self.token + label,
                owner=owner,
                company=self.company,
                docstatus=1,
                paid_to=self.account,
                received_amount_after_tax=amount,
                paid_amount_after_tax=0,
                paid_to_account_currency="USD",
                posting_date="2026-01-01",
            )
        frappe.db.set_value(
            "Payment Entry", self.token + "owned", "owner", self.user.name
        )
        frappe.share.add(
            "Payment Entry", self.token + "shared", user=self.user.name, read=1
        )
        frappe.set_user(str(self.user.name))
        rows = [row for row in self._report()["result"] if isinstance(row, dict)]
        summary = next(
            row
            for row in rows
            if row.get("payment_entry") == "Outstanding Cheques and Deposits to clear"
        )
        assert summary["debit"] == 27
        assert {
            row["payment_entry"] for row in rows if row.get("payment_document")
        } == {self.token + "owned", self.token + "shared"}

    def test_party_title_lookup_does_not_bypass_supplier_read_permission(self) -> None:
        frappe.get_doc(
            {
                "doctype": "Custom DocPerm",
                "parent": "Supplier",
                "role": "System Manager",
                "read": 1,
            }
        ).insert()
        self.addCleanup(frappe.clear_cache, doctype="Supplier")
        frappe.clear_cache(doctype="Supplier")
        self._insert("Supplier", self.token, supplier_name="Private supplier title")
        self._insert(
            "Journal Entry",
            self.token,
            company=self.company,
            docstatus=1,
            posting_date="2026-01-01",
            is_opening="No",
        )
        self._insert(
            "Journal Entry Account",
            self.token,
            parent=self.token,
            parenttype="Journal Entry",
            parentfield="accounts",
            account=self.account,
            party_type="Supplier",
            party=self.token,
            debit_in_account_currency=20,
            credit_in_account_currency=0,
            account_currency="USD",
        )
        frappe.set_user(str(self.user.name))
        assert not frappe.has_permission("Supplier", "read")
        rows = [row for row in self._report()["result"] if isinstance(row, dict)]
        assert all(row.get("party_name") != "Private supplier title" for row in rows)

    def test_cost_centre_permissions_apply_before_all_payment_totals(self) -> None:
        for suffix, amount, movement, incorrect in (
            ("Allowed", 100, 20, 7),
            ("Denied", 900, 180, 63),
        ):
            centre = self.token + suffix
            self._insert(
                "Cost Center",
                centre,
                company=self.company,
                cost_center_name=centre,
                is_group=0,
                lft=1 if suffix == "Allowed" else 3,
                rgt=2 if suffix == "Allowed" else 4,
            )
            self._insert(
                "GL Entry",
                centre,
                company=self.company,
                account=self.account,
                cost_center=centre,
                posting_date="2026-01-01",
                debit_in_account_currency=amount,
                credit_in_account_currency=0,
                is_cancelled=0,
            )
            for name, posting, cleared, value in (
                (centre, "2026-01-01", None, movement),
                (centre + "future", "2026-02-01", "2026-01-15", incorrect),
            ):
                self._insert(
                    "Payment Entry",
                    name,
                    company=self.company,
                    cost_center=centre,
                    docstatus=1,
                    paid_to=self.account,
                    received_amount_after_tax=value,
                    paid_amount_after_tax=0,
                    paid_to_account_currency="USD",
                    posting_date=posting,
                    clearance_date=cleared,
                )
        self._allow("Cost Center", self.token + "Allowed")
        unrestricted = [
            row for row in self._report()["result"] if isinstance(row, dict)
        ]
        assert (
            next(
                row
                for row in unrestricted
                if row.get("payment_entry") == "Calculated Bank Statement balance"
            )["debit"]
            == 870
        )
        frappe.set_user(str(self.user.name))
        # Frappe appends an automatic total as a list; that separate defect is issue 07.
        rows = [row for row in self._report()["result"] if isinstance(row, dict)]
        summary = {row.get("payment_entry"): row for row in rows}
        assert summary["Bank Statement balance as per General Ledger"]["debit"] == 100
        assert summary["Outstanding Cheques and Deposits to clear"]["debit"] == 20
        assert summary["Cheques and Deposits incorrectly cleared"]["debit"] == 7
        assert summary["Calculated Bank Statement balance"]["debit"] == 87
        assert {
            row["payment_entry"] for row in rows if row.get("payment_document")
        } == {self.token + "Allowed"}

    def test_journal_child_dimensions_are_checked_before_totals(self) -> None:
        for suffix, amount in (("Allowed", 20), ("Denied", 180)):
            name = self.token + suffix
            self._insert(
                "Cost Center",
                name,
                company=self.company,
                cost_center_name=name,
                is_group=0,
                lft=1 if suffix == "Allowed" else 3,
                rgt=2 if suffix == "Allowed" else 4,
            )
            self._insert(
                "Journal Entry",
                name,
                company=self.company,
                docstatus=1,
                posting_date="2026-01-01",
                is_opening="No",
            )
            self._insert(
                "Journal Entry Account",
                name,
                parent=name,
                parenttype="Journal Entry",
                parentfield="accounts",
                account=self.account,
                cost_center=name,
                debit_in_account_currency=amount,
                credit_in_account_currency=0,
                account_currency="USD",
            )
        self._allow("Cost Center", self.token + "Allowed")
        frappe.set_user(str(self.user.name))
        assert frappe.has_permission(
            "Journal Entry", doc=frappe.get_doc("Journal Entry", self.token + "Allowed")
        )
        assert not frappe.has_permission(
            "Journal Entry", doc=frappe.get_doc("Journal Entry", self.token + "Denied")
        )
        rows = [row for row in self._report()["result"] if isinstance(row, dict)]
        outstanding = next(
            row
            for row in rows
            if row.get("payment_entry") == "Outstanding Cheques and Deposits to clear"
        )
        assert outstanding["debit"] == 20
        assert {
            row["payment_entry"] for row in rows if row.get("payment_document")
        } == {self.token + "Allowed"}

    def test_invoice_permissions_cover_outstanding_and_incorrect_clearance(
        self,
    ) -> None:
        for allowed, factor, future in (
            (True, 1, False),
            (True, 1, True),
            (False, 9, False),
            (False, 9, True),
        ):
            suffix = str(allowed) + str(future)
            posting = "2026-02-01" if future else "2026-01-01"
            clearance = "2026-01-15" if future else None
            purchase, sale = self.token + "PI" + suffix, self.token + "SI" + suffix
            self._insert(
                "Purchase Invoice",
                purchase,
                company=self.company,
                docstatus=1,
                is_paid=1,
                cash_bank_account=self.account,
                paid_amount=(7 if future else 20) * factor,
                base_paid_amount=(7 if future else 20) * factor,
                posting_date=posting,
                clearance_date=clearance,
            )
            self._insert(
                "Sales Invoice",
                sale,
                company=self.company,
                docstatus=1,
                is_pos=1,
                posting_date=posting,
            )
            self._insert(
                "Sales Invoice Payment",
                sale,
                parent=sale,
                parenttype="Sales Invoice",
                parentfield="payments",
                account=self.account,
                amount=(11 if future else 30) * factor,
                base_amount=(11 if future else 30) * factor,
                clearance_date=clearance,
            )
            if allowed:
                self._allow("Purchase Invoice", purchase)
                self._allow("Sales Invoice", sale)
        frappe.set_user(str(self.user.name))
        rows = [
            row
            for row in self._report(include_pos_transactions=1)["result"]
            if isinstance(row, dict)
        ]
        summary = {row.get("payment_entry"): row for row in rows}
        assert summary["Outstanding Cheques and Deposits to clear"]["debit"] == 30
        assert summary["Outstanding Cheques and Deposits to clear"]["credit"] == 20
        assert summary["Cheques and Deposits incorrectly cleared"]["debit"] == 4
        assert summary["Calculated Bank Statement balance"]["credit"] == 6

    def test_unverifiable_extension_total_is_rejected_for_restricted_user(self) -> None:
        frappe.set_user(str(self.user.name))
        with (
            patch_hooks(
                {
                    "get_amounts_not_reflected_in_system_for_bank_reconciliation_statement": [
                        "frappe.utils.flt"
                    ]
                }
            ),
            self.assertRaises(frappe.PermissionError),
        ):
            self._report()

    def test_missing_gl_read_access_is_rejected(self) -> None:
        frappe.get_doc(
            {
                "doctype": "Custom DocPerm",
                "parent": "GL Entry",
                "role": "System Manager",
                "read": 1,
            }
        ).insert()
        self.addCleanup(frappe.clear_cache, doctype="GL Entry")
        frappe.clear_cache(doctype="GL Entry")
        frappe.set_user(str(self.user.name))
        with self.assertRaises(frappe.PermissionError):
            self._report()

    def test_source_permission_queries_are_batched_across_transactions(self) -> None:
        def payment(number: int) -> None:
            self._insert(
                "Payment Entry",
                self.token + str(number),
                company=self.company,
                docstatus=1,
                paid_to=self.account,
                received_amount_after_tax=1,
                paid_amount_after_tax=0,
                paid_to_account_currency="USD",
                posting_date="2026-01-01",
            )

        def query_count(expected_amount: int) -> int:
            self._report()  # Warm framework metadata and permission caches equally.
            with patch.object(frappe.db, "sql", wraps=frappe.db.sql) as sql:
                rows = self._report()["result"]
            outstanding = next(
                row
                for row in rows
                if isinstance(row, dict)
                and row.get("payment_entry")
                == "Outstanding Cheques and Deposits to clear"
            )
            assert outstanding["debit"] == expected_amount
            return sql.call_count

        payment(0)
        frappe.set_user(str(self.user.name))
        one_source = query_count(1)
        frappe.set_user("Administrator")
        for number in range(1, 31):
            payment(number)
        frappe.set_user(str(self.user.name))
        many_sources = query_count(31)
        self.assertLessEqual(many_sources, one_source + 2, (one_source, many_sources))
