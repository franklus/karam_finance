"""Site-backed public report and reconciliation permission regressions."""

from __future__ import annotations

import importlib
from typing import Any, override

import frappe
from frappe.tests import IntegrationTestCase
from karam_finance.letter_reconciliation.doctype.letter_reconciliation import (
    letter_reconciliation as reconciliation,
)


class TestFinanceReviewPermissions(IntegrationTestCase):
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
        self._ledger()
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
                "allow": "Cost Center",
                "for_value": self.allowed,
                "apply_to_all_doctypes": 1,
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

    def test_reconciliation_rpc_requires_its_doctype_permission(self) -> None:
        with self.assertRaises(frappe.PermissionError):
            reconciliation.journal_entry_list(self.account)
        with self.assertRaises(frappe.PermissionError):
            reconciliation.set_letter([{"jv_row_name": "A"}], [{"jv_row_name": "B"}])

    def test_pnl_filters_sources_before_totals_chart_and_summary(self) -> None:
        report = importlib.import_module(
            "karam_finance.karam_general.report.profit_and_loss_statement_by_cost_center_(karam)."
            "profit_and_loss_statement_by_cost_center_(karam)"
        )
        result = report.execute(
            {
                "company": self.company,
                "filter_based_on": "Date Range",
                "period_start_date": "2026-01-01",
                "period_end_date": "2026-01-31",
                "periodicity": "Monthly",
                "include_default_book_entries": 1,
            }
        )
        assert result[5] == 100
        amounts = [row["values"] for row in result[3]["data"]["datasets"]]
        assert amounts == [[100], [0], [100]]
        rows = [row for row in result[1] if row.get("cost_center")]
        assert {row["cost_center"] for row in rows} == {self.allowed}
        footer = result[1][-1]["footer_rows"]
        assert [row["jan_2026"] for row in footer] == [100, 0, 100]

    def test_trial_balance_permissions_cover_openings_and_movements(self) -> None:
        rows = self._trial_balance_rows()
        account = next(row for row in rows if row.get("account") == self.account)
        assert account["opening_credit"] == 20
        assert account["credit"] == 100
        assert account["closing_credit"] == 120
        parent = next(row for row in rows if row.get("account") == self.parent_account)
        assert parent["closing_credit"] == 120
        total = next(row for row in rows if row.get("is_total"))
        assert total["closing_credit"] == 120

    def test_restricted_openings_bypass_account_closing_balances(self) -> None:
        frappe.set_user("Administrator")
        self._insert(
            "Period Closing Voucher",
            self.token + " Closing",
            company=self.company,
            period_end_date="2025-12-31",
            docstatus=1,
        )
        self._insert(
            "Account Closing Balance",
            self.token + " Closing balance",
            company=self.company,
            account=self.account,
            account_currency="USD",
            period_closing_voucher=self.token + " Closing",
            debit=0,
            credit=999,
            debit_in_account_currency=0,
            credit_in_account_currency=999,
            is_period_closing_voucher_entry=0,
        )
        frappe.db.set_single_value(
            "Accounts Settings", "ignore_account_closing_balance", 0
        )
        unrestricted = self._trial_balance_rows()
        assert (
            next(row for row in unrestricted if row.get("account") == self.account)[
                "opening_credit"
            ]
            == 999
        )
        frappe.set_user(str(self.user.name))
        restricted = self._trial_balance_rows()
        assert (
            next(row for row in restricted if row.get("account") == self.account)[
                "opening_credit"
            ]
            == 20
        )

    def test_project_permission_further_restricts_cost_centre_access(self) -> None:
        frappe.set_user("Administrator")
        self._insert("Project", self.token + " Project", project_name=self.token)
        frappe.db.set_value(
            "GL Entry", self.token + "Aperiod", "project", self.token + " Project"
        )
        frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": self.user.name,
                "allow": "Project",
                "for_value": self.token + " Project",
                "applicable_for": "GL Entry",
                "apply_to_all_doctypes": 0,
            }
        ).insert()
        frappe.db.set_single_value(
            "System Settings", "apply_strict_user_permissions", 1
        )
        frappe.clear_cache(user=self.user.name)
        frappe.set_user(str(self.user.name))
        rows = self._trial_balance_rows()
        account = next(row for row in rows if row.get("account") == self.account)
        assert account["credit"] == 100
        assert account["opening_credit"] == 0

    def _allow_source(self, doctype: str, name: str) -> None:
        frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": self.user.name,
                "allow": doctype,
                "for_value": name,
                "applicable_for": "GL Entry",
                "apply_to_all_doctypes": 0,
            }
        ).insert()
        frappe.clear_cache(user=self.user.name)

    def test_account_and_company_permissions_narrow_source_totals(self) -> None:
        frappe.set_user("Administrator")
        other = self.token + " Other"
        self._insert(
            "Account",
            other,
            company=self.company,
            root_type="Income",
            report_type="Profit and Loss",
            account_currency="USD",
            is_group=0,
            lft=5,
            rgt=6,
            account_name="Other",
        )
        self._insert(
            "GL Entry",
            other,
            company=self.company,
            account=other,
            cost_center=self.allowed,
            posting_date="2026-01-15",
            docstatus=1,
            credit=700,
            debit=0,
            credit_in_account_currency=700,
            debit_in_account_currency=0,
            is_cancelled=0,
            is_opening="No",
        )
        self._allow_source("Account", self.account)
        self._allow_source("Company", self.company)
        frappe.set_user(str(self.user.name))
        rows = self._trial_balance_rows()
        total = next(row for row in rows if row.get("is_total"))
        assert total["credit"] == 100
        assert total["opening_credit"] == 20
        assert all(not row.get("credit") for row in rows if row.get("account") == other)

    def test_ownership_permissions_restrict_opening_and_current_rows(self) -> None:
        frappe.set_user("Administrator")
        role = self.token + " Owner"
        self._insert("Role", role, role_name=role)
        permission = frappe.get_doc(
            {
                "doctype": "Custom DocPerm",
                "parent": "GL Entry",
                "role": role,
                "read": 1,
                "if_owner": 1,
            }
        ).insert()
        try:
            self.user.set("roles", [{"role": role}])
            self.user.save()
            frappe.db.set_value(
                "GL Entry", self.token + "Aperiod", "owner", self.user.name
            )
            frappe.clear_cache(doctype="GL Entry")
            frappe.set_user(str(self.user.name))
            rows = self._trial_balance_rows()
            total = next(row for row in rows if row.get("is_total"))
            assert total["credit"] == 100
            assert total["opening_credit"] == 0
        finally:
            frappe.set_user("Administrator")
            frappe.db.delete("Custom DocPerm", {"name": permission.name})
            frappe.clear_cache(doctype="GL Entry")

    def test_finance_book_and_opening_filters_further_narrow_permitted_rows(
        self,
    ) -> None:
        frappe.set_user("Administrator")
        book = self.token + " Book"
        self._insert("Finance Book", book, finance_book_name=book)
        frappe.db.set_value("GL Entry", self.token + "Aperiod", "finance_book", book)
        frappe.db.set_value(
            "GL Entry",
            self.token + "Aopening",
            {"finance_book": book, "posting_date": "2026-01-01", "is_opening": "Yes"},
        )
        frappe.set_user(str(self.user.name))
        rows = self._trial_balance_rows(
            finance_book=book, include_default_book_entries=0
        )
        total = next(row for row in rows if row.get("is_total"))
        assert total["opening_credit"] == 20
        assert total["credit"] == 100

    def test_custom_branch_permission_and_filter_narrow_all_report_surfaces(
        self,
    ) -> None:
        frappe.set_user("Administrator")
        branch = self.token + " Branch"
        self._insert("Branch", branch, branch=branch)
        frappe.db.set_value("GL Entry", self.token + "Aperiod", "branch", branch)
        self._allow_source("Branch", branch)
        frappe.db.set_single_value(
            "System Settings", "apply_strict_user_permissions", 1
        )
        frappe.set_user(str(self.user.name))
        self.test_pnl_filters_sources_before_totals_chart_and_summary()
        rows = self._trial_balance_rows(branch=[branch])
        total = next(row for row in rows if row.get("is_total"))
        assert total["credit"] == 100
        assert total["opening_credit"] == 0

    def test_company_permission_excludes_an_explicitly_requested_other_company(
        self,
    ) -> None:
        frappe.set_user("Administrator")
        other = self.token + " Other Company"
        self._insert("Company", other, default_currency="USD")
        self._insert("Cost Center", other, company=other, is_group=0, lft=1, rgt=2)
        self._insert(
            "Account",
            other,
            company=other,
            account_name="Foreign income",
            account_currency="USD",
            root_type="Income",
            report_type="Profit and Loss",
            is_group=0,
            lft=1,
            rgt=2,
        )
        self._insert(
            "GL Entry",
            other,
            company=other,
            account=other,
            cost_center=other,
            account_currency="USD",
            posting_date="2026-01-15",
            credit=800,
            credit_in_account_currency=800,
            docstatus=1,
            is_cancelled=0,
            is_opening="No",
        )
        self._allow_source("Cost Center", other)
        self._allow_source("Company", self.company)
        frappe.set_user(str(self.user.name))
        assert not any(
            row.get("credit") for row in self._trial_balance_rows(company=other)
        )

    def test_internal_gl_exclusions_preserve_same_number_payment_entries(self) -> None:
        frappe.set_user("Administrator")
        shared = self.token + " SHARED"
        self._insert(
            "Journal Entry",
            shared,
            company=self.company,
            docstatus=1,
            voucher_type="Exchange Rate Revaluation",
            is_system_generated=1,
        )
        frappe.db.set_single_value(
            "Reporting Currency Settings", "reporting_currency", "USD"
        )
        for label, voucher_type in (("A", "Journal Entry"), ("B", "Payment Entry")):
            name = self.token + label + "period"
            frappe.db.set_value(
                "GL Entry", name, {"voucher_type": voucher_type, "voucher_no": shared}
            )
            values = frappe.get_doc("GL Entry", name).as_dict()
            values.pop("doctype")
            values.pop("name")
            values.update(
                gl_entry=name,
                reporting_currency="USD",
                reporting_doe=0,
                manual_entry=0,
                reporting_debit=0,
                reporting_credit=values["credit"],
            )
            self._insert("Reporting Currency GLE", "RC-" + name, **values)
        for journal_type, flag in (
            ("Exchange Rate Revaluation", "ignore_err"),
            ("Credit Note", "ignore_cr_dr_notes"),
            ("Debit Note", "ignore_cr_dr_notes"),
        ):
            frappe.db.set_value("Journal Entry", shared, "voucher_type", journal_type)
            for namespace in (
                "karam_general.report.general_ledger_(karam)",
                "reporting_currency.report.general_ledger_(reporting_currency)",
            ):
                with self.subTest(report=namespace, journal_type=journal_type):
                    query = importlib.import_module(
                        "karam_finance." + namespace + ".gl_query"
                    )
                    entries = query.get_gl_entries(
                        {
                            "company": self.company,
                            "from_date": "2026-01-01",
                            "to_date": "2026-01-31",
                            flag: 1,
                            "presentation_currency": "USD",
                        },
                        [],
                    )
                    assert len(entries) == 1
                    assert entries[0].voucher_type == "Payment Entry"
                    assert entries[0].voucher_no == shared

    def test_ordinary_currency_save_cannot_relabel_an_existing_ledger(self) -> None:
        frappe.set_user("Administrator")
        frappe.db.set_single_value(
            "Reporting Currency Settings", "reporting_currency", "USD"
        )
        self._insert(
            "Reporting Currency GLE",
            self.token + " Manual",
            company=self.company,
            account=self.account,
            manual_entry=1,
            reporting_currency="USD",
            posting_date="2026-01-15",
        )
        settings = frappe.get_single("Reporting Currency Settings")
        settings.set("reporting_currency", "EUR")
        with self.assertRaisesRegex(
            frappe.ValidationError, "Confirm the reporting currency change"
        ):
            settings.save()
        assert (
            frappe.db.get_single_value(
                "Reporting Currency Settings", "reporting_currency"
            )
            == "USD"
        )

    def _trial_balance_rows(self, **overrides: Any) -> list[Any]:
        report = importlib.import_module(
            "karam_finance.karam_general.report.trial_balance_(karam).trial_balance_(karam)"
        )
        filters = frappe._dict(
            company=self.company,
            fiscal_year=self.token,
            from_date="2026-01-01",
            to_date="2026-01-31",
            show_unclosed_fy_pl_balances=1,
            include_default_book_entries=1,
            show_group_accounts=1,
        )
        filters.update(overrides)
        _columns, rows = report.execute(filters)
        return rows
