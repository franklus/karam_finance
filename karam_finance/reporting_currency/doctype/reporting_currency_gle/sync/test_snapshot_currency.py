"""Site-free regression tests for queued rate snapshot currency identity."""

from contextlib import ExitStack
from datetime import date
from typing import override
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from . import conversion, doe, orchestrator, phases
from .context import SyncSnapshot


class TestSnapshotCurrency(TestCase):
    @override  # noqa: V105 - unittest and Frappe test lifecycle callback.
    def setUp(self) -> None:
        self.stack = self.enterContext(ExitStack())
        self.settings = {"reporting_currency": "USD", "last_sync_timestamp": None}
        self.snapshot = SyncSnapshot(
            currency_coverage={"direct": True, "inverse": False},
            rate_timeline=[
                {
                    "date": date(2026, 1, 1),
                    "rate": 0.01,
                    "direction": "direct",
                    "currency_exchange": "KES-USD",
                }
            ],
            default_currency="KES",
            cutoff="2026-09-07 12:00:00.123456",
            reporting_currency="USD",
        )
        self.gl_entries = [
            {
                "name": "GLE-1",
                "account_currency": "KES",
                "posting_date": "2026-02-01",
                "debit": 1000,
                "credit": 0,
            }
        ]
        self.frappe_mock = Mock()
        self.frappe_mock.throw.side_effect = self.throw_validation_error
        for module in (orchestrator, phases, conversion):
            self.stack.enter_context(patch.object(module, "frappe", self.frappe_mock))
        for module in (orchestrator, phases):
            self.stack.enter_context(patch.object(module, "_", side_effect=str))
            self.stack.enter_context(patch.object(module, "publish_sync_progress"))
        self.stack.enter_context(
            patch.object(
                frappe, "get_system_settings", return_value="Banker's Rounding"
            )
        )
        self.stack.enter_context(
            patch.object(conversion, "get_currency_precision", return_value=2)
        )
        self.stack.enter_context(
            patch.object(orchestrator, "ensure_currency_columns_capacity")
        )
        self.stack.enter_context(
            patch.object(orchestrator, "validate_settings", return_value=self.settings)
        )
        self.validation = self.stack.enter_context(
            patch.object(
                orchestrator,
                "run_validation_phase",
                return_value=(
                    self.gl_entries,
                    "KES",
                    False,
                    "Full Sync",
                    self.snapshot.currency_coverage,
                    None,
                ),
            )
        )
        self.deletion = self.stack.enter_context(
            patch.object(orchestrator, "run_deletion_phase", return_value={})
        )
        self.temporal = self.stack.enter_context(
            patch.object(
                orchestrator,
                "run_temporal_reconciliation_phase",
                return_value=(self.snapshot.rate_timeline, [date(2026, 1, 1)]),
            )
        )
        self.convert = self.stack.enter_context(
            patch.object(
                orchestrator, "run_conversion_phase", wraps=phases.run_conversion_phase
            )
        )
        self.insertion = self.stack.enter_context(
            patch.object(
                orchestrator, "run_insertion_phase", return_value={"inserted": 1}
            )
        )
        self.compute_doe = self.stack.enter_context(
            patch.object(doe, "compute_doe_inline", return_value={"success": True})
        )

    @staticmethod
    def throw_validation_error(message: str, **_kwargs: object) -> None:
        raise frappe.ValidationError(message)

    def run_job(self) -> None:
        orchestrator.run_reporting_currency_sync_job(
            "event", "done", cached_currency_coverage=self.snapshot
        )

    def assert_rejected_without_sync_writes(self, message: str) -> None:
        with self.assertRaisesRegex(frappe.ValidationError, message):
            self.run_job()
        for phase in (
            self.validation,
            self.deletion,
            self.temporal,
            self.convert,
            self.insertion,
            self.compute_doe,
        ):
            phase.assert_not_called()
        self.frappe_mock.db.set_single_value.assert_not_called()
        self.frappe_mock.db.commit.assert_not_called()
        self.frappe_mock.db.rollback.assert_called_once()
        notification = self.frappe_mock.publish_realtime.call_args.kwargs["message"]
        assert notification["status"] == "error"
        assert "Run Sync again" in notification["message"]

    def test_changed_currency_rejects_usd_rates_before_eur_conversion(self) -> None:
        self.settings["reporting_currency"] = "EUR"
        self.assert_rejected_without_sync_writes("USD to EUR")

    def test_legacy_deserialised_snapshot_without_currency_is_rejected(self) -> None:
        # Old pickles restore an instance dictionary without the new field.
        object.__delattr__(self.snapshot, "reporting_currency")
        self.assert_rejected_without_sync_writes("no target currency")

    def test_matching_currency_converts_with_original_snapshot_and_cutoff(self) -> None:
        self.run_job()
        record = self.insertion.call_args.args[2][0]
        assert record["reporting_currency"] == "USD"
        assert record["reporting_debit"] == 10
        assert record["reporting_credit"] == 0
        assert record["currency_exchange"] == "KES-USD"
        assert self.insertion.call_args.args[4].cutoff == self.snapshot.cutoff
        assert self.validation.call_args.args[4] is self.snapshot.currency_coverage
        assert self.temporal.call_args.args[6] is self.snapshot.rate_timeline
        self.frappe_mock.db.commit.assert_called_once()
        self.compute_doe.assert_called_once()

    def test_unrelated_settings_edit_does_not_reject_snapshot(self) -> None:
        self.settings["modified"] = "2026-09-07 13:00:00"
        self.test_matching_currency_converts_with_original_snapshot_and_cutoff()

    def test_direct_call_without_snapshot_uses_worker_cutoff_and_fresh_inputs(
        self,
    ) -> None:
        with patch.object(orchestrator, "now", return_value="2026-09-07 14:00:00"):
            orchestrator.sync_reporting_currency_entries("event")
        assert self.validation.call_args.args[4:6] == (None, None)
        assert self.temporal.call_args.args[6] is None
        assert self.insertion.call_args.args[4].cutoff == "2026-09-07 14:00:00"
