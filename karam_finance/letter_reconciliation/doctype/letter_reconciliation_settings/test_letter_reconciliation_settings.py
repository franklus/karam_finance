"""Tests for Letter Reconciliation Settings and historical rebuild helpers."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock, patch

import frappe
from erpnext.accounts.doctype.gl_entry import gl_entry as gl_entry_module
from frappe.tests.utils import FrappeTestCase

from karam_finance.letter_reconciliation.utils.doc_events import (
    _is_merge_prevention_enabled,
    gl_entry_before_insert,
    je_before_submit,
    journal_entry_on_update_after_submit,
)

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


class TestLetterReconciliationSettings(FrappeTestCase):
    """Test cases for Letter Reconciliation Settings."""

    def test_default_toggle_is_off(self) -> None:
        """The prevent_gl_merge field metadata should default to off (0)."""
        field = frappe.get_meta("Letter Reconciliation Settings").get_field(
            "prevent_gl_merge"
        )
        assert field.default in (0, "0", None)

    def test_helper_returns_false_when_off(self) -> None:
        """_is_merge_prevention_enabled returns False when off."""
        settings = frappe.get_single("Letter Reconciliation Settings")
        settings.prevent_gl_merge = 0
        settings.save(ignore_permissions=True)

        assert not _is_merge_prevention_enabled()

    def test_helper_returns_true_when_on(self) -> None:
        """_is_merge_prevention_enabled returns True when on."""
        settings = frappe.get_single("Letter Reconciliation Settings")
        settings.prevent_gl_merge = 1
        settings.save(ignore_permissions=True)

        assert _is_merge_prevention_enabled()

        settings.prevent_gl_merge = 0
        settings.save(ignore_permissions=True)

    def test_gl_entry_before_insert_gated(self) -> None:
        """gl_entry_before_insert returns early when off."""

        class _Doc:
            voucher_type = "Journal Entry"
            voucher_no = "JV-00001"
            account = "Test Account"
            letter = ""

        doc = _Doc()

        with patch(_GUARD_PATH, return_value=False):
            gl_entry_before_insert(doc)

        assert doc.letter == ""

    def test_je_before_submit_gated(self) -> None:
        """je_before_submit returns early when off."""
        row = MagicMock()
        row.reference_detail_no = None
        row.name = "row-001"

        doc = MagicMock()
        doc.accounts = [row]

        with patch(_GUARD_PATH, return_value=False):
            je_before_submit(doc)

        assert row.reference_detail_no is None

    def test_journal_entry_on_update_after_submit_gated(
        self,
    ) -> None:
        """journal_entry_on_update_after_submit returns early when off."""
        doc = MagicMock()
        doc.name = "JV-00001"

        with patch(_GUARD_PATH, return_value=False) as mock_check:
            journal_entry_on_update_after_submit(doc)
            mock_check.assert_called_once()

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
                        "account_samples": [],
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
                        "account_samples": [],
                        "voucher_samples": ["JV-0002"],
                    }
                ],
                "items": [{"voucher_no": "JV-0002"}],
            },
            "already_correct": {
                "count": 1,
                "summary_reason": "",
                "samples": ["JV-0003"],
                "groups": [],
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
                        "account_samples": [],
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
                        "account_samples": [],
                        "voucher_samples": ["JV-0002"],
                    }
                ],
            },
            "already_correct": {
                "count": 1,
                "summary_reason": "",
                "samples": ["JV-0003"],
                "groups": [],
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
                        "groups": [],
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
                },
            ),
            patch.object(frappe.cache, "set_value") as mock_cache_set,
            patch(f"{_SETTINGS_MODULE}._save_rebuild_state") as mock_save_state,
            patch(f"{_SETTINGS_MODULE}._enqueue_rebuild_batch") as mock_enqueue_batch,
        ):
            result = settings_module.enqueue_historical_gl_rebuild()

        assert "progress_event" in result
        assert "done_event" in result
        mock_cache_set.assert_called_once()
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
                },
            ),
            self.assertRaises(frappe.ValidationError),  # noqa: PT027
        ):
            settings_module.enqueue_historical_gl_rebuild()

    def test_retired_split_endpoints_raise(self) -> None:
        """Legacy split endpoints must stay blocked."""
        with self.assertRaises(frappe.ValidationError):  # noqa: PT027
            settings_module.enqueue_gl_entry_migration()

        with self.assertRaises(frappe.ValidationError):  # noqa: PT027
            settings_module.run_gl_split_diagnostic()

    def test_build_rebuild_preview_classifies_subset(self) -> None:
        """Preview should split vouchers into eligible, blocked, and correct."""
        with (
            patch(
                f"{_REBUILD_MODULE}._get_subset_voucher_rows",
                return_value=[
                    {
                        "voucher_no": "JV-ELIG",
                        "posting_date": "2026-01-10",
                        "missing_detail_rows": 2,
                        "active_gl_rows": 2,
                    },
                    {
                        "voucher_no": "JV-BLOCK",
                        "posting_date": "2024-01-10",
                        "missing_detail_rows": 1,
                        "active_gl_rows": 2,
                    },
                    {
                        "voucher_no": "JV-OK",
                        "posting_date": "2026-01-11",
                        "missing_detail_rows": 0,
                        "active_gl_rows": 2,
                    },
                ],
            ),
            patch(
                f"{_REBUILD_MODULE}._get_subset_voucher_account_map",
                return_value={
                    "JV-ELIG": ["11110000 - Cash - WS"],
                    "JV-BLOCK": ["22220000 - Bank - WS"],
                    "JV-OK": ["33330000 - Misc - WS"],
                },
            ),
            patch(
                f"{_REBUILD_MODULE}._get_latest_closed_period_end",
                return_value="2024-12-31",
            ),
            patch(
                f"{_REBUILD_MODULE}._is_journal_entry_repost_allowed", return_value=True
            ),
        ):
            preview = rebuild.build_rebuild_preview(
                {
                    "company": "_Test Company",
                    "whole_history": False,
                    "from_posting_date": "2024-01-01",
                    "to_posting_date": "2026-01-31",
                }
            )

        assert preview["eligible"]["count"] == 1
        assert preview["blocked"]["count"] == 1
        assert preview["already_correct"]["count"] == 1
        assert preview["eligible"]["summary_reason"] == "Ready to repost"
        assert preview["eligible"]["samples"] == ["JV-ELIG"]
        assert (
            preview["blocked"]["summary_reason"]
            == "Voucher falls within a closed fiscal year."
        )
        assert preview["blocked"]["samples"] == ["JV-BLOCK"]
        assert preview["already_correct"]["summary_reason"] == ""
        assert preview["already_correct"]["samples"] == ["JV-OK"]

    def test_build_rebuild_preview_allows_invalid_party_accounts(self) -> None:
        """Preview should keep party/account mismatches eligible for repost bypass."""
        with (
            patch(
                f"{_REBUILD_MODULE}._get_subset_voucher_rows",
                return_value=[
                    {
                        "voucher_no": "JV-BAD",
                        "posting_date": "2026-01-10",
                        "missing_detail_rows": 2,
                        "active_gl_rows": 2,
                    }
                ],
            ),
            patch(
                f"{_REBUILD_MODULE}._get_subset_voucher_account_map",
                return_value={"JV-BAD": ["34552000 - TVA Recuperable a 20 % - WS"]},
            ),
            patch(
                f"{_REBUILD_MODULE}._get_latest_closed_period_end",
                return_value=None,
            ),
            patch(
                f"{_REBUILD_MODULE}._is_journal_entry_repost_allowed", return_value=True
            ),
        ):
            preview = rebuild.build_rebuild_preview(
                {
                    "company": "_Test Company",
                    "whole_history": True,
                    "from_posting_date": None,
                    "to_posting_date": None,
                }
            )

        assert preview["eligible"]["count"] == 1
        assert preview["blocked"]["count"] == 0
        assert preview["eligible"]["summary_reason"] == "Ready to repost"
        assert preview["eligible"]["samples"] == ["JV-BAD"]

    def test_build_rebuild_preview_allows_invalid_balance_type(self) -> None:
        """Preview should keep balance-side mismatches eligible for repost bypass."""
        with (
            patch(
                f"{_REBUILD_MODULE}._get_subset_voucher_rows",
                return_value=[
                    {
                        "voucher_no": "JV-BAL",
                        "posting_date": "2026-01-10",
                        "missing_detail_rows": 1,
                        "active_gl_rows": 1,
                    }
                ],
            ),
            patch(
                f"{_REBUILD_MODULE}._get_subset_voucher_account_map",
                return_value={"JV-BAL": ["34552900 - TVA récupérable a 9% - WS"]},
            ),
            patch(
                f"{_REBUILD_MODULE}._get_latest_closed_period_end",
                return_value=None,
            ),
            patch(
                f"{_REBUILD_MODULE}._is_journal_entry_repost_allowed", return_value=True
            ),
        ):
            preview = rebuild.build_rebuild_preview(
                {
                    "company": "_Test Company",
                    "whole_history": True,
                    "from_posting_date": None,
                    "to_posting_date": None,
                }
            )

        assert preview["eligible"]["count"] == 1
        assert preview["blocked"]["count"] == 0
        assert preview["eligible"]["summary_reason"] == "Ready to repost"
        assert preview["eligible"]["samples"] == ["JV-BAL"]

    def test_get_validated_rebuild_filters_allows_whole_history_without_dates(
        self,
    ) -> None:
        """Whole-history mode should not require explicit posting dates."""
        fake_settings = SimpleNamespace(
            rebuild_company="_Test Company",
            rebuild_whole_history=1,
            rebuild_from_posting_date=None,
            rebuild_to_posting_date=None,
        )

        with patch(f"{_REBUILD_MODULE}.frappe.get_single", return_value=fake_settings):
            filters = rebuild.get_validated_rebuild_filters()

        assert filters == {
            "company": "_Test Company",
            "whole_history": True,
            "from_posting_date": None,
            "to_posting_date": None,
        }

    def test_get_subset_voucher_rows_omits_date_filter_for_whole_history(self) -> None:
        """Whole-history mode should query the full company history."""
        with patch(f"{_REBUILD_MODULE}.frappe.db.sql", return_value=[]) as mock_sql:
            rebuild._get_subset_voucher_rows(
                {
                    "company": "_Test Company",
                    "whole_history": True,
                    "from_posting_date": None,
                    "to_posting_date": None,
                }
            )

        query = mock_sql.call_args.args[0]
        values = mock_sql.call_args.args[1]
        assert "je.posting_date BETWEEN" not in query
        assert values["company"] == "_Test Company"

    def test_subset_voucher_account_map_respects_whole_history(self) -> None:
        """Whole-history account-map scan should not require a date filter."""
        with patch(f"{_REBUILD_MODULE}.frappe.db.sql", return_value=[]) as mock_sql:
            rebuild._get_subset_voucher_account_map(
                {
                    "company": "_Test Company",
                    "whole_history": True,
                    "from_posting_date": None,
                    "to_posting_date": None,
                }
            )

        query = mock_sql.call_args.args[0]
        values = mock_sql.call_args.args[1]
        assert "je.posting_date BETWEEN" not in query
        assert values["company"] == "_Test Company"

    def test_get_subset_voucher_rows_normalises_posting_date_to_string(self) -> None:
        """Subset row snapshots should serialise posting dates to plain strings."""
        with patch(
            f"{_REBUILD_MODULE}.frappe.db.sql",
            return_value=[
                {
                    "voucher_no": "JV-0001",
                    "posting_date": date(2026, 1, 10),
                    "missing_detail_rows": 1,
                    "active_gl_rows": 2,
                }
            ],
        ):
            rows = rebuild._get_subset_voucher_rows(
                {
                    "company": "_Test Company",
                    "whole_history": True,
                    "from_posting_date": None,
                    "to_posting_date": None,
                }
            )

        assert rows == [
            {
                "voucher_no": "JV-0001",
                "posting_date": "2026-01-10",
                "missing_detail_rows": 1,
                "active_gl_rows": 2,
            }
        ]

    def test_build_bucket_formats_shared_reason_for_heading(self) -> None:
        """Shared bucket reasons should move to the section heading."""
        bucket = rebuild._build_bucket(
            [
                {
                    "voucher_no": "JV-0001",
                    "posting_date": "2026-03-10",
                    "reason": "Ready to repost",
                }
            ]
        )

        assert bucket["summary_reason"] == "Ready to repost"
        assert bucket["samples"] == ["JV-0001"]
        assert bucket["groups"] == [
            {
                "reason": "Ready to repost",
                "voucher_count": 1,
                "unique_account_count": 0,
                "account_samples": [],
                "voucher_samples": ["JV-0001"],
            }
        ]

    def test_build_bucket_uses_item_accounts_for_grouping(self) -> None:
        """Preview groups should include explicit voucher account lists."""
        bucket = rebuild._build_bucket(
            [
                {
                    "voucher_no": "JV-0001",
                    "posting_date": "2026-03-10",
                    "reason": "Ready to repost",
                    "accounts": [
                        "11110000 - Cash - WS",
                        "22220000 - Bank - WS",
                    ],
                },
                {
                    "voucher_no": "JV-0002",
                    "posting_date": "2026-03-11",
                    "reason": "Ready to repost",
                    "accounts": [
                        "22220000 - Bank - WS",
                    ],
                },
            ]
        )

        assert bucket["groups"] == [
            {
                "reason": "Ready to repost",
                "voucher_count": 2,
                "unique_account_count": 2,
                "account_samples": [
                    "11110000 - Cash - WS",
                    "22220000 - Bank - WS",
                ],
                "voucher_samples": ["JV-0001", "JV-0002"],
            }
        ]

    def test_build_bucket_omits_empty_reason_text(self) -> None:
        """Already-correct samples should show only the voucher label."""
        bucket = rebuild._build_bucket(
            [
                {
                    "voucher_no": "JV-0002",
                    "posting_date": "2026-03-10",
                    "reason": "",
                }
            ]
        )

        assert bucket["summary_reason"] == ""
        assert bucket["samples"] == ["JV-0002"]
        assert bucket["groups"] == []

    def test_build_bucket_clears_summary_reason_for_mixed_reasons(self) -> None:
        """Mixed bucket reasons should not display a misleading shared label."""
        bucket = rebuild._build_bucket(
            [
                {
                    "voucher_no": "JV-0003",
                    "posting_date": "2026-03-10",
                    "reason": "Voucher falls within a closed fiscal year.",
                },
                {
                    "voucher_no": "JV-0004",
                    "posting_date": "2026-03-11",
                    "reason": (
                        "Journal Entry is not enabled in Repost Accounting Ledger "
                        "Settings."
                    ),
                },
            ]
        )

        assert bucket["summary_reason"] == ""
        assert bucket["samples"] == ["JV-0003", "JV-0004"]

    def test_build_failure_groups_counts_unique_accounts(self) -> None:
        """Completion summaries should group repeated failures by reason and account."""
        groups = settings_module._build_failure_groups(
            [
                {
                    "voucher_no": "20220101-AN01",
                    "reason": (
                        "Party Type and Party can only be set for Receivable / "
                        "Payable account<br><br>34530000 - Acomptes sur impôts"
                    ),
                },
                {
                    "voucher_no": "20220102-ACH006",
                    "reason": (
                        "Party Type and Party can only be set for Receivable / "
                        "Payable account<br><br>34552000 - TVA Recuperable a 20 % - WS"
                    ),
                },
                {
                    "voucher_no": "20220102-ACH013",
                    "reason": (
                        "Party Type and Party can only be set for Receivable / "
                        "Payable account<br><br>34552000 - TVA Recuperable a 20 % - WS"
                    ),
                },
            ]
        )

        assert groups == [
            {
                "reason": (
                    "Party Type and Party can only be set for Receivable / "
                    "Payable account"
                ),
                "voucher_count": 3,
                "unique_account_count": 2,
                "account_samples": [
                    "34530000 - Acomptes sur impôts",
                    "34552000 - TVA Recuperable a 20 % - WS",
                ],
                "accounts": [
                    "34530000 - Acomptes sur impôts",
                    "34552000 - TVA Recuperable a 20 % - WS",
                ],
                "voucher_samples": [
                    "20220101-AN01",
                    "20220102-ACH006",
                    "20220102-ACH013",
                ],
                "vouchers": [
                    "20220101-AN01",
                    "20220102-ACH006",
                    "20220102-ACH013",
                ],
            }
        ]

    def test_build_failure_groups_extracts_validation_error_from_traceback(
        self,
    ) -> None:
        """Traceback-heavy failures should collapse to the root validation reason."""
        groups = settings_module._build_failure_groups(
            [
                {
                    "voucher_no": "ACH000012",
                    "reason": (
                        "Traceback (most recent call last):\n"
                        "  File "
                        '"apps/erpnext/erpnext/accounts/doctype/gl_entry/'
                        'gl_entry.py", line 321, in validate_balance_type\n'
                        "frappe.exceptions.ValidationError: "
                        "Balance for Account 34552900 - TVA récupérable a 9% - WS - WS "
                        "must always be Debit"
                    ),
                }
            ]
        )

        assert groups == [
            {
                "reason": "Account balance must always be Debit",
                "voucher_count": 1,
                "unique_account_count": 1,
                "account_samples": [
                    "34552900 - TVA récupérable a 9% - WS - WS",
                ],
                "accounts": [
                    "34552900 - TVA récupérable a 9% - WS - WS",
                ],
                "voucher_samples": ["ACH000012"],
                "vouchers": ["ACH000012"],
            }
        ]

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
            "eligible_vouchers": [],
            "next_index": 0,
            "rebuilt_count": 0,
            "blocked_count": 0,
            "already_correct_count": 0,
            "failures": [],
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
            "failures": [],
        }

        with (
            patch(f"{_SETTINGS_MODULE}._REBUILD_BATCH_SIZE", 2),
            patch(f"{_SETTINGS_MODULE}._get_rebuild_state", return_value=state),
            patch(
                f"{_SETTINGS_MODULE}.backfill_reference_detail_no_bulk"
            ) as mock_backfill_bulk,
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
        mock_backfill_bulk.assert_called_once_with(["JV-0001", "JV-0002"])
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
        assert settings_module._REBUILD_CACHE_KEY in deleted_keys
        assert settings_module._get_rebuild_state_key("run-001") in deleted_keys

    def test_run_historical_gl_rebuild_job_bulk_backfills_before_savepoint(
        self,
    ) -> None:
        """Batch reference-detail backfill should happen before voucher savepoints."""
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
            "failures": [],
        }

        with (
            patch(f"{_SETTINGS_MODULE}._get_rebuild_state", return_value=state),
            patch(
                f"{_SETTINGS_MODULE}.backfill_reference_detail_no_bulk"
            ) as mock_backfill_bulk,
            patch(
                f"{_SETTINGS_MODULE}.rebuild_single_voucher",
                side_effect=frappe.ValidationError("blocked"),
            ) as mock_rebuild,
            patch(
                f"{_SETTINGS_MODULE}.frappe.db.savepoint",
                side_effect=lambda _name: assert_backfill_called(mock_backfill_bulk),
            ) as mock_savepoint,
            patch(f"{_SETTINGS_MODULE}.frappe.db.rollback"),
            patch(f"{_SETTINGS_MODULE}.frappe.log_error"),
            patch(f"{_SETTINGS_MODULE}._publish_progress"),
            patch(f"{_SETTINGS_MODULE}._save_rebuild_state"),
            patch(f"{_SETTINGS_MODULE}._finalise_rebuild_run"),
        ):
            settings_module.run_historical_gl_rebuild_job("run-001")

        mock_backfill_bulk.assert_called_once_with(["JV-0001"])
        mock_savepoint.assert_called_once()
        mock_rebuild.assert_called_once_with(
            "JV-0001",
            reference_detail_backfilled=True,
        )

    def test_rebuild_single_voucher_uses_repost_path(self) -> None:
        """Single-voucher rebuild should cancel and recreate via JE methods."""
        original_validate_balance_type = gl_entry_module.validate_balance_type

        def assert_balance_check_is_temporarily_bypassed(*_args: object) -> None:
            assert (
                gl_entry_module.validate_balance_type
                is not original_validate_balance_type
            )

        fake_doc = SimpleNamespace(
            docstatus=1,
            validate_for_repost=MagicMock(),
            make_gl_entries=MagicMock(
                side_effect=assert_balance_check_is_temporarily_bypassed
            ),
            accounts=[],
            name="JV-0001",
        )

        with (
            patch(f"{_REBUILD_MODULE}.backfill_reference_detail_no") as mock_backfill,
            patch(f"{_REBUILD_MODULE}.frappe.get_doc", return_value=fake_doc),
            patch(f"{_REBUILD_MODULE}.sync_journal_entry_gl_letters") as mock_sync,
        ):
            rebuild.rebuild_single_voucher("JV-0001")

        mock_backfill.assert_called_once_with("JV-0001")
        fake_doc.validate_for_repost.assert_called_once_with()
        assert fake_doc.make_gl_entries.call_count == 2
        first_call = fake_doc.make_gl_entries.call_args_list[0]
        second_call = fake_doc.make_gl_entries.call_args_list[1]
        assert first_call.args == (1,)
        assert second_call.args == ()
        mock_sync.assert_called_once_with(fake_doc, ignore_setting=True)
        assert gl_entry_module.validate_balance_type is original_validate_balance_type

    def test_rebuild_single_voucher_rejects_non_submitted_doc(self) -> None:
        """Rebuild should stop if the voucher is no longer submitted."""
        fake_doc = SimpleNamespace(docstatus=2)

        with (
            patch(f"{_REBUILD_MODULE}.frappe.get_doc", return_value=fake_doc),
            self.assertRaises(frappe.ValidationError),  # noqa: PT027
        ):
            rebuild.rebuild_single_voucher(
                "JV-0001",
                reference_detail_backfilled=True,
            )

    def test_rebuild_single_voucher_raises_when_doc_is_missing(self) -> None:
        """Rebuild should raise a user-facing error when JE no longer exists."""
        with (
            patch(
                f"{_REBUILD_MODULE}.frappe.get_doc",
                side_effect=frappe.DoesNotExistError,
            ),
            self.assertRaises(frappe.ValidationError),  # noqa: PT027
        ):
            rebuild.rebuild_single_voucher(
                "JV-404",
                reference_detail_backfilled=True,
            )


def assert_backfill_called(mock_backfill: MagicMock) -> None:
    """Assert that backfill has already run before the savepoint is created."""
    assert mock_backfill.called
