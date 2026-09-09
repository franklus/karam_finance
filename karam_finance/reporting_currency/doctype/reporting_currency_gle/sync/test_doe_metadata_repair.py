"""Recovery and provenance checks for historical DOE metadata repair."""

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, override
from unittest import TestCase
from unittest.mock import Mock, patch

import pytest

from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync import (
    doe_metadata_repair as repair,
)


def _pair() -> list[dict[str, Any]]:
    common = {
        "company": "Karam",
        "posting_date": "2025-12-31",
        "fiscal_year": "2025",
        "reporting_currency": "USD",
        "voucher_type": "Exchange Rate Revaluation",
        "voucher_no": "DOE-2025-4011",
        "party_type": "Supplier",
        "party": "VEN-1",
        "reporting_doe": 1,
        "manual_entry": 0,
        "is_cancelled": 0,
        "docstatus": 1,
        "is_opening": None,
        "reporting_doe_difference": 100,
        "difference_reporting_currency": 90,
        "modified": "2026-01-01 00:00:00",
    }
    return [
        {
            **common,
            "name": "KE-RCDOE-GLE-2025-00001",
            "account": "Payable EUR",
            "_account_number": "4011",
            "_account_type": "Payable",
            "_report_type": "Balance Sheet",
            "account_currency": "EUR",
            "against": "DOE Profit",
            "reporting_debit": 10,
            "reporting_credit": 0,
        },
        {
            **common,
            "name": "KE-RCDOE-GLE-2025-00002",
            "account": "DOE Profit",
            "_account_number": "7751",
            "_account_type": "Income Account",
            "_report_type": "Profit and Loss",
            "account_currency": "USD",
            "against": "Payable EUR",
            "reporting_debit": 0,
            "reporting_credit": 10,
        },
    ]


def _throw(message: str) -> None:
    raise ValueError(message)


class TestDoeMetadataRepair(TestCase):
    """Repair changes metadata only and rejects ambiguous/stale snapshots."""

    @override
    def setUp(self) -> None:
        """Use memory-backed storage doubles and private temporary recovery files."""
        self.enterContext(
            patch("frappe.translate.get_all_translations", return_value={})
        )
        self.rows = _pair()
        self.directory = self.enterContext(TemporaryDirectory())
        self.db = Mock()
        self.db.bulk_update.side_effect = self._bulk_update
        self.enterContext(patch.object(repair.frappe, "db", self.db))
        self.enterContext(patch.object(repair.frappe, "throw", side_effect=_throw))
        self.enterContext(
            patch.object(repair.frappe, "get_site_path", return_value=self.directory)
        )
        self.enterContext(
            patch.object(
                repair,
                "_read_rows",
                side_effect=self._read_rows,
            )
        )

    def _read_rows(self, *_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return copy.deepcopy(self.rows)

    def _bulk_update(
        self,
        doctype: str,
        updates: dict[str, dict[str, Any]],
        *,
        chunk_size: int,
        update_modified: bool,
    ) -> None:
        assert doctype == "Reporting Currency GLE"
        assert update_modified is False
        assert chunk_size == 100
        assert list(Path(self.directory).glob("doe-metadata-recovery-*.json"))
        for name, values in updates.items():
            next(row for row in self.rows if row["name"] == name).update(values)

    def test_preview_preserves_amounts_and_is_idempotent_after_apply(self) -> None:
        """No numeric ledger field may enter the repair change set."""
        before = copy.deepcopy(self.rows)
        preview = repair.preview_doe_metadata_repair("Karam")
        assert preview["pair_count"] == 1
        assert preview["skipped"] == []
        assert self.rows == before
        result = repair.apply_doe_metadata_repair("Karam", preview["fingerprint"])
        assert result["updated"] == 2
        assert self.rows[0]["party"] == "VEN-1"
        assert self.rows[1]["party"] is None
        assert self.rows[1]["party_type"] is None
        assert self.rows[1]["against"] == "VEN-1"
        assert self.rows[0]["voucher_no"] == self.rows[1]["voucher_no"]
        assert all(row["is_opening"] == "No" for row in self.rows)
        for old, new in zip(before, self.rows, strict=True):
            assert {
                k: v for k, v in old.items() if k not in repair.METADATA_FIELDS
            } == {k: v for k, v in new.items() if k not in repair.METADATA_FIELDS}
        second = repair.preview_doe_metadata_repair("Karam")
        assert second["changes"] == []
        assert second["skipped"] == []
        assert (
            repair.apply_doe_metadata_repair("Karam", second["fingerprint"])["updated"]
            == 0
        )
        self.db.commit.assert_not_called()

    def test_recovery_restores_exact_snapshot(self) -> None:
        """Rollback restores prior metadata without renaming or recalculating rows."""
        before = copy.deepcopy(self.rows)
        preview = repair.preview_doe_metadata_repair("Karam")
        result = repair.apply_doe_metadata_repair("Karam", preview["fingerprint"])
        path = Path(result["recovery_file"])
        assert path.stat().st_mode & 0o777 == 0o600
        assert json.loads(path.read_text())["fingerprint"] == preview["fingerprint"]
        assert repair.restore_doe_metadata(str(path)) == {"restored": 2}
        assert self.rows == before
        self.db.commit.assert_not_called()

    def test_stale_preview_and_new_records_block_apply(self) -> None:
        """A new record or changed amount invalidates the entire reviewed snapshot."""
        preview = repair.preview_doe_metadata_repair("Karam")
        self.rows[0]["reporting_debit"] = 11
        with pytest.raises(ValueError, match="changed since preview"):
            repair.apply_doe_metadata_repair("Karam", preview["fingerprint"])
        self.db.bulk_update.assert_not_called()
        self.rows = [*_pair(), {**_pair()[0], "name": "KE-RCDOE-GLE-2025-00003"}]
        with pytest.raises(ValueError, match="changed since preview"):
            repair.apply_doe_metadata_repair("Karam", preview["fingerprint"])
        self.db.bulk_update.assert_not_called()

    def test_changed_repaired_record_blocks_restore(self) -> None:
        """Recovery must not overwrite edits made after the repair."""
        preview = repair.preview_doe_metadata_repair("Karam")
        result = repair.apply_doe_metadata_repair("Karam", preview["fingerprint"])
        self.rows[0]["modified"] = "2026-02-01 00:00:00"
        self.db.bulk_update.reset_mock()
        with pytest.raises(ValueError, match="changed after repair"):
            repair.restore_doe_metadata(result["recovery_file"])
        self.db.bulk_update.assert_not_called()

    def test_ambiguous_pairs_block_repair(self) -> None:
        """Consecutive names alone do not prove that entries form a pair."""
        cases = [
            (0, "manual_entry", 1),
            (0, "is_cancelled", 1),
            (0, "docstatus", 0),
            (0, "is_opening", "Yes"),
            (0, "party_type", None),
            (1, "against", "Other Account"),
            (1, "party", "OTHER"),
            (1, "reporting_credit", 10.0001),
            (1, "account", "Payable EUR"),
            (1, "_report_type", "Balance Sheet"),
            (0, "reporting_debit", -10),
            (0, "debit", 1),
            (0, "reporting_doe_difference", 120),
        ]
        for index, field, value in cases:
            with self.subTest(field=field):
                self.rows = _pair()
                self.rows[index][field] = value
                preview = repair.preview_doe_metadata_repair("Karam")
                assert preview["skipped"]
                with pytest.raises(ValueError, match="Unmatched DOE"):
                    repair.apply_doe_metadata_repair("Karam", preview["fingerprint"])
        self.db.bulk_update.assert_not_called()

    def test_repair_helpers_reject_missing_account_context_and_malformed_pairs(
        self,
    ) -> None:
        primary, offset = _pair()
        primary.pop("_report_type")
        assert repair._row_error(primary) == "Account metadata missing"

        primary, offset = _pair()
        offset["account"] = primary["account"]
        assert repair._context_error(primary, offset) == (
            "Counterentry is not a distinct income/expense account"
        )

        primary, offset = _pair()
        offset["_report_type"] = "Balance Sheet"
        assert repair._context_error(primary, offset) == (
            "Counterentry is not a distinct income/expense account"
        )
        primary, offset = _pair()
        primary["against"] = "Wrong Account"
        assert repair._context_error(primary, offset) == (
            "Primary Against does not identify the counterentry account"
        )
        assert (
            repair._find_offset("not-a-generated-doe-name", {offset["name"]: offset})
            is None
        )

    def test_preview_rejects_mixed_company_and_write_rejects_source_changes_before_io(
        self,
    ) -> None:
        rows = _pair()
        rows[1]["company"] = "Other Company"
        with pytest.raises(ValueError, match="different company"):
            repair.build_repair_preview("Karam", rows)

        with (
            patch.object(repair, "_read_rows") as read_rows,
            pytest.raises(ValueError, match="cannot alter monetary or source fields"),
        ):
            repair._write_metadata(
                [{"name": self.rows[0]["name"], "after": {"reporting_debit": 11}}],
                "Karam",
                "irrelevant",
            )
        read_rows.assert_not_called()
        self.db.savepoint.assert_not_called()
        self.db.bulk_update.assert_not_called()

    def test_failure_rolls_back_to_savepoint(self) -> None:
        """Insertion errors cannot silently leave a partial metadata update."""
        preview = repair.preview_doe_metadata_repair("Karam")
        self.db.bulk_update.side_effect = RuntimeError("write failed")
        with pytest.raises(RuntimeError, match="write failed"):
            repair.apply_doe_metadata_repair("Karam", preview["fingerprint"])
        self.db.rollback.assert_called_once_with(save_point="doe_metadata_repair")
        self.db.commit.assert_not_called()

    def test_silent_write_failure_fails_post_verification(self) -> None:
        """A successful DB call is not enough if the saved snapshot differs."""
        preview = repair.preview_doe_metadata_repair("Karam")
        self.db.bulk_update.side_effect = None
        with pytest.raises(ValueError, match="verification failed"):
            repair.apply_doe_metadata_repair("Karam", preview["fingerprint"])
        self.db.rollback.assert_called_once_with(save_point="doe_metadata_repair")
