"""Isolated date, recovery and legacy split regressions; never repost real vouchers."""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock, call, patch

import frappe
import pytest

from karam_finance.patches import fix_merged_gl_entries as legacy

from . import historical_gl_rebuild as history
from . import letter_reconciliation_settings as jobs


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    database = MagicMock()
    monkeypatch.setattr(frappe, "db", database)
    cache = MagicMock()
    cache.hget.return_value = dict[str, str]()
    monkeypatch.setattr(frappe, "cache", cache)
    monkeypatch.setattr(frappe, "log_error", MagicMock())
    monkeypatch.setattr(frappe, "publish_realtime", MagicMock())
    monkeypatch.setattr(
        frappe.local, "flags", frappe._dict(mute_messages=True), raising=False
    )
    monkeypatch.setattr(frappe.local, "message_log", [], raising=False)
    monkeypatch.setattr(frappe.local, "lang", "en", raising=False)
    return database


@pytest.fixture
def state() -> jobs.RebuildRunState:
    return {
        "run_id": "run",
        "user": "test@example.com",
        "progress_event": "progress",
        "done_event": "done",
        "filters": {
            "company": "ACME",
            "whole_history": True,
            "from_posting_date": None,
            "to_posting_date": None,
        },
        "total_submitted_vouchers": 2,
        "eligible_vouchers": ["JV-1", "JV-2"],
        "next_index": 0,
        "rebuilt_count": 0,
        "blocked_count": 0,
        "already_correct_count": 0,
        "failures": [],
    }


@pytest.mark.parametrize("start", ["2026-01-01", date(2026, 1, 1)])
def test_date_range_normalises_inclusive_boundary(
    database: MagicMock, start: str | date
) -> None:
    assert history._validated_date_range(
        start, date(2026, 1, 1), whole_history=False
    ) == ("2026-01-01", "2026-01-01")
    assert history._validated_date_range(start, None, whole_history=True) == (
        None,
        None,
    )
    database.sql.assert_not_called()


@pytest.mark.parametrize(
    ("start", "end", "message"),
    [(None, "2026-01-01", "Set both"), ("2026-02-01", "2026-01-01", "cannot be after")],
)
def test_invalid_scope_is_rejected(
    database: MagicMock, *, start: str | None, end: str, message: str
) -> None:
    with pytest.raises(frappe.ValidationError, match=message):
        history._validated_date_range(start, end, whole_history=False)
    database.sql.assert_not_called()


@pytest.mark.parametrize(
    "endpoint", ["preview_historical_gl_rebuild", "enqueue_historical_gl_rebuild"]
)
def test_rebuild_role_gate_precedes_queries(database: MagicMock, endpoint: str) -> None:
    with (
        patch.object(
            frappe, "only_for", side_effect=frappe.PermissionError
        ) as only_for,
        patch.object(jobs, "get_validated_rebuild_filters") as filters,
        pytest.raises(frappe.PermissionError),
    ):
        getattr(jobs, endpoint)()
    only_for.assert_called_once_with(["System Manager", "Accounts Manager"])
    filters.assert_not_called()
    database.commit.assert_not_called()


@pytest.mark.parametrize("fails", [False, True])
def test_voucher_transaction_and_progress_order(
    database: MagicMock, state: jobs.RebuildRunState, *, fails: bool
) -> None:
    sequence = MagicMock()
    sequence.attach_mock(database.savepoint, "savepoint")
    sequence.attach_mock(database.rollback, "rollback")
    failure = RuntimeError("broken voucher") if fails else None
    with (
        patch.object(jobs, "_publish_progress") as progress,
        patch.object(jobs, "rebuild_single_voucher", side_effect=failure) as rebuild,
        patch.object(jobs, "_commit_rebuild_progress") as commit,
        patch.object(jobs, "_save_rebuild_state") as save,
    ):
        sequence.attach_mock(progress, "progress")
        sequence.attach_mock(rebuild, "rebuild")
        sequence.attach_mock(commit, "commit")
        sequence.attach_mock(save, "save")
        jobs._rebuild_voucher_at_index(state, "JV-1", 1)
    assert [entry[0] for entry in sequence.mock_calls] == [
        "progress",
        "savepoint",
        "rebuild",
        "rollback" if fails else "commit",
        "save",
    ]
    rebuild.assert_called_once_with("JV-1", reference_detail_backfilled=True)
    assert state["next_index"] == 1
    assert state["rebuilt_count"] == (0 if fails else 1)
    assert state["failures"] == (
        [{"voucher_no": "JV-1", "reason": "broken voucher"}] if fails else []
    )
    database.commit.assert_not_called()


def test_partial_batch_continues_and_finalises(
    database: MagicMock, state: jobs.RebuildRunState
) -> None:
    snapshots: list[tuple[int, int, int]] = []

    def save(current: jobs.RebuildRunState) -> None:
        snapshots.append(
            (current["next_index"], current["rebuilt_count"], len(current["failures"]))
        )

    with (
        patch.object(jobs, "_get_rebuild_state", return_value=state),
        patch.object(jobs, "backfill_reference_detail_no_bulk"),
        patch.object(
            jobs, "rebuild_single_voucher", side_effect=[RuntimeError("bad"), None]
        ),
        patch.object(jobs, "_save_rebuild_state", side_effect=save),
        patch.object(jobs, "_commit_rebuild_progress") as commit,
        patch.object(jobs, "_finalise_rebuild_run") as finalise,
        patch.object(jobs, "_enqueue_rebuild_batch") as enqueue,
    ):
        jobs.run_historical_gl_rebuild_job("run")
    assert snapshots == [(1, 0, 1), (2, 1, 1)]
    database.rollback.assert_called_once_with(save_point="historical_gl_rebuild_1")
    commit.assert_called_once_with()
    finalise.assert_called_once_with(state)
    enqueue.assert_not_called()


def test_fatal_batch_rolls_back_then_reports_and_clears(
    database: MagicMock, state: jobs.RebuildRunState
) -> None:
    sequence = MagicMock()
    sequence.attach_mock(database.rollback, "rollback")
    with (
        patch.object(jobs, "_get_rebuild_state", return_value=state),
        patch.object(
            jobs,
            "backfill_reference_detail_no_bulk",
            side_effect=RuntimeError("backfill"),
        ),
        patch.object(jobs, "_publish_rebuild_failure") as publish,
        patch.object(jobs, "_clear_rebuild_state") as clear,
    ):
        sequence.attach_mock(publish, "publish")
        sequence.attach_mock(clear, "clear")
        jobs.run_historical_gl_rebuild_job("run")
    assert sequence.mock_calls == [
        call.rollback(),
        call.publish(state),
        call.clear("run"),
    ]
    database.commit.assert_not_called()


def test_finalisation_clears_both_cache_keys(
    database: MagicMock, state: jobs.RebuildRunState
) -> None:
    with patch.object(jobs, "_update_rebuild_audit"):
        jobs._finalise_rebuild_run(state)
    cache = frappe.cache
    assert isinstance(cache, MagicMock)
    assert cache.delete_value.call_args_list == [
        call(jobs._REBUILD_CACHE_KEY),
        call(jobs._get_rebuild_state_key("run")),
    ]
    database.commit.assert_not_called()


@pytest.mark.parametrize("fail_delete", [False, True])
def test_legacy_split_inserts_before_delete_and_rolls_back_failure(
    database: MagicMock, *, fail_delete: bool
) -> None:
    original = {"name": "GL-1", "debit": 30, "credit": 0}
    rows = [{"name": "JEA-1", "debit": 10}, {"name": "JEA-2", "debit": 20}]
    database.get_value.return_value = original
    database.delete.side_effect = RuntimeError("delete failed") if fail_delete else None
    sequence = MagicMock()
    sequence.attach_mock(database.savepoint, "savepoint")
    sequence.attach_mock(database.delete, "delete")
    sequence.attach_mock(database.rollback, "rollback")
    with patch.object(legacy, "_insert_split_entry") as insert:
        sequence.attach_mock(insert, "insert")
        legacy._split_single_gl_entry(original, rows)
    expected = [
        call.savepoint("split_gl_GL_1"),
        call.insert(original, rows[0]),
        call.insert(original, rows[1]),
        call.delete("GL Entry", {"name": "GL-1"}),
    ]
    if fail_delete:
        expected.append(call.rollback(save_point="split_gl_GL_1"))
    assert sequence.mock_calls == expected
    database.commit.assert_not_called()


def test_split_preserves_currency_fields_and_proportional_amounts(
    database: MagicMock,
) -> None:
    original: dict[str, Any] = {
        "name": "GL-1",
        "debit": 30,
        "credit": 0,
        "account_currency": "EUR",
        "transaction_currency": "USD",
        "debit_in_transaction_currency": 60,
        "credit_in_transaction_currency": 0,
        "company": "ACME",
        "to_rename": 0,
    }
    row = {"name": "JEA-1", "debit": 10, "debit_in_account_currency": 8, "letter": "A"}
    document = MagicMock()
    with patch.object(frappe, "new_doc", return_value=document):
        legacy._insert_split_entry(original, row)
    assert document.debit == 10
    assert document.debit_in_account_currency == 8
    assert document.debit_in_transaction_currency == 20
    assert document.voucher_detail_no == "JEA-1"
    document.set.assert_any_call("account_currency", "EUR")
    document.set.assert_any_call("transaction_currency", "USD")
    document.set.assert_any_call("letter", "A")
    assert call("to_rename", 0) not in document.set.call_args_list
    document.insert.assert_called_once_with(ignore_permissions=True)
    assert original["debit"] == 30
    database.commit.assert_not_called()


@pytest.mark.parametrize("previous", [None, False, True])
def test_repost_failure_restores_validation_functions_and_flag(
    database: MagicMock, previous: bool | None
) -> None:
    document = MagicMock(docstatus=1)
    document.make_gl_entries.side_effect = RuntimeError("repost failed")
    flags = frappe._dict[str, object]()
    if previous is not None:
        flags.through_repost_accounting_ledger = previous
    originals = (
        history.erpnext_party.validate_account_party_type,
        history.erpnext_gl_entry.validate_account_party_type,
        history.erpnext_gl_entry.validate_balance_type,
    )
    with (
        patch.object(frappe, "flags", flags),
        patch.object(frappe, "get_doc", return_value=document),
        patch.object(history, "sync_journal_entry_gl_letters") as sync,
        pytest.raises(RuntimeError, match="repost failed"),
    ):
        history.rebuild_single_voucher("JV-1", reference_detail_backfilled=True)
    assert (
        history.erpnext_party.validate_account_party_type,
        history.erpnext_gl_entry.validate_account_party_type,
        history.erpnext_gl_entry.validate_balance_type,
    ) == originals
    assert flags.get("through_repost_accounting_ledger") is previous
    assert ("through_repost_accounting_ledger" in flags) is (previous is not None)
    sync.assert_not_called()
    database.commit.assert_not_called()


def test_audit_failure_still_publishes_fatal_completion(
    database: MagicMock, state: jobs.RebuildRunState
) -> None:
    with patch.object(jobs, "_update_rebuild_audit", side_effect=RuntimeError("audit")):
        jobs._publish_rebuild_failure(state)
    publish = frappe.publish_realtime
    assert isinstance(publish, MagicMock)
    assert publish.call_args.args[0] == "done"
    assert publish.call_args.args[1]["status"] == "fatal_error"
    assert publish.call_args.kwargs == {"user": "test@example.com"}
    database.commit.assert_not_called()


def test_enqueue_failure_releases_created_run_state(
    database: MagicMock, state: jobs.RebuildRunState
) -> None:
    preview: history.RebuildPreview = {
        "filters": state["filters"],
        "total_submitted_vouchers": 1,
        "eligible": history._build_bucket(
            [{"voucher_no": "JV-1", "posting_date": "2026-01-01", "reason": ""}]
        ),
        "blocked": history._build_bucket([]),
        "already_correct": history._build_bucket([]),
    }
    cache = frappe.cache
    assert isinstance(cache, MagicMock)
    cache.get_value.return_value = None
    with (
        patch.object(frappe, "only_for"),
        patch.object(frappe, "session", frappe._dict(user="test@example.com")),
        patch.object(
            jobs, "get_validated_rebuild_filters", return_value=state["filters"]
        ),
        patch.object(jobs, "build_rebuild_preview", return_value=preview),
        patch.object(frappe, "generate_hash", side_effect=["run", "progress", "done"]),
        patch.object(jobs, "_enqueue_rebuild_batch", side_effect=RuntimeError("queue")),
        pytest.raises(RuntimeError, match="queue"),
    ):
        jobs.enqueue_historical_gl_rebuild()
    assert cache.set_value.call_count == 2
    assert cache.delete_value.call_args_list == [
        call(jobs._REBUILD_CACHE_KEY),
        call(jobs._get_rebuild_state_key("run")),
    ]
    database.commit.assert_not_called()


def _merge_key() -> legacy._MergeKey:
    return ("JV-1", "Cash", "", "", "")


def _merged_originals() -> list[legacy._GLRow]:
    return [{"name": "GL-1"}, {"name": "GL-2"}]


def _split_children() -> list[legacy._GLRow]:
    return [{"name": "JEA-1"}, {"name": "JEA-2"}]


def _assert_split_lookup(get_all: MagicMock) -> None:
    get_all.assert_called_once_with(
        "GL Entry",
        filters={
            "voucher_no": "JV-1",
            "voucher_detail_no": ["in", ["JEA-1", "JEA-2"]],
            "is_cancelled": 0,
        },
        fields=["name", "voucher_detail_no"],
        limit=3,
    )


@pytest.mark.parametrize(
    "existing_children",
    [
        [{"name": "split-1", "voucher_detail_no": "JEA-1"}],
        [
            {"name": "split-1", "voucher_detail_no": "JEA-1"},
            {"name": "split-2", "voucher_detail_no": "JEA-1"},
        ],
    ],
)
def test_legacy_split_retains_originals_for_partial_or_duplicate_children(
    existing_children: list[legacy._GLRow],
) -> None:
    with (
        patch.object(legacy, "_matching_jea_rows", return_value=_split_children()),
        patch.object(
            legacy.frappe, "get_all", return_value=existing_children
        ) as get_all,
        patch.object(legacy.frappe, "log_error"),
        patch.object(legacy, "_delete_merged_rows") as delete,
    ):
        assert legacy._process_merge_group(_merge_key(), _merged_originals()) == 0

    delete.assert_not_called()
    _assert_split_lookup(get_all)


def test_legacy_split_accepts_complete_existing_children() -> None:
    existing_children = [
        {"name": "split-1", "voucher_detail_no": "JEA-1"},
        {"name": "split-2", "voucher_detail_no": "JEA-2"},
    ]
    originals = _merged_originals()
    with (
        patch.object(legacy, "_matching_jea_rows", return_value=_split_children()),
        patch.object(
            legacy.frappe, "get_all", return_value=existing_children
        ) as get_all,
        patch.object(legacy.frappe, "log_error"),
        patch.object(legacy, "_delete_merged_rows") as delete,
    ):
        assert legacy._process_merge_group(_merge_key(), originals) == 2

    delete.assert_called_once_with(originals)
    _assert_split_lookup(get_all)


def test_legacy_split_failure_keeps_every_original() -> None:
    database = MagicMock()
    database.get_value.return_value = {"name": "GL-1", "debit": 10, "credit": 0}
    inserted = MagicMock(side_effect=RuntimeError("insert"))
    replacement = MagicMock(flags=MagicMock())
    replacement.set = MagicMock()
    replacement.insert = inserted

    with (
        patch.object(legacy.frappe, "db", database),
        patch.object(legacy.frappe, "new_doc", return_value=replacement),
        patch.object(legacy.frappe, "get_all", return_value=[]),
        patch.object(legacy, "_matching_jea_rows", return_value=_split_children()),
        patch.object(legacy.frappe, "get_traceback", return_value="trace"),
        patch.object(legacy.frappe, "log_error"),
        patch.object(legacy, "_delete_merged_rows") as delete,
    ):
        assert legacy._process_merge_group(_merge_key(), _merged_originals()) == 0

    database.rollback.assert_called_once_with(save_point="split_gl_GL_1")
    delete.assert_not_called()


def test_legacy_split_deletes_extras_only_after_replacement_original() -> None:
    database = MagicMock()
    database.get_value.return_value = {"name": "GL-1", "debit": 10, "credit": 0}
    replacement = MagicMock(flags=MagicMock())
    replacement.set = MagicMock()
    replacement.insert = MagicMock()

    def delete_extras(_rows: list[legacy._GLRow]) -> None:
        assert database.delete.call_args_list == [call("GL Entry", {"name": "GL-1"})]

    with (
        patch.object(legacy.frappe, "db", database),
        patch.object(legacy.frappe, "new_doc", return_value=replacement),
        patch.object(legacy.frappe, "get_all", return_value=[]),
        patch.object(legacy, "_matching_jea_rows", return_value=_split_children()),
        patch.object(
            legacy, "_delete_merged_rows", side_effect=delete_extras
        ) as delete,
    ):
        assert legacy._process_merge_group(_merge_key(), _merged_originals()) == 2

    delete.assert_called_once_with([{"name": "GL-2"}])


@pytest.mark.parametrize(
    "existing_children",
    [
        [
            {"name": "split-1", "voucher_detail_no": "JEA-1"},
            {"name": "split-2", "voucher_detail_no": None},
        ],
        [
            {"name": "split-1", "voucher_detail_no": "JEA-1"},
            {"name": "split-2", "voucher_detail_no": "unexpected"},
        ],
    ],
)
def test_legacy_split_retains_originals_for_malformed_child_metadata(
    existing_children: list[legacy._GLRow],
) -> None:
    with (
        patch.object(legacy, "_matching_jea_rows", return_value=_split_children()),
        patch.object(legacy.frappe, "get_all", return_value=existing_children),
        patch.object(legacy.frappe, "log_error"),
        patch.object(legacy, "_delete_merged_rows") as delete,
    ):
        assert legacy._process_merge_group(_merge_key(), _merged_originals()) == 0

    delete.assert_not_called()


def test_legacy_split_reports_missing_original_and_keeps_extras() -> None:
    database = MagicMock()
    database.get_value.return_value = None

    with (
        patch.object(legacy.frappe, "db", database),
        patch.object(legacy.frappe, "get_all", return_value=[]),
        patch.object(legacy, "_matching_jea_rows", return_value=_split_children()),
        patch.object(legacy, "_delete_merged_rows") as delete,
    ):
        assert legacy._process_merge_group(_merge_key(), _merged_originals()) == 0

    delete.assert_not_called()


def test_legacy_split_rolls_back_original_delete_failure_and_keeps_extras() -> None:
    database = MagicMock()
    database.get_value.return_value = {"name": "GL-1", "debit": 10, "credit": 0}
    database.delete.side_effect = RuntimeError("delete")
    replacement = MagicMock(flags=MagicMock())
    replacement.set = MagicMock()
    replacement.insert = MagicMock()

    with (
        patch.object(legacy.frappe, "db", database),
        patch.object(legacy.frappe, "new_doc", return_value=replacement),
        patch.object(legacy.frappe, "get_all", return_value=[]),
        patch.object(legacy, "_matching_jea_rows", return_value=_split_children()),
        patch.object(legacy.frappe, "get_traceback", return_value="trace"),
        patch.object(legacy.frappe, "log_error"),
        patch.object(legacy, "_delete_merged_rows") as delete,
    ):
        assert legacy._process_merge_group(_merge_key(), _merged_originals()) == 0

    database.rollback.assert_called_once_with(save_point="split_gl_GL_1")
    delete.assert_not_called()
