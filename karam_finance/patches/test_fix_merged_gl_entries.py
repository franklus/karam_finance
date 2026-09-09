"""Safety contracts for the merged GL-entry repair patch."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any, cast, override
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

from karam_finance.patches import (
    backfill_gl_letter_from_je_account,
    delete_stale_general_ledger_reporting_currency_report,
    delete_stale_trial_balance_reporting_report,
    disable_general_ledger_karam_auto_total,
)
from karam_finance.patches import (
    fix_merged_gl_entries as module,
)


class TestFixMergedGlEntries(TestCase):
    @override
    def setUp(self) -> None:
        self.frappe = MagicMock()
        self.enterContext(patch.object(module, "frappe", self.frappe))

    def test_sql_backfills_are_limited_to_safe_journal_entry_scope(self) -> None:
        module._backfill_reference_detail_no()
        module._backfill_voucher_detail_no_single_match()
        reference_sql, single_sql = [
            item.args[0] for item in self.frappe.db.sql.call_args_list
        ]
        assert (
            "reference_detail_no IS NULL OR reference_detail_no = ''" in reference_sql
        )
        for predicate in (
            "gl.voucher_type = 'Journal Entry'",
            "gl.voucher_detail_no IS NULL OR gl.voucher_detail_no = ''",
            "HAVING COUNT(*) = 1",
            "IFNULL(gl.party_type, '') = matched.party_type",
            "IFNULL(gl.party, '') = matched.party",
            "IFNULL(gl.cost_center, '') = matched.cost_center",
        ):
            assert predicate in single_sql

    def test_split_driver_does_not_commit_when_no_merged_rows_exist(self) -> None:
        self.frappe.db.sql.return_value = cast("list[object]", [])
        assert module._split_merged_gl_entries() == 0
        self.frappe.db.commit.assert_not_called()

    def test_split_driver_groups_rows_and_commits_only_positive_work(self) -> None:
        self.frappe.db.sql.return_value = [
            {
                "voucher_no": "JE-1",
                "account": "A",
                "party_type": "",
                "party": "",
                "cost_center": "",
                "name": "GL-1",
            },
            {
                "voucher_no": "JE-1",
                "account": "A",
                "party_type": "",
                "party": "",
                "cost_center": "",
                "name": "GL-2",
            },
        ]
        second_group = {
            "voucher_no": "JE-2",
            "account": "B",
            "party_type": "Customer",
            "party": "CUST-1",
            "cost_center": "Main",
            "name": "GL-3",
        }
        self.frappe.db.sql.return_value.append(second_group)
        with patch.object(
            module, "_process_merge_group", side_effect=[2, 1]
        ) as process:
            assert module._split_merged_gl_entries() == 3
        assert process.call_args_list == [
            call(("JE-1", "A", "", "", ""), self.frappe.db.sql.return_value[:2]),
            call(("JE-2", "B", "Customer", "CUST-1", "Main"), [second_group]),
        ]
        self.frappe.db.commit.assert_called_once_with()

    def test_split_driver_does_not_commit_when_groups_do_no_work(self) -> None:
        rows = [
            {
                "voucher_no": "JE-1",
                "account": "A",
                "party_type": "",
                "party": "",
                "cost_center": "",
                "name": "GL-1",
            }
        ]
        self.frappe.db.sql.return_value = rows
        with patch.object(module, "_process_merge_group", return_value=0):
            assert module._split_merged_gl_entries() == 0
        self.frappe.db.commit.assert_not_called()

    def test_single_group_empty_and_letter_fallback_are_safe(self) -> None:
        module._backfill_single_group([{"name": "GL-1"}], [])
        self.frappe.db.set_value.assert_not_called()
        module._backfill_single_group(
            [{"name": "GL-1"}], [{"name": "JEA-1", "letter": None}]
        )
        self.frappe.db.set_value.assert_called_once_with(
            "GL Entry",
            "GL-1",
            {"voucher_detail_no": "JEA-1", "letter": ""},
            update_modified=False,
        )
        self.frappe.db.set_value.reset_mock()
        module._backfill_single_group(
            [{"name": "GL-1"}], [{"name": "JEA-1", "letter": "LETTER-1"}]
        )
        assert self.frappe.db.set_value.call_args.args[2]["letter"] == "LETTER-1"

    def test_delete_merged_rows_accepts_an_empty_batch(self) -> None:
        module._delete_merged_rows([])
        self.frappe.db.delete.assert_not_called()

    def test_copy_excludes_identity_rename_and_unknown_fields(self) -> None:
        new_gle = SimpleNamespace(allowed=None, set=MagicMock())
        original = {"name": "old", "to_rename": 0, "unknown": 1, "allowed": 2}
        module._copy_original_fields(cast(Any, new_gle), original)
        new_gle.set.assert_called_once_with("allowed", 2)

    def test_renamer_precedes_reconciliation_without_a_commit(self) -> None:
        calls = MagicMock()
        self.frappe.db.sql.side_effect = calls.sql
        gl_entry = types.ModuleType("erpnext.accounts.doctype.gl_entry.gl_entry")
        cast(Any, gl_entry).rename_temporarily_named_docs = calls.rename
        reconcile = types.ModuleType(
            "karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.reconcile_gl_entry_links"
        )
        cast(Any, reconcile).reconcile_gl_entry_links = calls.reconcile
        with patch.dict(
            sys.modules, {gl_entry.__name__: gl_entry, reconcile.__name__: reconcile}
        ):
            module._fix_unrenamed_gl_entries()
        assert calls.mock_calls[0][0] == "sql"
        assert calls.mock_calls[1:] == [
            call.rename("GL Entry"),
            call.reconcile(commit=False),
        ]
        sql = calls.sql.call_args.args[0]
        for predicate in (
            "SET to_rename = 1",
            "WHERE to_rename = 0",
            "name NOT LIKE 'ACC-GLE-%%'",
            "CHAR_LENGTH(name) = 10",
        ):
            assert predicate in sql
        self.frappe.db.commit.assert_not_called()

    def test_diagnostics_and_duplicate_repair_are_bounded_and_commit_only_after_delete(
        self,
    ) -> None:
        duplicate_rows = [
            {"voucher_no": f"JE-{index}", "account": "A", "cnt": 2}
            for index in range(12)
        ]
        unprocessed_rows = [
            {"voucher_no": f"UP-{index}", "account": "B", "cnt": 1}
            for index in range(12)
        ]
        self.frappe.db.sql.side_effect = [duplicate_rows, unprocessed_rows]
        result = module.diagnose_gl_split_issues()
        assert result["duplicates"]["count"] == 12
        assert result["unprocessed"]["count"] == 12
        assert cast("list[str]", result["duplicates"]["samples"]) == [
            f"JE-{index} / A (2x)" for index in range(10)
        ]
        assert cast("list[str]", result["unprocessed"]["samples"]) == [
            f"UP-{index} / B (1)" for index in range(10)
        ]
        for query in (item.args[0] for item in self.frappe.db.sql.call_args_list):
            assert "LIMIT 50" in query
        self.frappe.db.sql.reset_mock()
        self.frappe.db.sql.side_effect = [
            [{"voucher_no": "JE-1", "account": "A", "voucher_detail_no": "JEA-1"}],
            [{"name": "EARLIEST"}, {"name": "LATER"}],
        ]
        assert module.repair_duplicate_gl_entries() == {"deleted": 1}
        assert "ORDER BY creation ASC" in self.frappe.db.sql.call_args_list[1].args[0]
        assert self.frappe.db.sql.call_args_list[1].args[1] == {
            "voucher_no": "JE-1",
            "account": "A",
            "voucher_detail_no": "JEA-1",
        }
        assert (
            "voucher_type = 'Journal Entry'"
            in self.frappe.db.sql.call_args_list[1].args[0]
        )
        self.frappe.db.delete.assert_called_once_with("GL Entry", {"name": "LATER"})
        self.frappe.db.commit.assert_called_once_with()

    def test_duplicate_repair_skips_empty_and_single_entry_groups(self) -> None:
        self.frappe.db.sql.return_value = cast("list[object]", [])
        assert module.repair_duplicate_gl_entries() == {"deleted": 0}
        self.frappe.db.delete.assert_not_called()
        self.frappe.db.commit.assert_not_called()

    def test_duplicate_repair_commits_after_deleting_rows(self) -> None:
        self.frappe.db.sql.side_effect = [
            [{"voucher_no": "JE-1", "account": "A", "voucher_detail_no": "JEA-1"}],
            [{"name": "KEEP"}, {"name": "DELETE"}],
        ]
        module.repair_duplicate_gl_entries()
        mutations = [
            item
            for item in self.frappe.db.mock_calls
            if item[0] in {"delete", "commit"}
        ]
        assert mutations == [call.delete("GL Entry", {"name": "DELETE"}), call.commit()]
        self.frappe.db.reset_mock()
        self.frappe.db.sql.side_effect = [
            [{"voucher_no": "JE-1", "account": "A", "voucher_detail_no": "JEA-1"}],
            [{"name": "ONLY"}],
        ]
        assert module.repair_duplicate_gl_entries() == {"deleted": 0}
        self.frappe.db.delete.assert_not_called()
        self.frappe.db.commit.assert_not_called()

    def test_execute_dispatches_each_repair_phase_in_order(self) -> None:
        calls = MagicMock()
        with (
            patch.object(module, "_backfill_reference_detail_no", calls.reference),
            patch.object(
                module, "_backfill_voucher_detail_no_single_match", calls.single
            ),
            patch.object(module, "_split_merged_gl_entries", calls.split),
            patch.object(module, "_fix_unrenamed_gl_entries", calls.rename),
        ):
            module.execute()
        assert calls.mock_calls == [
            call.reference(),
            call.single(),
            call.split(),
            call.rename(),
        ]

    def test_matching_rows_and_small_groups_use_exact_safe_scope(self) -> None:
        key = ("JE-1", "A", "Customer", "CUST-1", "Main")
        self.frappe.db.sql.return_value = [{"name": "JEA-1"}]
        module._matching_jea_rows(key)
        query, params = self.frappe.db.sql.call_args.args[:2]
        assert params == {
            "voucher_no": "JE-1",
            "account": "A",
            "party_type": "Customer",
            "party": "CUST-1",
            "cost_center": "Main",
        }
        for predicate in (
            "IFNULL(party_type, '')",
            "IFNULL(party, '')",
            "IFNULL(cost_center, '')",
            "ORDER BY idx",
        ):
            assert predicate in query
        with patch.object(module, "_backfill_single_group") as backfill:
            assert module._process_merge_group(key, [{"name": "GL-1"}]) == 0
        backfill.assert_called_once_with([{"name": "GL-1"}], [{"name": "JEA-1"}])

    def test_delete_merged_rows_deletes_every_named_row(self) -> None:
        module._delete_merged_rows([{"name": "GL-1"}, {"name": "GL-2"}])
        assert self.frappe.db.delete.call_args_list == [
            call("GL Entry", {"name": "GL-1"}),
            call("GL Entry", {"name": "GL-2"}),
        ]

    def test_tiny_patch_targets_preserve_their_absent_and_present_contracts(
        self,
    ) -> None:
        backfill = MagicMock()
        with patch.object(backfill_gl_letter_from_je_account, "frappe", backfill):
            backfill_gl_letter_from_je_account.execute()
        sql = backfill.db.sql.call_args.args[0]
        assert "voucher_type = 'Journal Entry'" in sql
        assert "voucher_detail_no is null or gl.voucher_detail_no = ''" in sql
        assert "jea.letter_count = 1" in sql

        noop = MagicMock()
        with patch.object(
            delete_stale_general_ledger_reporting_currency_report,
            "frappe",
            noop,
            create=True,
        ):
            assert (
                delete_stale_general_ledger_reporting_currency_report.execute() is None
            )
        assert noop.mock_calls == []

        stale = MagicMock()
        stale.db.exists.return_value = True
        with patch.object(delete_stale_trial_balance_reporting_report, "frappe", stale):
            delete_stale_trial_balance_reporting_report.execute()
        stale.delete_doc.assert_called_once_with(
            "Report", "Trial Balance Reporting", force=True, ignore_permissions=True
        )
        stale.clear_cache.assert_called_once_with(doctype="Report")

        disabled = MagicMock()
        disabled.db.exists.return_value = True
        disabled.db.get_value.return_value = 1
        with patch.object(disable_general_ledger_karam_auto_total, "frappe", disabled):
            disable_general_ledger_karam_auto_total.execute()
        disabled.db.set_value.assert_called_once_with(
            "Report",
            "General Ledger (Karam)",
            "add_total_row",
            0,
            update_modified=False,
        )
        disabled.clear_cache.assert_called_once_with(doctype="Report")
