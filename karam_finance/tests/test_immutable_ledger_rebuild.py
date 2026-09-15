"""Historical rebuild must preserve period balances or reject before mutation."""

from operator import itemgetter
from typing import Any, cast, override
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_months, getdate, today
from frappe.utils.redis_wrapper import RedisWrapper
from karam_finance.letter_reconciliation.doctype.letter_reconciliation_settings.historical_gl_rebuild import (
    rebuild_single_voucher,
)
from karam_finance.letter_reconciliation.doctype.letter_reconciliation_settings.letter_reconciliation_settings import (
    enqueue_historical_gl_rebuild,
    run_historical_gl_rebuild_job,
)
from karam_finance.patches.fix_merged_gl_entries import execute
from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.orchestrator import (
    sync_reporting_currency_entries,
)

EXTRA_TEST_RECORD_DEPENDENCIES = ["Journal Entry"]  # noqa: V107 - Frappe fixture loader.


class TestImmutableLedgerRebuild(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "immutable_review_" + frappe.generate_hash(length=8)
        previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, previous_user)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        self.addCleanup(
            frappe.clear_document_cache, "Accounts Settings", "Accounts Settings"
        )
        self.cache = cast(RedisWrapper, frappe.cache)
        self._immutable(0)
        self.voucher = frappe.copy_doc(self.globalTestRecords["Journal Entry"][0])
        self.voucher.set("posting_date", add_months(today(), -1))
        self.voucher.insert()
        self.voucher.submit()
        assert self.voucher.name
        self.name = self.voucher.name
        self.bank = self.voucher.get("accounts")[1].account
        for child in self.voucher.get("accounts"):
            frappe.db.set_value(
                "Journal Entry Account", child.name, "reference_detail_no", ""
            )
        for row in self._rows():
            frappe.db.set_value("GL Entry", row.name, "voucher_detail_no", "")

    @staticmethod
    def _immutable(enabled: int) -> None:
        frappe.db.set_single_value(
            "Accounts Settings", "enable_immutable_ledger", enabled
        )
        frappe.clear_document_cache("Accounts Settings", "Accounts Settings")

    def _rows(self) -> list[Any]:
        names = frappe.get_list(
            "GL Entry",
            filters={"voucher_type": "Journal Entry", "voucher_no": self.name},
            pluck="name",
            limit=0,
        )
        return [frappe.get_doc("GL Entry", name) for name in names]

    def _snapshot(self) -> tuple[Any, Any]:
        return (
            sorted((row.as_dict() for row in self._rows()), key=itemgetter("name")),
            frappe.get_doc("Journal Entry", self.name).as_dict(),
        )

    def test_direct_rebuild_rejects_immutable_ledger_without_changing_voucher(
        self,
    ) -> None:
        self._immutable(1)
        before = self._snapshot()
        with self.assertRaisesRegex(frappe.ValidationError, "Immutable Ledger"):
            rebuild_single_voucher(self.name)
        assert self._snapshot() == before

    def _prepare_queue(self) -> None:
        frappe.db.set_single_value(
            "Letter Reconciliation Settings",
            {
                "rebuild_company": self.voucher.get("company"),
                "rebuild_whole_history": 0,
                "rebuild_from_posting_date": self.voucher.get("posting_date"),
                "rebuild_to_posting_date": self.voucher.get("posting_date"),
            },
        )

    def test_enqueue_rejects_immutable_ledger_without_queuing_or_changing_voucher(
        self,
    ) -> None:
        self._prepare_queue()
        self._immutable(1)
        before = self._snapshot()
        assert not self.cache.get_value("historical_gl_rebuild_running")
        with patch.object(frappe, "enqueue") as queue:
            try:
                with self.assertRaisesRegex(frappe.ValidationError, "Immutable Ledger"):
                    enqueue_historical_gl_rebuild()
                queue.assert_not_called()
                assert not self.cache.get_value("historical_gl_rebuild_running")
                assert self._snapshot() == before
            finally:
                run_id = self.cache.get_value("historical_gl_rebuild_running")
                if run_id:
                    self.cache.delete_value("historical_gl_rebuild_state:" + run_id)
                    self.cache.delete_value("historical_gl_rebuild_running")

    def test_legacy_repair_rejects_immutable_ledger_and_preserves_originals(
        self,
    ) -> None:
        self._immutable(1)
        before = self._snapshot()
        with self.assertRaisesRegex(frappe.ValidationError, "Immutable Ledger"):
            execute(voucher_nos=[self.name])
        assert self._snapshot() == before

    def _prepare_sync(self) -> None:
        # Normal Sync is site-wide; isolate its source data in this rollback fixture.
        frappe.db.delete("Reporting Currency GLE")
        frappe.db.delete("GL Entry", {"voucher_no": ["!=", self.name]})
        currency = frappe.get_doc("Company", self.voucher.get("company")).get(
            "default_currency"
        )
        offset = frappe.get_list(
            "Account",
            filters={
                "company": self.voucher.get("company"),
                "root_type": "Income",
                "is_group": 0,
            },
            pluck="name",
            limit=1,
        )[0]
        settings = frappe.get_single("Reporting Currency Settings")
        settings.update(
            {
                "reporting_currency": currency,
                "last_sync_timestamp": None,
                "last_ce_sync_timestamp": None,
            }
        )
        settings.set("rc_parameters", [])
        settings.append(
            "rc_parameters",
            {
                "doe_posting_date": self.voucher.get("posting_date"),
                "exchange_rate": 1,
                "profit_account": offset,
                "loss_account": offset,
            },
        )
        settings.save()

    def _assert_period_amounts_after_sync(self) -> None:
        result = sync_reporting_currency_entries(self.token, "Administrator")
        assert result["errors"] == 0
        source = [
            row
            for row in self._rows()
            if row.account == self.bank and not row.is_cancelled
        ]
        assert sum(row.debit - row.credit for row in source) == 400
        assert {getdate(row.posting_date) for row in source} == {
            getdate(self.voucher.get("posting_date"))
        }
        names = frappe.get_list(
            "Reporting Currency GLE",
            filters={
                "voucher_no": self.name,
                "account": self.bank,
                "reporting_doe": 0,
            },
            pluck="name",
            limit=0,
        )
        rows = [frappe.get_doc("Reporting Currency GLE", name) for name in names]
        assert (
            sum(
                row.get("reporting_debit") - row.get("reporting_credit") for row in rows
            )
            == 400
        )
        assert {getdate(row.get("posting_date")) for row in rows} == {
            getdate(self.voucher.get("posting_date"))
        }
        assert {row.get("gl_entry") for row in rows} == {row.name for row in source}

    def test_sync_preserves_original_period_after_immutable_rebuild_is_rejected(
        self,
    ) -> None:
        self._prepare_sync()
        self._assert_period_amounts_after_sync()
        self._immutable(1)
        before = self._snapshot()
        with self.assertRaisesRegex(frappe.ValidationError, "Immutable Ledger"):
            rebuild_single_voucher(self.name)
        assert self._snapshot() == before
        self._assert_period_amounts_after_sync()
        frappe.db.set_single_value(
            "Reporting Currency Settings", "last_sync_timestamp", None
        )
        self._assert_period_amounts_after_sync()

    def test_supported_rebuild_and_sync_preserve_original_period(self) -> None:
        self._prepare_sync()
        self._assert_period_amounts_after_sync()
        rebuild_single_voucher(self.name)
        assert all(
            child.reference_detail_no == child.name
            for child in frappe.get_doc("Journal Entry", self.name).get("accounts")
        )
        self._assert_period_amounts_after_sync()
        frappe.db.set_single_value(
            "Reporting Currency Settings", "last_sync_timestamp", None
        )
        self._assert_period_amounts_after_sync()

    def test_queued_worker_rechecks_immutable_mode_before_batch_backfill(self) -> None:
        assert not self.cache.get_value("historical_gl_rebuild_running")
        run_id = self.token
        state: dict[str, Any] = {
            "run_id": run_id,
            "user": "Administrator",
            "progress_event": self.token + "progress",
            "done_event": self.token + "done",
            "filters": {
                "company": self.voucher.get("company"),
                "whole_history": True,
                "from_posting_date": None,
                "to_posting_date": None,
            },
            "total_submitted_vouchers": 1,
            "eligible_vouchers": [self.name],
            "next_index": 0,
            "rebuilt_count": 0,
            "blocked_count": 0,
            "already_correct_count": 0,
            "failures": [],
        }
        self.cache.set_value("historical_gl_rebuild_running", run_id)
        self.cache.set_value("historical_gl_rebuild_state:" + run_id, state)
        self.addCleanup(
            self.cache.delete_value, "historical_gl_rebuild_state:" + run_id
        )
        self.addCleanup(self.cache.delete_value, "historical_gl_rebuild_running")
        self._immutable(1)
        before = self._snapshot()
        savepoint = self.token + "_worker"
        frappe.db.savepoint(savepoint)
        rollback = frappe.db.rollback

        def isolated_rollback(*, save_point: str | None = None) -> None:
            rollback(save_point=save_point or savepoint)

        # Keep the worker's commit/rollback boundary inside this fixture transaction.
        # Voucher processing, settings reads, SQL and savepoint rollbacks remain real.
        with (
            patch.object(frappe.db, "commit"),
            patch.object(frappe.db, "rollback", side_effect=isolated_rollback),
            patch.object(frappe, "publish_realtime") as events,
        ):
            run_historical_gl_rebuild_job(run_id)
        assert self._snapshot() == before
        assert any(
            call.args[1].get("status") == "fatal_error"
            for call in events.call_args_list
        )
        assert not self.cache.get_value("historical_gl_rebuild_running")
