"""Tests for Letter Reconciliation Settings and historical rebuild helpers."""

from __future__ import annotations

from contextlib import nullcontext
from typing import cast, override
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from . import historical_gl_rebuild as rebuild
from . import letter_reconciliation_settings as settings_module

_GUARD_PATH = (
    "karam_finance.letter_reconciliation.utils.doc_events._is_merge_prevention_enabled"
)
_SETTINGS_MODULE = (
    "karam_finance.letter_reconciliation.doctype.letter_reconciliation_settings"
    ".letter_reconciliation_settings"
)
_REBUILD_MODULE = (
    "karam_finance.letter_reconciliation.doctype.letter_reconciliation_settings"
    ".historical_gl_rebuild"
)


class TestHistoricalGLRebuildJobs(FrappeTestCase):
    """Regression tests for historical rebuild behaviour."""

    @override
    def setUp(self) -> None:
        super().setUp()
        # Isolate the existing job orchestration contracts from native authorisation.
        self.enterContext(patch.object(settings_module, "check_rebuild_scope"))
        self.enterContext(patch.object(settings_module, "check_rebuild_journals"))
        self.enterContext(patch.object(settings_module, "check_rebuild_batch"))
        self.enterContext(patch.object(settings_module, "as_rebuild_user", nullcontext))
        self.enterContext(
            patch.object(
                settings_module, "rebuild_execution", lambda: nullcontext(True)
            )
        )
        # Native isolation tests verify real Redis ownership; these retain fake job contracts.
        self.enterContext(
            patch.object(settings_module, "acquire_rebuild_guard", return_value=True)
        )
        self.enterContext(
            patch.object(settings_module, "renew_rebuild_guard", return_value=True)
        )
        self.release_guard = self.enterContext(
            patch.object(settings_module, "release_rebuild_guard")
        )

    def test_preview_historical_gl_rebuild_returns_summary(self) -> None:
        """The preview endpoint should return the helper summary."""
        preview = {
            "filters": {
                "company": "_Test Company",
                "whole_history": False,
                "from_posting_date": "2026-01-01",
                "to_posting_date": "2026-01-31",
            },
            "total_submitted_vouchers": 3,
            "eligible": {
                "count": 1,
                "summary_reason": "Ready to repost",
                "samples": ["JV-0001"],
                "groups": [
                    {
                        "reason": "Ready to repost",
                        "voucher_count": 1,
                        "unique_account_count": 0,
                        "account_samples": list[str](),
                        "voucher_samples": ["JV-0001"],
                    }
                ],
                "items": [{"voucher_no": "JV-0001"}],
            },
            "blocked": {
                "count": 1,
                "summary_reason": "Voucher falls within a closed fiscal year.",
                "samples": ["JV-0002"],
                "groups": [
                    {
                        "reason": "Voucher falls within a closed fiscal year.",
                        "voucher_count": 1,
                        "unique_account_count": 0,
                        "account_samples": list[str](),
                        "voucher_samples": ["JV-0002"],
                    }
                ],
                "items": [{"voucher_no": "JV-0002"}],
            },
            "already_correct": {
                "count": 1,
                "summary_reason": "",
                "samples": ["JV-0003"],
                "groups": list[rebuild.ReasonSummaryGroup](),
                "items": [{"voucher_no": "JV-0003"}],
            },
        }
        expected = {
            "filters": preview["filters"],
            "total_submitted_vouchers": 3,
            "eligible": {
                "count": 1,
                "summary_reason": "Ready to repost",
                "samples": ["JV-0001"],
                "groups": [
                    {
                        "reason": "Ready to repost",
                        "voucher_count": 1,
                        "unique_account_count": 0,
                        "account_samples": list[str](),
                        "voucher_samples": ["JV-0001"],
                    }
                ],
            },
            "blocked": {
                "count": 1,
                "summary_reason": "Voucher falls within a closed fiscal year.",
                "samples": ["JV-0002"],
                "groups": [
                    {
                        "reason": "Voucher falls within a closed fiscal year.",
                        "voucher_count": 1,
                        "unique_account_count": 0,
                        "account_samples": list[str](),
                        "voucher_samples": ["JV-0002"],
                    }
                ],
            },
            "already_correct": {
                "count": 1,
                "summary_reason": "",
                "samples": ["JV-0003"],
                "groups": list[rebuild.ReasonSummaryGroup](),
            },
        }

        with (
            patch(
                f"{_SETTINGS_MODULE}.get_validated_rebuild_filters",
                return_value={
                    "company": "_Test Company",
                    "whole_history": False,
                    "from_posting_date": "2026-01-01",
                    "to_posting_date": "2026-01-31",
                },
            ),
            patch(
                f"{_SETTINGS_MODULE}.build_rebuild_preview",
                return_value=preview,
            ),
            patch(f"{_SETTINGS_MODULE}._update_preview_audit") as mock_audit,
        ):
            result = settings_module.preview_historical_gl_rebuild()

        assert result == expected
        mock_audit.assert_called_once()

    def test_enqueue_historical_gl_rebuild_queues_background_job(self) -> None:
        """Eligible subsets should enqueue the new rebuild worker."""
        filters = {
            "company": "_Test Company",
            "whole_history": False,
            "from_posting_date": "2026-01-01",
            "to_posting_date": "2026-01-31",
        }

        with (
            patch.object(frappe.cache, "get_value", return_value=False),
            patch(
                f"{_SETTINGS_MODULE}.get_validated_rebuild_filters",
                return_value=filters,
            ),
            patch(
                f"{_SETTINGS_MODULE}.build_rebuild_preview",
                return_value={
                    "filters": filters,
                    "total_submitted_vouchers": 1,
                    "eligible": {
                        "count": 1,
                        "summary_reason": "Ready to repost",
                        "samples": ["JV-0001"],
                        "groups": list[rebuild.ReasonSummaryGroup](),
                        "items": [
                            {
                                "voucher_no": "JV-0001",
                                "posting_date": "2026-01-10",
                                "reason": "Ready to repost",
                            }
                        ],
                    },
                    "blocked": {
                        "count": 0,
                        "summary_reason": "",
                        "samples": [],
                        "groups": list[rebuild.ReasonSummaryGroup](),
                        "items": [],
                    },
                    "already_correct": {
                        "count": 0,
                        "summary_reason": "",
                        "samples": [],
                        "groups": list[rebuild.ReasonSummaryGroup](),
                        "items": [],
                    },
                },
            ),
            patch.object(frappe.cache, "set_value") as mock_cache_set,
            patch(f"{_SETTINGS_MODULE}._save_rebuild_state") as mock_save_state,
            patch(f"{_SETTINGS_MODULE}._enqueue_rebuild_batch") as mock_enqueue_batch,
        ):
            result = settings_module.enqueue_historical_gl_rebuild()

        assert "progress_event" in result
        assert "done_event" in result
        mock_cache_set.assert_not_called()
        mock_save_state.assert_called_once()
        run_state = mock_save_state.call_args.args[0]
        assert run_state["filters"] == filters
        assert run_state["total_submitted_vouchers"] == 1
        assert run_state["eligible_vouchers"] == ["JV-0001"]
        mock_enqueue_batch.assert_called_once_with(run_state["run_id"])

    def test_enqueue_historical_gl_rebuild_rejects_empty_subset(self) -> None:
        """Empty eligible subsets should not enqueue the rebuild."""
        with (
            patch.object(frappe.cache, "get_value", return_value=False),
            patch(
                f"{_SETTINGS_MODULE}.get_validated_rebuild_filters",
                return_value={
                    "company": "_Test Company",
                    "whole_history": False,
                    "from_posting_date": "2026-01-01",
                    "to_posting_date": "2026-01-31",
                },
            ),
            patch(
                f"{_SETTINGS_MODULE}.build_rebuild_preview",
                return_value={
                    "filters": {
                        "company": "_Test Company",
                        "whole_history": False,
                        "from_posting_date": "2026-01-01",
                        "to_posting_date": "2026-01-31",
                    },
                    "total_submitted_vouchers": 0,
                    "eligible": {
                        "count": 0,
                        "summary_reason": "",
                        "samples": [],
                        "groups": list[rebuild.ReasonSummaryGroup](),
                        "items": [],
                    },
                    "blocked": {
                        "count": 0,
                        "summary_reason": "",
                        "samples": [],
                        "groups": list[rebuild.ReasonSummaryGroup](),
                        "items": [],
                    },
                    "already_correct": {
                        "count": 0,
                        "summary_reason": "",
                        "samples": [],
                        "groups": list[rebuild.ReasonSummaryGroup](),
                        "items": [],
                    },
                },
            ),
            self.assertRaises(frappe.ValidationError),
        ):
            settings_module.enqueue_historical_gl_rebuild()

    def test_retired_split_endpoints_raise(self) -> None:
        """Legacy split endpoints must stay blocked."""
        with self.assertRaises(frappe.ValidationError):
            settings_module.enqueue_gl_entry_migration()

        with self.assertRaises(frappe.ValidationError):
            settings_module.run_gl_split_diagnostic()

    def test_finalise_rebuild_run_publishes_completion_summary(self) -> None:
        """Completed rebuilds should publish grouped failure details."""
        state = {
            "run_id": "run-001",
            "user": "test@example.com",
            "progress_event": "progress",
            "done_event": "done",
            "filters": {
                "company": "_Test Company",
                "whole_history": True,
                "from_posting_date": None,
                "to_posting_date": None,
            },
            "total_submitted_vouchers": 3,
            "eligible_vouchers": ["JV-0001", "JV-0002"],
            "next_index": 2,
            "rebuilt_count": 1,
            "blocked_count": 0,
            "already_correct_count": 0,
            "failures": [{"voucher_no": "JV-0002", "reason": "Boom"}],
        }

        with (
            patch(f"{_SETTINGS_MODULE}._publish_progress"),
            patch(f"{_SETTINGS_MODULE}._update_rebuild_audit"),
            patch(f"{_SETTINGS_MODULE}._clear_rebuild_state"),
            patch(f"{_SETTINGS_MODULE}.frappe.publish_realtime") as mock_publish,
        ):
            settings_module._finalise_rebuild_run(
                cast("settings_module.RebuildRunState", state)
            )

        realtime_payload = mock_publish.call_args.args[1]
        assert realtime_payload["status"] == "partial_success"
        assert realtime_payload["failed_count"] == 1
        assert realtime_payload["rebuilt_count"] == 1

    def test_finalise_rebuild_run_marks_all_failed_as_partial_success(self) -> None:
        """All-failed reruns should still show grouped completion details."""
        state = {
            "run_id": "run-002",
            "user": "test@example.com",
            "progress_event": "progress",
            "done_event": "done",
            "filters": {
                "company": "_Test Company",
                "whole_history": True,
                "from_posting_date": None,
                "to_posting_date": None,
            },
            "total_submitted_vouchers": 2,
            "eligible_vouchers": ["JV-0001", "JV-0002"],
            "next_index": 2,
            "rebuilt_count": 0,
            "blocked_count": 0,
            "already_correct_count": 0,
            "failures": [
                {
                    "voucher_no": "JV-0001",
                    "reason": "Balance for Account X must always be Debit",
                },
                {
                    "voucher_no": "JV-0002",
                    "reason": "Balance for Account X must always be Debit",
                },
            ],
        }

        with (
            patch(f"{_SETTINGS_MODULE}._publish_progress"),
            patch(f"{_SETTINGS_MODULE}._update_rebuild_audit"),
            patch(f"{_SETTINGS_MODULE}._clear_rebuild_state"),
            patch(f"{_SETTINGS_MODULE}.frappe.publish_realtime") as mock_publish,
        ):
            settings_module._finalise_rebuild_run(
                cast("settings_module.RebuildRunState", state)
            )

        realtime_payload = mock_publish.call_args.args[1]
        assert realtime_payload["status"] == "partial_success"
        assert realtime_payload["rebuilt_count"] == 0
        assert realtime_payload["failed_count"] == 2

    def test_publish_rebuild_failure_uses_fatal_error_status(self) -> None:
        """Fatal runner errors should be clearly marked as fatal_error."""
        state = {
            "run_id": "run-003",
            "user": "test@example.com",
            "progress_event": "progress",
            "done_event": "done",
            "filters": {
                "company": "_Test Company",
                "whole_history": True,
                "from_posting_date": None,
                "to_posting_date": None,
            },
            "total_submitted_vouchers": 0,
            "eligible_vouchers": list[str](),
            "next_index": 0,
            "rebuilt_count": 0,
            "blocked_count": 0,
            "already_correct_count": 0,
            "failures": list[settings_module.RebuildFailure](),
        }

        with (
            patch(f"{_SETTINGS_MODULE}._update_rebuild_audit"),
            patch(f"{_SETTINGS_MODULE}.frappe.publish_realtime") as mock_publish,
        ):
            settings_module._publish_rebuild_failure(
                cast("settings_module.RebuildRunState", state)
            )

        realtime_payload = mock_publish.call_args.args[1]
        assert realtime_payload["status"] == "fatal_error"

    def test_run_historical_gl_rebuild_job_queues_next_batch(self) -> None:
        """Long rebuilds should process a batch and enqueue the next one."""
        state = {
            "run_id": "run-001",
            "user": "test@example.com",
            "progress_event": "progress",
            "done_event": "done",
            "filters": {
                "company": "_Test Company",
                "whole_history": True,
                "from_posting_date": None,
                "to_posting_date": None,
            },
            "total_submitted_vouchers": 3,
            "eligible_vouchers": ["JV-0001", "JV-0002", "JV-0003"],
            "next_index": 0,
            "rebuilt_count": 0,
            "blocked_count": 0,
            "already_correct_count": 0,
            "failures": list[settings_module.RebuildFailure](),
        }

        with (
            patch(f"{_SETTINGS_MODULE}._REBUILD_BATCH_SIZE", 2),
            patch(f"{_SETTINGS_MODULE}._get_rebuild_state", return_value=state),
            patch(f"{_SETTINGS_MODULE}.rebuild_single_voucher"),
            patch(f"{_SETTINGS_MODULE}._publish_progress"),
            patch(f"{_SETTINGS_MODULE}._save_rebuild_state") as mock_save_state,
            patch(f"{_SETTINGS_MODULE}._enqueue_rebuild_batch") as mock_enqueue_batch,
            patch(f"{_SETTINGS_MODULE}._finalise_rebuild_run") as mock_finalise,
            patch(f"{_SETTINGS_MODULE}._commit_rebuild_progress"),
        ):
            settings_module.run_historical_gl_rebuild_job("run-001")

        assert state["next_index"] == 2
        assert state["rebuilt_count"] == 2
        assert mock_save_state.call_count == 2
        mock_enqueue_batch.assert_called_once_with("run-001")
        mock_finalise.assert_not_called()

    def test_run_historical_gl_rebuild_job_clears_orphaned_lock(self) -> None:
        """Missing cached state should release the stale rebuild lock."""
        with (
            patch(f"{_SETTINGS_MODULE}._get_rebuild_state", return_value=None),
            patch.object(frappe.cache, "get_value", return_value="run-001"),
            patch.object(frappe.cache, "delete_value") as mock_delete,
        ):
            settings_module.run_historical_gl_rebuild_job("run-001")

        deleted_keys = [call.args[0] for call in mock_delete.call_args_list]
        self.release_guard.assert_called_once_with("run-001")
        assert settings_module._get_rebuild_state_key("run-001") in deleted_keys

    def test_run_historical_gl_rebuild_job_rebuilds_inside_savepoint(
        self,
    ) -> None:
        """Each rebuild, including its backfill, starts after its voucher savepoint."""
        state = {
            "run_id": "run-001",
            "user": "test@example.com",
            "progress_event": "progress",
            "done_event": "done",
            "filters": {
                "company": "_Test Company",
                "whole_history": True,
                "from_posting_date": None,
                "to_posting_date": None,
            },
            "total_submitted_vouchers": 1,
            "eligible_vouchers": ["JV-0001"],
            "next_index": 0,
            "rebuilt_count": 0,
            "blocked_count": 0,
            "already_correct_count": 0,
            "failures": list[settings_module.RebuildFailure](),
        }

        def check_savepoint_order(_name: str) -> None:
            assert mock_savepoint.called
            raise frappe.ValidationError("blocked")

        with (
            patch(f"{_SETTINGS_MODULE}._get_rebuild_state", return_value=state),
            patch(
                f"{_SETTINGS_MODULE}.rebuild_single_voucher",
                side_effect=check_savepoint_order,
            ) as mock_rebuild,
            patch(
                f"{_SETTINGS_MODULE}.frappe.db.savepoint",
            ) as mock_savepoint,
            patch(f"{_SETTINGS_MODULE}.frappe.db.rollback"),
            patch(f"{_SETTINGS_MODULE}.frappe.log_error"),
            patch(f"{_SETTINGS_MODULE}._publish_progress"),
            patch(f"{_SETTINGS_MODULE}._save_rebuild_state"),
            patch(f"{_SETTINGS_MODULE}._finalise_rebuild_run"),
        ):
            settings_module.run_historical_gl_rebuild_job("run-001")

        mock_savepoint.assert_called_once()
        mock_rebuild.assert_called_once_with(
            "JV-0001",
        )
