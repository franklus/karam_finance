"""Tests for Trial Balance (Karam) report helpers."""

from __future__ import annotations

import importlib
from datetime import date
from typing import TYPE_CHECKING
from unittest.mock import patch

import frappe
import pytest
from frappe import _dict

if TYPE_CHECKING:
    from types import ModuleType

REPORT_PACKAGE = "karam_finance.karam_general.report.trial_balance_(karam)"
MODULE_NAME = f"{REPORT_PACKAGE}.trial_balance_(karam)"


def _load_module() -> ModuleType:
    return importlib.import_module(MODULE_NAME)


def _load_helper_module(name: str) -> ModuleType:
    """Load a split report helper without relying on wrapper internals."""
    return importlib.import_module(f"{REPORT_PACKAGE}.{name}")


@pytest.fixture(autouse=True)
def _frappe_local_context():
    """Provide the minimal request state needed by Frappe validation helpers."""
    frappe.local.flags = _dict(mute_messages=True, print_messages=False)
    frappe.local.message_log = []
    frappe.local.lang = "en"
    yield


class TestTrialBalanceReport:
    """Regression tests for split trial balance helpers."""

    def test_validate_filters_requires_fiscal_year_by_default(self) -> None:
        """Fiscal Year should remain mandatory unless date-range mode is enabled."""
        module = _load_module()
        filters = _dict(fiscal_year=None, ignore_fiscal_year=0)

        with pytest.raises(frappe.ValidationError, match="Fiscal Year"):
            module.validate_filters(filters)

    def test_validate_filters_allows_date_range_without_fiscal_year(self) -> None:
        """Date-range mode should not require Fiscal Year."""
        module = _load_module()
        filters = _dict(
            company="Karam",
            fiscal_year=None,
            ignore_fiscal_year=1,
            from_date="2026-01-15",
            to_date="2026-02-20",
        )
        tbk_filters = _load_helper_module("tbk_filters")

        with patch.object(
            tbk_filters,
            "get_fiscal_year",
            return_value=("2026", "2026-01-01", "2026-12-31"),
        ) as mock_get_fiscal_year:
            module.validate_filters(filters)

        assert filters.from_date == date(2026, 1, 15)
        assert filters.to_date == date(2026, 2, 20)
        assert filters.year_start_date == date(2026, 1, 1)
        assert filters.year_end_date == date(2026, 12, 31)
        mock_get_fiscal_year.assert_called_once_with(
            date(2026, 1, 15), company="Karam", verbose=0
        )

    def test_validate_filters_requires_dates_when_fiscal_year_is_ignored(self) -> None:
        """Date-range mode should fail clearly if either boundary is missing."""
        module = _load_module()
        filters = _dict(fiscal_year=None, ignore_fiscal_year=1, from_date=None)

        with pytest.raises(frappe.ValidationError, match="From Date"):
            module.validate_filters(filters)

    def test_prepare_opening_closing_nets_asset_balances(self) -> None:
        """Asset rows should net opening and closing to debit side."""
        module = _load_module()
        row = {
            "root_type": "Asset",
            "opening_debit": 120.0,
            "opening_credit": 20.0,
            "closing_debit": 150.0,
            "closing_credit": 50.0,
            "opening_debit_in_account_currency": 12.0,
            "opening_credit_in_account_currency": 2.0,
            "closing_debit_in_account_currency": 15.0,
            "closing_credit_in_account_currency": 5.0,
        }

        module.prepare_opening_closing(row)

        assert row["opening_debit"] == 100.0
        assert row["opening_credit"] == 0.0
        assert row["closing_debit"] == 100.0
        assert row["closing_credit"] == 0.0
        assert row["opening_debit_in_account_currency"] == 10.0
        assert row["opening_credit_in_account_currency"] == 0.0

    def test_get_blank_row_is_spacer_with_null_amounts(self) -> None:
        """Spacer row should keep amount columns blank."""
        module = _load_module()

        row = module.get_blank_row()

        assert row["is_spacer"] is True
        for field in module.VALUE_FIELDS + module.ACCOUNT_CCY_VALUE_FIELDS:
            assert row[field] is None

    def test_calculate_total_row_sums_root_accounts_only(self) -> None:
        """Only root-level rows should contribute to total row values."""
        module = _load_module()
        accounts = [
            _dict(parent_account=None, opening_debit=10.0, opening_credit=0.0),
            _dict(parent_account="Root", opening_debit=99.0, opening_credit=0.0),
            _dict(parent_account=None, opening_debit=5.0, opening_credit=2.0),
        ]
        for account in accounts:
            for field in module.VALUE_FIELDS + module.ACCOUNT_CCY_VALUE_FIELDS:
                account.setdefault(field, 0.0)

        row = module.calculate_total_row(accounts, "USD")

        assert row["opening_debit"] == 15.0
        assert row["opening_credit"] == 2.0

    def test_get_data_returns_none_when_no_accounts(self) -> None:
        """No chart-of-accounts rows should return no data."""
        module = _load_module()
        tbk_data = _load_helper_module("tbk_data")
        filters = _dict(company="Karam", presentation_currency=None)

        with patch.object(tbk_data, "_get_accounts", return_value=[]):
            data = module.get_data(filters)

        assert data is None

    def test_prepare_account_currency_opening_closing_only_nets_account_currency(
        self,
    ) -> None:
        """Account-currency netting should not mutate company-currency fields."""
        tbk_aggregation = _load_helper_module("tbk_aggregation")
        row = {
            "root_type": "Asset",
            "opening_debit": 120.0,
            "opening_credit": 20.0,
            "closing_debit": 150.0,
            "closing_credit": 50.0,
            "opening_debit_in_account_currency": 12.0,
            "opening_credit_in_account_currency": 2.0,
            "closing_debit_in_account_currency": 15.0,
            "closing_credit_in_account_currency": 5.0,
        }

        tbk_aggregation.prepare_account_currency_opening_closing(row)

        assert row["opening_debit"] == 120.0
        assert row["opening_credit"] == 20.0
        assert row["closing_debit"] == 150.0
        assert row["closing_credit"] == 50.0
        assert row["opening_debit_in_account_currency"] == 10.0
        assert row["opening_credit_in_account_currency"] == 0.0
        assert row["closing_debit_in_account_currency"] == 10.0
        assert row["closing_credit_in_account_currency"] == 0.0

    def test_apply_account_currency_data_skips_opening_entries_when_flag_off(
        self,
    ) -> None:
        """Movement account-currency totals should mirror vanilla opening rules."""
        tbk_aggregation = _load_helper_module("tbk_aggregation")
        accounts = [_dict(name="Cash", root_type="Asset")]
        gl_entries_by_account = {
            "Cash": [
                _dict(
                    is_opening="Yes",
                    debit_in_account_currency=9.0,
                    credit_in_account_currency=0.0,
                ),
                _dict(
                    is_opening="No",
                    debit_in_account_currency=4.0,
                    credit_in_account_currency=1.0,
                ),
            ]
        }
        opening_balances = {
            "Cash": {
                "opening_debit_in_account_currency": 10.0,
                "opening_credit_in_account_currency": 2.0,
            }
        }

        tbk_aggregation.apply_account_currency_data_to_accounts(
            accounts,
            gl_entries_by_account,
            opening_balances,
            show_net_values=False,
            ignore_is_opening=0,
        )

        row = accounts[0]
        assert row["opening_debit_in_account_currency"] == 10.0
        assert row["opening_credit_in_account_currency"] == 2.0
        assert row["debit_in_account_currency"] == 4.0
        assert row["credit_in_account_currency"] == 1.0
        assert row["closing_debit_in_account_currency"] == 14.0
        assert row["closing_credit_in_account_currency"] == 3.0

    def test_mixed_currency_group_and_total_are_blank(self) -> None:
        """Unlike currencies must never be added in group or total columns."""
        module = _load_module()
        tbk_aggregation = _load_helper_module("tbk_aggregation")
        tbk_rows = _load_helper_module("tbk_rows")
        root = _dict(
            name="Root",
            parent_account=None,
            root_type="Asset",
            account_currency="USD",
            is_group=1,
            account_name="Root",
            account_number="",
            indent=0,
        )
        usd = _dict(
            name="USD Account",
            parent_account="Root",
            root_type="Asset",
            account_currency="USD",
            is_group=0,
            account_name="USD Account",
            account_number="",
            indent=1,
        )
        eur = _dict(
            name="EUR Account",
            parent_account="Root",
            root_type="Asset",
            account_currency="EUR",
            is_group=0,
            account_name="EUR Account",
            account_number="",
            indent=1,
        )
        accounts = [root, usd, eur]
        opening = {
            "USD Account": {
                "opening_debit": 10,
                "opening_debit_in_account_currency": 10,
                "account_currencies": {"USD"},
            },
            "EUR Account": {
                "opening_debit": 20,
                "opening_debit_in_account_currency": 20,
                "account_currencies": {"EUR"},
            },
        }
        period = {
            "USD Account": {
                "debit": 1,
                "debit_in_account_currency": 1,
                "account_currencies": {"USD"},
            },
            "EUR Account": {
                "debit": 2,
                "debit_in_account_currency": 2,
                "account_currencies": {"EUR"},
            },
        }
        tbk_aggregation.apply_balances_to_accounts(accounts, opening, period)
        tbk_aggregation.accumulate_values_into_parents(
            accounts, {account.name: account for account in accounts}
        )
        tbk_aggregation.finalize_account_currency_values(accounts)

        assert root["account_currency"] == ""
        assert root["debit_in_account_currency"] is None
        total = module.calculate_total_row(accounts, "USD")
        assert total["account_currency"] == ""
        assert total["debit_in_account_currency"] is None

        with patch.object(tbk_rows, "get_zero_cutoff", return_value=0):
            rows = module.prepare_data(
                accounts,
                _dict(
                    from_date=date(2026, 1, 1),
                    to_date=date(2026, 1, 31),
                    show_group_accounts=1,
                    show_net_values=1,
                ),
                {},
                "USD",
            )
        root_row = next(row for row in rows if row.get("account") == "Root")
        assert root_row["account_currency"] == ""
        assert root_row["debit_in_account_currency"] is None

    def test_arbitrary_period_crosses_fiscal_year_without_clamping(self) -> None:
        """Date-range mode must preserve a period spanning two fiscal years."""
        module = _load_module()
        filters = _dict(
            company="Karam",
            fiscal_year=None,
            ignore_fiscal_year=1,
            from_date="2025-12-15",
            to_date="2026-02-20",
        )
        tbk_filters = _load_helper_module("tbk_filters")
        with (
            patch.object(
                tbk_filters,
                "get_fiscal_year",
                return_value=("2025", "2025-01-01", "2025-12-31"),
            ),
            patch.object(module, "get_columns", return_value=[]),
            patch.object(module, "get_data", return_value=[]) as get_data,
        ):
            columns, data = module.execute(filters)

        assert columns == []
        assert data == []
        assert filters.from_date == date(2025, 12, 15)
        assert filters.to_date == date(2026, 2, 20)
        get_data.assert_called_once_with(filters)
