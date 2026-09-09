"""Hand-calculated ledgers and display contracts for both GL families."""

import importlib
from datetime import date
from decimal import Decimal
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
def aggregation(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    module = importlib.import_module(
        "karam_finance." + request.param + ".gl_aggregation"
    )
    monkeypatch.setattr(frappe.local, "lang", "en", raising=False)
    monkeypatch.setattr(frappe, "db", MagicMock())
    monkeypatch.setattr(
        frappe, "get_system_settings", MagicMock(return_value="Banker's Rounding")
    )
    frappe.db.get_single_value.return_value = 0
    monkeypatch.setattr(module, "get_currency_precision", MagicMock(return_value=2))
    return module


def _row(amount: str = "10.25", **values: Any) -> Any:
    return (
        frappe._dict(
            posting_date=date(2026, 1, 15),
            account="A",
            account_currency="USD",
            party_type="Customer",
            party="P",
            voucher_type="Journal Entry",
            voucher_no="V",
            gl_entry="G",
            is_opening="No",
            debit=float(amount),
            credit=0.0,
            debit_in_account_currency=float(amount),
            credit_in_account_currency=0.0,
            debit_in_company_currency=float(amount),
            credit_in_company_currency=0.0,
            debit_in_transaction_currency=float(amount),
            credit_in_transaction_currency=0.0,
            transaction_currency="USD",
        )
        | values
    )


def _filters(mode: str, **values: Any) -> dict[str, Any]:
    return {
        "company": "C",
        "categorize_by": mode,
        "from_date": "2026-01-01",
        "to_date": "2026-01-31",
        "_bill_no_joined": True,
    } | values


@pytest.mark.parametrize(
    "mode",
    [
        "Flat Chronological",
        "Categorise by Account",
        "Group by Account w/ Opening",
        "Categorise by Party",
        "Categorise by Voucher",
        "Categorise by Voucher (Consolidated)",
        "",
    ],
)
def test_layout_preserves_independent_opening_movement_and_closing(
    aggregation: ModuleType, mode: str
) -> None:
    ledger = [
        frappe._dict(_row("100.125", posting_date=date(2025, 12, 31))),
        frappe._dict(_row("10.25", posting_date=date(2026, 1, 1))),
        frappe._dict(
            _row(
                "0",
                posting_date=date(2026, 1, 31),
                credit=3.125,
                credit_in_account_currency=3.125,
                credit_in_company_currency=3.125,
            )
        ),
        frappe._dict(_row("999", posting_date=date(2026, 2, 1))),
    ]
    data = aggregation.get_data_with_opening_closing(_filters(mode), [], ledger)
    opening, total, closing = data[0], data[-2], data[-1]
    assert (opening["debit"], opening["credit"]) == (Decimal("100.125"), 0)
    assert (total["debit"], total["credit"]) == (Decimal("10.25"), Decimal("3.125"))
    assert closing["debit"] - closing["credit"] == Decimal("107.25")
    assert sum(
        row["debit"] - row["credit"] for row in data if row.get("row_type") == "entry"
    ) == Decimal("7.125")


@pytest.mark.parametrize(
    "mode", ["Flat Chronological", "Categorise by Voucher (Consolidated)"]
)
def test_dimensions_and_transaction_amounts_survive_consolidation(
    aggregation: ModuleType, mode: str
) -> None:
    ledger = [
        frappe._dict(
            _row("0.1", project="P", cost_center="C", region="R", against_voucher="I1")
        ),
        frappe._dict(
            _row("0.2", project="P", cost_center="C", region="R", against_voucher="I2")
        ),
    ]
    data = aggregation.get_data_with_opening_closing(
        _filters(mode, include_dimensions=1, add_values_in_transaction_currency=1),
        ["region"],
        ledger,
    )
    entries = [row for row in data if row.get("row_type") == "entry"]
    assert float(
        sum(row["debit_in_transaction_currency"] for row in entries)
    ) == pytest.approx(0.3)
    assert all(row["region"] == "R" for row in entries)
    if "Consolidated" in mode:
        assert len(entries) == 1
        assert entries[0]["against_voucher"] == "I1, I2"


@pytest.mark.parametrize(
    "field",
    [
        "account_currency",
        "transaction_currency",
        "party",
        "party_type",
        "voucher_type",
        "voucher_no",
        "account",
        "posting_date",
        "creation",
        "project",
        "cost_center",
        "region",
    ],
)
def test_consolidated_identity_preserves_each_selected_dimension(
    aggregation: ModuleType, field: str
) -> None:
    original = frappe._dict(_row())
    changed = frappe._dict(original | {field: "Other"})
    key = aggregation._consolidated_key(
        original, 1, 1, accounting_dimensions=["region"]
    )
    assert (
        aggregation._consolidated_key(changed, 1, 1, accounting_dimensions=["region"])
        != key
    )
    raw = frappe._dict(original | {"party": " P "})
    assert (
        aggregation._consolidated_key(raw, 1, 1, accounting_dimensions=["region"])
        == key
    )


@pytest.mark.parametrize("names", [None, {"A", "B"}])
def test_account_types_are_bounded_to_selected_company(
    aggregation: ModuleType, monkeypatch: pytest.MonkeyPatch, names: set[str] | None
) -> None:
    lookup = MagicMock(return_value=[("A", "Receivable")])
    monkeypatch.setattr(frappe, "get_all", lookup)
    assert aggregation._get_account_type_map("C", names) == {"A": "Receivable"}
    assert lookup.call_args.kwargs["filters"] == (
        {"company": "C", "name": ["in", ["A", "B"]]} if names else {"company": "C"}
    )
    assert lookup.call_args.kwargs["limit_page_length"] == (2 if names else 100_000)


def test_party_net_values_net_each_currency_layer(
    aggregation: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        frappe, "get_all", MagicMock(return_value=[("A", "Receivable")])
    )
    row = frappe._dict(
        _row(
            "10",
            credit=4.0,
            credit_in_account_currency=12.0,
            credit_in_company_currency=3.0,
        )
    )
    data = aggregation.get_data_with_opening_closing(
        _filters("Flat Chronological", show_net_values_in_party_account=1), [], [row]
    )
    total = data[-2]
    assert (total["debit"], total["credit"]) == (10, 4)
    # Account-level netting is applied when rows are consolidated into that account.
    aggregation._apply_net_values(row)
    assert (row.debit, row.credit) == (6, 0)
    assert (row.debit_in_account_currency, row.credit_in_account_currency) == (0, 2)
    assert (row.debit_in_company_currency, row.credit_in_company_currency) == (7, 0)


@pytest.mark.parametrize(
    "opening,expected",
    [
        ({}, False),
        ({"debit": None}, False),
        ({"debit": Decimal("0.001")}, False),
        ({"credit": Decimal("0.01")}, True),
        ({"debit_in_account_currency": 1}, True),
    ],
)
def test_opening_visibility_uses_display_precision(
    aggregation: ModuleType, opening: dict[str, Any], expected: bool
) -> None:
    assert aggregation._has_displayed_opening_balance(opening, 2) is expected


@pytest.mark.parametrize(
    "mode,expected_header",
    [("Group by Account w/ Opening", True), ("Categorise by Account", False)],
)
def test_opening_only_group_visibility(
    aggregation: ModuleType, mode: str, expected_header: bool
) -> None:
    data = aggregation.get_data_with_opening_closing(
        _filters(mode), [], [frappe._dict(_row("10", posting_date=date(2025, 12, 31)))]
    )
    headers = [row for row in data if row.get("row_type") == "account_header"]
    assert bool(headers) is expected_header
    if headers:
        assert headers[0]["account"] == "A"
        assert headers[0]["debit"] == ""


@pytest.mark.parametrize(
    "rows,expected_count",
    [
        ([], 0),
        ([{"account": "A"}], 1),
        ([{"row_type": "report_total"}], 2),
        ([{"row_type": "separator"}, {"row_type": "report_total"}], 2),
        ([{"is_separator": 1}, {"row_type": "closing"}], 2),
        ([{"account": "A"}, {"account": "'Total'"}, {"account": "'Closing'"}], 4),
    ],
)
def test_footer_separator_is_inserted_once(
    aggregation: ModuleType, rows: list[dict[str, Any]], expected_count: int
) -> None:
    result = aggregation._insert_footer_separator(rows)
    assert len(result) == expected_count
    assert aggregation._insert_footer_separator(result) == result
    assert [row for row in result if not row.get("is_separator")] == [
        row for row in rows if not row.get("is_separator")
    ]


@pytest.mark.parametrize(
    "values,expected",
    [
        ([{"against_voucher_type": "Sales Invoice", "against_voucher": "P"}], [""]),
        (
            [
                {"against_voucher_type": "Purchase Invoice", "against_voucher": "P"},
                {
                    "against_voucher_type": "Purchase Invoice",
                    "against_voucher": "Missing",
                },
            ],
            ["Bill", ""],
        ),
    ],
)
def test_bill_lookup_keeps_missing_values_blank(
    aggregation: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    *,
    values: list[dict[str, Any]],
    expected: list[str],
) -> None:
    monkeypatch.setattr(frappe, "qb", MariaDB)
    execute = MagicMock(return_value=[frappe._dict(name="P", bill_no="Bill")])
    monkeypatch.setattr(QueryBuilder, "run", execute)
    rows = [frappe._dict(row) for row in values]
    aggregation._set_bill_no(rows)
    assert [row.bill_no for row in rows] == expected
    assert execute.call_count == int("Bill" in expected)


def test_consolidation_keeps_currency_amounts_in_their_denominations(
    aggregation: ModuleType,
) -> None:
    rows = [
        frappe._dict(_row("10", transaction_currency="USD")),
        frappe._dict(_row("20", transaction_currency="EUR")),
    ]
    result = aggregation.get_data_with_opening_closing(
        _filters(
            "Categorise by Voucher (Consolidated)", add_values_in_transaction_currency=1
        ),
        [],
        rows,
    )
    entries = [row for row in result if row.get("row_type") == "entry"]
    assert {
        (row["transaction_currency"], row["debit_in_transaction_currency"])
        for row in entries
    } == {("USD", 10), ("EUR", 20)}
    assert result[-2]["debit"] == 30


def test_compatibility_dispatch_consolidates_rows_and_totals(
    aggregation: ModuleType,
) -> None:
    row = frappe._dict(_row("10"))
    for key in (
        "debit",
        "credit",
        "debit_in_account_currency",
        "credit_in_account_currency",
        "debit_in_company_currency",
        "credit_in_company_currency",
    ):
        row[key] = int(row[key])
    totals = aggregation._get_totals_dict()
    state = aggregation._build_aggregation_state(
        _filters("Categorise by Voucher (Consolidated)"),
        [],
        [row],
        gle_map={},
        totals=totals,
    )
    aggregation._process_report_entry(row, "A", state)
    aggregation._append_consolidated_entries(state)
    assert state.entries == [row]
    assert totals.total.debit == 10
    assert totals.closing.debit == 10


def test_absent_dimensions_and_amount_fields_remain_absent(
    aggregation: ModuleType,
) -> None:
    row = frappe._dict(account="A", region=None)
    aggregation._translate_dimension_values(row, ["region"], {})
    assert row == {"account": "A", "region": None}
    if "reporting_currency" in aggregation.__name__:
        aggregation._prepare_decimal_entries([row])
        assert "debit" not in row


@pytest.mark.parametrize(
    "aggregation",
    ["reporting_currency.report.general_ledger_(reporting_currency)"],
    indirect=True,
)
def test_reporting_manual_and_doe_rows_remain_individual(
    aggregation: ModuleType,
) -> None:
    rows = [
        frappe._dict(_row("10", gl_entry="M1", manual_entry=1)),
        frappe._dict(_row("20", gl_entry="M2", manual_entry=1)),
        frappe._dict(_row("30", gl_entry="D1", reporting_doe=1)),
    ]
    data = aggregation.get_data_with_opening_closing(
        _filters("Categorise by Voucher (Consolidated)"), [], rows
    )
    assert {row["gl_entry"] for row in data if row.get("row_type") == "entry"} == {
        "M1",
        "M2",
        "D1",
    }
    assert data[-2]["debit"] == 60


@pytest.mark.parametrize(
    "aggregation",
    ["reporting_currency.report.general_ledger_(reporting_currency)"],
    indirect=True,
)
def test_reporting_aggregation_keeps_significant_digits(
    aggregation: ModuleType,
) -> None:
    rows = [
        frappe._dict(_row("0", debit="9007199254740992.0001")),
        frappe._dict(_row("0", debit="0.0002")),
    ]
    data = aggregation.get_data_with_opening_closing(
        _filters("Flat Chronological"), [], rows
    )
    assert data[-2]["debit"] == Decimal("9007199254740992.0003")
    assert data[-1]["debit"] == Decimal("9007199254740992.0003")
