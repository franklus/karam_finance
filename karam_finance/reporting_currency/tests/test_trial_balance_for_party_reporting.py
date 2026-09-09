"""Tests for Trial Balance for Party (Reporting Currency) helpers."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, override
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe
from frappe import _dict

if TYPE_CHECKING:
    from types import ModuleType

MODULE_NAME = (
    "karam_finance.reporting_currency.report.trial_balance_for_party_(reporting_currency)."
    "trial_balance_for_party_(reporting_currency)"
)


def _load_module() -> ModuleType:
    return importlib.import_module(MODULE_NAME)


class TestTrialBalanceForPartyReporting(TestCase):
    """Regression tests for Trial Balance for Party (Reporting Currency)."""

    @override
    def setUp(self) -> None:
        self.enterContext(patch.object(frappe, "db", Mock()))
        self.enterContext(patch.object(frappe, "logger", return_value=Mock()))
        self.enterContext(patch.object(frappe.local, "lang", "en", create=True))
        self.enterContext(
            patch("frappe.translate.get_all_translations", return_value={})
        )

    def test_account_columns_are_identifiers_not_amount_currencies(self) -> None:
        module = _load_module()
        columns = module.get_columns(_dict(party_type="Supplier"), True)
        by_field = {column["fieldname"]: column for column in columns}
        assert by_field["account"]["options"] == "Account"
        assert by_field["account_currency"]["options"] == "Currency"
        assert all(
            by_field[field]["options"] == "currency" for field in module.VALUE_FIELDS
        )

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

        data_module = importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".tbfpr_data"
        )
        with (
            patch.object(
                data_module,
                "get_reporting_currency_balances",
                return_value={},
            ),
            patch.object(
                data_module.frappe,
                "get_list",
                return_value=[{"name": "CUST-001", "customer_name": "Customer 1"}],
            ),
            patch.object(
                data_module.frappe.db,
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

        data_module = importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".tbfpr_data"
        )
        with (
            patch.object(
                data_module,
                "get_reporting_currency_balances",
                return_value={},
            ),
            patch.object(
                data_module.frappe,
                "get_list",
                return_value=[{"name": "CUST-001", "customer_name": "Customer 1"}],
            ),
            patch.object(
                data_module.frappe.db,
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

        data_module = importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".tbfpr_data"
        )
        with (
            patch.object(
                data_module,
                "get_reporting_currency_balances",
                return_value={
                    "CUST-002": [
                        {
                            "opening_debit": 0.0,
                            "opening_credit": 0.0,
                            "debit": 5.0,
                            "credit": 0.0,
                        }
                    ]
                },
            ),
            patch.object(
                data_module.frappe,
                "get_list",
                return_value=[
                    {"name": "CUST-001", "customer_name": "Customer 1"},
                    {"name": "CUST-002", "customer_name": "Customer 2"},
                ],
            ),
            patch.object(
                data_module.frappe.db,
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

        data_module = importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".tbfpr_data"
        )
        with (
            patch.object(
                data_module,
                "get_reporting_currency_balances",
                return_value={},
            ),
            patch.object(
                data_module.frappe,
                "get_list",
                return_value=[{"name": "CUST-001", "customer_name": ""}],
            ),
            patch.object(
                data_module.frappe.db,
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

        data_module = importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".tbfpr_data"
        )
        with (
            patch.object(
                data_module,
                "get_reporting_currency_balances",
                return_value={
                    "CUST-001": [
                        {
                            "opening_debit": 11.0,
                            "opening_credit": 1.0,
                            "debit": 5.0,
                            "credit": 2.0,
                        }
                    ],
                    "CUST-002": [
                        {
                            "opening_debit": 3.0,
                            "opening_credit": 0.0,
                            "debit": 0.0,
                            "credit": 4.0,
                        }
                    ],
                },
            ),
            patch.object(
                data_module.frappe,
                "get_list",
                return_value=[
                    {"name": "CUST-001", "customer_name": "Customer 1"},
                    {"name": "CUST-002", "customer_name": "Customer 2"},
                ],
            ),
            patch.object(
                data_module.frappe.db,
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
        assert total_row["closing_debit"] == 13.0
        assert total_row["closing_credit"] == 1.0

    def test_totals_keep_closing_sides_independent_across_parties(self) -> None:
        """One party's closing credit must not net another party's debit."""
        module = _load_module()
        filters = _dict(
            party_type="Customer",
            party=None,
            account=None,
            company="Karam",
            show_zero_values=0,
        )

        data_module = importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".tbfpr_data"
        )
        with (
            patch.object(
                data_module,
                "get_reporting_currency_balances",
                return_value={
                    "CUST-A": [{"opening_debit": 100.0, "opening_credit": 0.0}],
                    "CUST-B": [{"opening_debit": 0.0, "opening_credit": 100.0}],
                },
            ),
            patch.object(
                data_module.frappe,
                "get_list",
                return_value=[
                    {"name": "CUST-A", "customer_name": "Party A"},
                    {"name": "CUST-B", "customer_name": "Party B"},
                ],
            ),
            patch.object(
                data_module.frappe.db,
                "get_single_value",
                return_value="USD",
            ),
        ):
            data = module.get_data(filters, show_party_name=True)

        total_row = data[-1]
        assert total_row["closing_debit"] == 100.0
        assert total_row["closing_credit"] == 100.0

    def test_same_party_keeps_separate_rows_for_two_eur_accounts(self) -> None:
        """Account rows retain balances, currencies, and supplier billing currency."""
        module = _load_module()
        filters = _dict(
            party_type="Supplier",
            party="VEN-1",
            account=None,
            company="Karam",
            show_zero_values=0,
        )
        data_module = importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".tbfpr_data"
        )
        with (
            patch.object(
                data_module,
                "get_reporting_currency_balances",
                return_value={
                    "VEN-1": [
                        {
                            "account": "Payable A",
                            "account_currency": "EUR",
                            "opening_debit": 0.0,
                            "opening_credit": 100.0,
                            "debit": 20.0,
                            "credit": 5.0,
                        },
                        {
                            "account": "Payable B",
                            "account_currency": "EUR",
                            "opening_debit": 40.0,
                            "opening_credit": 0.0,
                            "debit": 3.0,
                            "credit": 10.0,
                        },
                    ]
                },
            ),
            patch.object(
                data_module.frappe,
                "get_list",
                return_value=[
                    {
                        "name": "VEN-1",
                        "supplier_name": "Vendor",
                        "default_currency": "GBP",
                    }
                ],
            ),
            patch.object(
                data_module.frappe.db,
                "get_single_value",
                return_value="USD",
            ),
        ):
            data = module.get_data(filters, show_party_name=True)

        assert len(data) == 4
        party_rows = data[:2]
        assert [row["party"] for row in party_rows] == ["VEN-1", "VEN-1"]
        assert [row["account"] for row in party_rows] == [
            "Payable A",
            "Payable B",
        ]
        assert [row["account_currency"] for row in party_rows] == ["EUR", "EUR"]
        assert [row["currency"] for row in party_rows] == ["USD", "USD"]
        assert [row["billing_currency"] for row in party_rows] == ["GBP", "GBP"]
        assert party_rows[0]["closing_credit"] == 85.0
        assert party_rows[1]["closing_debit"] == 33.0
        assert data[-1]["debit"] == 23.0
        assert data[-1]["credit"] == 15.0
        assert data[-1]["closing_debit"] == 33.0
        assert data[-1]["closing_credit"] == 85.0

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

        data_module = importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".tbfpr_data"
        )
        with (
            patch.object(
                data_module,
                "get_reporting_currency_balances",
                return_value={},
            ),
            patch.object(
                data_module.frappe,
                "get_list",
                return_value=[],
            ),
            patch.object(
                data_module.frappe.db,
                "get_single_value",
                return_value="USD",
            ),
        ):
            data = module.get_data(filters, show_party_name=True)

        assert data == []

    def test_party_master_pages_preserve_permissions_and_totals(self) -> None:
        """Large permitted party sets must not be truncated to the first page."""
        module = _load_module()
        filters = _dict(
            party_type="Customer",
            party=None,
            account=None,
            company="Karam",
            show_zero_values=1,
        )

        data_module = importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".tbfpr_data"
        )
        parties = [
            {"name": f"CUST-{index:03d}", "customer_name": f"Customer {index}"}
            for index in range(21)
        ]
        with (
            patch.object(data_module, "PARTY_PAGE_SIZE", 10),
            patch.object(
                data_module,
                "get_reporting_currency_balances",
                return_value={party["name"]: [{"debit": 1}] for party in parties},
            ),
            patch.object(
                data_module.frappe,
                "get_list",
                side_effect=[parties[:10], parties[10:20], parties[20:]],
            ) as get_list,
            patch.object(
                data_module.frappe.db,
                "get_single_value",
                return_value="USD",
            ),
        ):
            data = module.get_data(filters, show_party_name=True)

        assert len([row for row in data if row.get("party")]) == 22
        assert data[-1]["debit"] == 21
        assert get_list.call_count == 3
        assert [call.kwargs["filters"] for call in get_list.call_args_list] == [
            {},
            {"name": [">", "CUST-009"]},
            {"name": [">", "CUST-019"]},
        ]
        assert all(
            call.kwargs["limit_page_length"] == 10 for call in get_list.call_args_list
        )
        assert (
            get_list.call_args.kwargs["reference_doctype"] == "Reporting Currency GLE"
        )
