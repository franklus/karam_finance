"""Tests for profit and loss by cost centre report helpers."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

if TYPE_CHECKING:
    from types import ModuleType

MODULE_NAME = (
    "karam_finance.karam_general.report.profit_and_loss_statement_by_cost_center_(karam)."
    "profit_and_loss_statement_by_cost_center_(karam)"
)


def _load_module() -> ModuleType:
    return importlib.import_module(MODULE_NAME)


class TestProfitAndLossByCostCenterReport(FrappeTestCase):
    """Regression tests for refactored cost-centre P&L helpers."""

    def test_parse_multiselect_handles_comma_string(self) -> None:
        module = _load_module()

        values = module._parse_multiselect("A, B, C")

        assert values == ["A", "B", "C"]

    def test_normalise_scalar_handles_json_list(self) -> None:
        module = _load_module()

        value = module._normalise_scalar('["FY-2025","FY-2026"]')

        assert value == "FY-2025"

    def test_filter_out_zero_rows_keeps_group_with_valued_child(self) -> None:
        module = _load_module()
        rows = [
            {"cost_center": "Parent", "is_group": 1, "has_value": False},
            {"cost_center": "Child", "is_group": 0, "has_value": True},
        ]
        mapping = {"Parent": ["Child"]}

        filtered = module.filter_out_zero_value_rows(rows, mapping)

        assert [r["cost_center"] for r in filtered] == ["Parent", "Child"]

    def test_filter_out_zero_rows_keeps_all_valued_leaf_ancestors(self) -> None:
        module = _load_module()
        rows = [
            {"cost_center": "Root", "is_group": 1, "has_value": False},
            {"cost_center": "Parent", "is_group": 1, "has_value": False},
            {"cost_center": "Leaf", "is_group": 0, "has_value": True},
        ]
        mapping = {"Root": ["Parent"], "Parent": ["Leaf"]}

        filtered = module.filter_out_zero_value_rows(rows, mapping)

        assert [r["cost_center"] for r in filtered] == ["Root", "Parent", "Leaf"]

    def test_build_net_row_from_totals(self) -> None:
        module = _load_module()
        period_list = [type("Period", (), {"key": "p1"})()]

        row = module.build_net_row_from_totals(period_list, [100.0], [75.0])

        assert row["p1"] == 25.0

    def test_apply_growth_view_computes_period_on_period_percent(self) -> None:
        module = _load_module()
        period_list = [
            type("Period", (), {"key": "p1"})(),
            type("Period", (), {"key": "p2"})(),
            type("Period", (), {"key": "p3"})(),
        ]
        rows = [{"cost_center": "CC-A", "p1": 100.0, "p2": 150.0, "p3": 120.0}]

        module.apply_growth_view(rows, period_list)

        assert rows[0]["p1"] == 100.0
        assert rows[0]["p2"] == 50.0
        assert rows[0]["p3"] == -20.0

    def test_apply_growth_view_leaves_zero_basis_undefined(self) -> None:
        module = _load_module()
        period_list = [
            type("Period", (), {"key": "p1"})(),
            type("Period", (), {"key": "p2"})(),
        ]
        rows = [{"cost_center": "CC-A", "p1": 0.0, "p2": 100.0}]

        module.apply_growth_view(rows, period_list)

        assert rows[0]["p2"] is None

    def test_apply_margin_view_computes_percent_of_income(self) -> None:
        module = _load_module()
        period_list = [
            type("Period", (), {"key": "p1"})(),
            type("Period", (), {"key": "p2"})(),
        ]
        rows = [{"cost_center": "CC-A", "p1": 50.0, "p2": -25.0}]

        module.apply_margin_view(rows, period_list, [100.0, 50.0])

        assert rows[0]["p1"] == 50.0
        assert rows[0]["p2"] == 50.0

    def test_get_report_amounts_presents_income_as_positive_net_result(self) -> None:
        module = _load_module()
        period_list = [
            type(
                "Period",
                (),
                {
                    "key": "p1",
                    "from_date": "2026-01-01",
                    "to_date": "2026-01-31",
                },
            )()
        ]
        query_rows = [
            {
                "cost_center": "CC-A",
                "root_type": "Income",
                "account_currency": "USD",
                "period_key": "p1",
                "base_amount": -100.0,
                "account_amount": -100.0,
            },
            {
                "cost_center": "CC-A",
                "root_type": "Expense",
                "account_currency": "USD",
                "period_key": "p1",
                "base_amount": 25.0,
                "account_amount": 25.0,
            },
        ]

        with patch.object(
            module.pnlcc_data, "_run_amount_query", return_value=query_rows
        ):
            amounts, totals = module.pnlcc_data.get_report_amounts(
                company="Karam",
                periods=period_list,
                finance_book=None,
                include_default_fb=True,
                project_filters=None,
                restrict_cost_centers=None,
            )

        assert amounts == {"CC-A": {"p1": 75.0}}
        assert totals == {"Income": [100.0], "Expense": [25.0]}

    def test_get_report_amounts_pivots_bucketed_company_currency_rows(self) -> None:
        module = _load_module()
        period_list = [
            type(
                "Period",
                (),
                {
                    "key": "p1",
                    "from_date": "2026-01-01",
                    "to_date": "2026-01-31",
                },
            )(),
            type(
                "Period",
                (),
                {
                    "key": "p2",
                    "from_date": "2026-02-01",
                    "to_date": "2026-02-28",
                },
            )(),
        ]
        query_rows = [
            {
                "cost_center": "CC-A",
                "root_type": "Income",
                "period_key": "p1",
                "base_amount": -100.0,
            },
            {
                "cost_center": "CC-A",
                "root_type": "Expense",
                "period_key": "p2",
                "base_amount": 25.0,
            },
        ]

        with patch.object(
            module.pnlcc_data, "_run_amount_query", return_value=query_rows
        ) as run_query:
            amounts, totals = module.pnlcc_data.get_report_amounts(
                company="Karam",
                periods=period_list,
                finance_book=None,
                include_default_fb=True,
                project_filters=None,
                restrict_cost_centers=None,
            )

        assert amounts == {"CC-A": {"p1": 100.0, "p2": -25.0}}
        assert totals == {"Income": [100.0, 0.0], "Expense": [0.0, 25.0]}
        assert run_query.call_args.kwargs["needs_account_currency"] is False
