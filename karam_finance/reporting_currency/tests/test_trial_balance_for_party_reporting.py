"""Tests for Trial Balance for Party (Reporting) helpers."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from unittest.mock import patch

from frappe import _dict
from frappe.tests.utils import FrappeTestCase

if TYPE_CHECKING:
    from types import ModuleType

MODULE_NAME = (
    "karam_finance.reporting_currency.report.trial_balance_for_party_(reporting)."
    "trial_balance_for_party_(reporting)"
)


def _load_module() -> ModuleType:
    return importlib.import_module(MODULE_NAME)


class TestTrialBalanceForPartyReporting(FrappeTestCase):
    """Regression tests for Trial Balance for Party (Reporting)."""

    def test_toggle_debit_credit_nets_values(self) -> None:
        """Only one side should remain after netting."""
        module = _load_module()

        debit, credit = module.toggle_debit_credit(150.0, 40.0)

        assert debit == 110.0
        assert credit == 0.0

    def test_get_blank_row_sets_numeric_fields_to_none(self) -> None:
        """Blank spacer row should have null value fields."""
        module = _load_module()

        row = module.get_blank_row()

        assert row["party"] == ""
        for field in module.VALUE_FIELDS:
            assert row[field] is None

    def test_get_party_name_field_for_shareholder(self) -> None:
        """Shareholder uses title as display field."""
        module = _load_module()

        field = module.get_party_name_field(_dict(party_type="Shareholder"))

        assert field == "title"

    def test_selected_party_with_no_balances_shown_when_zero_values_on(
        self,
    ) -> None:
        """A selected party with no RC GLE rows appears when show_zero_values=1."""
        module = _load_module()
        filters = _dict(
            party_type="Customer",
            party="CUST-001",
            account=None,
            company="Karam",
            show_zero_values=1,
        )

        with (
            patch.object(
                module.tbfpr_data,
                "get_reporting_currency_balances",
                return_value={},
            ),
            patch.object(
                module.tbfpr_data.frappe,
                "get_all",
                return_value=[{"name": "CUST-001", "customer_name": "Customer 1"}],
            ),
            patch.object(
                module.tbfpr_data.frappe.db,
                "get_single_value",
                return_value="USD",
            ),
        ):
            data = module.get_data(filters, show_party_name=True)

        assert len(data) == 3  # party row + blank + total
        assert data[0]["party"] == "CUST-001"
        assert data[0]["party_name"] == "Customer 1"
        assert data[0]["currency"] == "USD"
        assert data[0]["opening_debit"] == 0.0
        assert data[0]["closing_credit"] == 0.0

    def test_selected_party_with_no_balances_hidden_when_zero_values_off(
        self,
    ) -> None:
        """A selected party with no RC GLE rows is hidden when show_zero_values=0."""
        module = _load_module()
        filters = _dict(
            party_type="Customer",
            party="CUST-001",
            account=None,
            company="Karam",
            show_zero_values=0,
        )

        with (
            patch.object(
                module.tbfpr_data,
                "get_reporting_currency_balances",
                return_value={},
            ),
            patch.object(
                module.tbfpr_data.frappe,
                "get_all",
                return_value=[{"name": "CUST-001", "customer_name": "Customer 1"}],
            ),
            patch.object(
                module.tbfpr_data.frappe.db,
                "get_single_value",
                return_value="USD",
            ),
        ):
            data = module.get_data(filters, show_party_name=True)

        assert data == []

    def test_party_rows_sourced_from_party_master(self) -> None:
        """Parties missing from RC GLE keys still appear when show_zero_values=1."""
        module = _load_module()
        filters = _dict(
            party_type="Customer",
            party=None,
            account=None,
            company="Karam",
            show_zero_values=1,
        )

        with (
            patch.object(
                module.tbfpr_data,
                "get_reporting_currency_balances",
                return_value={
                    "CUST-002": {
                        "opening_debit": 0.0,
                        "opening_credit": 0.0,
                        "debit": 5.0,
                        "credit": 0.0,
                    }
                },
            ),
            patch.object(
                module.tbfpr_data.frappe,
                "get_all",
                return_value=[
                    {"name": "CUST-001", "customer_name": "Customer 1"},
                    {"name": "CUST-002", "customer_name": "Customer 2"},
                ],
            ),
            patch.object(
                module.tbfpr_data.frappe.db,
                "get_single_value",
                return_value="USD",
            ),
        ):
            data = module.get_data(filters, show_party_name=True)

        parties = [row["party"] for row in data if row.get("party")]
        assert "CUST-001" in parties
        assert "CUST-002" in parties
        cust_002 = next(row for row in data if row["party"] == "CUST-002")
        assert cust_002["debit"] == 5.0

    def test_row_currency_is_reporting_currency(self) -> None:
        """Hidden currency column must always be the configured reporting currency."""
        module = _load_module()
        filters = _dict(
            party_type="Customer",
            party=None,
            account=None,
            company="Karam",
            show_zero_values=1,
        )

        with (
            patch.object(
                module.tbfpr_data,
                "get_reporting_currency_balances",
                return_value={},
            ),
            patch.object(
                module.tbfpr_data.frappe,
                "get_all",
                return_value=[{"name": "CUST-001", "customer_name": ""}],
            ),
            patch.object(
                module.tbfpr_data.frappe.db,
                "get_single_value",
                return_value="EUR",
            ),
        ):
            data = module.get_data(filters, show_party_name=False)

        assert data[0]["currency"] == "EUR"

    def test_totals_row_sums_reporting_currency_values(self) -> None:
        """Totals row must aggregate reporting-currency debit/credit columns."""
        module = _load_module()
        filters = _dict(
            party_type="Customer",
            party=None,
            account=None,
            company="Karam",
            show_zero_values=1,
        )

        with (
            patch.object(
                module.tbfpr_data,
                "get_reporting_currency_balances",
                return_value={
                    "CUST-001": {
                        "opening_debit": 11.0,
                        "opening_credit": 1.0,
                        "debit": 5.0,
                        "credit": 2.0,
                    },
                    "CUST-002": {
                        "opening_debit": 3.0,
                        "opening_credit": 0.0,
                        "debit": 0.0,
                        "credit": 4.0,
                    },
                },
            ),
            patch.object(
                module.tbfpr_data.frappe,
                "get_all",
                return_value=[
                    {"name": "CUST-001", "customer_name": "Customer 1"},
                    {"name": "CUST-002", "customer_name": "Customer 2"},
                ],
            ),
            patch.object(
                module.tbfpr_data.frappe.db,
                "get_single_value",
                return_value="USD",
            ),
        ):
            data = module.get_data(filters, show_party_name=True)

        total_row = data[-1]
        assert total_row["party"].strip("'") == "Totals"
        assert total_row["opening_debit"] == 13.0
        assert total_row["opening_credit"] == 0.0
        assert total_row["debit"] == 5.0
        assert total_row["credit"] == 6.0
        assert total_row["closing_debit"] == 12.0
        assert total_row["closing_credit"] == 0.0

    def test_no_parties_returns_empty_list(self) -> None:
        """No party master records should return no report rows."""
        module = _load_module()
        filters = _dict(
            party_type="Customer",
            party=None,
            account=None,
            company="Karam",
            show_zero_values=1,
        )

        with (
            patch.object(
                module.tbfpr_data,
                "get_reporting_currency_balances",
                return_value={},
            ),
            patch.object(
                module.tbfpr_data.frappe,
                "get_all",
                return_value=[],
            ),
            patch.object(
                module.tbfpr_data.frappe.db,
                "get_single_value",
                return_value="USD",
            ),
        ):
            data = module.get_data(filters, show_party_name=True)

        assert data == []
