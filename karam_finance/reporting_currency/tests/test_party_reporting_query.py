"""Execute the actual party report SQL against isolated in-memory ledger cases."""

import importlib
import sqlite3
from typing import TYPE_CHECKING, Any, override
from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe import _dict
from frappe.query_builder.builder import MariaDB

if TYPE_CHECKING:
    from pypika.queries import QueryBuilder

QUERY_MODULE = (
    "karam_finance.reporting_currency.report.trial_balance_for_party_(reporting_currency)."
    "tbfpr_query"
)


class TestPartyReportingQuery(TestCase):
    """Exercise selection, date boundaries and account grouping without site writes."""

    @override
    def setUp(self) -> None:
        """Run the real generated SELECT, not a mocked aggregate response."""
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE tabAccount (name TEXT, account_currency TEXT)")
        self.db.executemany(
            "INSERT INTO tabAccount VALUES (?, ?)",
            [("Payable A", "EUR"), ("Payable B", "EUR"), ("DOE Profit", "USD")],
        )
        self.db.execute("""CREATE TABLE `tabReporting Currency GLE`
            (company TEXT, account TEXT, party_type TEXT, party TEXT, posting_date TEXT,
             is_opening TEXT, is_cancelled INTEGER,
             reporting_debit REAL, reporting_credit REAL)""")
        self.enterContext(patch.object(frappe, "qb", MariaDB))
        database = self.db

        def run(query: QueryBuilder, *, as_dict: bool = False) -> list[Any]:
            assert as_dict
            cursor = database.execute(query.get_sql())
            fields = [column[0] for column in cursor.description]
            return [_dict(zip(fields, row, strict=True)) for row in cursor]

        self.enterContext(patch.object(MariaDB._BuilderClasss, "run", run, create=True))
        self.query = importlib.import_module(QUERY_MODULE)
        # Permission subqueries are verified against MariaDB in the integration suite.
        self.enterContext(
            patch.object(
                self.query,
                "_apply_source_permissions",
                side_effect=_unchanged_query,
            )
        )
        self.filters = _dict(
            company="Karam",
            party_type="Supplier",
            party="VEN-1",
            from_date="2025-01-01",
            to_date="2025-12-31",
        )

    def _entry(
        self, date: str, debit: float, credit: float, **overrides: object
    ) -> None:
        row: dict[str, Any] = {
            "company": "Karam",
            "account": "Payable A",
            "party_type": "Supplier",
            "party": "VEN-1",
            "posting_date": date,
            "is_opening": "No",
            "is_cancelled": 0,
            "reporting_debit": debit,
            "reporting_credit": credit,
        }
        row.update(overrides)
        self.db.execute(
            "INSERT INTO `tabReporting Currency GLE` VALUES (?,?,?,?,?,?,?,?,?)",
            tuple(row.values()),
        )

    def test_opening_movement_and_account_filter(self) -> None:
        """Opening and period amounts remain independent for each chart account."""
        self._entry("2024-12-31", 0, 100)
        self._entry("2025-01-01", 10, 0)
        self._entry("2025-12-31", 0, 5)
        self._entry("2025-06-01", 0, 20, is_opening="Yes")
        self._entry("2025-06-01", 0, 7, account="Payable B")
        # A correctly attributed DOE counterentry has no party, and is excluded.
        self._entry(
            "2024-12-31", 1000, 0, account="DOE Profit", party=None, party_type=None
        )
        for overrides in (
            {"is_cancelled": 1},
            {"company": "Other"},
            {"party_type": "Customer"},
            {"party": "VEN-2"},
        ):
            self._entry("2025-06-01", 1000, 0, **overrides)
        self._entry("2026-01-01", 1000, 0)
        result = self.query.get_reporting_currency_balances(self.filters)
        assert len(result["VEN-1"]) == 2
        first = result["VEN-1"][0]
        assert first == {
            "account": "Payable A",
            "account_currency": "EUR",
            "opening_debit": 0,
            "opening_credit": 120,
            "debit": 10,
            "credit": 5,
        }
        selected = self.query.get_reporting_currency_balances(
            self.filters, ["Payable B"]
        )
        assert len(selected["VEN-1"]) == 1
        assert selected["VEN-1"][0]["credit"] == 7

    def test_in_period_doe_rolls_into_next_opening(self) -> None:
        """A persisted No flag prevents a DOE from disappearing in its own period."""
        self._entry("2024-12-31", 0, 100)
        self._entry("2025-12-31", 10, 0)
        current = self.query.get_reporting_currency_balances(self.filters)["VEN-1"][0]
        self.filters.update(from_date="2026-01-01", to_date="2026-12-31")
        following = self.query.get_reporting_currency_balances(self.filters)["VEN-1"][0]
        assert current["debit"] == 10
        assert (
            current["opening_credit"] - current["debit"]
            == following["opening_credit"] - following["opening_debit"]
            == 90
        )


def _unchanged_query(query: Any, *_args: Any) -> Any:
    return query
