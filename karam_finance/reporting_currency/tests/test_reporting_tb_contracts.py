"""Pure reporting-currency Trial Balance contracts."""

from __future__ import annotations

import importlib
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import frappe
import pytest
from frappe.query_builder.builder import MariaDB

MODULE = "karam_finance.reporting_currency.report.trial_balance_(reporting_currency).tbk_data"


def _identity_text(message: str) -> str:
    return message


def _identity_query(query: Any, *_args: Any, **_kwargs: Any) -> Any:
    return query


@pytest.fixture(scope="module")
def data() -> Any:
    return importlib.import_module(MODULE)


def test_opening_aggregation_preserves_three_money_layers(data: Any) -> None:
    entries = [
        frappe._dict(
            account="A",
            account_currency="EUR",
            debit=Decimal(10),
            credit=0,
            debit_in_company_currency=Decimal(100),
            credit_in_company_currency=0,
            debit_in_account_currency=Decimal(8),
            credit_in_account_currency=0,
        ),
        frappe._dict(
            account="A",
            account_currency="EUR",
            debit=0,
            credit=Decimal(2),
            debit_in_company_currency=0,
            credit_in_company_currency=Decimal(20),
            debit_in_account_currency=0,
            credit_in_account_currency=Decimal(1),
        ),
        frappe._dict(
            account="A",
            account_currency="JPY",
            debit=0,
            credit=0,
            debit_in_company_currency=0,
            credit_in_company_currency=0,
            debit_in_account_currency=0,
            credit_in_account_currency=0,
        ),
    ]
    with patch.object(data, "_get_gl_opening_currency_rows", return_value=entries):
        actual = data._get_opening_balances(frappe._dict(), False)
    row = actual["A"]
    assert (row["opening_debit"], row["opening_credit"]) == (Decimal(10), Decimal(2))
    assert (
        row["opening_debit_in_company_currency"],
        row["opening_credit_in_company_currency"],
    ) == (Decimal(100), Decimal(20))
    assert (
        row["opening_debit_in_account_currency"],
        row["opening_credit_in_account_currency"],
    ) == (Decimal(8), Decimal(1))
    assert row["account_currencies"] == {"EUR"}


def test_get_data_pipeline_preserves_reporting_company_and_account_layers(
    data: Any,
) -> None:
    root = frappe._dict(
        name="Root",
        parent_account=None,
        indent=0,
        is_group=1,
        account_name="Root",
        account_number="",
        root_type="Asset",
        account_currency="EUR",
    )
    child = frappe._dict(
        name="Child",
        parent_account="Root",
        indent=1,
        is_group=0,
        account_name="Child",
        account_number="",
        root_type="Asset",
        account_currency="EUR",
    )
    accounts = [root, child]
    mapping = {x.name: x for x in accounts}
    parents = {None: [root], "Root": [child]}
    f = frappe._dict(
        company="K",
        presentation_currency="USD",
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
            "opening_debit_in_company_currency": 1000,
            "opening_credit_in_company_currency": 200,
            "opening_debit_in_account_currency": 90,
            "opening_credit_in_account_currency": 18,
            "account_currencies": {"EUR"},
        }
    }
    period = {
        "Child": {
            "debit": 30,
            "credit": 10,
            "debit_in_company_currency": 300,
            "credit_in_company_currency": 100,
            "debit_in_account_currency": 27,
            "credit_in_account_currency": 9,
            "account_currencies": {"EUR"},
        }
    }
    rows_module = importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_rows")
    db = type(
        "DB",
        (),
        {
            "get_single_value": lambda *_: False,
            "count": lambda *_: False,
            "get_value": lambda *_: 100,
        },
    )()
    with (
        patch.object(data, "_get_accounts", return_value=accounts),
        patch.object(data.erpnext, "get_company_currency", return_value="INR"),
        patch.object(data.frappe, "db", db),
        patch.object(data, "get_accounting_dimensions", return_value=[]),
        patch.object(
            data, "filter_accounts", return_value=(accounts, mapping, parents)
        ),
        patch.object(data, "_get_opening_balances", return_value=opening),
        patch.object(data, "get_period_balances", return_value=period),
        patch.object(rows_module, "get_zero_cutoff", return_value=0.005),
    ):
        rows = data.get_data(f)
    row = next(x for x in rows if x["account"] == "Root")
    child_row = next(x for x in rows if x["account"] == "Child")
    total = next(x for x in rows if x.get("is_total"))
    assert (row["closing_debit"], row["closing_credit"]) == (100, 0)
    assert (
        row["closing_debit_in_company_currency"],
        row["closing_credit_in_company_currency"],
    ) == (1000, 0)
    assert (
        row["closing_debit_in_account_currency"],
        row["closing_credit_in_account_currency"],
    ) == (90, 0)
    assert (row["currency"], row["company_currency"], row["account_currency"]) == (
        "USD",
        "INR",
        "EUR",
    )
    assert (child_row["closing_debit"], child_row["closing_credit"]) == (100, 0)
    assert (
        child_row["closing_debit_in_company_currency"],
        child_row["closing_credit_in_company_currency"],
    ) == (1000, 0)
    assert (
        child_row["closing_debit_in_account_currency"],
        child_row["closing_credit_in_account_currency"],
    ) == (90, 0)
    assert (total["closing_debit"], total["closing_credit"]) == (100, 0)
    assert (
        total["closing_debit_in_company_currency"],
        total["closing_credit_in_company_currency"],
    ) == (1000, 0)
    assert (
        total["closing_debit_in_account_currency"],
        total["closing_credit_in_account_currency"],
    ) == (90, 0)


def test_get_data_hides_mixed_account_currency_at_parent_and_total(data: Any) -> None:
    accounts = [
        frappe._dict(
            name="Root",
            parent_account=None,
            indent=0,
            is_group=1,
            account_name="Root",
            account_number="",
            root_type="Asset",
            account_currency="",
        ),
        frappe._dict(
            name="EUR child",
            parent_account="Root",
            indent=1,
            is_group=0,
            account_name="EUR child",
            account_number="",
            root_type="Asset",
            account_currency="EUR",
        ),
        frappe._dict(
            name="USD child",
            parent_account="Root",
            indent=1,
            is_group=0,
            account_name="USD child",
            account_number="",
            root_type="Asset",
            account_currency="USD",
        ),
    ]
    mapping = {account.name: account for account in accounts}
    parents = {None: [accounts[0]], "Root": accounts[1:]}
    filters = frappe._dict(
        company="K",
        presentation_currency="CAD",
        from_date="2026-01-01",
        to_date="2026-01-31",
        show_group_accounts=1,
        show_zero_values=1,
    )
    period = {
        "EUR child": {
            "debit": 10,
            "debit_in_company_currency": 100,
            "debit_in_account_currency": 9,
            "account_currencies": {"EUR"},
        },
        "USD child": {
            "debit": 20,
            "debit_in_company_currency": 200,
            "debit_in_account_currency": 20,
            "account_currencies": {"USD"},
        },
    }
    rows_module = importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_rows")
    db = type(
        "DB",
        (),
        {
            "get_single_value": lambda *_: False,
            "count": lambda *_: False,
        },
    )()
    with (
        patch.object(data, "_get_accounts", return_value=accounts),
        patch.object(data.erpnext, "get_company_currency", return_value="INR"),
        patch.object(data.frappe, "db", db),
        patch.object(data, "get_accounting_dimensions", return_value=[]),
        patch.object(
            data, "filter_accounts", return_value=(accounts, mapping, parents)
        ),
        patch.object(data, "_get_opening_balances", return_value={}),
        patch.object(data, "get_period_balances", return_value=period),
        patch.object(rows_module, "get_zero_cutoff", return_value=0.005),
    ):
        rows = data.get_data(filters)

    root = next(row for row in rows if row["account"] == "Root")
    total = next(row for row in rows if row.get("is_total"))
    assert (root["debit"], root["debit_in_company_currency"]) == (30, 300)
    assert (total["debit"], total["debit_in_company_currency"]) == (30, 300)
    assert root["account_currency"] == total["account_currency"] == ""
    for row in (root, total):
        assert all(
            row[field] is None
            for field in (
                "opening_debit_in_account_currency",
                "opening_credit_in_account_currency",
                "debit_in_account_currency",
                "credit_in_account_currency",
                "closing_debit_in_account_currency",
                "closing_credit_in_account_currency",
            )
        )


def _use_mariadb_query_builder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind only the MariaDB query builder; no Frappe site or database is used."""
    monkeypatch.setattr(frappe, "qb", MariaDB)
    monkeypatch.setattr(frappe, "db", type("MariaDB", (), {"db_type": "mariadb"})())


def _capture_opening_sql(
    data: Any,
    filters: Any,
    ignore_is_opening: Any,
    *,
    start_date: str | None = None,
) -> str:
    builder = type(frappe.qb.from_(frappe.qb.DocType("Reporting Currency GLE")))
    captured: list[str] = []

    def capture(query: Any, *_args: Any, **_kwargs: Any) -> list[Any]:
        captured.append(str(query))
        return []

    with (
        patch.object(data, "apply_gl_filters", side_effect=_identity_query),
        patch.object(builder, "run", capture),
    ):
        data._get_gl_opening_currency_rows(
            filters, ignore_is_opening, start_date=start_date
        )
    return captured[0]


def test_opening_query_slice_keeps_normal_entries_and_all_currency_sums(
    monkeypatch: pytest.MonkeyPatch,
    data: Any,
) -> None:
    _use_mariadb_query_builder(monkeypatch)
    sql = _capture_opening_sql(
        data,
        frappe._dict(
            company="K",
            from_date="2026-01-01",
            to_date="2026-01-31",
            show_unclosed_fy_pl_balances=1,
            with_period_closing_entry_for_opening=0,
        ),
        0,
        start_date="2025-01-01",
    )
    assert "`posting_date`>='2025-01-01'" in sql
    assert "`posting_date`<'2026-01-01'" in sql
    assert "`posting_date`<='2026-01-31'" in sql
    assert "`is_opening` IS NULL" in sql
    assert "`is_opening`='No'" in sql
    assert "`voucher_type` IS NULL" in sql
    assert "`voucher_type`<>'Period Closing Voucher'" in sql
    assert "`account`" in sql and "`account_currency`" in sql
    assert "`report_type`" in sql
    # Casted sums retain exact values until decimal_amount rebuilds Decimal values.
    projection_sql = sql.replace("`tabReporting Currency GLE`.", "")
    assert all(
        projection in projection_sql
        for projection in (
            "CONCAT(SUM(`reporting_debit`),'') `debit`",
            "CONCAT(SUM(`reporting_credit`),'') `credit`",
            "CONCAT(SUM(CASE WHEN `reporting_doe`=1 OR `manual_entry`=1 THEN 0 ELSE `debit_amount_in_account_currency` END),'') `debit_in_account_currency`",
            "CONCAT(SUM(CASE WHEN `reporting_doe`=1 OR `manual_entry`=1 THEN 0 ELSE `credit_amount_in_account_currency` END),'') `credit_in_account_currency`",
            "CONCAT(SUM(CASE WHEN `reporting_doe`=1 OR `manual_entry`=1 THEN 0 ELSE `debit` END),'') `debit_in_company_currency`",
            "CONCAT(SUM(CASE WHEN `reporting_doe`=1 OR `manual_entry`=1 THEN 0 ELSE `credit` END),'') `credit_in_company_currency`",
        )
    )


def test_opening_query_ignore_opening_uses_only_the_requested_history(
    monkeypatch: pytest.MonkeyPatch,
    data: Any,
) -> None:
    _use_mariadb_query_builder(monkeypatch)
    filters = frappe._dict(
        company="K",
        from_date="2026-01-01",
        to_date="2026-01-31",
        show_unclosed_fy_pl_balances=1,
        with_period_closing_entry_for_opening=1,
    )
    sql = _capture_opening_sql(data, filters, 1)
    assert "`posting_date`<'2026-01-01'" in sql
    assert "`posting_date`<='2026-01-31'" in sql
    assert "`is_opening`" not in sql
    assert "Period Closing Voucher" not in sql


def test_opening_query_normal_history_includes_openings_and_limits_pl(
    monkeypatch: pytest.MonkeyPatch,
    data: Any,
) -> None:
    _use_mariadb_query_builder(monkeypatch)
    filters = frappe._dict(
        company="K",
        from_date="2026-01-01",
        to_date="2026-01-31",
        year_start_date="2026-01-01",
        show_unclosed_fy_pl_balances=0,
        with_period_closing_entry_for_opening=1,
    )
    sql = _capture_opening_sql(data, filters, 0)
    assert "`posting_date`<'2026-01-01'" in sql
    assert "`is_opening`='Yes'" in sql
    assert "`posting_date`<='2026-01-31'" in sql
    assert "`report_type`='Balance Sheet'" in sql
    assert "`report_type`='Profit and Loss'" in sql
    assert "`posting_date`>='2026-01-01'" in sql


def _capture_period_sql(query_module: Any, filters: Any, ignore_is_opening: Any) -> str:
    builder = type(frappe.qb.from_(frappe.qb.DocType("Reporting Currency GLE")))
    captured: list[str] = []

    def capture(query: Any, *_args: Any, **_kwargs: Any) -> list[Any]:
        captured.append(str(query))
        return []

    with (
        patch.object(
            query_module,
            "apply_gl_filters",
            side_effect=_identity_query,
        ),
        patch.object(builder, "run", capture),
    ):
        query_module.get_period_balances(filters, ignore_is_opening)
    return captured[0]


def test_period_query_uses_rc_physical_amounts_and_preserves_all_layers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_mariadb_query_builder(monkeypatch)
    query_module = importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_query")
    sql = _capture_period_sql(
        query_module,
        frappe._dict(
            company="K",
            from_date="2026-01-01",
            to_date="2026-01-31",
            with_period_closing_entry_for_current_period=0,
        ),
        0,
    )
    assert "`posting_date`>='2026-01-01'" in sql
    assert "`posting_date`<='2026-01-31'" in sql
    assert "`is_opening` IS NULL" in sql
    assert "`is_opening`='No'" in sql
    assert "`voucher_type` IS NULL" in sql
    assert "`voucher_type`<>'Period Closing Voucher'" in sql
    assert "`account`" in sql and "`account_currency`" in sql
    assert "`reporting_debit`" in sql and "`reporting_credit`" in sql
    assert "`debit_amount_in_account_currency`" in sql
    assert "`credit_amount_in_account_currency`" in sql
    assert "`debit`" in sql and "`credit`" in sql
    assert "`reporting_doe`=1" in sql and "`manual_entry`=1" in sql
    projections = (
        "CONCAT(SUM(`reporting_debit`),'') `debit`",
        "CONCAT(SUM(`reporting_credit`),'') `credit`",
        "CONCAT(SUM(CASE WHEN `reporting_doe`=1 OR `manual_entry`=1 THEN 0 ELSE "
        "`debit_amount_in_account_currency` END),'') `debit_in_account_currency`",
        "CONCAT(SUM(CASE WHEN `reporting_doe`=1 OR `manual_entry`=1 THEN 0 ELSE "
        "`credit_amount_in_account_currency` END),'') `credit_in_account_currency`",
        "CONCAT(SUM(CASE WHEN `reporting_doe`=1 OR `manual_entry`=1 THEN 0 ELSE "
        "`debit` END),'') `debit_in_company_currency`",
        "CONCAT(SUM(CASE WHEN `reporting_doe`=1 OR `manual_entry`=1 THEN 0 ELSE "
        "`credit` END),'') `credit_in_company_currency`",
    )
    assert all(projection in sql for projection in projections)


def test_get_data_returns_none_without_accounts(data: Any) -> None:
    with patch.object(data, "_get_accounts", return_value=[]):
        assert data.get_data(frappe._dict(company="K")) is None


def test_account_query_selects_contract_fields_in_tree_order(
    monkeypatch: pytest.MonkeyPatch, data: Any
) -> None:
    _use_mariadb_query_builder(monkeypatch)
    builder = type(frappe.qb.from_(frappe.qb.DocType("Account")))
    captured: list[str] = []

    def capture(query: Any, *_args: Any, **_kwargs: Any) -> list[Any]:
        captured.append(str(query))
        return []

    with patch.object(builder, "run", capture):
        assert data._get_accounts("K") == []
    sql = captured[0]
    assert "FROM `tabAccount`" in sql
    assert "`company`='K'" in sql
    assert "ORDER BY `lft`" in sql
    for field in data.ACCOUNT_FIELDS:
        assert f"`{field}`" in sql


def test_opening_slice_ignores_opening_flag_when_requested(
    monkeypatch: pytest.MonkeyPatch, data: Any
) -> None:
    _use_mariadb_query_builder(monkeypatch)
    sql = _capture_opening_sql(
        data,
        frappe._dict(
            company="K",
            from_date="2026-01-01",
            to_date="2026-01-31",
            show_unclosed_fy_pl_balances=1,
            with_period_closing_entry_for_opening=1,
        ),
        1,
        start_date="2025-01-01",
    )
    assert "`posting_date`>='2025-01-01'" in sql
    assert "`posting_date`<'2026-01-01'" in sql
    assert "`is_opening`" not in sql


def test_period_query_allows_openings_and_pcvs_when_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_mariadb_query_builder(monkeypatch)
    query_module = importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_query")
    sql = _capture_period_sql(
        query_module,
        frappe._dict(
            company="K",
            from_date="2026-01-01",
            to_date="2026-01-31",
            with_period_closing_entry_for_current_period=1,
        ),
        1,
    )
    assert "`is_opening`" not in sql
    assert "Period Closing Voucher" not in sql


def test_group_currency_rows_blanks_mixed_and_zero_currency_values() -> None:
    query_module = importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_query")
    rows = [
        frappe._dict(
            account="A",
            account_currency="",
            debit=1,
            debit_in_company_currency=10,
        ),
        frappe._dict(
            account="A",
            account_currency="EUR",
            debit_in_account_currency=0,
            credit_in_account_currency=0,
        ),
        frappe._dict(
            account="A",
            account_currency="USD",
            debit=2,
            debit_in_company_currency=20,
            debit_in_account_currency=3,
        ),
    ]
    grouped = query_module._group_currency_rows(rows)
    assert grouped["A"]["account_currencies"] == {"USD"}
    assert grouped["A"]["debit"] == Decimal(3)
    assert grouped["A"]["debit_in_company_currency"] == Decimal(30)
    assert grouped["A"]["debit_in_account_currency"] == Decimal(3)


def test_compatibility_query_reads_and_forwards_opening_setting() -> None:
    query_module = importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_query")
    db = type("DB", (), {"get_single_value": lambda *_: 1})()
    with (
        patch.object(query_module.frappe, "db", db),
        patch.object(query_module, "get_period_balances", return_value={}) as balances,
    ):
        assert query_module.get_gl_data_optimised(frappe._dict(company="K")) == {}
    balances.assert_called_once_with(frappe._dict(company="K"), 1)


def _conditions_query() -> tuple[Any, Any]:
    ledger = frappe.qb.DocType("Reporting Currency GLE")
    return frappe.qb.from_(ledger).select(ledger.name), ledger


def _permission_query() -> Any:
    permitted = frappe.qb.DocType("Permitted Reporting Currency GLE")
    return frappe.qb.from_(permitted).select(permitted.name)


def test_conditions_intersect_permissions_and_apply_doe_manual_cost_and_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_mariadb_query_builder(monkeypatch)
    conditions = importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_conditions")
    query, ledger = _conditions_query()
    calls: list[tuple[Any, Any]] = []

    def capture_permission_query(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return _permission_query()

    with (
        patch.object(
            frappe.qb,
            "get_query",
            side_effect=capture_permission_query,
            create=True,
        ),
        patch.object(conditions, "get_cost_centers_with_children", return_value=["CC"]),
    ):
        actual = conditions.apply_gl_filters(
            query,
            ledger,
            frappe._dict(
                exclude_reporting_doe=1,
                exclude_manual_entries=1,
                cost_center=["CC"],
                project="P1,P2",
            ),
            finance_books=False,
            accounting_dimensions=[],
        )
    sql = str(actual)
    assert calls == [
        (("Reporting Currency GLE",), {"fields": ["name"], "ignore_permissions": False})
    ]
    assert "WHERE `name` IN (SELECT" in sql
    assert ") AND COALESCE(`reporting_doe`,0)=0" in sql
    assert "Permitted Reporting Currency GLE" in sql
    assert "COALESCE(`reporting_doe`,0)=0" in sql
    assert "COALESCE(`manual_entry`,0)=0" in sql
    assert "`cost_center` IN ('CC')" in sql
    assert "`project` IN ('P1','P2')" in sql


def test_conditions_loads_dimensions_and_applies_tree_and_plain_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_mariadb_query_builder(monkeypatch)
    conditions = importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_conditions")
    query, ledger = _conditions_query()
    dimension = frappe._dict(
        fieldname="territory", label="Territory", document_type="Territory"
    )
    meta = type("Meta", (), {"has_field": lambda *_: True})()
    with (
        patch.object(
            frappe.qb, "get_query", return_value=_permission_query(), create=True
        ),
        patch.object(conditions, "get_accounting_dimensions", return_value=[dimension]),
        patch.object(conditions.frappe, "get_meta", return_value=meta),
        patch.object(conditions.frappe, "get_cached_value", return_value=True),
        patch.object(
            conditions, "get_dimension_with_children", return_value=["North", "North-1"]
        ),
    ):
        actual = conditions.apply_gl_filters(
            query, ledger, frappe._dict(territory="North"), finance_books=False
        )
    assert "`territory` IN ('North','North-1')" in str(actual)


def test_conditions_rejects_unstored_dimension(monkeypatch: pytest.MonkeyPatch) -> None:
    _use_mariadb_query_builder(monkeypatch)
    conditions = importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_conditions")
    query, ledger = _conditions_query()
    dimension = frappe._dict(fieldname="bad", label="Bad", document_type="Bad")
    meta = type("Meta", (), {"has_field": lambda *_: False})()
    with (
        patch.object(
            frappe.qb, "get_query", return_value=_permission_query(), create=True
        ),
        patch.object(conditions.frappe, "get_meta", return_value=meta),
        patch.object(conditions, "_", side_effect=_identity_text),
        patch.object(
            conditions.frappe,
            "throw",
            side_effect=frappe.ValidationError("unsupported"),
        ),
        pytest.raises(frappe.ValidationError),
    ):
        conditions.apply_gl_filters(
            query,
            ledger,
            frappe._dict(bad="X"),
            finance_books=False,
            accounting_dimensions=[dimension],
        )


def test_finance_book_filter_handles_off_default_and_include_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_mariadb_query_builder(monkeypatch)
    conditions = importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_conditions")
    query, ledger = _conditions_query()
    assert (
        conditions._apply_finance_book_filter(
            query, ledger, frappe._dict(), finance_books=False
        )
        is query
    )
    with patch.object(conditions.frappe, "get_cached_value", return_value="Default"):
        default_off = conditions._apply_finance_book_filter(
            query, ledger, frappe._dict(finance_book="Chosen"), finance_books=True
        )
        include_default = conditions._apply_finance_book_filter(
            query,
            ledger,
            frappe._dict(finance_book="Default", include_default_book_entries=1),
            finance_books=True,
        )
    assert "`finance_book` IN ('Chosen','')" in str(default_off)
    assert "`finance_book` IN ('Default','Default','')" in str(include_default)


def test_finance_book_filter_rejects_conflicting_default_book(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_mariadb_query_builder(monkeypatch)
    conditions = importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_conditions")
    query, ledger = _conditions_query()
    with (
        patch.object(conditions.frappe, "get_cached_value", return_value="Default"),
        patch.object(conditions, "_", side_effect=_identity_text),
        patch.object(
            conditions.frappe, "throw", side_effect=frappe.ValidationError("conflict")
        ),
        pytest.raises(frappe.ValidationError),
    ):
        conditions._apply_finance_book_filter(
            query,
            ledger,
            frappe._dict(finance_book="Other", include_default_book_entries=1),
            finance_books=True,
        )


def test_conditions_as_list_handles_strings_and_iterables() -> None:
    conditions = importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_conditions")
    assert conditions._as_list(" A, ,B ") == ["A", "B"]
    assert conditions._as_list(("A", "B")) == ["A", "B"]


def test_conditions_handles_empty_and_non_tree_dimensions_with_default_books(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _use_mariadb_query_builder(monkeypatch)
    conditions = importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_conditions")
    query, ledger = _conditions_query()
    dimension = frappe._dict(fieldname="branch", label="Branch", document_type="Branch")
    meta = type("Meta", (), {"has_field": lambda *_: True})()
    db = type("DB", (), {"count": lambda *_: 0})()
    with (
        patch.object(
            frappe.qb, "get_query", return_value=_permission_query(), create=True
        ),
        patch.object(conditions, "get_accounting_dimensions", return_value=[dimension]),
        patch.object(conditions.frappe, "db", db),
        patch.object(conditions.frappe, "get_meta", return_value=meta),
        patch.object(conditions.frappe, "get_cached_value", return_value=False),
    ):
        assert "`branch`" not in str(
            conditions.apply_gl_filters(query, ledger, frappe._dict())
        )
        actual = conditions.apply_gl_filters(
            query,
            ledger,
            frappe._dict(branch="B1"),
            finance_books=False,
            accounting_dimensions=[dimension],
        )
    assert "`branch` IN ('B1')" in str(actual)
