"""Report orchestration and optional display contracts with database boundaries stubbed."""

import importlib
from datetime import date
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import frappe
import pytest
from frappe.query_builder.builder import MariaDB
from pypika.queries import QueryBuilder


@pytest.fixture(
    params=[("karam_general", "karam"), ("reporting_currency", "reporting_currency")]
)
def report(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    family, suffix = request.param
    package = f"karam_finance.{family}.report.general_ledger_({suffix})"
    module = importlib.import_module(package + f".general_ledger_({suffix})")
    columns = importlib.import_module(package + ".gl_columns")
    monkeypatch.setattr(frappe, "qb", MariaDB)
    monkeypatch.setattr(QueryBuilder, "run", MagicMock(return_value=[]))
    monkeypatch.setattr(frappe, "db", MagicMock())
    monkeypatch.setattr(frappe.db, "get_single_value", _setting)
    monkeypatch.setattr(
        frappe, "get_system_settings", MagicMock(return_value="Banker's Rounding")
    )
    monkeypatch.setattr(frappe.local, "lang", "en", raising=False)
    monkeypatch.setattr(frappe, "throw", _throw)
    monkeypatch.setattr(
        frappe, "get_cached_doc", MagicMock(return_value=frappe._dict())
    )
    monkeypatch.setattr(frappe, "get_cached_value", MagicMock(return_value="USD"))
    monkeypatch.setattr(frappe, "get_all", _lookup)
    monkeypatch.setattr(
        frappe,
        "get_meta",
        MagicMock(return_value=MagicMock(has_field=MagicMock(return_value=True))),
    )
    monkeypatch.setattr(columns, "get_company_currency", MagicMock(return_value="USD"))
    monkeypatch.setattr(columns, "get_default_company", MagicMock(return_value="C"))
    monkeypatch.setattr(
        columns,
        "get_accounting_dimensions",
        MagicMock(return_value=[frappe._dict(fieldname="region", label="Region")]),
    )
    monkeypatch.setattr(module, "get_accounting_dimensions", _dimensions)
    monkeypatch.setattr(
        module._gl_filters, "get_account_currency", MagicMock(return_value="USD")
    )
    monkeypatch.setattr(module, "get_gl_entries", MagicMock(return_value=[]))
    monkeypatch.setattr(
        module, "get_flat_account_currency_openings", MagicMock(return_value={})
    )
    return module


def _throw(message: str, *_args: Any, **_kwargs: Any) -> None:
    raise frappe.ValidationError(message)


def _setting(_doctype: str, field: str) -> Any:
    return {
        "reporting_currency": "USD",
        "supp_master_name": "Supplier Name",
        "cust_master_name": "Customer Name",
    }.get(field)


def _lookup(doctype: str, **_kwargs: Any) -> list[Any]:
    return {
        "Account": [frappe._dict(name="A", is_group=1)],
        "Party Type": ["Customer"],
        "Customer": ["P"],
    }.get(doctype, [])


def _dimensions(*, as_list: bool = True) -> list[Any]:
    return ["region"] if as_list else [frappe._dict(fieldname="region", label="Region")]


def _filters(**values: Any) -> Any:
    return frappe._dict(
        {
            "company": "C",
            "from_date": "2026-01-01",
            "to_date": "2026-01-31",
            "categorize_by": "Flat Chronological",
        }
        | values
    )


def _entry() -> Any:
    return frappe._dict(
        posting_date=date(2026, 1, 15),
        gl_entry="G",
        account="A",
        account_currency="USD",
        transaction_currency="USD",
        debit=12.5,
        credit=2.5,
        debit_in_account_currency=25.0,
        credit_in_account_currency=5.0,
        debit_in_company_currency=12.5,
        credit_in_company_currency=2.5,
        debit_in_transaction_currency=25.0,
        credit_in_transaction_currency=5.0,
        voucher_type="Journal Entry",
        voucher_no="V",
        is_opening="No",
    )


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"show_exchange_details": 0},
        {
            "include_dimensions": 1,
            "show_remarks": 1,
            "show_source_currency_columns": 1,
            "show_cancelled_entries": 1,
            "show_exchange_details": 1,
            "add_values_in_transaction_currency": 1,
        },
        {
            "categorize_by": "Categorise by Account",
            "account": '["A"]',
            "party": '["P"]',
            "party_type": "Customer",
        },
    ],
)
def test_execute_uses_real_validation_columns_aggregation_and_balances(
    report: ModuleType, monkeypatch: pytest.MonkeyPatch, options: dict[str, Any]
) -> None:
    monkeypatch.setattr(report, "get_gl_entries", MagicMock(return_value=[_entry()]))
    columns, rows = report.execute(_filters(**options))
    detail = next(row for row in rows if row.get("gl_entry") == "G")
    assert detail["debit"] == 12.5
    assert detail["credit"] == 2.5
    assert detail["balance"] == 10
    fields = {column["fieldname"]: column for column in columns}
    amount_field = (
        "debit"
        if "reporting_currency" in report.__name__
        else "debit_in_company_currency"
    )
    assert fields[amount_field]["fieldtype"] == "Currency"
    assert ("remarks" in fields) == bool(options.get("show_remarks"))
    assert ("region" in fields) == bool(options.get("include_dimensions"))
    assert ("debit_in_transaction_currency" in fields) == bool(
        options.get("add_values_in_transaction_currency")
    )
    if "reporting_currency" in report.__name__:
        assert ("debit_in_account_currency" in fields) == bool(
            options.get("show_source_currency_columns")
        )
        assert "letter" not in fields
        _assert_reporting_exchange_columns(fields)
        assert rows[0]["_report_context_details"]["currency"] == "USD"
    assert report.get_gl_entries.call_args.kwargs == {"enrich_opening_entries": False}


def _assert_reporting_exchange_columns(fields: dict[str, Any]) -> None:
    rate_columns = {
        "currency_exchange": "Currency Exchange",
        "exchange_rate_date": "Rate Date",
        "source_exchange_rate": "Source Exchange Rate",
        "exchange_rate_application": "Rate Application",
    }
    for field, label in rate_columns.items():
        assert fields[field]["label"] == label
    assert fields["source_exchange_rate"]["precision"] == 9
    assert "exchange_rate" not in fields
    assert "conversion_basis" not in fields
    assert list(fields)[:8] == [
        "gl_entry",
        "source_gl_entry",
        "posting_date",
        "currency_exchange",
        "exchange_rate_date",
        "source_exchange_rate",
        "exchange_rate_application",
        "account",
    ]


def test_execute_empty_input_and_empty_ledger(report: ModuleType) -> None:
    assert report.execute() == ([], [])
    columns, rows = report.execute(_filters())
    assert columns
    assert [
        row["balance"] for row in rows if row.get("row_type") == "report_total"
    ] == [0]


def test_print_currency_requires_an_account_before_ledger_lookup(
    report: ModuleType,
) -> None:
    with pytest.raises(frappe.ValidationError, match="Select an account"):
        report.execute(_filters(print_in_account_currency=1))
    report.get_gl_entries.assert_not_called()


@pytest.mark.parametrize(
    "accounts,expected",
    [(None, {}), ("[]", {}), (["A"], {"A": frappe._dict(name="A", is_group=1)})],
)
def test_account_details_handle_empty_json_and_list_selection(
    report: ModuleType, accounts: Any, expected: dict[str, Any]
) -> None:
    assert report._get_account_details(_filters(account=accounts)) == expected


def test_optional_party_name_column_when_master_names_are_series(
    report: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def setting(doctype: str, field: str) -> Any:
        return (
            "Naming Series" if field == "supp_master_name" else _setting(doctype, field)
        )

    monkeypatch.setattr(frappe.db, "get_single_value", setting)
    columns, _rows = report.execute(_filters())
    assert "party_name" in {column["fieldname"] for column in columns}
    assert report.get_gl_entries.call_args.args[0]["_needs_party_name"] is True
