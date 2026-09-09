"""Pure contracts for Trial Balance for Party (Karam)."""

from __future__ import annotations

import importlib
from typing import Any
from unittest.mock import patch

import frappe
import pytest
from frappe.query_builder.builder import MariaDB

ROOT = "karam_finance.karam_general.report.trial_balance_for_party_(karam)."


def _filters() -> Any:
    return importlib.import_module(ROOT + "tbfp_filters")


def _rows() -> Any:
    return importlib.import_module(ROOT + "tbfp_rows")


def _raise(*_args: Any, **_kwargs: Any) -> None:
    raise frappe.ValidationError


def _identity_text(message: str) -> str:
    return message


def test_filters_require_fiscal_year_and_bound_dates() -> None:
    module = _filters()
    with (
        patch.object(module, "_", side_effect=_identity_text),
        patch.object(module.frappe, "throw", side_effect=_raise),
        pytest.raises(frappe.ValidationError),
    ):
        module.validate_filters(frappe._dict(fiscal_year=""))
    fiscal = frappe._dict(year_start_date="2026-01-01", year_end_date="2026-12-31")
    defaults = frappe._dict(fiscal_year="FY")
    outside = frappe._dict(
        fiscal_year="FY", from_date="2025-12-01", to_date="2027-01-01"
    )
    notices: list[str] = []
    with (
        patch.object(module.frappe, "get_cached_value", return_value=fiscal),
        patch.object(module, "formatdate", side_effect=str),
        patch.object(module.frappe, "msgprint", side_effect=notices.append),
    ):
        module.validate_filters(defaults)
        module.validate_filters(outside)
    assert str(defaults.from_date) == "2026-01-01"
    assert str(defaults.to_date) == "2026-12-31"
    assert (str(outside.from_date), str(outside.to_date), len(notices)) == (
        "2026-01-01",
        "2026-12-31",
        2,
    )


def test_filters_reject_unknown_and_reversed_dates() -> None:
    module = _filters()
    fiscal = frappe._dict(year_start_date="2026-01-01", year_end_date="2026-12-31")
    for filters, cached in (
        (frappe._dict(fiscal_year="Missing"), None),
        (
            frappe._dict(
                fiscal_year="FY", from_date="2026-02-01", to_date="2026-01-01"
            ),
            fiscal,
        ),
    ):
        with (
            patch.object(module, "_", side_effect=_identity_text),
            patch.object(module.frappe, "get_cached_value", return_value=cached),
            patch.object(module.frappe, "throw", side_effect=_raise),
            pytest.raises(frappe.ValidationError),
        ):
            module.validate_filters(filters)


def test_party_fields_and_visibility_follow_party_master_rules() -> None:
    module = _filters()
    assert (
        module.get_party_name_field(frappe._dict(party_type="Customer"))
        == "customer_name"
    )
    assert (
        module.get_party_name_field(frappe._dict(party_type="Shareholder")) == "title"
    )
    assert module.get_party_name_field(frappe._dict(party_type="Other")) == "name"
    db = type("DB", (), {"get_single_value": lambda *_: "Naming Series"})()
    with patch.object(module.frappe, "db", db):
        assert module.is_party_name_visible(frappe._dict(party_type="Customer"))
        assert module.is_party_name_visible(frappe._dict(party_type="Supplier"))
    assert module.is_party_name_visible(frappe._dict(party_type="Employee"))


def test_party_rows_keep_company_and_account_currency_layers_separate() -> None:
    rows = _rows()
    meta = {"party": "C1", "party_name": "Acme", "show_party_name": True}
    row = rows.build_party_row(
        meta,
        "EUR",
        "INR",
        values={
            "opening_debit": 10,
            "opening_credit": 3,
            "debit": 2,
            "credit": 1,
            "opening_debit_in_account_currency": 8,
            "opening_credit_in_account_currency": 1,
            "debit_in_account_currency": 2,
            "credit_in_account_currency": 0,
        },
    )
    assert (row["currency"], row["account_currency"], row["party_name"]) == (
        "INR",
        "EUR",
        "Acme",
    )
    assert (row["opening_debit"], row["closing_debit"]) == (7.0, 8.0)
    assert (
        row["opening_debit_in_account_currency"],
        row["closing_debit_in_account_currency"],
    ) == (7.0, 9.0)
    source = rows.build_party_row_from_sources(
        {
            "party": "C1",
            "party_name": "Acme",
            "show_party_name": True,
            "company_currency": "INR",
        },
        "USD",
        {"opening_debit": 5, "closing_debit": 6},
        account_currency_values={
            "opening_debit_in_account_currency": 4,
            "debit_in_account_currency": 2,
        },
        show_party_label=False,
    )
    assert (
        source["party"],
        source["party_name"],
        source["currency"],
        source["account_currency"],
    ) == ("", "", "INR", "USD")
    total = rows.build_total_row(
        "INR", {"debit": 3}, {"debit_in_account_currency": None}, account_currency=""
    )
    assert total["debit"] == 3.0 and total["debit_in_account_currency"] is None
    assert rows.toggle_debit_credit(2, 5) == (0.0, 3.0)


def test_columns_and_controller_wrappers_preserve_party_metadata() -> None:
    columns = importlib.import_module(ROOT + "tbfp_columns")
    filters = frappe._dict(party_type="Customer")
    base = columns.get_columns(filters, False)
    named = columns.get_columns(filters, True)
    assert base[0]["options"] == "Customer"
    assert named[1]["fieldname"] == "party_name"
    controller = importlib.import_module(ROOT + "trial_balance_for_party_(karam)")
    with (
        patch.object(controller, "_validate_filters", return_value="ok"),
        patch.object(controller, "_get_data", return_value=[]),
        patch.object(controller, "_get_blank_row", return_value={"blank": 1}),
        patch.object(controller, "_get_party_name_field", return_value="customer_name"),
        patch.object(controller, "_toggle_debit_credit", return_value=(1, 0)),
    ):
        assert controller.validate_filters(filters) == "ok"
        assert controller.get_data(filters, True) == []
        assert controller.get_blank_row() == {"blank": 1}
        assert controller.get_party_name_field(filters) == "customer_name"
        assert controller.toggle_debit_credit(1, 2) == (1, 0)


def test_data_selects_all_name_named_and_plain_paths_and_mixed_total_currency() -> None:
    data = importlib.import_module(ROOT + "tbfp_data")
    filters = frappe._dict(company="K", party_type="Customer", show_zero_values=1)
    balance = {
        "C": {
            "EUR": {"debit_in_account_currency": 1},
            "USD": {"debit_in_account_currency": 1},
        }
    }
    with (
        patch.object(data.frappe, "get_cached_value", return_value="INR"),
        patch.object(data, "get_party_name_field", return_value="customer_name"),
        patch.object(
            data,
            "get_party_currency_balances_with_all_names",
            return_value=(balance, {"C": "Acme"}),
        ),
    ):
        result = data.get_data(filters, True)
    assert result[-1]["account_currency"] == ""
    assert result[-1]["debit_in_account_currency"] is None
    plain = frappe._dict(company="K", party_type="Customer", show_zero_values=0)
    with (
        patch.object(data.frappe, "get_cached_value", return_value="INR"),
        patch.object(data, "get_party_name_field", return_value="customer_name"),
        patch.object(data, "get_party_currency_balances", return_value={}),
    ):
        assert len(data.get_data(plain, False)) == 2


def test_data_zero_party_and_query_error_guards() -> None:
    data = importlib.import_module(ROOT + "tbfp_data")
    display = {
        "party_name_field": "customer_name",
        "show_party_name": True,
        "company_currency": "INR",
    }
    row = data._rows_for_party(
        {"name": "C", "customer_name": "Acme"},
        {},
        display,
        filters=frappe._dict(show_zero_values=1),
    )
    assert row[0]["party_name"] == "Acme"
    assert (
        data._rows_for_party(
            {"name": "C"}, {}, display, filters=frappe._dict(show_zero_values=0)
        )
        == []
    )
    query = importlib.import_module(ROOT + "tbfp_query")
    with pytest.raises(RuntimeError):
        query._party_fields(
            frappe._dict(party="P"), None, None, include_all_parties=True
        )
    with pytest.raises(RuntimeError):
        query._party_query(
            frappe._dict(),
            None,
            frappe._dict(),
            filters=frappe._dict(),
            include_all_parties=True,
        )


def test_party_data_and_query_optional_filter_branches() -> None:
    data = importlib.import_module(ROOT + "tbfp_data")
    with (
        patch.object(
            data, "get_accounts_with_children", return_value=["A"]
        ) as accounts,
        patch.object(data.frappe, "get_cached_value", return_value="INR"),
        patch.object(data, "get_party_name_field", return_value="customer_name"),
        patch.object(data, "get_party_currency_balances", return_value={}),
    ):
        data.get_data(
            frappe._dict(
                company="K", party_type="Customer", account="Root", show_zero_values=0
            ),
            False,
        )
    accounts.assert_called_once_with("Root")
    assert not data._include_party(
        {"closing_debit": 0, "closing_credit": 0}, frappe._dict(show_zero_values=0)
    )
    assert data._contributing_account_currency({"account_currency": "EUR"}) == set()


def test_party_controller_remaining_wrappers() -> None:
    controller = importlib.import_module(ROOT + "trial_balance_for_party_(karam)")
    filters = frappe._dict(party_type="Customer")
    with (
        patch.object(controller, "_get_columns", return_value=["column"]) as columns,
        patch.object(
            controller, "_is_party_name_visible", return_value=True
        ) as visible,
    ):
        assert controller.get_columns(filters, True) == ["column"]
        assert controller.is_party_name_visible(filters)
    columns.assert_called_once_with(filters, True)
    visible.assert_called_once_with(filters)


def _use_mariadb_query_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(frappe, "qb", MariaDB)


def test_party_query_applies_account_and_party_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_mariadb_query_builder(monkeypatch)
    query = importlib.import_module(ROOT + "tbfp_query")
    gl = frappe.qb.DocType("GL Entry")
    filters = frappe._dict(party="C1")
    actual = query._party_query(
        gl, None, gl.company == "K", filters=filters, include_all_parties=False
    ).select(gl.name)
    assert "`company`='K'" in str(actual)
    assert "`party`='C1'" in str(actual)


def test_customer_name_visibility_and_hidden_name_row() -> None:
    filters = _filters()
    db = type("DB", (), {"get_single_value": lambda *_: "Customer Name"})()
    with patch.object(filters.frappe, "db", db):
        assert not filters.is_party_name_visible(frappe._dict(party_type="Customer"))
    row = _rows().build_party_row(
        {"party": "C", "party_name": "Hidden", "show_party_name": False},
        "EUR",
        "INR",
        values={"opening_credit": 2, "credit": 1},
    )
    assert "party_name" not in row
    assert (row["opening_credit"], row["closing_credit"]) == (2.0, 3.0)


def test_main_party_query_includes_account_filter_and_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_mariadb_query_builder(monkeypatch)
    query = importlib.import_module(ROOT + "tbfp_query")
    permission = frappe.qb.from_(frappe.qb.DocType("Permitted GL")).select("name")
    filters = frappe._dict(company="K", party_type="Customer", to_date="2026-01-31")
    captured: list[str] = []

    class Query:
        def where(self, condition: Any) -> Any:
            captured.append(str(condition))
            return self

        def select(self, *_args: Any) -> Any:
            return self

        def groupby(self, *_args: Any) -> Any:
            return self

        def run(self, **_kwargs: Any) -> list[Any]:
            return []

    def capture_party_query(
        source_gl: Any, _party: Any, scope: Any, **_kwargs: Any
    ) -> Query:
        captured.append(
            str(frappe.qb.from_(source_gl).select(source_gl.name).where(scope))
        )
        return Query()

    with (
        patch.object(
            frappe.qb, "get_query", return_value=permission, create=True
        ) as permission_query,
        patch.object(
            query,
            "_party_query",
            side_effect=capture_party_query,
        ),
    ):
        query.get_party_currency_balances(filters, ["A"])
    assert any("`account` IN ('A')" in statement for statement in captured)
    assert any("`company`='K'" in statement for statement in captured)
    assert any(
        "`name` IN (SELECT `name` FROM `tabPermitted GL`)" in statement
        for statement in captured
    )
    assert permission_query.call_args_list[0].args == ("GL Entry",)
    assert permission_query.call_args_list[0].kwargs == {
        "fields": ["name"],
        "ignore_permissions": False,
    }


@pytest.mark.parametrize("b_currency", ["EUR", "USD"])
def test_two_party_financial_rows_and_same_currency_total_are_independent(
    b_currency: str,
) -> None:
    data = importlib.import_module(ROOT + "tbfp_data")
    balances = {
        "A": {
            "EUR": {
                "opening_debit_in_account_currency": 4,
                "debit_in_account_currency": 2,
                "credit_in_account_currency": 1,
                "opening_debit": 10,
                "debit": 3,
                "credit": 1,
            }
        },
        "B": {
            b_currency: {
                "opening_debit_in_account_currency": 5,
                "debit_in_account_currency": 1,
                "credit_in_account_currency": 0,
                "opening_debit": 20,
                "debit": 0,
                "credit": 2,
            }
        },
    }
    filters = frappe._dict(company="K", party_type="Customer", show_zero_values=0)
    with (
        patch.object(data.frappe, "get_cached_value", return_value="INR"),
        patch.object(data, "get_party_name_field", return_value="customer_name"),
        patch.object(data, "get_party_currency_balances", return_value=balances),
    ):
        result = data.get_data(filters, False)
    rows = {row["party"]: row for row in result if row.get("party") in {"A", "B"}}
    assert (
        rows["A"]["opening_debit"],
        rows["A"]["debit"],
        rows["A"]["credit"],
        rows["A"]["closing_debit"],
    ) == (10.0, 3.0, 1.0, 12.0)
    assert (
        rows["A"]["opening_debit_in_account_currency"],
        rows["A"]["debit_in_account_currency"],
        rows["A"]["credit_in_account_currency"],
        rows["A"]["closing_debit_in_account_currency"],
    ) == (4.0, 2.0, 1.0, 5.0)
    assert (
        rows["B"]["opening_debit"],
        rows["B"]["debit"],
        rows["B"]["credit"],
        rows["B"]["closing_debit"],
    ) == (20.0, 0.0, 2.0, 18.0)
    assert (
        rows["B"]["opening_debit_in_account_currency"],
        rows["B"]["debit_in_account_currency"],
        rows["B"]["credit_in_account_currency"],
        rows["B"]["closing_debit_in_account_currency"],
    ) == (5.0, 1.0, 0.0, 6.0)
    total = result[-1]
    assert (total["currency"], total["opening_debit"], total["closing_debit"]) == (
        "INR",
        30.0,
        30.0,
    )
    if b_currency == "EUR":
        assert (
            total["account_currency"],
            total["opening_debit_in_account_currency"],
            total["closing_debit_in_account_currency"],
        ) == ("EUR", 9.0, 11.0)
    else:
        assert total["account_currency"] == ""
        assert all(total[field] is None for field in data.ACCOUNT_CCY_VALUE_FIELDS)
