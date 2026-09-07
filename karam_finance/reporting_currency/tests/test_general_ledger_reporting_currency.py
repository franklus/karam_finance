"""Tests for reporting-currency general ledger helpers."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

if TYPE_CHECKING:
    from types import ModuleType

MODULE_NAME = (
    "karam_finance.reporting_currency.report.general_ledger_(reporting_currency)."
    "general_ledger_(reporting_currency)"
)


def _load_module() -> ModuleType:
    return importlib.import_module(MODULE_NAME)


class TestGeneralLedgerReportingCurrency(FrappeTestCase):
    """Regression tests for modularised GL reporting-currency helpers."""

    def test_get_order_by_clause_for_voucher_grouping(self) -> None:
        module = _load_module()

        order_by = module._get_order_by_clause(
            {"categorize_by": "Categorise by Voucher"}
        )

        assert "voucher_type" in order_by
        assert "voucher_no" in order_by

    def test_insert_footer_separator_adds_single_blank_row(self) -> None:
        module = _load_module()
        rows = [
            {"account": "Debtors"},
            {"account": "'Total'"},
            {"account": "'Closing (Opening + Total)'"},
        ]

        with patch.object(module.frappe.db, "get_single_value", return_value="USD"):
            result = module._insert_footer_separator(rows)

        assert len(result) == 4
        assert result[1]["row_type"] == "separator"
        assert result[2]["account"] == "'Total'"

    def test_chunked_uses_requested_size(self) -> None:
        module = _load_module()

        chunks = list(module._chunked(["A", "B", "C", "D"], 2))

        assert chunks == [["A", "B"], ["C", "D"]]

    def test_ledger_projection_keeps_currency_layers_separate(self) -> None:
        module = _load_module()
        table = frappe.qb.DocType("Reporting Currency GLE")
        fields = module._gl_query._select_fields(table, {}, [])
        query = frappe.qb.from_(table).select(*fields)
        text, _parameters = query.walk()
        assert "CONCAT(`reporting_debit`" in text
        assert "debit_amount_in_account_currency" in text
        assert "debit_in_company_currency" in text
        assert "manual_entry" in text
        assert "reporting_doe" in text

    def test_account_grouping_keeps_opening_history_in_query(self) -> None:
        module = _load_module()
        table = frappe.qb.DocType("Reporting Currency GLE")
        filters = frappe._dict(
            categorize_by="Categorise by Account",
            from_date="2026-01-01",
            to_date="2026-12-31",
        )
        query = frappe.qb.from_(table).select(table.name)
        for condition in module._gl_query._build_qb_date_conditions(filters, table):
            query = query.where(condition)
        query_text, parameters = query.walk()
        assert "2026-01-01" not in parameters.values()
        assert "2026-12-31" in parameters.values()
        assert "Yes" not in parameters.values()
        assert " OR " not in query_text
