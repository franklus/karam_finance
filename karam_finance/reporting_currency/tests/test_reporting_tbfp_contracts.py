"""Pure filters and row contracts for reporting-currency party Trial Balance."""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import patch

import frappe
import pytest

ROOT = "karam_finance.reporting_currency.report.trial_balance_for_party_(reporting_currency)."


def mod(n: str) -> Any:
    return importlib.import_module(ROOT + n)


def boom(*_a: Any, **_k: Any) -> None:
    raise frappe.ValidationError


def identity_text(message: str) -> str:
    return message


def test_filters_validate_fiscal_dates_bounds_and_party_names() -> None:
    f = mod("tbfpr_filters")
    fy = frappe._dict(year_start_date="2026-01-01", year_end_date="2026-12-31")
    for x, cached in (
        (frappe._dict(fiscal_year=""), fy),
        (frappe._dict(fiscal_year="X"), None),
        (
            frappe._dict(
                fiscal_year="FY", from_date="2026-02-01", to_date="2026-01-01"
            ),
            fy,
        ),
    ):
        with (
            patch.object(f, "_", side_effect=identity_text),
            patch.object(f.frappe, "get_cached_value", return_value=cached),
            patch.object(f.frappe, "throw", side_effect=boom),
            pytest.raises(frappe.ValidationError),
        ):
            f.validate_filters(x)
    x = frappe._dict(fiscal_year="FY", from_date="2025-01-01", to_date="2027-01-01")
    notices: list[str] = []
    with (
        patch.object(f.frappe, "get_cached_value", return_value=fy),
        patch.object(f, "formatdate", side_effect=str),
        patch.object(f.frappe, "msgprint", side_effect=notices.append),
    ):
        f.validate_filters(x)
    assert (str(x.from_date), str(x.to_date), len(notices)) == (
        "2026-01-01",
        "2026-12-31",
        2,
    )
    assert (
        f.get_party_name_field(frappe._dict(party_type="Customer")) == "customer_name"
    )
    assert f.get_party_name_field(frappe._dict(party_type="Shareholder")) == "title"
    db = type("DB", (), {"get_single_value": lambda *_: "Customer Name"})()
    with patch.object(f.frappe, "db", db):
        assert not f.is_party_name_visible(frappe._dict(party_type="Customer"))
    assert f.is_party_name_visible(frappe._dict(party_type="Employee"))


def test_rows_columns_and_controller_wrappers() -> None:
    r = mod("tbfpr_rows")
    c = mod("tbfpr_columns")
    ctl = mod("trial_balance_for_party_(reporting_currency)")
    filters = frappe._dict(party_type="Customer")
    assert c.get_columns(filters, True)[1]["fieldname"] == "party_name"
    assert r.toggle_debit_credit(2, 5) == (0.0, 3.0)
    assert all(v is None for k, v in r.get_blank_row().items() if k in r.VALUE_FIELDS)
    assert r.build_total_row("USD", {"debit": 3})["debit"] == 3.0
    with (
        patch.object(ctl, "_get_data", return_value=[1]) as data,
        patch.object(ctl, "_get_columns", return_value=[2]) as columns,
    ):
        assert ctl.get_data(filters, True) == [1]
        assert ctl.get_columns(filters, True) == [2]
    data.assert_called_once_with(filters, True)
    columns.assert_called_once_with(filters, True)


def test_rc_party_filter_defaults_supplier_visibility_and_name_fallback() -> None:
    filters = mod("tbfpr_filters")
    fiscal = frappe._dict(year_start_date="2026-01-01", year_end_date="2026-12-31")
    value = frappe._dict(fiscal_year="FY")
    with patch.object(filters.frappe, "get_cached_value", return_value=fiscal):
        filters.validate_filters(value)
    assert (str(value.from_date), str(value.to_date)) == ("2026-01-01", "2026-12-31")
    assert filters.get_party_name_field(frappe._dict(party_type="Other")) == "name"
    db = type("DB", (), {"get_single_value": lambda *_args: "Naming Series"})()
    with patch.object(filters.frappe, "db", db):
        assert filters.is_party_name_visible(frappe._dict(party_type="Supplier"))


def test_rc_party_columns_and_controller_filter_visibility_wrappers() -> None:
    columns = mod("tbfpr_columns")
    customer = columns.get_columns(frappe._dict(party_type="Customer"), True)
    employee = columns.get_columns(frappe._dict(party_type="Employee"), False)
    assert customer[1]["fieldname"] == "party_name"
    assert any(column["fieldname"] == "billing_currency" for column in customer)
    assert not any(column["fieldname"] == "billing_currency" for column in employee)
    controller = mod("trial_balance_for_party_(reporting_currency)")
    filters = frappe._dict(party_type="Customer")
    with (
        patch.object(controller, "_validate_filters", return_value="valid") as validate,
        patch.object(
            controller, "_is_party_name_visible", return_value=True
        ) as visible,
    ):
        assert controller.validate_filters(filters) == "valid"
        assert controller.is_party_name_visible(filters)
    validate.assert_called_once_with(filters)
    visible.assert_called_once_with(filters)


def test_rc_party_data_account_filter_currency_fallback_and_billing_metadata() -> None:
    data = mod("tbfpr_data")
    filters = frappe._dict(
        company="K", party_type="Customer", account="Root", show_zero_values=1
    )
    party = frappe._dict(name="C", customer_name="Acme", default_currency="EUR")
    balances = {
        "C": [
            {
                "account": "Receivable",
                "account_currency": "EUR",
                "opening_debit": 3,
                "debit": 2,
            }
        ]
    }
    db = type("DB", (), {"get_single_value": lambda *_args: None})()
    with (
        patch.object(data.frappe, "db", db),
        patch.object(data.frappe, "get_cached_value", return_value="USD"),
        patch.object(data, "get_party_name_field", return_value="customer_name"),
        patch.object(data, "_iter_permitted_parties", return_value=iter([party])),
        patch.object(
            data, "get_accounts_with_children", return_value=["A", "B"]
        ) as children,
        patch.object(
            data, "get_reporting_currency_balances", return_value=balances
        ) as query,
    ):
        result = data.get_data(filters, True)
    children.assert_called_once_with("Root")
    query.assert_called_once_with(filters, ["A", "B"])
    assert (
        result[0]["currency"],
        result[0]["billing_currency"],
        result[0]["closing_debit"],
    ) == ("USD", "EUR", 5.0)


def test_rc_party_employee_rows_omit_billing_currency_and_query_account_scope() -> None:
    data = mod("tbfpr_data")
    employee = next(
        data._party_rows(
            frappe._dict(name="E"),
            [{"account": "Payroll", "debit": 4}],
            "USD",
            party_type="Employee",
        )
    )
    assert "billing_currency" not in employee
    assert (employee["debit"], employee["closing_debit"], employee["currency"]) == (
        4.0,
        4.0,
        "USD",
    )
    query = mod("tbfpr_query")
    calls: list[Any] = []

    class Scope:
        def where(self, condition: Any) -> Any:
            calls.append(condition)
            return self

    rcgle = type(
        "Entry",
        (),
        {
            "party": "party",
            "account": type(
                "Account", (), {"isin": lambda _self, values: ("account", values)}
            )(),
        },
    )()
    result = query._apply_common_filters(
        Scope(), rcgle, frappe._dict(), account_filter=["A"]
    )
    assert result is not None
    assert calls == [("account", ["A"])]
