"""Pure support contracts for historical Letter Reconciliation rebuilds."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
import pytest
from frappe.query_builder.builder import MariaDB
from karam_finance.letter_reconciliation.doctype.letter_reconciliation_settings import (
    historical_gl_rebuild as rebuild,
)
from karam_finance.letter_reconciliation.doctype.letter_reconciliation_settings import (
    letter_reconciliation_settings as settings,
)


def _raise(message: str, *_args: object, **_kwargs: object) -> None:
    raise frappe.ValidationError(message)


def test_filters_subset_maps_reasons_and_backfill_sql() -> None:
    with (
        patch.object(rebuild, "_", side_effect=str),
        patch.object(frappe, "throw", side_effect=_raise),
        pytest.raises(frappe.ValidationError),
    ):
        rebuild._validated_date_range(None, None, whole_history=False)
    assert rebuild._validated_date_range(None, None, whole_history=True) == (None, None)
    with patch.object(
        rebuild,
        "_run_subset_query",
        return_value=[
            {"voucher_no": "JV-1", "account": "Cash"},
            {"voucher_no": "JV-1", "account": "Cash"},
            {"voucher_no": "JV-1", "account": ""},
        ],
    ):
        assert rebuild._get_subset_voucher_account_map(
            {
                "company": "K",
                "whole_history": True,
                "from_posting_date": None,
                "to_posting_date": None,
            }
        ) == {"JV-1": ["Cash"]}
    db = MagicMock()
    with patch.object(frappe, "db", db):
        rebuild.backfill_reference_detail_no("JV-1")
    sql, params = db.sql.call_args.args
    assert "tabJournal Entry Account" in sql and "parent = %(voucher_no)s" in sql
    assert "IFNULL(reference_detail_no, '') = ''" in sql and params == {
        "voucher_no": "JV-1"
    }
    assert rebuild.extract_accounts_from_reason(
        "frappe.exceptions.ValidationError: Balance for Account Cash - Main must always be Debit<br>1000 - Cash"
    ) == ["Cash - Main", "1000 - Cash"]
    assert rebuild.normalise_reason_text("a<br>\n b") == "a\nb"


def test_closed_period_repost_and_settings_cache_audit_enqueue() -> None:
    db = MagicMock()
    no_closed_periods: list[str] = []
    db.get_all.side_effect = [["2026-01-31"], no_closed_periods]
    db.exists.return_value = False
    with patch.object(frappe, "db", db):
        assert rebuild._get_latest_closed_period_end("K") == "2026-01-31"
        assert rebuild._get_latest_closed_period_end("K") is None
        assert not rebuild._is_journal_entry_repost_allowed()
    assert db.get_all.call_args_list[0].args == ("Period Closing Voucher",)
    assert db.get_all.call_args_list[0].kwargs == {
        "filters": {"company": "K", "docstatus": 1},
        "pluck": "period_end_date",
        "order_by": "period_end_date desc",
        "limit": 1,
    }
    cache = MagicMock()
    state: settings.RebuildRunState = {
        "run_id": "r1",
        "user": "user",
        "progress_event": "progress",
        "done_event": "done",
        "filters": {
            "company": "K",
            "whole_history": True,
            "from_posting_date": None,
            "to_posting_date": None,
        },
        "total_submitted_vouchers": 0,
        "eligible_vouchers": [],
        "next_index": 0,
        "rebuilt_count": 0,
        "blocked_count": 0,
        "already_correct_count": 0,
        "failures": [],
    }
    with patch.object(settings.frappe, "cache", cache):
        settings._save_rebuild_state(state)
        cache.set_value.assert_called_once_with(
            "historical_gl_rebuild_state:r1", state, expires_in_sec=14400
        )
        settings._clear_rebuild_state("r1")
        cache.reset_mock()
        cache.get_value.return_value = "other"
        settings._clear_orphaned_rebuild_lock("r1")
    cache.delete_value.assert_called_once_with("historical_gl_rebuild_state:r1")
    audit = SimpleNamespace(save=MagicMock())
    with (
        patch.object(settings.frappe, "get_single", return_value=audit),
        patch.object(settings, "now_datetime", return_value="now"),
        patch.object(settings.frappe, "db", db),
    ):
        settings._update_rebuild_audit("user", "success")
    assert (
        audit.last_migration_run,
        audit.last_migration_status,
        audit.last_migration_user,
    ) == ("now", "success", "user")
    audit.save.assert_called_once_with(ignore_permissions=True)
    db.commit.assert_called_once_with()
    with patch.object(settings.frappe, "enqueue") as enqueue:
        settings._enqueue_rebuild_batch("r1")
    assert enqueue.call_args.args == (
        "karam_finance.letter_reconciliation.doctype.letter_reconciliation_settings.letter_reconciliation_settings.run_historical_gl_rebuild_job",
    )
    assert enqueue.call_args.kwargs == {
        "queue": "long",
        "timeout": 14400,
        "run_id": "r1",
    }


def test_remaining_reachable_historical_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with (
        patch.object(rebuild, "_", side_effect=str),
        patch.object(frappe, "throw", side_effect=_raise),
        patch.object(
            frappe,
            "get_single",
            return_value=SimpleNamespace(
                rebuild_company="",
                rebuild_whole_history=False,
                rebuild_from_posting_date=None,
                rebuild_to_posting_date=None,
            ),
        ),
        pytest.raises(frappe.ValidationError),
    ):
        rebuild.get_validated_rebuild_filters()
    assert rebuild._validated_date_range(
        "2026-01-01", "2026-01-02", whole_history=True
    ) == (None, None)
    with (
        patch.object(rebuild, "_", side_effect=str),
        patch.object(rebuild, "getdate", return_value=None),
        patch.object(frappe, "throw", side_effect=_raise),
        pytest.raises(frappe.ValidationError),
    ):
        rebuild._required_date("bad")
    assert rebuild._summarise_reason("") == ""
    assert rebuild.extract_accounts_from_reason("account: Cash") == ["Cash"]
    block_reason = rebuild._get_block_reason(
        posting_date="2026-01-01", latest_closed_period=None, repost_allowed=False
    )
    assert block_reason is not None and block_reason.startswith(
        "Journal Entry is not enabled"
    )
    db = MagicMock()
    with patch.object(frappe, "db", db):
        rebuild.backfill_reference_detail_no_bulk([])
    db.sql.assert_not_called()
    monkeypatch.setattr(frappe.local, "qb", MariaDB, raising=False)
    monkeypatch.setattr(frappe.local, "flags", frappe._dict(), raising=False)
    monkeypatch.setattr(frappe.local, "db", db, raising=False)
    with patch.object(frappe, "db", db):
        rebuild.backfill_reference_detail_no_bulk(["JE-1", "JE-2"])
    assert db.sql.call_count == 1
    query = str(db.sql.call_args.args[0])
    assert "UPDATE `tabJournal Entry Account`" in query
    assert "SET `reference_detail_no`=`name`" in query
    assert (
        "`parent` IN (%(param1)s,%(param2)s) AND "
        "(`reference_detail_no` IS NULL OR `reference_detail_no`=%(param3)s)"
    ) in query
    assert db.sql.call_args.args[1] == {
        "param1": "JE-1",
        "param2": "JE-2",
        "param3": "",
    }


def test_reason_groups_and_settings_guards() -> None:
    assert (
        rebuild._summarise_reason(
            "Party is set on a non-Receivable/Payable/Equity account: Cash"
        )
        == "Party is set on a non-Receivable/Payable/Equity account"
    )
    assert rebuild._summarise_reason("1000 - Cash") == "1000 - Cash"
    groups = rebuild.build_reason_summary_groups(
        [
            {
                "voucher_no": "JV-1",
                "posting_date": "2026-01-01",
                "reason": "same",
                "accounts": [],
            },
            {
                "voucher_no": "JV-2",
                "posting_date": "2026-01-01",
                "reason": "same",
                "accounts": [],
            },
        ],
        sample_limit=1,
    )
    assert groups[0]["voucher_count"] == 2 and groups[0]["voucher_samples"] == ["JV-1"]
    cache = MagicMock()
    cache.get_value.side_effect = [{"run_id": "r1"}, None]
    with patch.object(settings.frappe, "cache", cache):
        assert settings._get_rebuild_state("r1") == {"run_id": "r1"}
        assert settings._get_rebuild_state("missing") is None
    audit = SimpleNamespace(save=MagicMock())
    with (
        patch.object(settings.frappe, "get_single", return_value=audit),
        patch.object(settings, "now_datetime", return_value="now"),
    ):
        settings._update_preview_audit("user")
    assert (audit.last_preview_run, audit.last_preview_user) == ("now", "user")
    audit.save.assert_called_once_with(ignore_permissions=True)
    assert "No eligible" in settings._no_eligible_vouchers_message()


def test_mocked_repost_invokes_temporary_noop_validators_and_restores_them() -> None:
    original_party = rebuild.erpnext_party.validate_account_party_type
    original_gl_party = rebuild.erpnext_gl_entry.validate_account_party_type
    original_balance = rebuild.erpnext_gl_entry.validate_balance_type

    def invoke_replaced_validators(*_args: object) -> None:
        rebuild.erpnext_party.validate_account_party_type(None)
        rebuild.erpnext_gl_entry.validate_account_party_type(None)
        rebuild.erpnext_gl_entry.validate_balance_type(None)

    document = SimpleNamespace(
        docstatus=1,
        validate_for_repost=MagicMock(),
        make_gl_entries=MagicMock(side_effect=invoke_replaced_validators),
    )
    flags = SimpleNamespace()
    with (
        patch.object(rebuild.frappe, "flags", flags),
        patch.object(rebuild.frappe, "get_doc", return_value=document),
        patch.object(rebuild, "backfill_reference_detail_no") as backfill,
        patch.object(rebuild, "sync_journal_entry_gl_letters") as sync,
    ):
        rebuild.rebuild_single_voucher("JV-1")
    backfill.assert_called_once_with("JV-1")
    assert document.make_gl_entries.call_args_list[0].args == (1,)
    assert document.make_gl_entries.call_args_list[1].args == ()
    sync.assert_called_once_with(document, ignore_setting=True)
    assert rebuild.erpnext_party.validate_account_party_type is original_party
    assert rebuild.erpnext_gl_entry.validate_account_party_type is original_gl_party
    assert rebuild.erpnext_gl_entry.validate_balance_type is original_balance
    assert not hasattr(flags, "through_repost_accounting_ledger")


def test_enqueue_public_guards_reject_running_or_empty_without_queueing() -> None:
    with (
        patch.object(settings.frappe, "only_for"),
        patch.object(
            settings.frappe, "cache", MagicMock(get_value=MagicMock(return_value="run"))
        ),
        patch.object(settings, "_", side_effect=str),
        patch.object(settings.frappe, "throw", side_effect=_raise),
        patch.object(settings.frappe, "enqueue") as enqueue,
        pytest.raises(frappe.ValidationError, match="already running"),
    ):
        settings.enqueue_historical_gl_rebuild()
    enqueue.assert_not_called()


def test_worker_empty_eligible_state_uses_failure_boundary_without_repost() -> None:
    state: settings.RebuildRunState = {
        "run_id": "r1",
        "user": "user",
        "progress_event": "progress",
        "done_event": "done",
        "filters": {
            "company": "K",
            "whole_history": True,
            "from_posting_date": None,
            "to_posting_date": None,
        },
        "total_submitted_vouchers": 0,
        "eligible_vouchers": [],
        "next_index": 0,
        "rebuilt_count": 0,
        "blocked_count": 0,
        "already_correct_count": 0,
        "failures": [],
    }
    db = MagicMock()
    with (
        patch.object(settings, "_get_rebuild_state", return_value=state),
        patch.object(settings, "_", side_effect=str),
        patch.object(settings.frappe, "throw", side_effect=_raise),
        patch.object(settings.frappe, "db", db),
        patch.object(settings.frappe, "get_traceback", return_value="trace"),
        patch.object(settings.frappe, "log_error"),
        patch.object(settings, "_publish_rebuild_failure") as failure,
        patch.object(settings, "_clear_rebuild_state") as clear,
        patch.object(settings, "backfill_reference_detail_no_bulk") as backfill,
    ):
        settings.run_historical_gl_rebuild_job("r1")
    db.rollback.assert_called_once_with()
    failure.assert_called_once_with(state)
    clear.assert_called_once_with("r1")
    backfill.assert_not_called()
    preview: settings.RebuildPreview = {
        "filters": {
            "company": "K",
            "whole_history": True,
            "from_posting_date": None,
            "to_posting_date": None,
        },
        "total_submitted_vouchers": 0,
        "eligible": {
            "count": 0,
            "summary_reason": "",
            "samples": [],
            "groups": [],
            "items": [],
        },
        "blocked": {
            "count": 0,
            "summary_reason": "",
            "samples": [],
            "groups": [],
            "items": [],
        },
        "already_correct": {
            "count": 0,
            "summary_reason": "",
            "samples": [],
            "groups": [],
            "items": [],
        },
    }
    cache = MagicMock(get_value=MagicMock(return_value=None))
    with (
        patch.object(settings.frappe, "only_for"),
        patch.object(settings.frappe, "cache", cache),
        patch.object(
            settings, "get_validated_rebuild_filters", return_value=preview["filters"]
        ),
        patch.object(settings, "build_rebuild_preview", return_value=preview),
        patch.object(settings, "_", side_effect=str),
        patch.object(settings.frappe, "throw", side_effect=_raise),
        patch.object(settings.frappe, "enqueue") as enqueue,
        pytest.raises(frappe.ValidationError, match="No eligible"),
    ):
        settings.enqueue_historical_gl_rebuild()
    enqueue.assert_not_called()
