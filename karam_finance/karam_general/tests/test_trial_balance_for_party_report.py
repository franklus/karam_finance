"""Tests for Trial Balance for Party (Karam)."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from unittest.mock import patch

from frappe import _dict
from frappe.tests.utils import FrappeTestCase

if TYPE_CHECKING:
    from types import ModuleType


MODULE_NAME = (
    "karam_finance.karam_general.report.trial_balance_for_party_(karam)."
    "trial_balance_for_party_(karam)"
)
QUERY_MODULE_NAME = (
    "karam_finance.karam_general.report.trial_balance_for_party_(karam).tbfp_query"
)
DATA_MODULE_NAME = (
    "karam_finance.karam_general.report.trial_balance_for_party_(karam).tbfp_data"
)


def _load_module() -> ModuleType:
    return importlib.import_module(MODULE_NAME)


def _load_query_module() -> ModuleType:
    return importlib.import_module(QUERY_MODULE_NAME)


def _load_data_module() -> ModuleType:
    return importlib.import_module(DATA_MODULE_NAME)


def _filters(**overrides: object) -> _dict:
    values: dict[str, object] = {
        "company": "Karam",
        "party_type": "Customer",
        "party": None,
        "account": None,
        "from_date": "2026-01-01",
        "to_date": "2026-12-31",
        "show_zero_values": 1,
        "exclude_zero_balance_parties": 0,
    }
    values.update(overrides)
    return _dict(values)


class TestTrialBalanceForPartyReport(FrappeTestCase):
    """Regression tests for the public report contract and row semantics."""

    def test_execute_returns_public_columns_and_data_contract(self) -> None:
        """Execute should validate filters and return columns followed by rows."""
        module = _load_module()
        filters = _filters()
        columns = [{"fieldname": "party"}]
        data = [{"party": "CUST-001"}]

        with (
            patch.object(module, "validate_filters") as validate_filters,
            patch.object(module, "is_party_name_visible", return_value=True),
            patch.object(module, "get_columns", return_value=columns),
            patch.object(module, "get_data", return_value=data),
        ):
            result = module.execute(filters)

        validate_filters.assert_called_once_with(filters)
        assert result == (columns, data)

    def test_toggle_debit_credit_nets_values(self) -> None:
        """Only one side should remain after netting."""
        module = _load_module()

        debit, credit = module.toggle_debit_credit(150.0, 40.0)

        assert debit == 110.0
        assert credit == 0.0

    def test_query_rows_index_opening_net_without_negative_zero(self) -> None:
        """Grouped query rows should hydrate signed opening values safely."""
        module = _load_query_module()

        balances = module._index_rows(
            [
                _dict(
                    party="CUST-001",
                    account_currency="USD",
                    opening_net=-4.0,
                    debit=2.0,
                    credit=1.0,
                    opening_net_in_account_currency=-4.0,
                    debit_in_account_currency=2.0,
                    credit_in_account_currency=1.0,
                )
            ]
        )

        assert balances["CUST-001"]["USD"] == {
            "opening_debit": 0.0,
            "opening_credit": 4.0,
            "debit": 2.0,
            "credit": 1.0,
            "opening_debit_in_account_currency": 0.0,
            "opening_credit_in_account_currency": 4.0,
            "debit_in_account_currency": 2.0,
            "credit_in_account_currency": 1.0,
        }

    def test_query_falls_back_to_currency_grouping_for_mixed_parties(self) -> None:
        """Mixed account currencies must retain one row per currency."""
        module = _load_query_module()
        filters = _filters()
        grouped = [
            _dict(
                party="CUST-001",
                account_currency="LBP",
                account_currency_max="USD",
            )
        ]
        detailed = [
            _dict(
                party="CUST-001",
                account_currency="LBP",
                opening_net=2.0,
                debit=0.0,
                credit=0.0,
                opening_net_in_account_currency=2.0,
                debit_in_account_currency=0.0,
                credit_in_account_currency=0.0,
            ),
            _dict(
                party="CUST-001",
                account_currency="USD",
                opening_net=3.0,
                debit=0.0,
                credit=0.0,
                opening_net_in_account_currency=3.0,
                debit_in_account_currency=0.0,
                credit_in_account_currency=0.0,
            ),
        ]

        with patch.object(
            module,
            "_run_party_currency_query",
            side_effect=[grouped, detailed],
        ) as run_query:
            balances = module.get_party_currency_balances(filters)

        assert run_query.call_count == 2
        assert set(balances["CUST-001"]) == {"LBP", "USD"}

    def test_query_keeps_single_currency_party_grouped(self) -> None:
        """Single-currency parties use the no-sort fast path."""
        module = _load_query_module()
        filters = _filters()
        grouped = [
            _dict(
                party="CUST-001",
                account_currency="USD",
                account_currency_max="USD",
                opening_net=2.0,
                debit=0.0,
                credit=0.0,
                opening_net_in_account_currency=2.0,
                debit_in_account_currency=0.0,
                credit_in_account_currency=0.0,
            )
        ]

        with patch.object(
            module,
            "_run_party_currency_query",
            return_value=grouped,
        ) as run_query:
            balances = module.get_party_currency_balances(filters)

        run_query.assert_called_once_with(
            filters,
            None,
            None,
            group_by_account_currency=False,
        )
        assert balances["CUST-001"]["USD"]["opening_debit"] == 2.0

    def test_get_blank_row_sets_numeric_fields_to_none(self) -> None:
        """Blank spacer row should have null value fields."""
        module = _load_module()

        row = module.get_blank_row()

        assert row["party"] == ""
        for field in module.VALUE_FIELDS + module.ACCOUNT_CCY_VALUE_FIELDS:
            assert row[field] is None

    def test_get_party_name_field_for_shareholder(self) -> None:
        """Shareholder uses title as display field."""
        module = _load_module()

        assert module.get_party_name_field(_dict(party_type="Shareholder")) == "title"

    def test_get_data_emits_company_values_once_per_multi_currency_party(self) -> None:
        """Company-currency values belong only on the first currency row."""
        module = _load_module()
        data_module = _load_data_module()
        filters = _filters()
        parties = [
            _dict(name="CUST-001", customer_name="Customer 1"),
            _dict(name="CUST-002", customer_name="Customer 2"),
        ]
        balances = {
            "CUST-001": {
                "EUR": {
                    "opening_debit": 13.0,
                    "opening_credit": 1.0,
                    "debit": 7.0,
                    "credit": 3.0,
                    "opening_debit_in_account_currency": 13.0,
                    "opening_credit_in_account_currency": 1.0,
                    "debit_in_account_currency": 7.0,
                    "credit_in_account_currency": 3.0,
                },
                "USD": {
                    "opening_debit": 10.0,
                    "opening_credit": 0.0,
                    "debit": 0.0,
                    "credit": 0.0,
                    "opening_debit_in_account_currency": 10.0,
                    "opening_credit_in_account_currency": 0.0,
                    "debit_in_account_currency": 0.0,
                    "credit_in_account_currency": 0.0,
                },
            },
            "CUST-002": {
                "USD": {
                    "opening_debit": 2.0,
                    "opening_credit": 0.0,
                    "debit": 1.0,
                    "credit": 0.0,
                    "opening_debit_in_account_currency": 2.0,
                    "opening_credit_in_account_currency": 0.0,
                    "debit_in_account_currency": 1.0,
                    "credit_in_account_currency": 0.0,
                }
            },
        }

        with (
            patch.object(
                data_module.frappe,
                "get_cached_value",
                return_value="USD",
            ),
            patch.object(
                data_module,
                "get_party_currency_balances_with_all_names",
                return_value=(
                    balances,
                    {party["name"]: party["customer_name"] for party in parties},
                ),
            ),
        ):
            data = module.get_data(filters, show_party_name=True)

        first, second, third, spacer, totals = data
        assert [first["account_currency"], second["account_currency"]] == [
            "EUR",
            "USD",
        ]
        assert first["party"] == "CUST-001"
        assert second["party"] == ""
        assert first["opening_debit"] == 22.0
        assert second["opening_debit"] == 0.0
        assert third["party"] == "CUST-002"
        assert third["opening_debit"] == 2.0
        assert spacer["party"] == ""
        assert totals["party"] == "'Totals'"
        assert totals["account_currency"] == ""
        assert totals["opening_debit_in_account_currency"] is None

    def test_get_data_excludes_zero_balance_parties_by_default_filter(self) -> None:
        """The v16 zero-balance filter should remove settled parties."""
        module = _load_module()
        data_module = _load_data_module()
        filters = _filters(show_zero_values=1, exclude_zero_balance_parties=1)
        parties = [
            _dict(name="CUST-001", customer_name="Settled"),
            _dict(name="CUST-002", customer_name="Open"),
        ]
        balances = {
            "CUST-001": {
                "USD": {
                    "opening_debit": 5.0,
                    "opening_credit": 5.0,
                    "debit": 0.0,
                    "credit": 0.0,
                    "opening_debit_in_account_currency": 5.0,
                    "opening_credit_in_account_currency": 5.0,
                    "debit_in_account_currency": 0.0,
                    "credit_in_account_currency": 0.0,
                }
            },
            "CUST-002": {
                "USD": {
                    "opening_debit": 5.0,
                    "opening_credit": 0.0,
                    "debit": 0.0,
                    "credit": 0.0,
                    "opening_debit_in_account_currency": 5.0,
                    "opening_credit_in_account_currency": 0.0,
                    "debit_in_account_currency": 0.0,
                    "credit_in_account_currency": 0.0,
                }
            },
        }

        with (
            patch.object(
                data_module.frappe,
                "get_cached_value",
                return_value="USD",
            ),
            patch.object(
                data_module,
                "get_party_currency_balances_with_names",
                return_value=(
                    balances,
                    {party["name"]: party["customer_name"] for party in parties},
                ),
            ),
        ):
            data = module.get_data(filters, show_party_name=True)

        assert [row["party"] for row in data[:-2]] == ["CUST-002"]

    def test_get_data_keeps_zero_rows_when_show_zero_values_is_enabled(self) -> None:
        """Show zero values should retain a party with no GL activity."""
        module = _load_module()
        data_module = _load_data_module()
        filters = _filters(show_zero_values=1, exclude_zero_balance_parties=0)

        with (
            patch.object(
                data_module.frappe,
                "get_cached_value",
                return_value="USD",
            ),
            patch.object(
                data_module,
                "get_party_currency_balances_with_all_names",
                return_value=({}, {"CUST-001": "Customer 1"}),
            ),
        ):
            data = module.get_data(filters, show_party_name=True)

        assert data[0]["party"] == "CUST-001"
        assert data[0]["account_currency"] == "USD"
        assert data[-1]["opening_debit"] == 0.0
