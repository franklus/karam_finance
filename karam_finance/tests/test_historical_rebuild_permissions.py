"""Historical rebuild access checks through native public entry points."""

from operator import itemgetter
from typing import Any, cast, override
from unittest.mock import patch

import frappe
from frappe.core.doctype.user.user import User
from frappe.tests import IntegrationTestCase
from frappe.utils import add_months, today
from frappe.utils.redis_wrapper import RedisWrapper
from karam_finance.letter_reconciliation.doctype.letter_reconciliation_settings.historical_gl_rebuild import (
    rebuild_single_voucher,
)
from karam_finance.letter_reconciliation.doctype.letter_reconciliation_settings.letter_reconciliation_settings import (
    enqueue_historical_gl_rebuild,
    preview_historical_gl_rebuild,
    run_historical_gl_rebuild_job,
)

EXTRA_TEST_RECORD_DEPENDENCIES = ["Journal Entry"]  # noqa: V107 - Frappe fixture loader.


class TestHistoricalRebuildPermissions(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "rebuild_access_" + frappe.generate_hash(length=8)
        previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, previous_user)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        self.allowed_company = self.token + " Allowed"
        self.denied_company = self.token + " Denied"
        for name in (self.allowed_company, self.denied_company):
            frappe.get_doc(
                {"doctype": "Company", "name": name, "default_currency": "INR"}
            ).db_insert()
        user = frappe.get_doc(
            {
                "doctype": "User",
                "email": self.token + "@example.com",
                "first_name": "Rebuild permissions",
                "send_welcome_email": 0,
                "roles": [{"role": "Accounts Manager"}],
            }
        ).insert()
        assert user.name
        self.user = user.name
        frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": self.user,
                "allow": "Company",
                "for_value": self.allowed_company,
                "apply_to_all_doctypes": 1,
            }
        ).insert()
        frappe.clear_cache(user=self.user)

    def test_preview_rejects_a_company_outside_the_users_permissions(self) -> None:
        frappe.db.set_single_value(
            "Letter Reconciliation Settings",
            {
                "rebuild_company": self.denied_company,
                "rebuild_whole_history": 1,
            },
        )
        before = frappe.get_single("Letter Reconciliation Settings").as_dict()
        frappe.set_user(self.user)
        with self.assertRaises(frappe.PermissionError):
            preview_historical_gl_rebuild()

        frappe.set_user("Administrator")
        assert frappe.get_single("Letter Reconciliation Settings").as_dict() == before

    def test_enqueue_rejects_denied_company_without_queue_delivery(self) -> None:
        frappe.db.set_single_value("Accounts Settings", "enable_immutable_ledger", 0)
        frappe.db.set_single_value(
            "Letter Reconciliation Settings",
            {
                "rebuild_company": self.denied_company,
                "rebuild_whole_history": 1,
            },
        )
        frappe.set_user(self.user)
        with patch.object(frappe, "enqueue") as queue:
            with self.assertRaises(frappe.PermissionError):
                enqueue_historical_gl_rebuild()
            queue.assert_not_called()

    def test_preview_does_not_include_journals_outside_the_users_permissions(
        self,
    ) -> None:
        for suffix in ("allowed", "denied"):
            name = self.token + suffix
            frappe.get_doc(
                {
                    "doctype": "Journal Entry",
                    "name": name,
                    "company": self.allowed_company,
                    "posting_date": "2026-01-01",
                    "docstatus": 1,
                }
            ).db_insert()
            frappe.get_doc(
                {
                    "doctype": "GL Entry",
                    "name": name,
                    "company": self.allowed_company,
                    "posting_date": "2026-01-01",
                    "docstatus": 1,
                    "is_cancelled": 0,
                    "voucher_type": "Journal Entry",
                    "voucher_no": name,
                }
            ).db_insert()
        frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": self.user,
                "allow": "Journal Entry",
                "for_value": self.token + "allowed",
                "apply_to_all_doctypes": 1,
            }
        ).insert()
        frappe.clear_cache(user=self.user)
        frappe.db.set_single_value(
            "Letter Reconciliation Settings",
            {
                "rebuild_company": self.allowed_company,
                "rebuild_whole_history": 1,
            },
        )
        frappe.set_user(self.user)
        with self.assertRaises(frappe.PermissionError):
            preview_historical_gl_rebuild()


class TestRebuildExecutionPermissions(IntegrationTestCase):
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

    def _revoke_company(self) -> None:
        frappe.get_doc(
            {"doctype": "Company", "name": self.token, "default_currency": "INR"}
        ).db_insert()
        self.permission.set("for_value", self.token)
        self.permission.save()
        frappe.clear_cache(user=self.user)

    def test_direct_rebuild_rejects_denied_company_before_reference_backfill(
        self,
    ) -> None:
        self._revoke_company()
        before = self._snapshot()
        frappe.set_user(self.user)
        with self.assertRaises(frappe.PermissionError):
            rebuild_single_voucher(self.name)
        frappe.set_user("Administrator")
        assert self._snapshot() == before

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

    def test_worker_rechecks_initiators_company_access_before_backfill(self) -> None:
        run_id = self._queue_state()
        self._revoke_company()
        before = self._snapshot()
        events = self._run_worker(run_id)
        assert frappe.session.user == "Administrator"
        assert self._snapshot() == before
        assert any(event.get("status") == "fatal_error" for event in events)

    def test_permitted_user_can_preview_and_worker_rebuilds_as_that_user(self) -> None:
        frappe.set_user(self.user)
        preview = preview_historical_gl_rebuild()
        assert any(
            self.name in preview[bucket]["samples"]
            for bucket in ("eligible", "blocked", "already_correct")
        )
        frappe.set_user("Administrator")
        events = self._run_worker(self._queue_state())
        assert frappe.session.user == "Administrator"
        assert any(
            event.get("status") == "success" and event.get("rebuilt_count") == 1
            for event in events
        )
        active = [row for row in self._snapshot()[0] if not row["is_cancelled"]]
        assert sorted((row["debit"], row["credit"]) for row in active) == [
            (0, 400),
            (400, 0),
        ]
        assert all(row["modified_by"] == self.user for row in active)
        assert all(
            child.reference_detail_no == child.name
            for child in frappe.get_doc("Journal Entry", self.name).get("accounts")
        )

    def _deny_write(self, doctype: str) -> None:
        frappe.get_doc(
            {
                "doctype": "Custom DocPerm",
                "parent": doctype,
                "role": "Accounts Manager",
                "read": 1,
                "write": 0,
            }
        ).insert()
        frappe.clear_cache(doctype=doctype)
        self.addCleanup(frappe.clear_cache, doctype=doctype)

    def test_settings_read_access_does_not_authorise_enqueue(self) -> None:
        self._deny_write("Letter Reconciliation Settings")
        frappe.set_user(self.user)
        assert preview_historical_gl_rebuild()["total_submitted_vouchers"] >= 1
        with patch.object(frappe, "enqueue") as queue:
            with self.assertRaises(frappe.PermissionError):
                enqueue_historical_gl_rebuild()
            queue.assert_not_called()

    def test_journal_read_access_does_not_authorise_direct_or_queued_rebuild(
        self,
    ) -> None:
        run_id = self._queue_state()
        self._deny_write("Journal Entry")
        before = self._snapshot()
        frappe.set_user(self.user)
        assert frappe.has_permission("Journal Entry", "read", doc=self.name)
        with self.assertRaises(frappe.PermissionError):
            rebuild_single_voucher(self.name)
        frappe.set_user("Administrator")
        events = self._run_worker(run_id)
        assert self._snapshot() == before
        assert any(event.get("status") == "fatal_error" for event in events)

    def test_worker_rejects_disabled_initiator(self) -> None:
        run_id = self._queue_state()
        frappe.db.set_value("User", self.user, "enabled", 0)
        before = self._snapshot()
        events = self._run_worker(run_id)
        assert frappe.session.user == "Administrator"
        assert self._snapshot() == before
        assert any(event.get("status") == "fatal_error" for event in events)

    def test_worker_rejects_initiator_whose_manager_role_was_removed(self) -> None:
        run_id = self._queue_state()
        cast(User, frappe.get_doc("User", self.user)).remove_roles("Accounts Manager")
        before = self._snapshot()
        events = self._run_worker(run_id)
        assert frappe.session.user == "Administrator"
        assert self._snapshot() == before
        assert any(event.get("status") == "fatal_error" for event in events)

    def test_worker_rejects_a_voucher_that_no_longer_matches_queued_dates(self) -> None:
        run_id = self._queue_state()
        state = self.cache.get_value("historical_gl_rebuild_state:" + run_id)
        assert state is not None
        state["filters"].update(
            {
                "whole_history": False,
                "from_posting_date": today(),
                "to_posting_date": today(),
            }
        )
        self.cache.set_value("historical_gl_rebuild_state:" + run_id, state)
        before = self._snapshot()
        events = self._run_worker(run_id)
        assert self._snapshot() == before
        assert any(event.get("status") == "fatal_error" for event in events)

    def test_journal_child_account_restriction_blocks_preview_and_rebuild(self) -> None:
        frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": self.user,
                "allow": "Account",
                "for_value": self.voucher.get("accounts")[1].account,
                "apply_to_all_doctypes": 1,
            }
        ).insert()
        frappe.clear_cache(user=self.user)
        before = self._snapshot()
        frappe.set_user(self.user)
        with self.assertRaises(frappe.PermissionError):
            preview_historical_gl_rebuild()
        with self.assertRaises(frappe.PermissionError):
            rebuild_single_voucher(self.name)
        frappe.set_user("Administrator")
        assert self._snapshot() == before

    def test_manager_without_settings_read_permission_cannot_preview(self) -> None:
        frappe.get_doc(
            {
                "doctype": "Custom DocPerm",
                "parent": "Letter Reconciliation Settings",
                "role": "Accounts Manager",
                "read": 0,
                "write": 0,
            }
        ).insert()
        frappe.clear_cache(doctype="Letter Reconciliation Settings")
        self.addCleanup(frappe.clear_cache, doctype="Letter Reconciliation Settings")
        frappe.set_user(self.user)
        with self.assertRaises(frappe.PermissionError):
            preview_historical_gl_rebuild()

    def test_worker_rejects_voucher_cancelled_after_queueing(self) -> None:
        run_id = self._queue_state()
        frappe.get_doc("Journal Entry", self.name).cancel()
        before = self._snapshot()
        events = self._run_worker(run_id)
        assert self._snapshot() == before
        assert any(event.get("status") == "fatal_error" for event in events)
