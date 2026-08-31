# ruff: noqa: D102
"""Tests for reporting-currency general ledger helpers."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

if TYPE_CHECKING:
    from types import ModuleType

MODULE_NAME = (
    "karam_finance.reporting_currency.report.general_ledger_(reporting)."
    "general_ledger_(reporting)"
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
        assert result[1]["account"] is None
        assert result[2]["account"] == "'Total'"

    def test_chunked_uses_requested_size(self) -> None:
        module = _load_module()

        chunks = list(module._chunked(["A", "B", "C", "D"], 2))

        assert chunks == [["A", "B"], ["C", "D"]]
