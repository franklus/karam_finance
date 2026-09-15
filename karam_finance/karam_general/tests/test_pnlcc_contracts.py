"""Pure contracts for the Cost Centre Profit and Loss report."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import frappe
import pytest
from frappe.query_builder.builder import MariaDB

MODULE_NAME = (
    "karam_finance.karam_general.report.profit_and_loss_statement_by_cost_center_(karam)."
    "profit_and_loss_statement_by_cost_center_(karam)"
)


@pytest.fixture(autouse=True)  # noqa: V103 - pytest autouse fixture.
def unrestricted_source_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    # These unit contracts exercise aggregation; site tests cover real restrictions.
    monkeypatch.setattr(frappe, "has_permission", Mock(return_value=True))
    monkeypatch.setattr(frappe, "build_match_conditions", Mock(return_value=""))


@pytest.fixture(scope="module")
def report_module() -> Any:
    return importlib.import_module(MODULE_NAME)


@pytest.fixture
def periods() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            key="p1", label="Jan", from_date="2026-01-01", to_date="2026-01-31"
        ),
        SimpleNamespace(
            key="p2", label="Feb", from_date="2026-02-01", to_date="2026-02-28"
        ),
    ]


@pytest.fixture
def cost_centres() -> list[dict[str, Any]]:
    return [
        {"name": "All", "is_group": 1, "lft": 1, "rgt": 6},
        {
            "name": "Active",
            "parent_cost_center": "All",
            "is_group": 0,
            "lft": 2,
            "rgt": 3,
        },
        {
            "name": "Zero",
            "parent_cost_center": "All",
            "is_group": 0,
            "lft": 4,
            "rgt": 5,
        },
    ]


def _filters(**overrides: object) -> dict[str, object]:
    filters: dict[str, object] = {
        "company": "Karam",
        "from_fiscal_year": "2026",
        "to_fiscal_year": "2026",
        "periodicity": "Monthly",
        "selected_view": "Report",
        "include_default_book_entries": 1,
    }
    filters.update(overrides)
    return filters


def _execute(
    report_module: Any,
    *,
    periods: list[SimpleNamespace],
    cost_centres: list[dict[str, Any]],
    filters: dict[str, object],
) -> tuple[Any, Mock, Mock]:
    amounts = {"Active": {"p1": 70.0, "p2": 150.0}}
    totals = {"Income": [100.0, 200.0], "Expense": [30.0, 50.0]}
    with (
        patch.object(
            report_module, "get_report_periods", return_value=periods
        ) as get_periods,
        patch.object(report_module, "get_allowed_cost_centers", return_value=None),
        patch.object(
            report_module, "get_report_amounts", return_value=(amounts, totals)
        ),
        patch.object(
            report_module,
            "get_report_cost_centres",
            return_value=(cost_centres, None),
        ) as get_cost_centres,
        patch.object(frappe, "get_cached_value", return_value="USD"),
    ):
        result = report_module.execute(filters)
    return result, get_periods, get_cost_centres


def _payload_rows(
    result: tuple[Any, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = result[1]
    payload = rows[-1]
    assert payload["is_footer_payload"] is True
    return rows[:-1], payload["footer_rows"]


def test_execute_report_rolls_up_rows_and_exposes_totals(
    report_module: Any,
    periods: list[SimpleNamespace],
    cost_centres: list[dict[str, Any]],
) -> None:
    result, get_periods, get_cost_centres = _execute(
        report_module, periods=periods, cost_centres=cost_centres, filters=_filters()
    )
    rows, footers = _payload_rows(result)

    assert [row["cost_center"] for row in rows] == ["All", "Active"]
    assert [(row["p1"], row["p2"]) for row in rows] == [(70.0, 150.0), (70.0, 150.0)]
    assert [(row["p1"], row["p2"]) for row in footers] == [
        (100.0, 200.0),
        (30.0, 50.0),
        (70.0, 150.0),
    ]
    assert result[3]["type"] == "bar"
    assert result[4][4]["value"] == 220.0
    assert result[5] == 220.0
    assert get_periods.call_args.kwargs["accumulated_values"] is False
    assert get_cost_centres.call_args.kwargs["required_cost_centers"] == ["Active"]


def test_execute_integrates_ledger_aggregation_into_tree_and_footer(
    report_module: Any,
    periods: list[SimpleNamespace],
    cost_centres: list[dict[str, Any]],
) -> None:
    source_rows = [
        {
            "cost_center": "Active",
            "root_type": "Income",
            "period_key": "p1",
            "base_amount": -100,
        },
        {
            "cost_center": "Active",
            "root_type": "Expense",
            "period_key": "p1",
            "base_amount": 30,
        },
        {
            "cost_center": "Active",
            "root_type": "Income",
            "period_key": "p2",
            "base_amount": -200,
        },
        {
            "cost_center": "Active",
            "root_type": "Expense",
            "period_key": "p2",
            "base_amount": 50,
        },
    ]
    with (
        patch.object(report_module, "get_report_periods", return_value=periods),
        patch.object(report_module, "get_allowed_cost_centers", return_value=None),
        patch.object(
            report_module, "get_report_cost_centres", return_value=(cost_centres, None)
        ),
        patch.object(
            report_module.pnlcc_data, "_run_amount_query", return_value=source_rows
        ),
        patch.object(frappe, "get_cached_value", return_value="USD"),
    ):
        result = report_module.execute(_filters())
    rows, footers = _payload_rows(result)

    assert [(row["p1"], row["p2"]) for row in rows] == [(70.0, 150.0), (70.0, 150.0)]
    assert [(row["p1"], row["p2"]) for row in footers] == [
        (100.0, 200.0),
        (30.0, 50.0),
        (70.0, 150.0),
    ]


def test_execute_accumulated_values_accumulates_before_rows_and_summary(
    report_module: Any,
    periods: list[SimpleNamespace],
    cost_centres: list[dict[str, Any]],
) -> None:
    result, get_periods, _ = _execute(
        report_module,
        periods=periods,
        cost_centres=cost_centres,
        filters=_filters(accumulated_values=1),
    )
    rows, footers = _payload_rows(result)

    assert [(row["p1"], row["p2"]) for row in rows] == [(70.0, 220.0), (70.0, 220.0)]
    assert [(row["p1"], row["p2"]) for row in footers] == [
        (100.0, 300.0),
        (30.0, 80.0),
        (70.0, 220.0),
    ]
    assert result[3]["type"] == "line"
    assert [item["value"] for item in result[4] if "currency" in item] == [
        300.0,
        80.0,
        220.0,
    ]
    assert get_periods.call_args.kwargs["accumulated_values"] is True


def test_execute_growth_preserves_discrete_basis_and_returns_percentages(
    report_module: Any,
    periods: list[SimpleNamespace],
    cost_centres: list[dict[str, Any]],
) -> None:
    result, get_periods, _ = _execute(
        report_module,
        periods=periods,
        cost_centres=cost_centres,
        filters=_filters(selected_view="Growth", accumulated_values=1),
    )
    rows, footers = _payload_rows(result)

    assert [(row["p1"], row["p2"]) for row in rows] == [(70.0, 114.29), (70.0, 114.29)]
    assert [(row["p1"], row["p2"]) for row in footers] == [
        (100.0, 100.0),
        (30.0, 66.67),
        (70.0, 114.29),
    ]
    assert result[3]["type"] == "bar"
    assert get_periods.call_args.kwargs["accumulated_values"] is False


def test_execute_margin_uses_income_as_the_percentage_denominator(
    report_module: Any,
    periods: list[SimpleNamespace],
    cost_centres: list[dict[str, Any]],
) -> None:
    result, _, _ = _execute(
        report_module,
        periods=periods,
        cost_centres=cost_centres,
        filters=_filters(selected_view="Margin"),
    )
    rows, footers = _payload_rows(result)

    assert [(row["p1"], row["p2"]) for row in rows] == [(70.0, 75.0), (70.0, 75.0)]
    assert [(row["p1"], row["p2"]) for row in footers] == [
        (100.0, 100.0),
        (30.0, 25.0),
        (70.0, 75.0),
    ]


def test_execute_show_zero_keeps_requested_empty_cost_centres(
    report_module: Any,
    periods: list[SimpleNamespace],
    cost_centres: list[dict[str, Any]],
) -> None:
    result, _, get_cost_centres = _execute(
        report_module,
        periods=periods,
        cost_centres=cost_centres,
        filters=_filters(show_zero_values=1),
    )
    rows, _ = _payload_rows(result)

    assert [row["cost_center"] for row in rows] == ["All", "Active", "Zero"]
    assert rows[-1]["p1"] == rows[-1]["p2"] == 0.0
    assert get_cost_centres.call_args.kwargs["required_cost_centers"] == ["Active"]


def test_execute_requires_company(report_module: Any) -> None:
    with (
        patch.object(frappe, "throw", side_effect=RuntimeError("mandatory")) as throw,
        pytest.raises(RuntimeError, match="mandatory"),
    ):
        report_module.execute({})

    throw.assert_called_once()


def test_get_report_cost_centres_preserves_unrestricted_and_empty_child_expansion(
    report_module: Any, cost_centres: list[dict[str, Any]]
) -> None:
    filters = frappe._dict(company="Karam")
    with patch.object(
        report_module, "get_cost_centres", return_value=cost_centres
    ) as get_all:
        actual, allowed = report_module.get_report_cost_centres(
            filters, required_cost_centers=["Active"]
        )

    assert actual == cost_centres
    assert allowed is None
    assert get_all.call_args.kwargs["required_cost_centers"] == ["Active"]

    filters.cost_center = "All"
    with (
        patch.object(report_module, "get_cost_centers_with_children", return_value=[]),
        patch.object(report_module, "get_cost_centres", return_value=cost_centres),
    ):
        actual, allowed = report_module.get_report_cost_centres(filters)
    assert actual == cost_centres
    assert allowed is None


def test_get_allowed_cost_centres_treats_empty_child_expansion_as_unrestricted(
    report_module: Any,
) -> None:
    with patch.object(report_module, "get_cost_centers_with_children", return_value=[]):
        assert (
            report_module.get_allowed_cost_centers(frappe._dict(cost_center="All"))
            is None
        )
    assert report_module.get_allowed_cost_centers(frappe._dict()) is None


@pytest.mark.parametrize(
    "case",
    [
        {"rows": [], "currency": None, "show_company": False, "uses_account": False},
        {
            "rows": [{"account_currency": "USD"}],
            "currency": "USD",
            "show_company": False,
            "uses_account": True,
        },
        {
            "rows": [{"account_currency": "USD"}, {"account_currency": "EUR"}],
            "currency": "USD",
            "show_company": False,
            "uses_account": False,
        },
        {
            "rows": [{"account_currency": "USD"}],
            "currency": "USD",
            "show_company": True,
            "uses_account": False,
        },
    ],
)
def test_amount_currency_context_only_uses_account_values_when_all_rows_match(
    report_module: Any, periods: list[SimpleNamespace], case: dict[str, Any]
) -> None:
    data = report_module.pnlcc_data
    presentation_currency = case["currency"]
    currency = (
        {
            "presentation_currency": presentation_currency,
            "company_currency": "USD",
            "report_date": "2026-02-28",
        }
        if presentation_currency
        else None
    )
    with patch.object(data, "get_currency", return_value=currency) as get_currency:
        actual_currency, use_account = data._amount_currency_context(
            case["rows"],
            periods,
            presentation_currency,
            company="Karam",
            show_amount_in_company_currency=case["show_company"],
        )

    assert actual_currency == currency
    assert use_account is case["uses_account"]
    assert get_currency.call_count == int(bool(presentation_currency))


def test_aggregate_amount_rows_uses_income_minus_expense_and_skips_invalid_buckets(
    report_module: Any, periods: list[SimpleNamespace]
) -> None:
    rows = [
        {
            "cost_center": "A",
            "root_type": "Income",
            "period_key": "p1",
            "base_amount": -100,
        },
        {
            "cost_center": "A",
            "root_type": "Expense",
            "period_key": "p1",
            "base_amount": 30,
        },
        {
            "cost_center": "",
            "root_type": "Income",
            "period_key": "p2",
            "base_amount": -20,
        },
        {
            "cost_center": "A",
            "root_type": "Asset",
            "period_key": "p2",
            "base_amount": 999,
        },
        {
            "cost_center": "A",
            "root_type": "Expense",
            "period_key": "unknown",
            "base_amount": 999,
        },
    ]

    amounts, totals = report_module.pnlcc_data._aggregate_amount_rows(
        rows, periods, currency_info=None, use_account_currency=False
    )

    assert amounts == {"A": {"p1": 70.0}}
    assert totals == {"Income": [100.0, 20.0], "Expense": [30.0, 0.0]}


def test_amount_query_compiles_one_bucketed_sql_with_optional_scope_filters(
    report_module: Any, periods: list[SimpleNamespace]
) -> None:
    """The report makes one grouped query; each optional scope stays in that SQL."""
    data = report_module.pnlcc_data
    captured: list[str] = []
    query_type = type(MariaDB.from_(MariaDB.DocType("GL Entry")))

    def capture(query: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        captured.append(query.get_sql())
        return []

    qb = SimpleNamespace(DocType=MariaDB.DocType, from_=MariaDB.from_)
    gl = MariaDB.DocType("GL Entry")
    with (
        patch.object(data.frappe, "qb", qb),
        patch.object(query_type, "run", capture),
        patch.object(data, "get_accounting_dimensions", return_value=[]),
        patch.object(
            data, "finance_book_clause", return_value=gl.finance_book == "FB-1"
        ) as finance_clause,
    ):
        assert (
            data._run_amount_query(
                company="Karam",
                periods=periods,
                finance_book="FB-1",
                include_default_fb=True,
                project_filters='["Project A", "Project B"]',
                restrict_cost_centers=["Active"],
                dimension_filters=None,
                needs_account_currency=True,
            )
            == []
        )

    assert len(captured) == 1
    sql = captured[0]
    for token in (
        "`tabGL Entry`.`company`='Karam'",
        "`tabGL Entry`.`is_cancelled`=0",
        "`tabGL Entry`.`voucher_type`<>'Period Closing Voucher'",
        "`tabGL Entry`.`cost_center` IN ('Active')",
        "`tabGL Entry`.`project` IN ('Project A','Project B')",
        "`tabGL Entry`.`account_currency`",
        "GROUP BY",
        "CASE",
        "2026-01-01",
        "2026-02-28",
        "`finance_book`='FB-1'",
    ):
        assert token in sql
    assert sql.count("SUM(") == 2
    finance_clause.assert_called_once_with(
        gl,
        company="Karam",
        finance_book="FB-1",
        include_default_fb=True,
    )


def test_amount_query_omits_account_currency_when_no_conversion_is_needed(
    report_module: Any, periods: list[SimpleNamespace]
) -> None:
    data = report_module.pnlcc_data
    captured: list[str] = []
    query_type = type(MariaDB.from_(MariaDB.DocType("GL Entry")))

    def capture(query: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        captured.append(query.get_sql())
        return []

    qb = SimpleNamespace(DocType=MariaDB.DocType, from_=MariaDB.from_)
    gl = MariaDB.DocType("GL Entry")
    with (
        patch.object(data.frappe, "qb", qb),
        patch.object(query_type, "run", capture),
        patch.object(data, "get_accounting_dimensions", return_value=[]),
        patch.object(
            data, "finance_book_clause", return_value=gl.finance_book.isnull()
        ),
    ):
        data._run_amount_query(
            company="Karam",
            periods=periods,
            finance_book=None,
            include_default_fb=False,
            project_filters=None,
            restrict_cost_centers=None,
            dimension_filters=None,
            needs_account_currency=False,
        )

    assert "account_currency" not in captured[0]
    assert captured[0].count("SUM(") == 1


def test_dimension_filters_expand_tree_dimensions_and_ignore_empty_selection(
    report_module: Any,
) -> None:
    data = report_module.pnlcc_data
    gl = MariaDB.DocType("GL Entry")
    query = MariaDB.from_(gl).select(gl.name)
    dimensions = [
        SimpleNamespace(fieldname="branch", document_type="Branch"),
        SimpleNamespace(fieldname="department", document_type="Department"),
        SimpleNamespace(fieldname=None, document_type="Ignored"),
    ]
    with (
        patch.object(data, "get_accounting_dimensions", return_value=dimensions),
        patch.object(data.frappe, "get_cached_value", side_effect=[True, False]),
        patch.object(
            data, "get_dimension_with_children", return_value=["North", "North 1"]
        ) as children,
    ):
        actual = data._apply_dimension_filters(
            query, gl, frappe._dict(branch='["North"]', department=[])
        )

    sql = actual.get_sql()
    assert "`branch` IN ('North','North 1')" in sql
    assert "department" not in sql
    children.assert_called_once_with("Branch", ["North"])


@pytest.mark.parametrize(
    "case",
    [
        {
            "finance_book": None,
            "include_default": False,
            "company_default": None,
            "expected": ("",),
        },
        {
            "finance_book": "FB-1",
            "include_default": False,
            "company_default": None,
            "expected": ("FB-1", ""),
        },
        {
            "finance_book": "FB-1",
            "include_default": True,
            "company_default": "FB-1",
            "expected": ("FB-1", "FB-1", ""),
        },
    ],
)
def test_finance_book_clause_includes_default_and_null_values(
    report_module: Any, case: dict[str, Any]
) -> None:
    finance = report_module.pnlcc_finance
    gl = MariaDB.DocType("GL Entry")
    with patch.object(frappe, "get_cached_value", return_value=case["company_default"]):
        clause = finance.finance_book_clause(
            gl,
            company="Karam",
            finance_book=case["finance_book"],
            include_default_fb=case["include_default"],
        )

    sql = clause.get_sql()
    for value in case["expected"]:
        assert f"'{value}'" in sql
    assert "IS NULL" in sql


def test_finance_book_clause_rejects_conflicting_default_book(
    report_module: Any,
) -> None:
    finance = report_module.pnlcc_finance
    with (
        patch.object(frappe, "get_cached_value", return_value="Default FB"),
        patch.object(frappe, "throw", side_effect=RuntimeError("conflict")),
        pytest.raises(RuntimeError, match="conflict"),
    ):
        finance.finance_book_clause(
            MariaDB.DocType("GL Entry"),
            company="Karam",
            finance_book="Alternative FB",
            include_default_fb=True,
        )


def test_parsing_and_row_helpers_cover_scalar_and_tolerance_edges(
    report_module: Any,
) -> None:
    parsing = report_module.pnlcc_parsing
    rows = report_module.pnlcc_rows
    assert parsing.parse_multiselect([" A ", "", 2]) == ["A", "2"]
    assert parsing.parse_multiselect(42) == ["42"]
    assert parsing.normalise_scalar((" first ", "second")) == "first"
    assert parsing.normalise_scalar(42) == "42"
    values = [{"p1": 0.004}, {"p1": -0.005}]
    rows.mark_has_value(values, [SimpleNamespace(key="p1")])
    assert [row["has_value"] for row in values] == [False, True]


def test_controller_empty_and_helper_edge_contracts(report_module: Any) -> None:
    assert [
        row["cost_center"] for row in report_module.build_footer_rows([], [], [])
    ] == ["'Net Profit/Loss'"]
    assert report_module.accumulate_cost_centre_values({"A": {"p1": 2}}, []) == {
        "A": {}
    }
    assert report_module.accumulate_root_totals({"Income": [None, 2]}) == {
        "Income": [0.0, 2.0]
    }
    assert report_module._growth_percentage(None, 3) is None
    assert report_module._growth_percentage(3, None) is None
    assert report_module._growth_percentage(3, 0) is None
    assert report_module._summary_labels([], None) == (
        "Total Income",
        "Total Expense",
        "Net Profit",
    )
    assert report_module._summary_labels([object()], "Yearly") == (
        "Total Income This Year",
        "Total Expense This Year",
        "Profit This Year",
    )


def test_data_compatibility_accessors_and_empty_periods(report_module: Any) -> None:
    data = report_module.pnlcc_data
    assert data.get_report_amounts(
        company="Karam",
        periods=[],
        finance_book=None,
        include_default_fb=False,
        project_filters=None,
        restrict_cost_centers=None,
    ) == ({}, {"Income": [], "Expense": []})
    with patch.object(
        data,
        "get_report_amounts",
        return_value=({"A": {"single_period": 4}}, {"Income": [8], "Expense": [3]}),
    ):
        assert data.get_amounts_by_cost_centre(
            company="Karam",
            from_date="2026-01-01",
            to_date="2026-01-31",
            finance_book=None,
            include_default_fb=False,
            project_filters=None,
            restrict_cost_centers=None,
        ) == {"A": 4}
        assert (
            data.get_total_by_root_type(
                company="Karam",
                from_date="2026-01-01",
                to_date="2026-01-31",
                root_type="Income",
                finance_book=None,
                include_default_fb=False,
                project_filters=None,
                restrict_cost_centers=None,
            )
            == 8
        )
    assert (
        data.get_total_by_root_type(
            company="Karam",
            from_date="2026-01-01",
            to_date="2026-01-31",
            root_type="Asset",
            finance_book=None,
            include_default_fb=False,
            project_filters=None,
            restrict_cost_centers=None,
        )
        == 0.0
    )


def test_get_cost_centres_queries_full_or_active_tree_and_computes_indents(
    report_module: Any,
) -> None:
    data = report_module.pnlcc_data
    rows = [
        {"name": "Root", "parent_cost_center": None, "is_group": 1, "lft": 1, "rgt": 8},
        {
            "name": "Parent",
            "parent_cost_center": "Root",
            "is_group": 1,
            "lft": 2,
            "rgt": 5,
        },
        {
            "name": "Leaf",
            "parent_cost_center": "Parent",
            "is_group": 0,
            "lft": 3,
            "rgt": 4,
        },
        {
            "name": "Other",
            "parent_cost_center": "Root",
            "is_group": 0,
            "lft": 6,
            "rgt": 7,
        },
    ]
    query_type = type(MariaDB.from_(MariaDB.DocType("Cost Center")))
    captured: list[str] = []

    def capture(query: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        captured.append(query.get_sql())
        return [row.copy() for row in rows]

    qb = SimpleNamespace(DocType=MariaDB.DocType, from_=MariaDB.from_)
    with (
        patch.object(data.frappe, "qb", qb),
        patch.object(data, "DocType", MariaDB.DocType),
        patch.object(query_type, "run", capture),
    ):
        full = data.get_cost_centres("Karam")
        active = data.get_cost_centres("Karam", required_cost_centers=["Leaf"])
        empty = data.get_cost_centres("Karam", required_cost_centers=[])

    assert [row["indent"] for row in full] == [0, 1, 2, 1]
    assert [row["indent"] for row in active] == [0, 1, 2, 1]
    assert empty == []
    assert len(captured) == 2
    assert "`company`='Karam'" in captured[0]
    active_sql = captured[1]
    assert "JOIN `tabCost Center` `active_cost_center`" in active_sql
    assert "`tabCost Center`.`company`='Karam'" in active_sql
    assert "`active_cost_center`.`company`='Karam'" in active_sql
    assert "`tabCost Center`.`lft`<=`active_cost_center`.`lft`" in active_sql
    assert "`tabCost Center`.`rgt`>=`active_cost_center`.`rgt`" in active_sql
    assert "`active_cost_center`.`name` IN ('Leaf')" in active_sql
    assert "SELECT DISTINCT" in active_sql
    assert "ORDER BY `tabCost Center`.`lft`" in active_sql


def test_conversion_and_period_row_helpers_cover_all_value_paths(
    report_module: Any,
) -> None:
    data = report_module.pnlcc_data
    assert (
        data._convert_amount(
            base_value=2, account_value=7, use_account_currency=True, currency_info=None
        )
        == 7
    )
    with patch.object(data, "convert_currency", return_value=3.5) as convert:
        assert (
            data._convert_amount(
                base_value=2,
                account_value=7,
                use_account_currency=False,
                currency_info={
                    "presentation_currency": "USD",
                    "company_currency": "INR",
                    "report_date": "2026-02-28",
                },
            )
            == 3.5
        )
    convert.assert_called_once_with(2, "USD", "INR", "2026-02-28")
    assert (
        data._convert_amount(
            base_value=2,
            account_value=7,
            use_account_currency=False,
            currency_info=None,
        )
        == 2
    )
    indexes = {"p1": 0}
    assert data._amount_period("Asset", "p1", indexes) is None
    assert data._amount_period("Income", "unknown", indexes) is None
    assert next(
        data._period_amount_rows(
            [{"root_type": "Income", "cost_center": "A", "period_key": "p1"}], indexes
        )
    )[1:] == ("Income", "A", "p1", 0)


def test_dimension_filter_empty_tree_expansion_and_filter_value_forms(
    report_module: Any,
) -> None:
    data = report_module.pnlcc_data
    gl = MariaDB.DocType("GL Entry")
    query = MariaDB.from_(gl).select(gl.name)
    dimension = SimpleNamespace(fieldname="branch", document_type="Branch")
    with (
        patch.object(data, "get_accounting_dimensions", return_value=[dimension]),
        patch.object(data.frappe, "get_cached_value", return_value=True),
        patch.object(data, "get_dimension_with_children", return_value=[]),
    ):
        assert (
            "WHERE"
            not in data._apply_dimension_filters(
                query, gl, {"branch": ["North"]}
            ).get_sql()
        )
    assert data._get_filter_value(None, "branch") is None
    assert data._get_filter_value(SimpleNamespace(branch="North"), "branch") == "North"
    assert data._get_filter_value({"branch": "North"}, "branch") == "North"
    assert report_module.pnlcc_parsing.normalise_scalar("   ") is None


def test_controller_remaining_delegates_filters_and_empty_views(
    report_module: Any,
) -> None:
    filters = frappe._dict(
        company="Karam",
        from_fiscal_year="2026",
        to_fiscal_year="2026",
        period_start_date=None,
        period_end_date=None,
        filter_based_on="Fiscal Year",
        periodicity="Monthly",
        accumulated_values=1,
    )
    with patch.object(
        report_module, "get_period_list", return_value=["period"]
    ) as periods:
        assert report_module.get_report_periods(filters) == ["period"]
    assert periods.call_args.kwargs == {"accumulated_values": True, "company": "Karam"}
    centres = [{"name": "A"}, {"name": "B"}]
    with patch.object(
        report_module, "get_cost_centres", return_value=centres
    ) as get_centres:
        assert report_module.get_report_cost_centres(
            frappe._dict(company="Karam", show_zero_values=1),
            required_cost_centers=["A"],
            allowed_cost_centers=["A"],
        ) == ([{"name": "A"}], ["A"])
    assert get_centres.call_args.kwargs["required_cost_centers"] is None
    report_module.apply_growth_view([{"p1": 1}], [SimpleNamespace(key="p1")])
    report_module.apply_margin_view([], [], [])
    row = {"p1": None, "p2": 1}
    report_module.apply_margin_view(
        [row], [SimpleNamespace(key="p1"), SimpleNamespace(key="p2")], [1, 0]
    )
    assert row == {"p1": None, "p2": None}
    with patch.object(
        report_module.pnlcc_finance, "finance_book_clause", return_value="clause"
    ) as clause:
        assert (
            report_module._finance_book_clause(
                "gl", company="Karam", finance_book=None, include_default_fb=False
            )
            == "clause"
        )
    clause.assert_called_once()
    with patch.object(
        report_module.pnlcc_data, "get_cost_centres", return_value=centres
    ):
        assert report_module.get_cost_centres("Karam", ["A"]) == centres


def test_empty_footer_and_chart_totals_are_deliberate_noops(report_module: Any) -> None:
    rows: list[dict[str, Any]] = []
    report_module.append_footer_payload(rows, [])
    assert rows == []
    periods = [SimpleNamespace(label="Jan")]
    chart = report_module.get_chart_data(
        totals=([], [], []),
        filters=frappe._dict(selected_view="Report"),
        period_list=periods,
        currency="USD",
    )
    assert chart == {
        "data": {"labels": ["Jan"], "datasets": []},
        "type": "bar",
        "fieldtype": "Currency",
        "options": "currency",
        "currency": "USD",
    }
