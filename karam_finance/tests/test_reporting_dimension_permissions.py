"""Native reporting permissions when custom dimensions exist only on source GL."""

from typing import Any, override

import frappe
import frappe.share
from frappe.client import get as get_document
from frappe.desk.query_report import run
from frappe.tests import IntegrationTestCase


class TestReportingDimensionPermissions(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "finance_review_" + frappe.generate_hash(length=8)
        self.previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, self.previous_user)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        self.company = self.token
        self.account = self.token + " Income"
        self.parent_account = self.token + " Income group"
        self.allowed = self.token + " Allowed"
        self.restricted = self.token + " Restricted"
        self._accounts_and_centres()
        self.allowed_department = self.token + " Department A"
        self.denied_department = self.token + " Department B"
        for name in (self.allowed_department, self.denied_department):
            self._insert("Department", name, department_name=name, is_group=0)
        self._ledger()
        frappe.db.set_single_value(
            "Reporting Currency Settings", "reporting_currency", "USD"
        )
        for label, department in (
            ("A", self.allowed_department),
            ("B", self.denied_department),
        ):
            for suffix in ("period", "opening"):
                name = self.token + label + suffix
                frappe.db.set_value("GL Entry", name, "department", department)
                source = frappe.get_doc("GL Entry", name).as_dict()
                source.pop("doctype")
                source.pop("name")
                source.update(
                    gl_entry=name,
                    reporting_currency="USD",
                    reporting_doe=0,
                    manual_entry=0,
                    reporting_credit=source["credit"],
                    reporting_debit=0,
                )
                self._insert("Reporting Currency GLE", "RC-" + name, **source)
        self.user = frappe.get_doc(
            {
                "doctype": "User",
                "email": self.token + "@example.com",
                "first_name": "Finance review",
                "send_welcome_email": 0,
                "roles": [{"role": "Accounts User"}],
            }
        ).insert()
        frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": self.user.name,
                "allow": "Department",
                "for_value": self.allowed_department,
                "apply_to_all_doctypes": 0,
                "applicable_for": "GL Entry",
            }
        ).insert()
        frappe.set_user(str(self.user.name))

    def _accounts_and_centres(self) -> None:
        self._insert("Company", self.company, default_currency="USD")
        self._insert(
            "Account",
            self.parent_account,
            account_name="Review income group",
            company=self.company,
            account_currency="USD",
            root_type="Income",
            report_type="Profit and Loss",
            is_group=1,
            lft=1,
            rgt=4,
        )
        self._insert(
            "Account",
            self.account,
            account_name="Review income",
            company=self.company,
            account_currency="USD",
            root_type="Income",
            report_type="Profit and Loss",
            is_group=0,
            lft=2,
            rgt=3,
            parent_account=self.parent_account,
        )
        self._insert(
            "Fiscal Year",
            self.token,
            year_start_date="2026-01-01",
            year_end_date="2026-12-31",
            disabled=0,
        )
        for number, name in enumerate((self.allowed, self.restricted)):
            self._insert(
                "Cost Center",
                name,
                cost_center_name=name,
                company=self.company,
                is_group=0,
                lft=number * 2 + 1,
                rgt=number * 2 + 2,
            )

    def _ledger(self) -> None:
        for label, centre, amount in (
            ("A", self.allowed, 100),
            ("B", self.restricted, 900),
        ):
            for suffix, posting_date, credit in (
                ("period", "2026-01-15", amount),
                ("opening", "2025-12-31", amount / 5),
            ):
                self._insert(
                    "GL Entry",
                    self.token + label + suffix,
                    company=self.company,
                    account=self.account,
                    account_currency="USD",
                    cost_center=centre,
                    posting_date=posting_date,
                    credit=credit,
                    debit=0,
                    credit_in_account_currency=credit,
                    debit_in_account_currency=0,
                    voucher_type="Journal Entry",
                    voucher_no=self.token + label,
                    is_cancelled=0,
                    is_opening="No",
                    docstatus=1,
                )

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        # Minimal stored posting fixtures; public readers and real permissions are tested.
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()

    def test_trial_balance_applies_source_dimension_before_openings_and_totals(
        self,
    ) -> None:
        assert not frappe.get_meta("Reporting Currency GLE").has_field("department")
        assert set(
            frappe.get_list(
                "GL Entry", filters={"company": self.company}, pluck="name", limit=0
            )
        ) == {self.token + "Aperiod", self.token + "Aopening"}
        result = run(
            "Trial Balance (Reporting Currency)",
            filters={
                "company": self.company,
                "fiscal_year": self.token,
                "from_date": "2026-01-01",
                "to_date": "2026-01-31",
                "show_unclosed_fy_pl_balances": 1,
                "include_default_book_entries": 1,
                "show_group_accounts": 1,
            },
            ignore_prepared_report=True,
        )
        rows = result["result"]
        row = next(row for row in rows if row.get("account") == self.account)
        assert row["opening_credit"] == 20
        assert row["credit"] == 100
        assert row["closing_credit"] == 120
        parent = next(row for row in rows if row.get("account") == self.parent_account)
        assert parent["closing_credit"] == 120
        total = next(row for row in rows if row.get("is_total"))
        assert total["closing_credit"] == 120

    def test_general_ledger_omits_restricted_source_movements(self) -> None:
        result = run(
            "General Ledger (Reporting Currency)",
            filters={
                "company": self.company,
                "from_date": "2026-01-01",
                "to_date": "2026-01-31",
                "categorize_by": "Categorise by Account",
                "include_default_book_entries": 1,
            },
            ignore_prepared_report=True,
        )
        rows = result["result"]
        entries = [row for row in rows if row.get("gl_entry")]
        assert {row["gl_entry"] for row in entries} == {"RC-" + self.token + "Aperiod"}
        assert sum(row["credit"] for row in entries) == 100

    def test_lists_and_document_reads_reject_restricted_source_entries(self) -> None:
        visible = frappe.get_list(
            "Reporting Currency GLE",
            filters={"company": self.company},
            pluck="name",
            limit=0,
        )
        assert set(visible) == {
            "RC-" + self.token + "Aperiod",
            "RC-" + self.token + "Aopening",
        }
        assert (
            get_document("Reporting Currency GLE", "RC-" + self.token + "Aperiod")[
                "name"
            ]
            == "RC-" + self.token + "Aperiod"
        )
        with self.assertRaises(frappe.PermissionError):
            get_document("Reporting Currency GLE", "RC-" + self.token + "Bperiod")

    def test_unattributed_manual_and_doe_entries_are_hidden_from_dimension_restricted_users(
        self,
    ) -> None:
        frappe.set_user("Administrator")
        for label, manual, doe in (("Manual", 1, 0), ("DOE", 0, 1)):
            self._insert(
                "Reporting Currency GLE",
                self.token + label,
                company=self.company,
                account=self.account,
                posting_date="2026-01-15",
                reporting_currency="USD",
                reporting_credit=50,
                reporting_debit=0,
                manual_entry=manual,
                reporting_doe=doe,
                is_cancelled=0,
                docstatus=1,
            )
        frappe.set_user(str(self.user.name))
        visible = frappe.get_list(
            "Reporting Currency GLE",
            filters={"company": self.company},
            pluck="name",
            limit=0,
        )
        assert self.token + "Manual" not in visible
        assert self.token + "DOE" not in visible
        for label in ("Manual", "DOE"):
            with self.assertRaises(frappe.PermissionError):
                get_document("Reporting Currency GLE", self.token + label)
        self.test_trial_balance_applies_source_dimension_before_openings_and_totals()
        frappe.set_user("Administrator")
        visible = frappe.get_list(
            "Reporting Currency GLE",
            filters={"company": self.company},
            pluck="name",
            limit=0,
        )
        assert {self.token + "Manual", self.token + "DOE"} <= set(visible)
        permission = frappe.get_last_doc(
            "User Permission", filters={"user": self.user.name, "allow": "Department"}
        )
        permission.delete()
        frappe.clear_cache(user=self.user.name)
        frappe.set_user(str(self.user.name))
        visible = frappe.get_list(
            "Reporting Currency GLE",
            filters={"company": self.company},
            pluck="name",
            limit=0,
        )
        assert len(visible) == 6
        assert (
            get_document("Reporting Currency GLE", self.token + "Manual")["name"]
            == self.token + "Manual"
        )
        assert (
            get_document("Reporting Currency GLE", self.token + "DOE")["name"]
            == self.token + "DOE"
        )

    def test_party_trial_balance_filters_source_dimension_before_aggregation(
        self,
    ) -> None:
        frappe.set_user("Administrator")
        party = self.token + " Customer"
        self._insert("Customer", party, customer_name=party, customer_type="Company")
        for doctype in ("GL Entry", "Reporting Currency GLE"):
            frappe.db.set_value(
                doctype,
                {"company": self.company},
                {"party_type": "Customer", "party": party},
            )
        frappe.set_user(str(self.user.name))
        result = run(
            "Trial Balance for Party (Reporting Currency)",
            filters={
                "company": self.company,
                "party_type": "Customer",
                "fiscal_year": self.token,
                "from_date": "2026-01-01",
                "to_date": "2026-01-31",
            },
            ignore_prepared_report=True,
        )
        row = next(row for row in result["result"] if row.get("party") == party)
        assert row["opening_credit"] == 20
        assert row["credit"] == 100
        assert row["closing_credit"] == 120

    def test_sharing_a_snapshot_does_not_expand_source_report_totals(self) -> None:
        frappe.set_user("Administrator")
        frappe.share.add(
            "Reporting Currency GLE",
            "RC-" + self.token + "Bperiod",
            user=str(self.user.name),
            read=1,
        )
        frappe.set_user(str(self.user.name))
        self.test_trial_balance_applies_source_dimension_before_openings_and_totals()
        self.test_general_ledger_omits_restricted_source_movements()

    def test_source_permission_revocation_applies_without_sync(self) -> None:
        frappe.set_user("Administrator")
        # Alter source access after the snapshot was created; no Sync is required.
        frappe.db.set_value(
            "GL Entry", self.token + "Aperiod", "department", self.denied_department
        )
        frappe.set_user(str(self.user.name))
        assert set(
            frappe.get_list(
                "Reporting Currency GLE",
                filters={"company": self.company},
                pluck="name",
                limit=0,
            )
        ) == {"RC-" + self.token + "Aopening"}

    def test_restrictions_scoped_to_another_doctype_do_not_filter_reporting(
        self,
    ) -> None:
        frappe.set_user("Administrator")
        permission = frappe.get_last_doc(
            "User Permission", filters={"user": self.user.name, "allow": "Department"}
        )
        permission.set("applicable_for", "Journal Entry")
        permission.save()
        frappe.clear_cache(user=self.user.name)
        frappe.set_user(str(self.user.name))
        assert (
            len(
                frappe.get_list(
                    "Reporting Currency GLE",
                    filters={"company": self.company},
                    pluck="name",
                    limit=0,
                )
            )
            == 4
        )

    def test_two_custom_dimensions_intersect_instead_of_broadening_access(self) -> None:
        frappe.set_user("Administrator")
        branch = self.token + " Branch"
        self._insert("Branch", branch, branch=branch)
        frappe.db.set_value("GL Entry", self.token + "Aperiod", "branch", branch)
        frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": self.user.name,
                "allow": "Branch",
                "for_value": branch,
                "applicable_for": "GL Entry",
                "apply_to_all_doctypes": 0,
            }
        ).insert()
        frappe.db.set_single_value(
            "System Settings", "apply_strict_user_permissions", 1
        )
        frappe.clear_cache(user=self.user.name)
        frappe.set_user(str(self.user.name))
        visible = frappe.get_list(
            "Reporting Currency GLE",
            filters={"company": self.company},
            pluck="name",
            limit=0,
        )
        assert visible == ["RC-" + self.token + "Aperiod"]
