"""Execute report CASE expressions against SQL NULLs and date boundaries."""

import importlib
import sqlite3
from unittest import TestCase

from frappe import _dict
from pypika import Table

query_module = importlib.import_module(
    "karam_finance.reporting_currency.report."
    "trial_balance_for_party_(reporting_currency).tbfpr_query"
)


class TestPartyDoePeriod(TestCase):
    def test_opening_flags_partition_doe_between_opening_and_movement(self) -> None:
        for flag in (None, "No", "Yes"):
            with self.subTest(is_opening=flag):
                current = self.balances(flag, posting_date="2026-01-15")
                assert current == ((12.5, 0) if flag == "Yes" else (0, 12.5))
                subsequent = self.balances(
                    flag, posting_date="2026-01-15", from_date="2026-02-01"
                )
                assert subsequent == (12.5, 0)
                assert sum(current) == sum(subsequent) == 12.5

    def test_null_and_non_opening_rows_include_both_period_boundaries(self) -> None:
        for flag in (None, "No"):
            for posting_date in ("2026-01-01", "2026-01-31"):
                with self.subTest(is_opening=flag, posting_date=posting_date):
                    assert self.balances(flag, posting_date=posting_date) == (0, 12.5)

    def test_future_doe_is_excluded_even_if_explicitly_opening(self) -> None:
        for flag in (None, "No", "Yes"):
            with self.subTest(is_opening=flag):
                assert self.balances(flag, posting_date="2026-02-01") == (0, 0)

    @staticmethod
    def balances(
        flag: str | None, *, posting_date: str, from_date: str = "2026-01-01"
    ) -> tuple[float, float]:
        filters = _dict(
            from_date=from_date,
            to_date="2026-02-28" if from_date.startswith("2026-02") else "2026-01-31",
        )
        table = Table("ledger")
        opening = query_module._opening_case(table, filters, table.amount)
        period = query_module._period_case(table, filters, table.amount)
        # SQLite executes the same portable CASE/NULL predicates emitted for MariaDB.
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute(
                "CREATE TABLE ledger (posting_date TEXT, is_opening TEXT, amount REAL)"
            )
            connection.execute(
                "INSERT INTO ledger VALUES (?, ?, ?)", (posting_date, flag, 12.5)
            )
            return connection.execute(
                f"SELECT {opening.get_sql()}, {period.get_sql()} FROM ledger"  # noqa: S608
            ).fetchone()
        finally:
            connection.close()
