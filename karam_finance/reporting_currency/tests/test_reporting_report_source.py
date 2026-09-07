"""Execute reporting-currency projections against independent fixture rows."""

import importlib
import re
import sqlite3
from typing import Any, override
from unittest import TestCase
from unittest.mock import patch

import frappe

from karam_finance.reporting_currency.report.reporting_source import amount


def unchanged_query(query: Any, *_args: Any, **_kwargs: Any) -> Any:
    return query


class TestReportingReportSource(TestCase):
    @override  # noqa: V105 - unittest and Frappe test lifecycle callback.
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.addCleanup(self.connection.close)
        self.connection.execute(
            'CREATE TABLE "tabReporting Currency GLE" ('
            "name TEXT, account TEXT, company TEXT, account_currency TEXT, "
            "posting_date TEXT, is_cancelled INT, is_opening TEXT, voucher_type TEXT, "
            "reporting_doe INT, manual_entry INT, reporting_debit REAL, reporting_credit REAL, "
            "debit REAL, credit REAL, debit_amount_in_account_currency REAL, "
            "credit_amount_in_account_currency REAL)"
        )
        self.connection.executemany(
            'INSERT INTO "tabReporting Currency GLE" VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [
                (
                    "normal",
                    "Bank",
                    "Company",
                    "USD",
                    "2026-01-01",
                    0,
                    "No",
                    "Journal Entry",
                    0,
                    0,
                    2,
                    0,
                    180000,
                    0,
                    2,
                    0,
                ),
                (
                    "manual",
                    "Bank",
                    "Company",
                    "USD",
                    "2026-01-01",
                    0,
                    None,
                    None,
                    0,
                    1,
                    7,
                    0,
                    999,
                    0,
                    999,
                    0,
                ),
                (
                    "doe",
                    "Bank",
                    "Company",
                    "USD",
                    "2026-01-01",
                    0,
                    None,
                    "Exchange Rate Revaluation",
                    1,
                    0,
                    0,
                    3,
                    0,
                    999,
                    0,
                    999,
                ),
            ],
        )

    def run_query(
        self, query: str, values: dict[str, Any], **_kwargs: Any
    ) -> list[Any]:
        sql = re.sub(r"%\((\w+)\)s", r":\1", query)
        cursor = self.connection.execute(sql, values)
        columns = [column[0] for column in cursor.description]
        return [
            frappe._dict(zip(columns, row, strict=True)) for row in cursor.fetchall()
        ]

    def test_adjustments_only_contribute_to_reporting_layer(self) -> None:
        table = frappe.qb.DocType("Reporting Currency GLE")
        query = frappe.qb.from_(table).select(
            table.name,
            amount(table, "debit").as_("reporting"),
            amount(table, "debit_in_company_currency").as_("company"),
            amount(table, "debit_in_account_currency").as_("account"),
        )
        rows = {row.name: row for row in self.run_query(*query.walk())}
        assert rows["normal"].company == 180000
        assert rows["normal"].account == 2
        assert rows["manual"].reporting == 7
        assert rows["manual"].company == rows["manual"].account == 0
        assert rows["doe"].company == rows["doe"].account == 0

    def test_trial_balance_includes_null_voucher_and_opening_flags(self) -> None:
        module = importlib.import_module(
            "karam_finance.reporting_currency.report.trial_balance_(reporting_currency).tbk_query"
        )
        filters = frappe._dict(
            company="Company", from_date="2026-01-01", to_date="2026-12-31"
        )
        with (
            patch.object(
                module,
                "apply_gl_filters",
                side_effect=unchanged_query,
            ),
            patch.object(frappe.db, "sql", side_effect=self.run_query),
        ):
            rows = module.get_period_balances(filters)
        assert rows["Bank"]["debit"] == 9
        assert rows["Bank"]["credit"] == 3
        assert rows["Bank"]["debit_in_account_currency"] == 2
        assert rows["Bank"]["credit_in_account_currency"] == 0

    def test_voucher_union_binds_names_through_query_builder(self) -> None:
        module = importlib.import_module(
            "karam_finance.reporting_currency.report.general_ledger_(reporting_currency).gl_enrichment"
        )
        names = {"Journal Entry": {"JE'quoted"}, "Payment Entry": {"PE-001"}}
        with (
            patch.object(
                module,
                "_get_karam_fields_for_doctype",
                return_value=["name", "karam_series", "translation"],
            ),
            patch.object(frappe.db, "sql", return_value=[]) as sql,
        ):
            assert module._fetch_voucher_data(names) == {}
        query, parameters = sql.call_args.args[:2]
        assert "UNION ALL" in query
        assert "JE'quoted" not in query
        assert "JE'quoted" in parameters.values()
        assert "PE-001" in parameters.values()
