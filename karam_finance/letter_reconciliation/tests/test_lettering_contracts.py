"""Pure contracts for Letter Reconciliation controller helpers."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import frappe
import pytest
from frappe.query_builder.builder import MariaDB
from karam_finance.letter_reconciliation.doctype.letter_reconciliation import (
    letter_reconciliation as lr,
)


def _raise_validation(message: str, *_args: object, **_kwargs: object) -> None:
    raise frappe.ValidationError(message)


@pytest.fixture(autouse=True)
def database(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    db = MagicMock()
    monkeypatch.setattr(frappe, "db", db)
    monkeypatch.setattr(frappe, "log_error", MagicMock())
    return db


def test_increment_and_posting_year_boundaries() -> None:
    assert [
        lr.increment_string(value) for value in ("", "A", "Z", "AZ", "ZZ", "ZZZZZZ")
    ] == [
        "A",
        "B",
        "AA",
        "BA",
        "AAA",
        "ZZZZZZ",
    ]
    assert lr._posting_year("2026-01-01") == 2026
    assert lr._posting_year(date(2025, 1, 1)) == 2025
    assert lr._posting_year("bad") is None
    assert lr._posting_year(None) is None


def test_account_eligibility_and_adjacent_navigation(database: MagicMock) -> None:
    lr._validate_lettering_enabled_for_accounts(set())
    with patch.object(
        frappe, "get_all", return_value=[{"name": "Cash", "enable_lettering": 1}]
    ):
        lr._validate_lettering_enabled_for_accounts({"Cash"})
    database.get_value.side_effect = ["100", None]
    assert lr._adjacent_account_position("Cash", "next") == (
        [">", "100"],
        "account_number asc",
    )
    assert lr._adjacent_account_position("Cash", "previous") == (None, "")
    assert lr.get_adjacent_account("", "previous") is None
    with patch.object(frappe, "get_all", return_value=[{"name": "Bank"}]) as get_all:
        assert lr.get_adjacent_account("", "next", "K") == "Bank"
    assert get_all.call_args.kwargs["filters"] == {
        "enable_lettering": 1,
        "is_group": 0,
        "company": "K",
    }


def test_journal_query_filters_partition_and_enriches_party_names(
    database: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(frappe.local, "qb", MariaDB, raising=False)
    database.get_value.return_value = {"name": "Cash", "enable_lettering": 1}
    query = lr._journal_entry_query("Cash")
    filtered = lr._filter_journal_entries(
        query,
        {
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
            "party_type": "Customer",
            "party": "C-1",
            "show_letter": "Only assigned rows",
        },
    )
    sql = filtered.get_sql().replace("`", "")
    assert "Journal Entry" in sql and "Journal Entry Account" in sql
    assert "Journal Entry.docstatus=1" in sql
    assert "Journal Entry.voucher_type='Journal Entry'" in sql
    assert "Journal Entry Account.account='Cash'" in sql
    assert "tabJournal Entry.name=tabJournal Entry Account.parent" in sql
    assert "Journal Entry Account.name jv_row_name" in sql
    assert "debit_in_account_currency" in sql and "credit_in_account_currency" in sql
    assert "posting_date>='2026-01-01'" in sql and "posting_date<='2026-01-31'" in sql
    assert "party_type='Customer'" in sql and "party='C-1'" in sql
    assert "letter IS NOT NULL" in sql and "letter<>''" in sql
    entries = [
        {
            "credit_in_account_currency": 4,
            "debit_in_account_currency": 0,
            "party_type": "Customer",
            "party": "C-1",
        },
        {
            "credit_in_account_currency": 0,
            "debit_in_account_currency": 4,
            "party_type": "Customer",
            "party": "C-1",
        },
    ]
    query.run = MagicMock(return_value=entries)
    with (
        patch.object(lr, "_journal_entry_query", return_value=query),
        patch.object(
            frappe,
            "get_all",
            return_value=[{"name": "C-1", "customer_name": "Customer"}],
        ),
    ):
        payload = lr.journal_entry_list("Cash")
    assert [row["party_name"] for row in payload["cr"] + payload["dr"]] == [
        "Customer",
        "Customer",
    ]
    assert payload["cr"] == [entries[0]]
    assert payload["dr"] == [entries[1]]


def test_payload_validation_and_prepare_letter_rules(database: MagicMock) -> None:
    with (
        patch.object(lr, "_", side_effect=str),
        patch.object(frappe, "throw", side_effect=_raise_validation),
        pytest.raises(frappe.ValidationError),
    ):
        lr._coerce_items("{")
    assert lr._coerce_items('[{"letter": "A"}]') == [{"letter": "A"}]
    with patch.object(lr, "validate_sum_of_credit_and_debit"):
        assert (
            lr._prepare_letter_items(
                [{"letter": ""}], [{"letter": ""}], require_letter=False
            )[3]
            is None
        )
        assert (
            lr._prepare_letter_items(
                [{"letter": "A"}], [{"letter": "A"}], require_letter=True
            )[3]
            == "A"
        )
    with (
        patch.object(lr, "_", side_effect=str),
        patch.object(frappe, "throw", side_effect=_raise_validation),
        pytest.raises(frappe.ValidationError),
    ):
        lr._letter_text(3)
    database.bulk_update.assert_not_called()


def test_set_writes_exact_je_rows_and_set_remove_failures_roll_back(
    database: MagicMock,
) -> None:
    rows = [{"account": "Cash", "jv_row_name": "JEA-1", "posting_date": "2026-01-01"}]
    with (
        patch.object(
            lr, "_prepare_letter_items", return_value=(rows, rows, rows + rows, None)
        ),
        patch.object(lr, "_validate_lettering_enabled_for_accounts"),
        patch.object(lr, "_compute_latest_year", return_value=2026),
        patch.object(lr, "_get_letter_for_year_locked", return_value="B"),
        patch.object(lr, "_update_gl_letters") as update_gl,
        patch.object(lr, "update_year_letter") as update_year,
    ):
        assert lr.set_letter(rows, rows) == {"last_letter": "B", "next_letter": "C"}
    database.bulk_update.assert_called_once_with(
        "Journal Entry Account", {"JEA-1": {"letter": "B"}}
    )
    update_gl.assert_called_once_with(["JEA-1", "JEA-1"], "B")
    update_year.assert_called_once_with(2026, "C")

    database.reset_mock()
    with (
        patch.object(
            lr, "_prepare_letter_items", side_effect=RuntimeError("set payload")
        ),
        patch.object(lr, "_", side_effect=str),
        patch.object(frappe, "throw", side_effect=_raise_validation),
        pytest.raises(frappe.ValidationError, match="set payload"),
    ):
        lr.set_letter(rows, rows)
    database.rollback.assert_called_once_with(save_point="letter_reconciliation_assign")

    database.reset_mock()
    with (
        patch.object(
            lr, "_prepare_letter_items", side_effect=RuntimeError("bad payload")
        ),
        patch.object(frappe, "get_traceback", return_value="trace"),
        patch.object(lr, "_", side_effect=str),
        patch.object(frappe, "throw", side_effect=_raise_validation),
        pytest.raises(frappe.ValidationError, match="bad payload"),
    ):
        lr.remove_letter(rows, rows)
    database.rollback.assert_called_once_with(save_point="letter_reconciliation_remove")


def test_year_counter_locking_and_create_or_update_paths(database: MagicMock) -> None:
    database.get_value.return_value = "Q"
    assert lr.get_next_letter(2026) == "Q"
    database.exists.side_effect = [True, False]
    created = MagicMock()
    with patch.object(frappe, "get_doc", return_value=created):
        lr.update_year_letter(2026, "R")
        lr.update_year_letter(2027, "A")
    database.set_value.assert_called_once_with("Letter Settings", "2026", "letter", "R")
    created.insert.assert_called_once_with(ignore_permissions=True)


def test_remaining_reachable_helper_guards(database: MagicMock) -> None:
    with patch.object(lr, "validate_sum_of_credit_and_debit"):
        assert lr._prepare_letter_items([{}], [{}])[3] is None
    database.get_value.side_effect = ["20", None]
    assert lr._adjacent_account_position("Cash", "previous") == (
        ["<", "20"],
        "account_number desc",
    )
    assert lr._adjacent_account_position("Missing", "next") == (None, "")
    with patch.object(frappe, "get_all", return_value=[]):
        assert lr.get_adjacent_account("", "next") is None
    assert list(lr._chunked(["a", "b"], 0)) == [["a", "b"]]
    assert lr._selected_accounts([{"account": " Cash "}, {"account": ""}], "Bank") == {
        "Cash",
        "Bank",
    }
    assert lr._extract_account_from_items([{"account": ""}], [{}]) is None
    with patch.object(lr, "_update_gl_letters") as update_gl:
        lr._write_selected_letters([{}], "A")
    database.bulk_update.assert_not_called()
    update_gl.assert_called_once_with([], "A")
    lr._update_gl_letters([], "A")
    database.get_value.side_effect = None
    database.get_value.return_value = "100"
    with patch.object(frappe, "get_all", return_value=[{"name": "Bank"}]) as get_all:
        assert lr.get_adjacent_account("Cash", "next", "K") == "Bank"
    assert get_all.call_args.kwargs["filters"]["account_number"] == [">", "100"]
    with patch.object(frappe, "get_all", return_value=[]):
        assert lr.get_adjacent_account("Cash", "next", "K") is None
    database.get_value.return_value = None
    with patch.object(frappe, "get_all") as missing_number_get_all:
        assert lr.get_adjacent_account("Cash", "next", "K") is None
    missing_number_get_all.assert_not_called()


def test_public_error_and_empty_paths(database: MagicMock) -> None:
    with (
        patch.object(lr, "_journal_entry_query", side_effect=RuntimeError("query")),
        patch.object(lr, "_", side_effect=str),
        patch.object(frappe, "get_traceback", return_value="trace"),
    ):
        assert lr.journal_entry_list("Cash") == {
            "error": "Unable to fetch journal entries. See error log."
        }
    with (
        patch.object(lr, "_", side_effect=str),
        patch.object(frappe, "throw", side_effect=_raise_validation),
        patch.object(database, "get_value", return_value=None),
        pytest.raises(frappe.ValidationError),
    ):
        lr._journal_entry_query("Missing")
    with (
        patch.object(lr, "_", side_effect=str),
        patch.object(frappe, "throw", side_effect=_raise_validation),
        patch.object(database, "get_value", return_value={"enable_lettering": 0}),
        pytest.raises(frappe.ValidationError),
    ):
        lr._journal_entry_query("Cash")
    with (
        patch.object(lr, "_", side_effect=str),
        patch.object(frappe, "throw", side_effect=_raise_validation),
        patch.object(frappe, "get_traceback", return_value="trace"),
        pytest.raises(frappe.ValidationError),
    ):
        lr.validate_sum_of_credit_and_debit("bad", "[]")


def test_remove_success_validation_and_precision_year_fallbacks(
    database: MagicMock,
) -> None:
    rows = [{"account": "Cash", "jv_row_name": "JEA-1"}]
    with (
        patch.object(lr, "_prepare_letter_items", return_value=(rows, rows, rows, "A")),
        patch.object(lr, "_validate_lettering_enabled_for_accounts"),
        patch.object(lr, "_write_selected_letters") as write,
    ):
        assert lr.remove_letter(rows, rows) == {"success": True}
    write.assert_called_once_with(rows, "")
    with (
        patch.object(
            lr, "_prepare_letter_items", side_effect=frappe.ValidationError("bad")
        ),
        pytest.raises(frappe.ValidationError),
    ):
        lr.remove_letter(rows, rows)
    database.rollback.assert_called_with(save_point="letter_reconciliation_remove")
    database.reset_mock()
    with (
        patch.object(
            lr, "_prepare_letter_items", side_effect=frappe.ValidationError("invalid")
        ),
        pytest.raises(frappe.ValidationError),
    ):
        lr.remove_letter(rows, rows)
    database.rollback.assert_called_once_with(save_point="letter_reconciliation_remove")
    with patch.object(lr.frappe.utils, "now_datetime", return_value=date(2030, 1, 1)):
        database.get_value.return_value = None
        assert lr.get_next_letter(0) == "A"
    database.get_value.side_effect = [None, "USD", None]
    with patch.object(frappe, "get_precision", return_value=None):
        database.get_default.return_value = None
        assert lr._resolve_amount_precision("Cash") == 2

    no_locked_rows: list[dict[str, str]] = []
    database.sql.side_effect = [no_locked_rows, [{"letter": "b"}]]
    created = MagicMock()
    created.reset_mock()
    with patch.object(frappe, "get_doc", return_value=created) as get_doc:
        assert lr._get_letter_for_year_locked(2026) == "B"
    assert database.sql.call_count == 2
    assert database.sql.call_args_list[0].args == (
        "select letter from `tabLetter Settings` where name=%s for update",
        ("2026",),
    )
    assert database.sql.call_args_list[0].kwargs == {"as_dict": True}
    assert get_doc.call_args.args[0] == {
        "doctype": "Letter Settings",
        "year": 2026,
        "letter": "A",
    }
    created.insert.assert_called_once_with(ignore_permissions=True)


def test_lock_existing_missing_and_currency_extract_fallbacks(
    database: MagicMock,
) -> None:
    database.sql.return_value = [{"letter": "b"}]
    assert lr._get_letter_for_year_locked(2026) == "B"
    created = MagicMock()
    no_locked_rows: list[dict[str, str]] = []
    database.sql.side_effect = [no_locked_rows, no_locked_rows]
    with patch.object(frappe, "get_doc", return_value=created):
        assert lr._get_letter_for_year_locked(2026) == "A"
    created.insert.assert_called_once_with(ignore_permissions=True)
    assert lr._account_currency_precision(None) is None
    assert lr._extract_account_from_items([{"account": " Cash "}], [{}]) == "Cash"
