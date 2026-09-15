"""Real database/query and user-permission checks for an isolated Frappe site.

Synthetic ledger rows bypass document hooks deliberately: this suite tests reading
stored ledger data, not the synchronisation or document-creation lifecycle.
"""

import importlib
from decimal import Decimal
from typing import Any, override

import frappe
from frappe.tests import IntegrationTestCase

query = importlib.import_module(
    "karam_finance.reporting_currency.report.general_ledger_(reporting_currency).gl_query"
)


class TestReportingGLQueryIntegration(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.previous_user = frappe.session.user
        self.addCleanup(frappe.set_user, self.previous_user)
        frappe.set_user("Administrator")
        self.token = frappe.generate_hash(length=10)
        self.savepoint = "query_" + self.token
        frappe.db.savepoint(self.savepoint)
        self.addCleanup(frappe.db.rollback, save_point=self.savepoint)
        self.company = "Query Test " + self.token
        self._store("Company", self.company, default_currency="USD", abbr=self.token)
        self.allowed = self._account("Allowed", "USD")
        self.denied = self._account("Denied", "USD")
        self.eur = self._account("Euro", "EUR")
        self._seed_ledger()

    def _store(self, doctype: str, name: str, **values: Any) -> None:
        frappe.get_doc({"doctype": doctype, "name": name} | values).db_insert()

    def _account(self, label: str, currency: str) -> str:
        name = label + " " + self.token
        self._store(
            "Account",
            name,
            account_name=name,
            company=self.company,
            account_currency=currency,
            is_group=0,
            root_type="Asset",
        )
        return name

    def _seed_ledger(self) -> None:
        for index, (account, day, debit) in enumerate(
            (
                (self.allowed, "2025-12-31", "100.0001"),
                (self.allowed, "2026-01-01", "0.0001"),
                (self.allowed, "2026-01-31", "0.0002"),
                (self.allowed, "2026-02-01", "900.0000"),
                (self.denied, "2025-12-31", "700.0000"),
                (self.denied, "2026-01-15", "800.0000"),
                (self.eur, "2025-12-31", "3.0002"),
            )
        ):
            # A synced fixture must have a real source entry for permission checks.
            self._store(
                "GL Entry",
                f"SOURCE-{self.token}-{index}",
                company=self.company,
                account=account,
                posting_date=day,
                debit=debit,
                credit=0,
                account_currency="EUR" if account == self.eur else "USD",
                docstatus=1,
                is_cancelled=0,
            )
            self._store(
                "Reporting Currency GLE",
                f"QUERY-{self.token}-{index}",
                gl_entry=f"SOURCE-{self.token}-{index}",
                company=self.company,
                account=account,
                posting_date=day,
                account_currency="EUR" if account == self.eur else "USD",
                reporting_currency="USD",
                reporting_debit=debit,
                reporting_credit=0,
                debit_amount_in_account_currency=debit,
                credit_amount_in_account_currency=0,
                debit=debit,
                credit=0,
                manual_entry=0,
                reporting_doe=0,
                is_cancelled=0,
                is_opening="No",
                finance_book="",
            )

    def _filters(self, **options: Any) -> dict[str, Any]:
        return {
            "company": self.company,
            "from_date": "2026-01-01",
            "to_date": "2026-01-31",
            "presentation_currency": "USD",
            "categorize_by": "Categorise by Account",
        } | options

    def _restricted_user(self) -> str:
        email = f"query-{self.token}@example.invalid"
        frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": "Query test",
                "enabled": 1,
                "send_welcome_email": 0,
                "roles": [{"role": "Accounts User"}],
            }
        ).insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": email,
                "allow": "Account",
                "for_value": self.allowed,
                "apply_to_all_doctypes": 0,
                "applicable_for": "Reporting Currency GLE",
                "hide_descendants": 1,
            }
        ).insert(ignore_permissions=True)
        self.addCleanup(frappe.clear_cache, user=email)
        return email

    def test_restricted_user_cannot_read_denied_history_or_movements(self) -> None:
        # Positive control: excluded records exist and Administrator can read them.
        unrestricted = query.get_gl_entries(self._filters(), [])
        self.assertEqual(
            {row.account for row in unrestricted}, {self.allowed, self.denied, self.eur}
        )
        frappe.set_user(self._restricted_user())
        for enrich in (True, False):
            with self.subTest(enrich=enrich):
                rows = query.get_gl_entries(
                    self._filters(), [], enrich_opening_entries=enrich
                )
                self.assertEqual({row.account for row in rows}, {self.allowed})
                self.assertEqual(
                    sum((row.debit for row in rows), Decimal(0)), Decimal("100.0004")
                )
        openings = query.get_flat_account_currency_openings(
            self._filters(categorize_by="Flat Chronological")
        )
        self.assertEqual(openings, {(self.allowed, "USD"): Decimal("100.0001")})

    def test_compact_and_detailed_queries_preserve_totals_and_currencies(self) -> None:
        expected = {
            (self.allowed, "USD"): Decimal("100.0004"),
            (self.denied, "USD"): Decimal("1500.0000"),
            (self.eur, "EUR"): Decimal("3.0002"),
        }
        for enrich in (True, False):
            with self.subTest(enrich=enrich):
                rows = query.get_gl_entries(
                    self._filters(), [], enrich_opening_entries=enrich
                )
                totals: dict[tuple[str, str], Decimal] = {}
                for row in rows:
                    key = (row.account, row.account_currency)
                    totals[key] = (
                        totals.get(key, Decimal(0)) + row.debit_in_account_currency
                    )
                self.assertEqual(totals, expected)

    def test_period_query_keeps_both_boundary_dates(self) -> None:
        rows = query.get_gl_entries(
            self._filters(disable_opening_balance_calculation=1), []
        )
        allowed = [row for row in rows if row.account == self.allowed]
        self.assertEqual(
            [str(row.posting_date) for row in allowed], ["2026-01-01", "2026-01-31"]
        )
        self.assertEqual(
            sum((row.debit for row in allowed), Decimal(0)), Decimal("0.0003")
        )
