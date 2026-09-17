"""Safety contracts for the merged GL-entry repair patch."""

from __future__ import annotations

import re
import sqlite3
from typing import Any, cast, override
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

import frappe
import pytest

from karam_finance.patches import (
    backfill_gl_letter_from_je_account,
    delete_stale_general_ledger_reporting_currency_report,
    delete_stale_trial_balance_reporting_report,
    disable_general_ledger_karam_auto_total,
)
from karam_finance.patches import (
    fix_merged_gl_entries as module,
)


@pytest.mark.parametrize("ambiguous", [False, True])
def test_legacy_backfill_matches_party_and_includes_unlettered_sources(
    monkeypatch: pytest.MonkeyPatch, ambiguous: bool
) -> None:
    from frappe.query_builder.builder import MariaDB  # noqa: PLC0415

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row  # noqa: V101 - SQLite reads this connection callback.
    common = "account text, party_type text, party text, cost_center text, project text"
    connection.execute(
        f"create table `tabJournal Entry Account` (parent text, letter text, {common}, parenttype text default 'Journal Entry', parentfield text default 'accounts', docstatus integer default 1)"
    )
    connection.execute(
        f"create table `tabGL Entry` (name text, letter text, voucher_no text, voucher_type text, voucher_detail_no text, company text, posting_date text, {common})"
    )
    connection.execute(
        "create table `tabJournal Entry` (name text, company text, posting_date text, docstatus integer)"
    )
    connection.execute(
        "insert into `tabJournal Entry` values ('JE', 'Company', '2025-01-01', 1)"
    )
    connection.executemany(
        "insert into `tabGL Entry` values (?, '', 'JE', 'Journal Entry', '', 'Company', '2025-01-01', 'Account', 'Customer', ?, '', '')",
        [("GL-A", "A"), ("GL-B", "B")],
    )
    connection.executemany(
        "insert into `tabJournal Entry Account` (parent, letter, account, party_type, party, cost_center, project) values ('JE', ?, 'Account', 'Customer', ?, '', '')",
        [("A", "A"), ("", "B")],
    )
    if ambiguous:
        connection.execute(
            "insert into `tabJournal Entry Account` (parent, letter, account, party_type, party, cost_center, project) values ('JE', '', 'Account', 'Customer', 'A', '', '')"
        )

    def sql(query: str, params: dict[str, Any], **_kwargs: Any) -> list[Any]:
        query = re.sub(r"%\((\w+)\)s", r":\1", query)
        return [frappe._dict(dict(row)) for row in connection.execute(query, params)]

    def update(_doctype: str, values: dict[str, dict[str, str]]) -> None:
        connection.executemany(
            "update `tabGL Entry` set letter=? where name=?",
            [(row["letter"], name) for name, row in values.items()],
        )

    database = MagicMock(sql=sql, bulk_update=update)
    monkeypatch.setattr(frappe, "db", database)
    monkeypatch.setattr(frappe.local, "db", database, raising=False)
    monkeypatch.setattr(frappe.local, "flags", frappe._dict(), raising=False)
    monkeypatch.setattr(frappe, "qb", MariaDB)
    monkeypatch.setattr(
        frappe, "get_meta", MagicMock(return_value=frappe._dict(fields=[]))
    )
    try:
        backfill_gl_letter_from_je_account.execute()
        backfill_gl_letter_from_je_account.execute()
        actual = [
            tuple(row)
            for row in connection.execute(
                "select name, letter from `tabGL Entry` order by name"
            )
        ]
        assert actual == [("GL-A", "" if ambiguous else "A"), ("GL-B", "")]
        database.commit.assert_not_called()
    finally:
        connection.close()


class TestFixMergedGlEntries(TestCase):
    @override
    def setUp(self) -> None:
        self.frappe = MagicMock()
        self.enterContext(patch.object(module, "frappe", self.frappe))

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

    def test_tiny_patch_targets_preserve_their_absent_and_present_contracts(
        self,
    ) -> None:
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
