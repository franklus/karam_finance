"""Currency provenance and stored-ledger context display contracts."""

import importlib
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import frappe
import pytest


@pytest.fixture(
    params=[
        "karam_general.report.general_ledger_(karam)",
        "reporting_currency.report.general_ledger_(reporting_currency)",
    ]
)
def currency(request: pytest.FixtureRequest) -> ModuleType:
    return importlib.import_module("karam_finance." + request.param + ".gl_currency")


@pytest.mark.parametrize(
    "source",
    [
        {"account_currency": "EUR", "debit_in_account_currency": 1},
        {"account_currency": None, "credit_in_account_currency": 1},
        {"_mixed_account_currency": 1, "debit_in_account_currency": 1},
    ],
)
def test_mixed_currency_provenance_stays_mixed(
    currency: ModuleType, source: dict[str, Any]
) -> None:
    target = {"account_currency": "USD"}
    currency._track_account_currency(target, source)
    currency._track_account_currency(
        target, {"account_currency": "USD", "debit_in_account_currency": 1}
    )
    assert target == {"account_currency": "", "_mixed_account_currency": 1}


@pytest.mark.parametrize(
    "included",
    [
        [],
        ["opening"],
        ["report_total"],
        ["closing"],
        ["opening", "report_total", "closing"],
    ],
)
def test_flat_summary_balances_are_independent_and_optional(
    currency: ModuleType, included: list[str]
) -> None:
    rows: list[dict[str, Any]] = [{"row_type": kind} for kind in included]
    rows.append(
        {
            "posting_date": "2026-01-01",
            "account": "A",
            "account_currency": "USD",
            "debit_in_account_currency": 3,
            "credit_in_account_currency": 8,
        }
    )
    currency._apply_flat_account_currency_summaries(rows, {("A", "USD"): 20})
    expected = {
        "opening": (20, 0, 20),
        "report_total": (3, 8, -5),
        "closing": (15, 0, 15),
    }
    if "karam_general" in currency.__name__:
        expected["closing"] = (23, 8, 15)
    for row in rows[:-1]:
        assert row["account_currency"] == "USD"
        assert (
            row["debit_in_account_currency"],
            row["credit_in_account_currency"],
            row["balance_in_account_currency"],
        ) == expected[row["row_type"]]
    currency._attach_flat_account_currency_openings(rows, {("A", "USD"): 20})
    assert currency._flat_account_balance(rows[-1], {}) == 15


@pytest.mark.parametrize(
    "openings,rows",
    [
        ({("A", None): 3}, []),
        ({("A", "USD"): 3, ("B", "EUR"): -3}, []),
        ({}, [{"posting_date": "2026-01-01", "debit_in_account_currency": 1}]),
    ],
)
def test_unresolved_or_mixed_flat_currencies_cannot_be_relabelled(
    currency: ModuleType, openings: dict[Any, Any], rows: list[dict[str, Any]]
) -> None:
    summary: dict[str, Any] = {"row_type": "closing"}
    currency._apply_flat_account_currency_summaries([summary, *rows], openings)
    assert summary["_mixed_account_currency"] == 1
    assert currency._prepare_currency_row(summary, {"USD"}) is True
    assert summary["account_currency"] == ""
    assert summary["debit_in_account_currency"] == ""


def test_unknown_account_cannot_receive_a_running_balance(currency: ModuleType) -> None:
    assert currency._flat_account_balance({"account_currency": "USD"}, {}) == ""


@pytest.fixture
def context(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = importlib.import_module(
        "karam_finance.reporting_currency.report.general_ledger_(reporting_currency).gl_context"
    )
    monkeypatch.setattr(frappe, "db", MagicMock())
    frappe.db.get_single_value.return_value = None
    monkeypatch.setattr(frappe.local, "lang", "en", raising=False)
    monkeypatch.setattr(
        module, "format_datetime", MagicMock(return_value="8 September 2026")
    )
    return module


@pytest.mark.parametrize(
    "balance,label", [(0, None), (12.5, "12.50 Dr"), (-12.5, "12.50 Cr")]
)
def test_complete_ledger_context_uses_displayed_closing_balance(
    context: ModuleType, balance: float, label: str | None
) -> None:
    rows: list[dict[str, Any]] = [
        {"row_type": "opening"},
        {
            "row_type": "closing",
            "balance": 999,
            "_display_amounts": {"balance": balance},
        },
    ]
    context.attach_report_context(
        rows, frappe._dict(presentation_currency="USD", entry_type="All")
    )
    details = rows[0]["_report_context_details"]
    assert details["currency"] == "USD"
    assert details["sync"] == "Not recorded"
    assert details.get("closing") == label
    assert "refresh does not sync" in rows[0]["_report_context"]
    assert ("closing difference" in rows[0]["_report_context"]) == bool(balance)


def test_context_explains_period_slice_exclusions_and_recorded_sync(
    context: ModuleType,
) -> None:
    frappe.db.get_single_value.return_value = "2026-09-08 12:00:00"
    rows: list[dict[str, Any]] = [
        {"row_type": "opening"},
        {"row_type": "closing", "balance": 10},
    ]
    context.attach_report_context(
        rows,
        frappe._dict(
            presentation_currency="USD",
            entry_type="Manual",
            party="P",
            disable_opening_balance_calculation=1,
            exclude_manual_entries=1,
            exclude_reporting_doe=1,
        ),
    )
    text = rows[0]["_report_context"]
    for expected in (
        "Period movements only",
        "selected entry type: Manual",
        "Reporting DOE entries are excluded",
        "Manual entries are excluded",
        "Filtered RC slice",
    ):
        assert expected in text
    assert rows[-1]["account"] == "Period net movement"
    assert rows[0]["_report_context_details"]["sync"] == "8 September 2026"
    assert "closing" not in rows[0]["_report_context_details"]


def test_empty_context_is_a_noop(context: ModuleType) -> None:
    context.attach_report_context([], frappe._dict())
    frappe.db.get_single_value.assert_not_called()
