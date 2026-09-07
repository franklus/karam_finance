"""Exercise the public Party Trial Balance currency guard before aggregation."""

import importlib
from typing import override
from unittest import TestCase
from unittest.mock import patch

import frappe

REPORT = importlib.import_module(
    "karam_finance.reporting_currency.report.trial_balance_for_party_(reporting_currency)."
    "trial_balance_for_party_(reporting_currency)"
)


class TestPartyCurrencyGuard(TestCase):
    @override  # noqa: V105 - unittest and Frappe test lifecycle callback.
    def setUp(self) -> None:
        self.enterContext(patch.object(REPORT, "validate_filters"))
        self.enterContext(
            patch.object(REPORT, "is_party_name_visible", return_value=False)
        )
        self.enterContext(patch.object(REPORT, "get_columns", return_value=["columns"]))
        self.data = self.enterContext(
            patch.object(REPORT, "get_data", return_value=[{"debit": 10}])
        )
        self.enterContext(
            patch.object(frappe.db, "get_single_value", return_value="USD")
        )

    def test_mixed_or_missing_currency_blocks_aggregation(self) -> None:
        with (
            patch.object(frappe.db, "sql", return_value=[("bad-row",)]),
            self.assertRaises(frappe.ValidationError),
        ):
            REPORT.execute({"company": "Karam"})
        self.data.assert_not_called()

    def test_consistent_currency_preserves_report_output(self) -> None:
        with patch.object(frappe.db, "sql", return_value=[]) as sql:
            result = REPORT.execute({"company": "Karam"})
        assert result == (["columns"], [{"debit": 10}])
        assert self.data.call_args.args[0].presentation_currency == "USD"
        sql.assert_called_once()
        query = sql.call_args.args[0]
        assert "reporting_currency" in query
        assert "company" in query

    def test_requested_currency_must_match_settings(self) -> None:
        with self.assertRaises(frappe.ValidationError):
            REPORT.execute({"company": "Karam", "presentation_currency": "AED"})
        self.data.assert_not_called()
