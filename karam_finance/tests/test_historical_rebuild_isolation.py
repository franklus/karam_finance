"""Historical rebuild access checks through native public entry points."""

from collections.abc import Callable, Generator
from contextlib import contextmanager
from operator import itemgetter
from typing import Any, cast, override
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_months, today
from frappe.utils.redis_wrapper import RedisWrapper
from karam_finance.letter_reconciliation.doctype.letter_reconciliation_settings.letter_reconciliation_settings import (
    enqueue_historical_gl_rebuild,
    run_historical_gl_rebuild_job,
)

EXTRA_TEST_RECORD_DEPENDENCIES = ["Journal Entry"]  # noqa: V107 - Frappe fixture loader.


class TestHistoricalRebuildIsolation(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "rebuild_worker_" + frappe.generate_hash(length=8)
        previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, previous_user)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        self.cache = cast(RedisWrapper, frappe.cache)
        frappe.db.set_single_value("Accounts Settings", "enable_immutable_ledger", 0)
        self.voucher = frappe.copy_doc(self.globalTestRecords["Journal Entry"][0])
        self.voucher.set("posting_date", add_months(today(), -1))
        self.voucher.insert()
        self.voucher.submit()
        assert self.voucher.name
        self.name = self.voucher.name
        for child in self.voucher.get("accounts"):
            frappe.db.set_value(
                "Journal Entry Account", child.name, "reference_detail_no", ""
            )
        frappe.db.set_single_value(
            "Letter Reconciliation Settings",
            {
                "rebuild_company": self.voucher.get("company"),
                "rebuild_whole_history": 1,
            },
        )
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": self.token + "@example.com",
                "first_name": "Rebuild worker",
                "send_welcome_email": 0,
                "roles": [{"role": "Accounts Manager"}],
            }
        ).insert()
        assert user.name
        self.user = user.name
        self.permission = frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": self.user,
                "allow": "Company",
                "for_value": self.voucher.get("company"),
                "apply_to_all_doctypes": 1,
            }
        ).insert()
        frappe.clear_cache(user=self.user)

    def _snapshot(self) -> tuple[Any, Any]:
        names = frappe.get_list(
            "GL Entry",
            filters={
                "voucher_type": "Journal Entry",
                "voucher_no": self.name,
            },
            pluck="name",
            limit=0,
        )
        return (
            sorted(
                (frappe.get_doc("GL Entry", name).as_dict() for name in names),
                key=itemgetter("name"),
            ),
            frappe.get_doc("Journal Entry", self.name).as_dict(),
        )

    def _queue_state(self) -> str:
        assert not self.cache.get_value("historical_gl_rebuild_running")
        state: dict[str, Any] = {
            "run_id": self.token,
            "user": self.user,
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
        self.cache.set_value("historical_gl_rebuild_running", self.token)
        self.cache.set_value("historical_gl_rebuild_state:" + self.token, state)
        self.addCleanup(self.cache.delete_value, "historical_gl_rebuild_running")
        self.addCleanup(
            self.cache.delete_value, "historical_gl_rebuild_state:" + self.token
        )
        return self.token

    def _run_worker(self, run_id: str) -> list[Any]:
        savepoint = self.token + "_worker"
        frappe.db.savepoint(savepoint)
        rollback = frappe.db.rollback

        def isolated_rollback(*, save_point: str | None = None) -> None:
            rollback(save_point=save_point or savepoint)

        # Contain transaction completion at the database boundary; all app logic is real.
        with (
            patch.object(frappe.db, "commit"),
            patch.object(frappe.db, "rollback", side_effect=isolated_rollback),
            patch.object(frappe, "publish_realtime") as events,
        ):
            run_historical_gl_rebuild_job(run_id)
        return [call.args[1] for call in events.call_args_list]

    def test_failed_voucher_is_unchanged_while_its_neighbour_rebuilds(self) -> None:
        good = frappe.copy_doc(self.voucher)
        good.insert()
        good.submit()
        assert good.name
        # An invalid historical amount passes repost eligibility but fails GL balancing.
        frappe.db.set_value(
            "Journal Entry Account",
            self.voucher.get("accounts")[0].name,
            {"credit_in_account_currency": 399, "credit": 399},
        )
        before = self._snapshot()
        run_id = self._queue_state()
        state = self.cache.get_value("historical_gl_rebuild_state:" + run_id)
        assert state is not None
        state["eligible_vouchers"].append(good.name)
        self.cache.set_value("historical_gl_rebuild_state:" + run_id, state)
        events = self._run_worker(run_id)
        summary = next(event for event in events if "rebuilt_count" in event)
        assert summary["rebuilt_count"] == 1
        assert summary["failed_count"] == 1
        assert self._snapshot() == before
        assert all(
            child.reference_detail_no == child.name
            for child in frappe.get_doc("Journal Entry", good.name).get("accounts")
        )

    def test_superseded_worker_cannot_repost_or_remove_the_new_owner(self) -> None:
        run_id = self._queue_state()
        self.cache.set_value("historical_gl_rebuild_running", "new-owner")
        before = self._snapshot()
        self._run_worker(run_id)
        assert self._snapshot() == before
        assert (
            self.cache.get_value("historical_gl_rebuild_running", use_local_cache=False)
            == "new-owner"
        )

    def test_second_enqueue_during_preview_cannot_start_another_run(self) -> None:
        assert not self.cache.get_value(
            "historical_gl_rebuild_running", use_local_cache=False
        )
        rejected = []

        def compete() -> None:
            try:
                enqueue_historical_gl_rebuild()
            except frappe.ValidationError as exc:
                rejected.append(str(exc))

        with (
            self._legacy_repost_lookup(on_lookup=compete),
            patch.object(frappe, "enqueue") as queue,
        ):
            try:
                enqueue_historical_gl_rebuild()
                assert queue.call_count == 1
                assert len(rejected) == 1
                assert "already running" in rejected[0]
            finally:
                for call in queue.call_args_list:
                    self.cache.delete_value(
                        "historical_gl_rebuild_state:" + call.kwargs["run_id"]
                    )
                self.cache.delete_value("historical_gl_rebuild_running")

    @contextmanager
    def _legacy_repost_lookup(
        self, on_lookup: Callable[[], None] | None = None
    ) -> Generator[None]:
        exists = frappe.db.exists
        intercepted = False

        def enabled_lookup(
            doctype: str, filters: Any = None, *args: Any, **kwargs: Any
        ) -> Any:
            nonlocal intercepted
            if doctype == "Repost Allowed Types" and filters == {
                "document_type": "Journal Entry",
                "allowed": True,
            }:
                # Only the inherited absent-column lookup is supplied by this fixture.
                if on_lookup and not intercepted:
                    intercepted = True
                    on_lookup()
                return "test-enabled"
            return exists(doctype, filters, *args, **kwargs)

        with patch.object(frappe.db, "exists", side_effect=enabled_lookup):
            yield

    def test_enqueue_delivery_failure_releases_its_guard_and_allows_retry(self) -> None:
        with (
            self._legacy_repost_lookup(),
            patch.object(
                frappe, "enqueue", side_effect=RuntimeError("Queue unavailable")
            ),
            self.assertRaisesRegex(RuntimeError, "Queue unavailable"),
        ):
            enqueue_historical_gl_rebuild()
        assert (
            self.cache.get_value("historical_gl_rebuild_running", use_local_cache=False)
            is None
        )
        with self._legacy_repost_lookup(), patch.object(frappe, "enqueue") as queue:
            enqueue_historical_gl_rebuild()
            run_id = queue.call_args.kwargs["run_id"]
            self.addCleanup(self.cache.delete_value, "historical_gl_rebuild_running")
            self.addCleanup(
                self.cache.delete_value, "historical_gl_rebuild_state:" + run_id
            )
            assert (
                self.cache.get_value(
                    "historical_gl_rebuild_running", use_local_cache=False
                )
                == run_id
            )

    def test_worker_renews_a_nearly_expired_guard_before_progress_and_completion(
        self,
    ) -> None:
        run_id = self._queue_state()
        key = self.cache.make_key("historical_gl_rebuild_running")
        self.cache.expire(key, 5)
        observed: list[int] = []

        def observe(*_args: Any, **_kwargs: Any) -> None:
            observed.append(cast(int, self.cache.ttl(key)))

        with (
            patch.object(frappe.db, "commit"),
            patch.object(
                frappe,
                "publish_realtime",
                side_effect=observe,
            ),
        ):
            run_historical_gl_rebuild_job(run_id)
        assert observed and min(observed) > 4 * 60 * 60
        assert (
            self.cache.get_value("historical_gl_rebuild_running", use_local_cache=False)
            is None
        )

    def test_orphaned_worker_does_not_release_a_successor_guard(self) -> None:
        run_id = self._queue_state()
        self.cache.delete_value("historical_gl_rebuild_state:" + run_id)
        self.cache.set_value("historical_gl_rebuild_running", "successor")
        self._run_worker(run_id)
        assert (
            self.cache.get_value("historical_gl_rebuild_running", use_local_cache=False)
            == "successor"
        )

    def test_expired_worker_does_not_rebuild_without_its_guard(self) -> None:
        run_id = self._queue_state()
        # Keep the request-local cached token to exercise stale local cache handling.
        self.cache.delete(self.cache.make_key("historical_gl_rebuild_running"))
        before = self._snapshot()
        self._run_worker(run_id)
        assert self._snapshot() == before

    def test_final_audit_does_not_persist_backfill_for_an_all_failed_run(self) -> None:
        frappe.db.set_value(
            "Journal Entry Account",
            self.voucher.get("accounts")[0].name,
            {"credit_in_account_currency": 399, "credit": 399},
        )
        before = self._snapshot()
        events = self._run_worker(self._queue_state())
        summary = next(event for event in events if "rebuilt_count" in event)
        assert summary["rebuilt_count"] == 0 and summary["failed_count"] == 1
        assert self._snapshot() == before

    def test_duplicate_worker_delivery_cannot_rebuild_the_same_run_concurrently(
        self,
    ) -> None:
        run_id = self._queue_state()
        entered = False
        summaries = []
        starts = []
        rollback = frappe.db.rollback
        savepoint = self.token + "_delivery"
        frappe.db.savepoint(savepoint)

        def isolated_rollback(*, save_point: str | None = None) -> None:
            rollback(save_point=save_point or savepoint)

        def deliver_again(_event: str, payload: dict[str, Any], **_kwargs: Any) -> None:
            nonlocal entered
            if "rebuilt_count" in payload:
                summaries.append(payload)
            if payload.get("current") == 0:
                starts.append(payload)
                if not entered:
                    entered = True
                    run_historical_gl_rebuild_job(run_id)

        with (
            patch.object(frappe.db, "commit"),
            patch.object(frappe, "publish_realtime", side_effect=deliver_again),
            patch.object(frappe.db, "rollback", side_effect=isolated_rollback),
        ):
            run_historical_gl_rebuild_job(run_id)
        assert len(starts) == 1
        assert len(summaries) == 1
        assert summaries[0]["rebuilt_count"] == 1

    def test_chained_batch_renews_ownership_and_releases_execution_before_delivery(
        self,
    ) -> None:
        names = [self.name]
        for _ in range(200):
            voucher = frappe.copy_doc(self.voucher)
            voucher.insert()
            voucher.submit()
            assert voucher.name
            names.append(voucher.name)
        run_id = self._queue_state()
        state = self.cache.get_value("historical_gl_rebuild_state:" + run_id)
        assert state is not None
        state["eligible_vouchers"] = names
        self.cache.set_value("historical_gl_rebuild_state:" + run_id, state)
        delivered = []

        def deliver(*_args: Any, **kwargs: Any) -> None:
            assert (
                self.cache.get(self.cache.make_key("historical_gl_rebuild_execution"))
                is None
            )
            assert (
                cast(
                    int,
                    self.cache.ttl(
                        self.cache.make_key("historical_gl_rebuild_running")
                    ),
                )
                > 4 * 60 * 60
            )
            delivered.append(kwargs["run_id"])

        with patch.object(frappe, "enqueue", side_effect=deliver):
            first_events = self._run_worker(run_id)
        assert delivered == [run_id]
        assert not any("rebuilt_count" in event for event in first_events)
        state = self.cache.get_value(
            "historical_gl_rebuild_state:" + run_id, use_local_cache=False
        )
        assert state is not None
        assert state["next_index"] == 200 and state["rebuilt_count"] == 200
        final_events = self._run_worker(run_id)
        final = next(event for event in final_events if "rebuilt_count" in event)
        assert final["rebuilt_count"] == 201 and final["failed_count"] == 0
        assert (
            self.cache.get_value("historical_gl_rebuild_running", use_local_cache=False)
            is None
        )
