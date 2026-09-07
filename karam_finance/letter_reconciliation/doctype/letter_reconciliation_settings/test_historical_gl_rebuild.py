"""Tests for Letter Reconciliation Settings and historical rebuild helpers."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from erpnext.accounts.doctype.gl_entry import gl_entry as gl_entry_module
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


class TestHistoricalGLRebuild(FrappeTestCase):
    """Regression tests for historical rebuild behaviour."""

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
                "account_samples": list[str](),
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
            self.assertRaises(frappe.ValidationError),
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
            self.assertRaises(frappe.ValidationError),
        ):
            rebuild.rebuild_single_voucher(
                "JV-404",
                reference_detail_backfilled=True,
            )
