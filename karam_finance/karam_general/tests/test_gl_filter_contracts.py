"""Validation contracts shared only where both GL reports support them."""

import importlib
import sqlite3
from datetime import date
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import frappe
import pytest
from frappe.query_builder.builder import MariaDB
from pypika.queries import QueryBuilder


@pytest.fixture(
    params=[
        "karam_general.report.general_ledger_(karam)",
        "reporting_currency.report.general_ledger_(reporting_currency)",
    ]
)
def validation(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    module = importlib.import_module("karam_finance." + request.param + ".gl_filters")
    monkeypatch.setattr(frappe.local, "lang", "en", raising=False)
    monkeypatch.setattr(frappe, "throw", _throw)
    monkeypatch.setattr(
        frappe, "get_all", MagicMock(return_value=["Customer", "Supplier"])
    )
    monkeypatch.setattr(frappe, "db", MagicMock())
    return module


def _throw(message: str, *_args: Any, **_kwargs: Any) -> None:
    raise frappe.ValidationError(message)


def _filters(**values: Any) -> Any:
    return (
        frappe._dict(company="Company", from_date="2026-01-01", to_date="2026-01-31")
        | values
    )


@pytest.mark.parametrize(
    "values,message",
    [
        ({"company": None}, "Company"),
        ({"from_date": None}, "From Date"),
        ({"to_date": None}, "To Date"),
        ({"from_date": None, "to_date": None}, "From Date"),
        ({"from_date": "2026-02-01"}, "before To Date"),
        ({"from_date": "invalid"}, "date"),
    ],
)
def test_invalid_dates_fail_with_validation_error(
    validation: ModuleType, values: dict[str, Any], message: str
) -> None:
    with pytest.raises(frappe.ValidationError, match=message):
        validation.validate_filters(frappe._dict(_filters(**values)), {})


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, []),
        (["A"], ["A"]),
        (("A",), ["A"]),
        ({"A"}, ["A"]),
        ('["A", "B"]', ["A", "B"]),
        ('"A"', ["A"]),
    ],
)
def test_multiselect_keeps_selected_values(
    validation: ModuleType, value: Any, expected: list[str]
) -> None:
    assert validation._as_list(value) == expected


def test_filter_normalisation_preserves_scopes(validation: ModuleType) -> None:
    filters = frappe._dict(
        _filters(
            account='["Group"]',
            project='["P"]',
            cost_center='["C"]',
            group_by="Categorize by Account",
            show_amount_in_company_currency=1,
        )
    )
    validation.validate_filters(filters, {"Group": frappe._dict(is_group=1)})
    assert filters.account == ["Group"]
    assert filters.project == ["P"]
    assert filters.cost_center == ["C"]
    assert filters.categorize_by == "Categorise by Account"
    assert "show_amount_in_company_currency" not in filters


def test_unknown_account_is_rejected(validation: ModuleType) -> None:
    with pytest.raises(frappe.ValidationError, match="Account Missing"):
        validation.validate_filters(frappe._dict(_filters(account=["Missing"])), {})


@pytest.mark.parametrize(
    "values,existing,error",
    [
        ({}, [], False),
        ({"party": ["A"]}, [], False),
        ({"party_type": "Customer", "party": ["A", "B"]}, ["A", "B"], False),
        ({"party_type": "Supplier", "party": ["A", "B"]}, ["A"], True),
    ],
)
def test_party_existence_checks_complete_selection(
    validation: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    values: dict[str, Any],
    existing: list[str],
    error: bool,
) -> None:
    lookup = MagicMock(return_value=existing)
    monkeypatch.setattr(frappe, "get_all", lookup)
    if error:
        with pytest.raises(frappe.ValidationError, match="Invalid Supplier: B"):
            validation.validate_party(frappe._dict(values))
    else:
        validation.validate_party(frappe._dict(values))
    if values.get("party_type"):
        assert lookup.call_args.kwargs == {
            "filters": {"name": ["in", ["A", "B"]]},
            "pluck": "name",
            "limit_page_length": 2,
        }
    else:
        lookup.assert_not_called()


@pytest.mark.parametrize(
    "case",
    [
        ({}, None, None, None),
        ({"party": ["A", "B"]}, None, None, None),
        ({"account": ["A"]}, "EUR", None, "EUR"),
        ({"account": ["A", "B"]}, None, None, "USD"),
        ({"party": ["A"], "party_type": "Customer"}, None, "GBP", "GBP"),
        ({"party": ["A"], "party_type": "Supplier"}, None, None, "CAD"),
        ({"party": ["A"], "party_type": "Employee"}, None, None, "USD"),
        ({"party": ["A"]}, None, None, "USD"),
        ({"account": ["A"], "presentation_currency": "JPY"}, "EUR", None, "EUR"),
    ],
)
def test_account_currency_fallback_and_explicit_presentation(
    validation: ModuleType, monkeypatch: pytest.MonkeyPatch, case: tuple[Any, ...]
) -> None:
    options, account_currency, ledger_currency, expected = case
    filters = frappe._dict(_filters(**options))
    monkeypatch.setattr(
        validation,
        "_selected_accounts_currency",
        MagicMock(return_value=account_currency),
    )
    frappe.db.get_value.return_value = ledger_currency

    def cached_currency(dt: str, *_args: Any) -> str:
        return "USD" if dt == "Company" else "CAD"

    monkeypatch.setattr(frappe, "get_cached_value", cached_currency)
    assert validation.set_account_currency(filters) is filters
    assert filters.get("account_currency") == expected
    if expected and expected != "USD":
        assert filters.presentation_currency == options.get(
            "presentation_currency", expected
        )
    if ledger_currency:
        expected_dt = (
            "Reporting Currency GLE"
            if "reporting_currency" in validation.__name__
            else "GL Entry"
        )
        assert frappe.db.get_value.call_args.args[0] == expected_dt
        assert frappe.db.get_value.call_args.args[1]["company"] == "Company"


@pytest.mark.parametrize(
    "currencies,expected",
    [(["USD"], "USD"), (["EUR", "EUR"], "EUR"), (["USD", "EUR"], None)],
)
def test_selected_accounts_require_one_common_currency(
    validation: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    currencies: list[str],
    expected: str | None,
) -> None:
    def account_currency(account: str) -> str:
        return currencies[int(account)]

    monkeypatch.setattr(validation, "get_account_currency", account_currency)
    assert (
        validation._selected_accounts_currency([str(i) for i in range(len(currencies))])
        == expected
    )


@pytest.mark.parametrize(
    "accounts,expected",
    [([], []), (" A, B,,C ", ["A", "B", "C"]), (["A", "B"], ["A", "B"])],
)
def test_account_list_parsing_preserves_names(
    validation: ModuleType, accounts: Any, expected: list[str]
) -> None:
    assert validation._parse_account_names(accounts) == expected


def test_reporting_options_default_off_and_retain_explicit_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module(
        "karam_finance.reporting_currency.report.general_ledger_(reporting_currency).gl_filters"
    )
    monkeypatch.setattr(frappe.local, "lang", "en", raising=False)
    for value in (None, 0, 1):
        filters = frappe._dict(
            {}
            if value is None
            else {"include_default_book_entries": value, "include_dimensions": value}
        )
        module._validate_options(filters)
        assert filters.categorize_by == "Flat Chronological"
        assert filters.include_default_book_entries == (value or 0)
        assert filters.include_dimensions == (value or 0)
        assert filters.manual_entry == 0


@pytest.mark.parametrize(
    "case",
    [
        ({"categorize_by": "Unsafe"}, "Invalid Categorise"),
        ({"party": ["A"]}, "Select Party Type"),
        ({"party_type": "Unsafe"}, "Invalid Party Type"),
        ({"manual_entry": 1, "reporting_doe": 1}, "Select one Entry Type"),
        ({"manual_entry": 1, "entry_type": "Synced GL"}, "Conflicting Entry Type"),
        ({"entry_type": "Unsafe"}, "Invalid Entry Type"),
    ],
)
def test_reporting_invalid_options_rejected(
    monkeypatch: pytest.MonkeyPatch, case: tuple[dict[str, Any], str]
) -> None:
    module = importlib.import_module(
        "karam_finance.reporting_currency.report.general_ledger_(reporting_currency).gl_filters"
    )
    monkeypatch.setattr(frappe.local, "lang", "en", raising=False)
    monkeypatch.setattr(frappe, "throw", _throw)
    monkeypatch.setattr(frappe, "get_all", MagicMock(return_value=["Customer"]))
    values, message = case
    with pytest.raises(frappe.ValidationError, match=message):
        module.validate_filters(frappe._dict(_filters(**values)), {})


@pytest.mark.parametrize(
    "options,expected",
    [
        ({}, "All"),
        ({"manual_entry": 1}, "Manual"),
        ({"reporting_doe": 1}, "Reporting DOE"),
        ({"entry_type": "Synced GL", "party_type": "Customer"}, "Synced GL"),
    ],
)
def test_reporting_entry_types_and_date_normalisation(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, Any], expected: str
) -> None:
    module = importlib.import_module(
        "karam_finance.reporting_currency.report.general_ledger_(reporting_currency).gl_filters"
    )
    monkeypatch.setattr(frappe, "get_all", MagicMock(return_value=["Customer"]))
    filters = frappe._dict(_filters(**options))
    module.validate_filters(filters, {})
    assert filters.entry_type == expected
    assert filters.from_date == date(2026, 1, 1)


@pytest.mark.parametrize(
    "options,message",
    [
        (
            {"account": ["Leaf"], "categorize_by": "Categorise by Account"},
            "Child Account",
        ),
        ({"voucher_no": "V", "categorize_by": "Categorise by Voucher"}, "Voucher No"),
    ],
)
def test_karam_grouping_restrictions(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, Any], message: str
) -> None:
    module = importlib.import_module(
        "karam_finance.karam_general.report.general_ledger_(karam).gl_filters"
    )
    monkeypatch.setattr(frappe.local, "lang", "en", raising=False)
    monkeypatch.setattr(frappe, "throw", _throw)
    with pytest.raises(frappe.ValidationError, match=message):
        module.validate_filters(
            frappe._dict(_filters(**options)), {"Leaf": frappe._dict(is_group=0)}
        )


def test_date_type_error_becomes_validation_error(validation: ModuleType) -> None:
    with pytest.raises(frappe.ValidationError, match="Invalid date for from_date"):
        validation.validate_filters(frappe._dict(_filters(from_date=[2026, 1, 1])), {})


@pytest.mark.parametrize(
    "selected,expected",
    [
        ([], None),
        (["Missing"], None),
        (["Leaf"], ["Leaf"]),
        (["Group", "Other"], ["Group", "Leaf", "Other"]),
    ],
)
def test_account_tree_expansion_keeps_leaf_scope(
    validation: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    selected: list[str],
    expected: list[str] | None,
) -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        'CREATE TABLE "tabAccount" (name TEXT, lft INT, rgt INT, is_group INT)'
    )
    connection.executemany(
        'INSERT INTO "tabAccount" VALUES (?,?,?,?)',
        [
            ("Group", 1, 4, 1),
            ("Leaf", 2, 3, 0),
            ("Other", 5, 6, 0),
            ("Outside", 7, 8, 0),
        ],
    )

    def run(query: QueryBuilder, **options: Any) -> list[Any]:
        cursor = connection.execute(query.get_sql())
        rows = cursor.fetchall()
        if options.get("pluck"):
            return [row[0] for row in rows]
        keys = [column[0] for column in cursor.description]
        return [frappe._dict(zip(keys, row, strict=True)) for row in rows]

    monkeypatch.setattr(frappe, "qb", MariaDB)
    monkeypatch.setattr(QueryBuilder, "run", run)
    try:
        actual = validation.get_accounts_with_children(selected)
        assert (sorted(actual) if actual else actual) == expected
    finally:
        connection.close()
