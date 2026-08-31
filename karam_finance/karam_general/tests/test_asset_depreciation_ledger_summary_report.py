"""Contract and regression tests for Asset Depreciation Ledger Summary."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from unittest import TestCase
from unittest.mock import MagicMock, patch

from frappe import _dict
from pypika import Table

if TYPE_CHECKING:
    from types import ModuleType

MODULE_NAME = (
    "karam_finance.karam_general.report.asset_depreciation_ledger_summary."
    "asset_depreciation_ledger_summary"
)


def _load_module() -> ModuleType:
    return importlib.import_module(MODULE_NAME)


class TestAssetDepreciationLedgerSummaryReport(TestCase):
    """Regression tests for the public report boundary and as-of arithmetic."""

    def test_execute_preserves_one_row_per_asset_contract(self) -> None:
        """The Script Report returns columns and exactly one row per Asset."""
        module = _load_module()
        filters = _dict(
            company="Karam",
            from_date="2024-02-01",
            to_date="2024-03-31",
            include_default_book_assets=0,
        )
        assets = {
            "AST-001": _dict(
                asset="AST-001",
                asset_name="Asset One",
                status="Partially Depreciated",
                asset_category="Machinery",
                purchase_date="2023-12-15",
                net_purchase_amount=5000,
                opening_accumulated_depreciation=100,
                opening_number_of_booked_depreciations=1,
                total_number_of_depreciations=10,
            ),
            "AST-002": _dict(
                asset="AST-002",
                asset_name="Asset Two",
                status="Submitted",
                asset_category="IT Equipment",
                purchase_date="2024-01-05",
                net_purchase_amount=3000,
                opening_accumulated_depreciation=40,
                opening_number_of_booked_depreciations=2,
                total_number_of_depreciations=12,
            ),
        }

        with (
            patch.object(module, "_", side_effect=lambda value: value),
            patch.object(module, "_resolve_finance_book", return_value=None),
            patch.object(module, "_get_assets_details", return_value=assets),
            patch.object(module, "_get_schedule_details", return_value={}),
            patch.object(module, "_get_gl_totals", return_value={}),
            patch.object(module, "_get_booked_depreciation_counts", return_value={}),
        ):
            columns, data = module.execute(filters)

        assert [column["fieldname"] for column in columns] == [
            "asset",
            "asset_name",
            "status",
            "asset_category",
            "purchase_date",
            "purchase_amount",
            "total_no_of_depreciations",
            "opening_no_of_booked_depreciations",
            "pending_depreciations",
            "opening_accumulated_depreciation",
            "depreciation_amount",
            "accumulated_depreciation",
            "value_after_depreciation",
        ]
        assert [row.asset for row in data] == ["AST-001", "AST-002"]
        assert data[0].purchase_amount == 5000
        assert data[1].purchase_amount == 3000

    def test_reversals_and_disposals_reduce_accumulated_depreciation(self) -> None:
        """Accumulated-account debits for reversals/disposals reduce the balance."""
        module = _load_module()
        filters = _dict(from_date="2024-02-01", to_date="2024-03-31")
        assets = {
            "AST-DISPOSAL": _dict(
                asset="AST-DISPOSAL",
                asset_name="Disposed Asset",
                status="Scrapped",
                asset_category="Machinery",
                purchase_date="2023-01-01",
                disposal_date="2024-03-15",
                net_purchase_amount=1000,
                opening_accumulated_depreciation=150,
                opening_number_of_booked_depreciations=1,
                total_number_of_depreciations=10,
            )
        }
        schedules = {
            "AST-DISPOSAL": _dict(
                name="ADS-DISPOSAL",
                total_number_of_depreciations=10,
                opening_accumulated_depreciation=150,
                opening_number_of_booked_depreciations=1,
            )
        }
        # +40 normal depreciation, -10 reversal, -180 disposal removal.
        gl_totals = {
            "AST-DISPOSAL": _dict(
                opening_accumulated_activity=0,
                accumulated_activity=-150,
                depreciation_amount=30,
            )
        }

        rows = module._build_summary_rows(
            filters,
            assets,
            schedules,
            gl_totals,
            {"AST-DISPOSAL": (1, 2)},
        )

        assert len(rows) == 1
        assert rows[0].depreciation_amount == 30
        assert rows[0].opening_accumulated_depreciation == 150
        assert rows[0].accumulated_depreciation == 0
        assert rows[0].value_after_depreciation == 0
        assert rows[0].opening_no_of_booked_depreciations == 2
        assert rows[0].pending_depreciations == 7

    def test_future_purchase_is_excluded_from_asset_filters(self) -> None:
        """An as-of report must not include an Asset bought after to_date."""
        module = _load_module()
        filters = _dict(company="Karam", to_date="2024-03-31")

        asset_filters = module._build_asset_filters(filters)

        assert asset_filters["purchase_date"] == ("<=", "2024-03-31")
        assert asset_filters["docstatus"] == 1
        assert asset_filters["status"] == ("not in", ["Draft", "Cancelled"])

    def test_booked_counts_map_schedule_rows_back_to_each_asset(self) -> None:
        """Booked rows are counted per Asset through the selected schedule parent."""
        module = _load_module()
        schedules = {
            "AST-001": _dict(name="ADS-001"),
            "AST-002": _dict(name="ADS-002"),
        }
        query_rows = [
            _dict(schedule="ADS-001", schedule_date="2024-01-31"),
            _dict(schedule="ADS-001", schedule_date="2024-02-15"),
            _dict(schedule="ADS-002", schedule_date="2024-01-15"),
            _dict(schedule="ADS-002", schedule_date="2024-03-20"),
            _dict(schedule="UNRELATED", schedule_date="2024-01-01"),
        ]
        query = MagicMock()
        query.select.return_value = query
        query.where.return_value = query
        query.run.return_value = query_rows

        qb = MagicMock()
        qb.DocType.return_value = Table("tabDepreciation Schedule")
        qb.from_.return_value = query
        with patch.object(module.frappe, "qb", qb):
            counts = module._get_booked_depreciation_counts(
                _dict(from_date="2024-02-01", to_date="2024-03-31"),
                schedules,
            )

        assert counts == {"AST-001": (1, 2), "AST-002": (1, 2)}
        query.run.assert_called_once_with(as_dict=True)

    def test_selected_finance_book_prefers_matching_schedule(self) -> None:
        """A named book wins over the blank/default fallback and unrelated books."""
        module = _load_module()
        candidates = [
            _dict(
                name="ADS-DEFAULT",
                asset="AST-001",
                finance_book="",
                idx=1,
            ),
            _dict(
                name="ADS-OTHER",
                asset="AST-001",
                finance_book="IFRS",
                idx=1,
            ),
            _dict(
                name="ADS-SELECTED",
                asset="AST-001",
                finance_book="GAAP",
                idx=2,
            ),
        ]

        selected = module._select_schedule_rows(candidates, ("GAAP", ""))

        assert selected["AST-001"].name == "ADS-SELECTED"
        assert module._select_schedule_rows(candidates, ("IFRS",))["AST-001"].name == (
            "ADS-OTHER"
        )
        assert module._select_schedule_rows(candidates, ("LOCAL",)) == {}

    def test_pending_depreciations_is_not_negative(self) -> None:
        """Pending depreciations must never be negative after booked rows exceed total."""
        module = _load_module()
        filters = _dict(from_date="2024-01-01", to_date="2024-12-31")
        assets = {
            "AST-NEG": _dict(
                asset="AST-NEG",
                status="Fully Depreciated",
                purchase_date="2019-01-01",
                net_purchase_amount=1000,
                opening_accumulated_depreciation=100,
                opening_number_of_booked_depreciations=60,
                total_number_of_depreciations=0,
            )
        }

        rows = module._build_summary_rows(filters, assets, {}, {}, {"AST-NEG": (0, 0)})

        assert len(rows) == 1
        assert rows[0].pending_depreciations == 0
