"""Pure opening-balance contracts for Karam Trial Balance."""

from __future__ import annotations

import importlib
from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import frappe
import pytest
from frappe.query_builder.builder import MariaDB

MODULE = "karam_finance.karam_general.report.trial_balance_(karam).tbk_data"


@pytest.fixture(autouse=True)  # noqa: V103 - pytest autouse fixture.
def unrestricted_source_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    # These unit contracts exercise aggregation; site tests cover real restrictions.
    monkeypatch.setattr(frappe, "has_permission", MagicMock(return_value=True))
    monkeypatch.setattr(frappe, "build_match_conditions", MagicMock(return_value=""))


@pytest.fixture(scope="module")
def data() -> Any:
    return importlib.import_module(MODULE)


def identity_query(query: Any, *_args: Any, **_kwargs: Any) -> Any:
    return query


def false_value(*_args: Any, **_kwargs: Any) -> bool:
    return False


def true_value(*_args: Any, **_kwargs: Any) -> bool:
    return True


def hundred_value(*_args: Any, **_kwargs: Any) -> int:
    return 100


def test_opening_balances_pcv_paths_and_aggregate_currency(data: Any) -> None:
    filters = frappe._dict(company="K", from_date="2026-02-01")
    closing = [
        frappe._dict(
            account="A",
            account_currency="USD",
            debit=10,
            credit=2,
            debit_in_account_currency=10,
            credit_in_account_currency=2,
        )
    ]
    gl = [
        frappe._dict(
            account="A",
            account_currency="USD",
            debit=3,
            credit=1,
            debit_in_account_currency=3,
            credit_in_account_currency=1,
        )
    ]
    db = SimpleNamespace(
        get_single_value=MagicMock(return_value=False),
        get_all=MagicMock(return_value=[]),
    )
    with (
        patch.object(data.frappe, "db", db),
        patch.object(db, "get_all", return_value=[]) as pcv,
        patch.object(data, "_get_gl_opening_currency_rows", return_value=gl) as opening,
    ):
        result = data._get_opening_balances(filters, False)
    assert result["A"]["opening_debit"] == 3 and result["A"]["account_currencies"] == {
        "USD"
    }
    assert pcv.call_args.kwargs == {
        "filters": {
            "docstatus": 1,
            "company": "K",
            "period_end_date": ("<", "2026-02-01"),
        },
        "fields": ["period_end_date", "name"],
        "order_by": "period_end_date desc",
        "limit": 1,
    }
    opening.assert_called_once()
    exact = [SimpleNamespace(period_end_date="2026-01-31", name="PCV")]
    db = SimpleNamespace(
        get_single_value=MagicMock(return_value=False),
        get_all=MagicMock(return_value=[]),
    )
    with (
        patch.object(data.frappe, "db", db),
        patch.object(data.frappe.db, "get_all", return_value=exact),
        patch.object(data, "_get_account_closing_currency_rows", return_value=closing),
        patch.object(data, "_get_gl_opening_currency_rows") as gap,
    ):
        result = data._get_opening_balances(filters, False)
    assert result["A"]["opening_debit"] == 10
    gap.assert_not_called()
    old = [SimpleNamespace(period_end_date="2026-01-20", name="PCV")]
    db = SimpleNamespace(
        get_single_value=MagicMock(return_value=False),
        get_all=MagicMock(return_value=[]),
    )
    with (
        patch.object(data.frappe, "db", db),
        patch.object(data.frappe.db, "get_all", return_value=old),
        patch.object(data, "_get_account_closing_currency_rows", return_value=closing),
        patch.object(data, "_get_gl_opening_currency_rows", return_value=gl) as gap,
    ):
        result = data._get_opening_balances(filters, False)
    assert result["A"]["opening_debit"] == 13
    assert gap.call_args.kwargs["start_date"].isoformat() == "2026-01-21"


def test_gl_opening_and_closing_query_sql_contracts(data: Any) -> None:

    captured = []
    typ = type(MariaDB.from_(MariaDB.DocType("GL Entry")))

    def capture(q: Any, **_k: Any) -> list[Any]:
        captured.append(q.get_sql())
        return []

    qb = SimpleNamespace(DocType=MariaDB.DocType, from_=MariaDB.from_)
    filters = frappe._dict(
        company="K",
        from_date="2026-02-01",
        year_start_date="2026-01-01",
        show_unclosed_fy_pl_balances=0,
        with_period_closing_entry_for_opening=0,
    )
    with (
        patch.object(data.frappe, "qb", qb),
        patch.object(data, "apply_gl_filters", side_effect=identity_query),
        patch.object(typ, "run", capture),
    ):
        data._get_gl_opening_currency_rows(
            filters,
            False,
            start_date="2026-01-20",
            finance_books=True,
            accounting_dimensions=[],
        )
        data._get_account_closing_currency_rows(
            filters, "PCV", finance_books=True, accounting_dimensions=[]
        )
    gl, closing = captured
    for t in (
        "`tabGL Entry`.`company`='K'",
        "`tabGL Entry`.`is_cancelled`=0",
        "`posting_date`>='2026-01-20'",
        "`posting_date`<'2026-02-01'",
        "`is_opening`='No'",
        "`voucher_type`<>'Period Closing Voucher'",
        "`report_type`='Balance Sheet'",
        "`report_type`='Profit and Loss'",
        "`posting_date`>='2026-01-01'",
    ):
        assert t in gl
    for t in (
        "`tabAccount Closing Balance`.`company`='K'",
        "`period_closing_voucher`='PCV'",
        "`report_type` IN ('Balance Sheet','Profit and Loss')",
        "`is_period_closing_voucher_entry`=0",
    ):
        assert t in closing


def test_opening_variants_and_ignore_closing_bypass(data: Any) -> None:
    filters = frappe._dict(
        company="K",
        from_date="2026-02-01",
        show_unclosed_fy_pl_balances=1,
        with_period_closing_entry_for_opening=1,
    )
    captured = []
    typ = type(MariaDB.from_(MariaDB.DocType("GL Entry")))

    def capture(q: Any, **_k: Any) -> list[Any]:
        captured.append(q.get_sql())
        return []

    qb = SimpleNamespace(DocType=MariaDB.DocType, from_=MariaDB.from_)
    with (
        patch.object(data.frappe, "qb", qb),
        patch.object(data, "apply_gl_filters", side_effect=identity_query),
        patch.object(typ, "run", capture),
    ):
        data._get_gl_opening_currency_rows(filters, True, start_date="2026-01-20")
        data._get_gl_opening_currency_rows(filters, True)
        data._get_gl_opening_currency_rows(filters, False)
    assert "`is_opening`" not in captured[0]
    assert (
        "`posting_date`<'2026-02-01'" in captured[1]
        and "`is_opening`" not in captured[1]
    )
    assert "OR `tabGL Entry`.`is_opening`='Yes'" in captured[2]


def test_conditions_apply_real_qb_filters_and_finance_conflict() -> None:
    conditions = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_conditions"
    )
    gl = MariaDB.DocType("GL Entry")
    query = MariaDB.from_(gl).select(gl.name)
    filters = frappe._dict(
        company="K",
        cost_center="Root",
        project="P1, P2",
        finance_book="FB",
        include_default_book_entries=0,
        branch="North",
    )
    dims = [SimpleNamespace(fieldname="branch", document_type="Branch")]
    with (
        patch.object(
            conditions, "get_cost_centers_with_children", return_value=["Root", "Child"]
        ),
        patch.object(conditions.frappe, "get_cached_value", return_value=False),
    ):
        sql = conditions.apply_gl_filters(
            query, gl, filters, finance_books=True, accounting_dimensions=dims
        ).get_sql()
    for token in (
        "`cost_center` IN ('Root','Child')",
        "`project` IN ('P1','P2')",
        "`finance_book` IN ('FB','')",
        "`finance_book` IS NULL",
        "`branch` IN ('North')",
    ):
        assert token in sql
    assert conditions._as_list(" A, B ") == ["A", "B"] and conditions._as_list(
        ("A", "B")
    ) == ["A", "B"]
    with (
        patch.object(conditions.frappe, "get_cached_value", return_value="Default"),
        patch.object(conditions.frappe, "throw", side_effect=RuntimeError("conflict")),
        pytest.raises(RuntimeError),
    ):
        conditions._apply_finance_book_filter(
            query,
            gl,
            frappe._dict(
                company="K", finance_book="Other", include_default_book_entries=1
            ),
            finance_books=True,
        )


def test_get_data_no_accounts_short_circuits(data: Any) -> None:
    filters = frappe._dict(company="K")
    db = SimpleNamespace(get_single_value=MagicMock())
    with (
        patch.object(data, "_get_accounts", return_value=[]),
        patch.object(data.frappe, "db", db),
    ):
        assert data.get_data(filters) is None
    db.get_single_value.assert_not_called()


def test_get_data_real_financial_pipeline_rolls_parent_once(data: Any) -> None:
    root = frappe._dict(
        name="Root",
        parent_account=None,
        indent=0,
        is_group=1,
        account_name="Root",
        account_number="",
        root_type="Asset",
        account_currency="USD",
    )
    child = frappe._dict(
        name="Child",
        parent_account="Root",
        indent=1,
        is_group=0,
        account_name="Child",
        account_number="",
        root_type="Asset",
        account_currency="USD",
    )
    accounts = [root, child]
    mapping = {"Root": root, "Child": child}
    parents = {None: [root], "Root": [child]}
    filters = frappe._dict(
        company="K",
        from_date="2026-01-01",
        to_date="2026-01-31",
        show_group_accounts=1,
        show_zero_values=1,
        show_net_values=1,
    )
    opening = {
        "Child": {
            "opening_debit": 100,
            "opening_credit": 20,
            "opening_debit_in_account_currency": 100,
            "opening_credit_in_account_currency": 20,
            "account_currencies": {"USD"},
        }
    }
    period = {
        "Child": {
            "debit": 30,
            "credit": 10,
            "debit_in_account_currency": 30,
            "credit_in_account_currency": 10,
            "account_currencies": {"USD"},
        }
    }
    db = SimpleNamespace(
        get_single_value=false_value,
        count=false_value,
        get_value=hundred_value,
    )
    with (
        patch.object(data, "_get_accounts", return_value=accounts),
        patch.object(data.erpnext, "get_company_currency", return_value="USD"),
        patch.object(data.frappe, "db", db),
        patch.object(data, "get_accounting_dimensions", return_value=[]),
        patch.object(
            data, "filter_accounts", return_value=(accounts, mapping, parents)
        ),
        patch.object(data, "_get_opening_balances", return_value=opening),
        patch.object(data, "get_period_balances", return_value=period),
    ):
        rows = data.get_data(filters)
    root_row = next(row for row in rows if row["account"] == "Root")
    assert (root_row["opening_debit"], root_row["opening_credit"]) == (80, 0)
    assert (root_row["debit"], root_row["credit"]) == (30, 10)
    assert (root_row["closing_debit"], root_row["closing_credit"]) == (100, 0)
    assert (
        root_row["closing_debit_in_account_currency"],
        root_row["closing_credit_in_account_currency"],
    ) == (100, 0)


def test_get_data_mixed_currency_suppresses_parent_account_values(data: Any) -> None:
    accounts = [
        frappe._dict(
            name="Root",
            parent_account=None,
            indent=0,
            is_group=1,
            account_name="Root",
            account_number="",
            root_type="Asset",
        ),
        frappe._dict(
            name="USD",
            parent_account="Root",
            indent=1,
            is_group=0,
            account_name="USD",
            account_number="",
            root_type="Asset",
            account_currency="USD",
        ),
        frappe._dict(
            name="EUR",
            parent_account="Root",
            indent=1,
            is_group=0,
            account_name="EUR",
            account_number="",
            root_type="Asset",
            account_currency="EUR",
        ),
    ]
    mapping = {a.name: a for a in accounts}
    parents = {None: [accounts[0]], "Root": accounts[1:]}
    filters = frappe._dict(
        company="K",
        from_date="2026-01-01",
        to_date="2026-01-31",
        show_group_accounts=1,
        show_zero_values=1,
        show_net_values=1,
    )
    opening = {
        "USD": {
            "opening_debit": 100,
            "opening_debit_in_account_currency": 100,
            "account_currencies": {"USD"},
        },
        "EUR": {
            "opening_debit": 200,
            "opening_debit_in_account_currency": 180,
            "account_currencies": {"EUR"},
        },
    }
    db = SimpleNamespace(
        get_single_value=false_value,
        count=false_value,
        get_value=hundred_value,
    )
    with (
        patch.object(data, "_get_accounts", return_value=accounts),
        patch.object(data.erpnext, "get_company_currency", return_value="USD"),
        patch.object(data.frappe, "db", db),
        patch.object(data, "get_accounting_dimensions", return_value=[]),
        patch.object(
            data, "filter_accounts", return_value=(accounts, mapping, parents)
        ),
        patch.object(data, "_get_opening_balances", return_value=opening),
        patch.object(data, "get_period_balances", return_value={}),
    ):
        rows = data.get_data(filters)
    root = next(r for r in rows if r["account"] == "Root")
    usd = next(r for r in rows if r["account"] == "USD")
    eur = next(r for r in rows if r["account"] == "EUR")
    assert (
        root["closing_debit"] == 300
        and root["account_currency"] == ""
        and root["closing_debit_in_account_currency"] is None
    )
    assert (usd["account_currency"], usd["closing_debit_in_account_currency"]) == (
        "USD",
        100,
    )
    assert (eur["account_currency"], eur["closing_debit_in_account_currency"]) == (
        "EUR",
        180,
    )


def test_accounts_query_and_ignore_closing_and_currency_aggregate(data: Any) -> None:
    captured = []
    typ = type(MariaDB.from_(MariaDB.DocType("Account")))

    def capture(q: Any, **_k: Any) -> list[Any]:
        captured.append(q.get_sql())
        return [frappe._dict(name="A")]

    qb = SimpleNamespace(DocType=MariaDB.DocType, from_=MariaDB.from_)
    with patch.object(data.frappe, "qb", qb), patch.object(typ, "run", capture):
        assert data._get_accounts("K") == [frappe._dict(name="A")]
    assert "`company`='K'" in captured[0] and "ORDER BY `lft`" in captured[0]
    filters = frappe._dict(company="K", from_date="2026-02-01")
    db = SimpleNamespace(get_single_value=true_value, get_all=MagicMock())
    with (
        patch.object(data.frappe, "db", db),
        patch.object(data, "_get_gl_opening_currency_rows", return_value=[]),
    ):
        assert data._get_opening_balances(filters, False) == {}
    db.get_all.assert_not_called()


def test_opening_presentation_currency_groups_and_provenance(data: Any) -> None:
    rows = [
        frappe._dict(
            account="A",
            report_type="Balance Sheet",
            account_currency="USD",
            debit=10,
            credit=0,
            debit_in_account_currency=10,
            credit_in_account_currency=0,
        ),
        frappe._dict(
            account="A",
            report_type="Profit and Loss",
            account_currency="EUR",
            debit=0,
            credit=5,
            debit_in_account_currency=0,
            credit_in_account_currency=5,
        ),
        frappe._dict(
            account="A",
            report_type="Profit and Loss",
            account_currency="JPY",
            debit=0,
            credit=0,
            debit_in_account_currency=0,
            credit_in_account_currency=0,
        ),
    ]
    calls = []

    def convert(group: Any, *_a: Any) -> None:
        calls.append({r.report_type for r in group})
        for r in group:
            r.debit *= 2
            r.credit *= 2

    with (
        patch.object(data, "get_currency", return_value={}),
        patch.object(data, "convert_to_presentation_currency", side_effect=convert),
    ):
        result = data._aggregate_opening_entries(
            rows, frappe._dict(presentation_currency="INR")
        )
    assert calls == [{"Balance Sheet"}, {"Profit and Loss"}]
    assert (
        result["A"]["opening_debit"] == 20
        and result["A"]["opening_credit"] == 10
        and result["A"]["account_currencies"] == {"USD", "EUR"}
    )


def test_closing_query_including_pcv_omits_exclusion(data: Any) -> None:
    captured = []
    typ = type(MariaDB.from_(MariaDB.DocType("Account Closing Balance")))

    def capture(q: Any, **_k: Any) -> list[Any]:
        captured.append(q.get_sql())
        return []

    qb = SimpleNamespace(DocType=MariaDB.DocType, from_=MariaDB.from_)
    with (
        patch.object(data.frappe, "qb", qb),
        patch.object(data, "apply_gl_filters", side_effect=identity_query),
        patch.object(typ, "run", capture),
    ):
        data._get_account_closing_currency_rows(
            frappe._dict(company="K", with_period_closing_entry_for_opening=1), "PCV"
        )
    assert "is_period_closing_voucher_entry" not in captured[0]


def test_conditions_metadata_default_book_and_tree_dimension_branches() -> None:
    c = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_conditions"
    )
    gl = MariaDB.DocType("GL Entry")
    q = MariaDB.from_(gl).select(gl.name)
    dim = SimpleNamespace(fieldname="branch", document_type="Branch")
    db = SimpleNamespace(count=MagicMock(return_value=1))
    filters = frappe._dict(
        company="K", finance_book="FB", include_default_book_entries=1, branch="North"
    )
    with (
        patch.object(c.frappe, "db", db),
        patch.object(c, "get_accounting_dimensions", return_value=[dim]) as meta,
        patch.object(c.frappe, "get_cached_value", side_effect=["FB", True]),
        patch.object(c, "get_dimension_with_children", return_value=["North", "Child"]),
    ):
        sql = c.apply_gl_filters(q, gl, filters).get_sql()
    meta.assert_called_once_with(as_list=False)
    db.count.assert_called_once_with("Finance Book")
    for token in (
        "`finance_book` IN ('FB','FB','')",
        "`finance_book` IS NULL",
        "`branch` IN ('North','Child')",
    ):
        assert token in sql
    with patch.object(c.frappe, "get_cached_value") as lookup:
        assert (
            c.apply_gl_filters(
                q,
                gl,
                frappe._dict(company="K"),
                finance_books=False,
                accounting_dimensions=[],
            ).get_sql()
            == q.get_sql()
        )
    lookup.assert_not_called()


def test_filter_fiscal_year_defaults_bounds_and_ignore_path() -> None:
    f = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_filters"
    )
    fy = frappe._dict(year_start_date="2026-01-01", year_end_date="2026-12-31")
    filters = frappe._dict(fiscal_year="2026", show_group_accounts=1)
    with patch.object(f.frappe, "get_cached_value", return_value=fy):
        f.validate_filters(filters)
    assert isinstance(filters.from_date, date)
    assert isinstance(filters.to_date, date)
    assert (
        filters.from_date.isoformat(),
        filters.to_date.isoformat(),
        filters.show_group_accounts,
    ) == ("2026-01-01", "2026-12-31", 1)
    clamped = frappe._dict(
        fiscal_year="2026", from_date="2025-01-01", to_date="2027-01-01"
    )
    with (
        patch.object(f.frappe, "get_cached_value", return_value=fy),
        patch.object(f.frappe, "msgprint") as msg,
        patch.object(f, "formatdate", side_effect=date.isoformat),
    ):
        f.validate_filters(clamped)
    assert isinstance(clamped.from_date, date)
    assert isinstance(clamped.to_date, date)
    assert (clamped.from_date.isoformat(), clamped.to_date.isoformat()) == (
        "2026-01-01",
        "2026-12-31",
    )
    assert msg.call_count == 2
    ignored = frappe._dict(
        ignore_fiscal_year=1, from_date="2026-02-01", to_date="2026-02-02", company="K"
    )
    with patch.object(
        f, "get_fiscal_year", return_value=("FY", "2026-01-01", "2026-12-31")
    ) as getfy:
        f.validate_filters(ignored)
    getfy.assert_called_once_with(ignored.from_date, company="K", verbose=0)


@pytest.mark.parametrize(
    "filters",
    [
        frappe._dict(),
        frappe._dict(ignore_fiscal_year=1, to_date="2026-01-01"),
        frappe._dict(ignore_fiscal_year=1, from_date="2026-01-01"),
    ],
)
def test_filter_required_values_throw(filters: Any) -> None:
    f = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_filters"
    )
    with (
        patch.object(f.frappe, "throw", side_effect=RuntimeError("invalid")) as throw,
        pytest.raises(RuntimeError),
    ):
        f.validate_filters(filters)
    throw.assert_called_once()


def test_filter_reversed_ranges_and_checkbox() -> None:
    f = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_filters"
    )
    fy = frappe._dict(year_start_date="2026-01-01", year_end_date="2026-12-31")
    with (
        patch.object(f.frappe, "get_cached_value", return_value=fy),
        patch.object(f.frappe, "throw", side_effect=RuntimeError("reverse")),
        pytest.raises(RuntimeError),
    ):
        f.validate_filters(
            frappe._dict(fiscal_year="FY", from_date="2026-02-01", to_date="2026-01-01")
        )
    with (
        patch.object(
            f, "get_fiscal_year", return_value=("FY", "2026-01-01", "2026-12-31")
        ),
        patch.object(f.frappe, "throw", side_effect=RuntimeError("reverse")),
        pytest.raises(RuntimeError),
    ):
        f.validate_filters(
            frappe._dict(
                ignore_fiscal_year=1,
                from_date="2026-02-01",
                to_date="2026-01-01",
                company="K",
            )
        )
    filters = frappe._dict(fiscal_year="FY")
    with patch.object(f.frappe, "get_cached_value", return_value=fy):
        f.validate_filters(filters)
    assert filters.show_group_accounts == 0


def test_period_currency_grouping_and_optimised_forwarding() -> None:
    q = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_query"
    )
    rows = [
        frappe._dict(
            account="A",
            account_currency="USD",
            debit=10,
            credit=2,
            debit_in_account_currency=10,
            credit_in_account_currency=2,
        ),
        frappe._dict(
            account="A",
            account_currency="EUR",
            debit=5,
            credit=1,
            debit_in_account_currency=0,
            credit_in_account_currency=0,
        ),
        frappe._dict(
            account="A",
            account_currency="",
            debit=1,
            credit=0,
            debit_in_account_currency=0,
            credit_in_account_currency=0,
        ),
    ]
    grouped = q._group_currency_rows(rows)["A"]
    assert (grouped["debit"], grouped["credit"], grouped["account_currencies"]) == (
        16,
        3,
        {"USD"},
    )
    db = SimpleNamespace(get_single_value=MagicMock(return_value=True))
    with (
        patch.object(q.frappe, "db", db),
        patch.object(q, "get_period_balances", return_value={"A": {}}) as period,
    ):
        assert q.get_gl_data_optimised(frappe._dict()) == {"A": {}}
    period.assert_called_once_with(frappe._dict(), True)


def test_period_balances_real_qb_sql_and_presentation_conversion() -> None:
    q = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_query"
    )
    captured = []
    typ = type(MariaDB.from_(MariaDB.DocType("GL Entry")))
    rows = [
        frappe._dict(
            account="A",
            account_currency="USD",
            debit=10,
            credit=2,
            debit_in_account_currency=10,
            credit_in_account_currency=2,
        )
    ]

    def capture(query: Any, **_k: Any) -> list[Any]:
        captured.append(query.get_sql())
        return rows

    qb = SimpleNamespace(DocType=MariaDB.DocType, from_=MariaDB.from_)
    f = frappe._dict(
        company="K",
        from_date="2026-01-01",
        to_date="2026-01-31",
        with_period_closing_entry_for_current_period=0,
    )
    with (
        patch.object(q.frappe, "qb", qb),
        patch.object(q, "apply_gl_filters", side_effect=identity_query),
        patch.object(typ, "run", capture),
    ):
        out = q.get_period_balances(f, 0)
    assert out["A"]["debit"] == 10
    for token in (
        "`company`='K'",
        "`is_cancelled`=0",
        "`posting_date`>='2026-01-01'",
        "`posting_date`<='2026-01-31'",
        "`is_opening`='No'",
        "`voucher_type`<>'Period Closing Voucher'",
        "GROUP BY `account`,`account_currency`",
    ):
        assert token in captured[0]


def test_period_balances_ignore_opening_pcv_and_conversion() -> None:
    q = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_query"
    )
    captured = []
    typ = type(MariaDB.from_(MariaDB.DocType("GL Entry")))
    rows = [
        frappe._dict(
            account="A",
            account_currency="USD",
            debit=10,
            credit=0,
            debit_in_account_currency=7,
            credit_in_account_currency=0,
        )
    ]

    def capture(query: Any, **_k: Any) -> list[Any]:
        captured.append(query.get_sql())
        return rows

    def convert(rs: Any, *_a: Any) -> None:
        rs[0].debit *= 2

    qb = SimpleNamespace(DocType=MariaDB.DocType, from_=MariaDB.from_)
    f = frappe._dict(
        company="K",
        from_date="2026-01-01",
        to_date="2026-01-31",
        with_period_closing_entry_for_current_period=1,
        presentation_currency="INR",
    )
    with (
        patch.object(q.frappe, "qb", qb),
        patch.object(q, "apply_gl_filters", side_effect=identity_query),
        patch.object(typ, "run", capture),
        patch.object(q, "get_currency", return_value={}),
        patch.object(q, "convert_to_presentation_currency", side_effect=convert),
    ):
        out = q.get_period_balances(f, 1)
    assert "is_opening" not in captured[0] and "voucher_type" not in captured[0]
    assert out["A"]["debit"] == 20 and out["A"]["debit_in_account_currency"] == 7


def test_aggregation_compatibility_nets_negative_and_currency_modes() -> None:
    a = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_aggregation"
    )
    account = frappe._dict(name="A", root_type="Asset", account_currency="USD")
    a.apply_gl_data_to_accounts(
        [account],
        {
            "A": {
                "opening_debit": 20,
                "opening_credit": 100,
                "debit": 5,
                "credit": 0,
                "opening_debit_in_account_currency": 20,
                "opening_credit_in_account_currency": 100,
                "debit_in_account_currency": 5,
                "credit_in_account_currency": 0,
                "account_currencies": {"USD"},
            }
        },
        show_net_values=True,
    )
    a.prepare_opening_closing(account)
    assert (
        account.opening_debit,
        account.opening_credit,
        account.closing_debit,
        account.closing_credit,
    ) == (0, 80, 0, 75)
    assert (
        account.opening_debit_in_account_currency,
        account.opening_credit_in_account_currency,
    ) == (0, 80)
    blank = frappe._dict(
        opening_debit=1, opening_credit=2, closing_debit=1, closing_credit=2
    )
    a.prepare_opening_closing(blank)
    assert blank.opening_debit == 1


def test_filter_unknown_fy_and_real_invalid_date_throw() -> None:
    f = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_filters"
    )
    with (
        patch.object(f.frappe, "get_cached_value", return_value=None),
        patch.object(f.frappe, "throw", side_effect=RuntimeError("missing")),
        pytest.raises(RuntimeError),
    ):
        f.validate_filters(frappe._dict(fiscal_year="Unknown"))
    with (
        patch.object(f.frappe, "throw", side_effect=RuntimeError("date")),
        pytest.raises(RuntimeError),
    ):
        f._require_date("0000-00-00", "From Date")


def test_account_currency_opening_ignore_switch() -> None:
    a = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_aggregation"
    )
    account = frappe._dict(name="A", root_type="Asset")
    entries = {
        "A": [
            frappe._dict(
                is_opening="Yes",
                account_currency="USD",
                debit_in_account_currency=5,
                credit_in_account_currency=0,
            )
        ]
    }
    a.apply_account_currency_data_to_accounts(
        [account], entries, {}, show_net_values=False, ignore_is_opening=0
    )
    assert account.debit_in_account_currency == 0
    a.apply_account_currency_data_to_accounts(
        [account], entries, {}, show_net_values=False, ignore_is_opening=1
    )
    assert account.debit_in_account_currency == 5


def test_final_tbk_helper_noops_and_empty_dimension() -> None:
    a = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_aggregation"
    )
    a.prepare_account_currency_opening_closing(
        frappe._dict(
            opening_debit_in_account_currency=1, opening_credit_in_account_currency=2
        )
    )
    c = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_conditions"
    )
    gl = MariaDB.DocType("GL Entry")
    q = MariaDB.from_(gl).select(gl.name)
    dim = SimpleNamespace(fieldname="branch", document_type="Branch")
    with patch.object(c.frappe, "get_cached_value", return_value=False):
        sql = c._apply_dimension_filters(
            q, gl, frappe._dict(branch=[]), accounting_dimensions=[dim]
        ).get_sql()
    assert "WHERE" not in sql


def test_tbk_rows_columns_and_controller_wrappers() -> None:
    rows = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_rows"
    )
    assert rows._hide_group_accounts(
        [{"is_group_account": 1}, {"is_group_account": 0, "indent": 4}]
    ) == [{"is_group_account": 0, "indent": 0}]
    controller = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).trial_balance_(karam)"
    )
    with (
        patch.object(controller, "_get_gl_data_optimised", return_value={"x": 1}) as gl,
        patch.object(
            controller, "_apply_gl_data_to_accounts", return_value="ok"
        ) as apply,
        patch.object(
            controller, "_accumulate_values_into_parents", return_value="sum"
        ) as acc,
        patch.object(controller, "_get_columns", return_value=["c"]),
    ):
        assert controller.get_gl_data_optimised({"f": 1}) == {"x": 1}
        assert controller.apply_gl_data_to_accounts([], {}, True) == "ok"
        assert controller.accumulate_values_into_parents([], {}) == "sum"
        assert controller.get_columns() == ["c"]
    gl.assert_called_once_with({"f": 1})
    apply.assert_called_once_with([], {}, True)
    acc.assert_called_once_with([], {})
