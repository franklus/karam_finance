"""Pure contracts for Letter Reconciliation document events."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

from karam_finance.letter_reconciliation.utils import doc_events


def _row(name: str, letter: str | None = None, reference: str | None = None) -> Any:
    return SimpleNamespace(name=name, letter=letter, reference_detail_no=reference)


def test_settings_failures_and_gl_insert_gates_leave_documents_unchanged() -> None:
    database = SimpleNamespace(
        get_single_value=Mock(side_effect=RuntimeError("missing"))
    )
    with patch.object(doc_events.frappe, "db", database):
        assert not doc_events._is_merge_prevention_enabled()

    disabled = SimpleNamespace(
        voucher_type="Journal Entry", voucher_no="JE-1", account="Bank", letter="old"
    )
    with patch.object(doc_events, "_is_merge_prevention_enabled", return_value=False):
        doc_events.gl_entry_before_insert(cast(doc_events._GLInsertDoc, disabled))
    assert disabled.letter == "old"

    for document in (
        SimpleNamespace(
            voucher_type="Payment Entry", voucher_no="PE-1", account="Bank"
        ),
        SimpleNamespace(voucher_type="Journal Entry", voucher_no="", account="Bank"),
        SimpleNamespace(voucher_type="Journal Entry", voucher_no="JE-1", account=""),
    ):
        with patch.object(
            doc_events, "_is_merge_prevention_enabled", return_value=True
        ):
            doc_events.gl_entry_before_insert(cast(doc_events._GLInsertDoc, document))
        assert not hasattr(document, "letter")


def test_gl_insert_uses_only_exact_je_child_and_logs_lookup_failures() -> None:
    exact = SimpleNamespace(
        voucher_type="Journal Entry",
        voucher_no="JE-1",
        voucher_detail_no="JEA-1",
        account="Bank",
        letter="old",
    )
    no_detail = SimpleNamespace(
        voucher_type="Journal Entry", voucher_no="JE-1", account="Bank", letter="old"
    )
    with (
        patch.object(doc_events, "_is_merge_prevention_enabled", return_value=True),
        patch.object(
            doc_events, "_cached_voucher_letters", return_value={"JEA-1": "A"}
        ),
    ):
        doc_events.gl_entry_before_insert(cast(doc_events._GLInsertDoc, exact))
        doc_events.gl_entry_before_insert(cast(doc_events._GLInsertDoc, no_detail))
    assert exact.letter == "A"
    assert no_detail.letter == "old"

    with (
        patch.object(doc_events, "_is_merge_prevention_enabled", return_value=True),
        patch.object(
            doc_events, "_cached_voucher_letters", side_effect=RuntimeError("lookup")
        ),
        patch.object(doc_events.frappe, "get_traceback", return_value="trace"),
        patch.object(doc_events.frappe, "log_error") as log_error,
    ):
        doc_events.gl_entry_before_insert(cast(doc_events._GLInsertDoc, exact))
    log_error.assert_called_once_with(
        "trace", "gl_entry_before_insert error (letter sync)"
    )


def test_voucher_cache_fetches_once_skips_blank_names_and_normalises_letters() -> None:
    local = SimpleNamespace()
    rows = [
        {"name": "JEA-1", "account": "Bank", "letter": None, "idx": 1},
        {"name": "", "account": "Bank", "letter": "ignored", "idx": 2},
    ]
    with (
        patch.object(doc_events.frappe, "local", local),
        patch.object(doc_events.frappe, "get_all", return_value=rows) as get_all,
    ):
        assert doc_events._cached_voucher_letters("JE-1") == {"JEA-1": ""}
        assert doc_events._cached_voucher_letters("JE-1") == {"JEA-1": ""}
    get_all.assert_called_once_with(
        "Journal Entry Account",
        filters={"parent": "JE-1"},
        fields=["name", "account", "letter", "idx"],
        order_by="idx asc",
        limit=0,
    )


def test_before_submit_sets_only_missing_reference_details() -> None:
    missing = _row("JEA-1")
    existing = _row("JEA-2", reference="kept")
    document = SimpleNamespace(accounts=[missing, existing])
    with patch.object(doc_events, "_is_merge_prevention_enabled", return_value=True):
        doc_events.je_before_submit(cast(doc_events._JournalEntryDoc, document))
    assert (missing.reference_detail_no, existing.reference_detail_no) == (
        "JEA-1",
        "kept",
    )


def test_submitted_hook_logs_sync_failure_without_aborting() -> None:
    document = SimpleNamespace(name="JE-1", accounts=[])
    with (
        patch.object(doc_events, "_is_merge_prevention_enabled", return_value=True),
        patch.object(
            doc_events,
            "sync_journal_entry_gl_letters",
            side_effect=RuntimeError("sync"),
        ),
        patch.object(doc_events.frappe, "get_traceback", return_value="trace"),
        patch.object(doc_events.frappe, "log_error") as log_error,
    ):
        doc_events.journal_entry_on_update_after_submit(
            cast(doc_events._JournalEntryDoc, document)
        )
    log_error.assert_called_once_with(
        "trace", "journal_entry_on_update_after_submit error (letter sync)"
    )


def test_sync_skips_unknown_name_and_no_gl_entries() -> None:
    with (
        patch.object(doc_events, "_is_merge_prevention_enabled", return_value=False),
        patch.object(doc_events.frappe, "get_all") as disabled_get_all,
    ):
        doc_events.sync_journal_entry_gl_letters(
            cast(doc_events._JournalEntryDoc, SimpleNamespace(name="JE-1", accounts=[]))
        )
    disabled_get_all.assert_not_called()

    with patch.object(doc_events.frappe, "get_all") as get_all:
        doc_events.sync_journal_entry_gl_letters(
            cast(doc_events._JournalEntryDoc, SimpleNamespace(name="", accounts=[])),
            ignore_setting=True,
        )
    get_all.assert_not_called()

    local = SimpleNamespace(_letter_reconciliation_account_cache={"JE-1": {"x": "A"}})
    with (
        patch.object(doc_events.frappe, "local", local),
        patch.object(doc_events.frappe, "get_all", return_value=[]),
    ):
        doc_events.sync_journal_entry_gl_letters(
            cast(
                doc_events._JournalEntryDoc, SimpleNamespace(name="JE-1", accounts=[])
            ),
            ignore_setting=True,
        )
    assert "JE-1" in local._letter_reconciliation_account_cache


def test_sync_updates_only_changed_exact_rows_clears_letters_and_refreshes_cache() -> (
    None
):
    accounts = [_row("JEA-1", "New"), _row("JEA-2", None)]
    document = SimpleNamespace(name="JE-1", accounts=accounts)
    entries = [
        {"name": "GLE-1", "voucher_detail_no": "JEA-1", "letter": "Old"},
        {"name": "GLE-2", "voucher_detail_no": "JEA-2", "letter": "Keep"},
        {"name": "GLE-3", "voucher_detail_no": "unknown", "letter": "X"},
        {"name": "GLE-4", "voucher_detail_no": None, "letter": "X"},
    ]
    database = SimpleNamespace(bulk_update=Mock())
    local = SimpleNamespace(
        _letter_reconciliation_account_cache={"JE-1": {}, "JE-2": {}}
    )
    with (
        patch.object(doc_events.frappe, "db", database),
        patch.object(doc_events.frappe, "local", local),
        patch.object(doc_events.frappe, "get_all", return_value=entries) as get_all,
    ):
        doc_events.sync_journal_entry_gl_letters(
            cast(doc_events._JournalEntryDoc, document), ignore_setting=True
        )
    database.bulk_update.assert_called_once_with(
        "GL Entry", {"GLE-1": {"letter": "New"}, "GLE-2": {"letter": ""}}
    )
    get_all.assert_called_once_with(
        "GL Entry",
        filters={"voucher_type": "Journal Entry", "voucher_no": "JE-1"},
        fields=["name", "voucher_detail_no", "letter"],
        limit=0,
    )
    assert local._letter_reconciliation_account_cache == {"JE-2": {}}


def test_sync_no_changes_does_not_bulk_update_but_invalidates_its_cache() -> None:
    document = SimpleNamespace(name="JE-1", accounts=[_row("JEA-1", "A")])
    database = SimpleNamespace(bulk_update=Mock())
    local = SimpleNamespace(_letter_reconciliation_account_cache={"JE-1": {}})
    with (
        patch.object(doc_events, "_is_merge_prevention_enabled", return_value=True),
        patch.object(doc_events.frappe, "db", database),
        patch.object(doc_events.frappe, "local", local),
        patch.object(
            doc_events.frappe,
            "get_all",
            return_value=[
                {"name": "GLE-1", "voucher_detail_no": "JEA-1", "letter": "A"}
            ],
        ),
    ):
        doc_events.sync_journal_entry_gl_letters(
            cast(doc_events._JournalEntryDoc, document)
        )
    database.bulk_update.assert_not_called()
    assert local._letter_reconciliation_account_cache == {}
