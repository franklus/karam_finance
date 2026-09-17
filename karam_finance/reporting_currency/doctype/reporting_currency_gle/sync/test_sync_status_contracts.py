"""Pure contract tests for reporting-currency status recovery."""

from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import call, patch

import frappe
from frappe.utils.background_jobs import JobStatus

from karam_finance.reporting_currency import currency_change
from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync import (
    orchestrator,
)


def _raise_validation(message: str, *_args: object, **_kwargs: object) -> None:
    raise frappe.ValidationError(message)


class TestSyncStatusRecovery(TestCase):
    def test_completed_results_survive_missed_realtime_events(self) -> None:
        event = "rc_gle_sync_abcdef123456"
        with (
            patch.object(orchestrator, "frappe") as api,
            patch.object(orchestrator, "get_job_status") as job_status,
        ):
            for status in ("success", "partial_success", "error"):
                with self.subTest(status=status):
                    payload = {"status": status, "inserted": 2}
                    orchestrator._publish_sync_result(event + "_done", "user", payload)
                    saved = api.cache.set_value.call_args
                    assert saved.args == ("rc_sync_result:" + event + "_done", payload)
                    assert saved.kwargs["expires_in_sec"] == 3600
                    api.cache.get_value.return_value = saved.args[1]
                    job_status.reset_mock()
                    assert orchestrator.get_reporting_currency_sync_status(event) == {
                        "state": "complete",
                        "result": payload,
                    }
                    job_status.assert_called_once_with(event)
            api.has_permission.assert_called_with(
                "Reporting Currency GLE", "write", throw=True
            )

    def test_active_job_status_is_read_without_enqueuing(self) -> None:
        with (
            patch.object(orchestrator, "frappe") as api,
            patch.object(
                orchestrator,
                "get_job_status",
                return_value=JobStatus.QUEUED,
            ) as status,
        ):
            api.cache.get_value.return_value = None
            assert orchestrator.get_reporting_currency_sync_status(
                "rc_gle_sync_abcdef123456"
            ) == {"state": "queued"}
            status.assert_called_once_with("rc_gle_sync_abcdef123456")
            api.enqueue.assert_not_called()

    def test_currency_change_status_uses_the_same_cached_result_recovery(self) -> None:
        event = "rc_currency_change_abcdef1234567890"
        payload = {"status": "error", "message": "failed"}
        with (
            patch.object(orchestrator, "frappe") as api,
            patch.object(orchestrator, "get_job_status", return_value=None) as status,
        ):
            api.cache.get_value.return_value = payload
            assert orchestrator.get_reporting_currency_sync_status(event) == {
                "state": "complete",
                "result": payload,
            }
        assert api.has_permission.call_args_list == [
            call("Reporting Currency Settings", "write", throw=True),
            call("Reporting Currency GLE", "write", throw=True),
        ]
        status.assert_called_once_with(event)

    def test_currency_change_retry_gets_new_event_after_terminal_failure(self) -> None:
        settings = SimpleNamespace(
            modified="2026-09-10 10:00:00",
            rc_parameters=[],
        )
        cached: dict[str, object] = {}
        job_states: dict[str, JobStatus] = {}
        with (
            patch.object(currency_change, "frappe") as api,
            patch.object(currency_change, "_check_permissions"),
            patch.object(currency_change, "_validate_request", return_value=settings),
            patch.object(
                currency_change,
                "get_job_status",
                side_effect=job_states.get,
            ),
        ):
            api.session.user = "Administrator"

            api.cache.get_value.side_effect = cached.get

            def cache_set(key: str, value: object, **_kwargs: object) -> None:
                cached.__setitem__(key, value)

            api.cache.set_value.side_effect = cache_set
            api.generate_hash.return_value = "retry1234"
            api.enqueue.return_value = SimpleNamespace(id="job")

            first = currency_change.enqueue_currency_change(
                "GBP", settings.modified, confirmed=True
            )
            first_event = first["progress_event"]
            job_states[first_event] = JobStatus.FAILED
            cached[f"rc_sync_result:{first_event}_done"] = {
                "status": "error",
                "message": "first attempt failed",
            }

            retry = currency_change.enqueue_currency_change(
                "GBP", settings.modified, confirmed=True
            )
            retry_event = retry["progress_event"]
            job_states[retry_event] = JobStatus.STARTED
            duplicate = currency_change.enqueue_currency_change(
                "GBP", settings.modified, confirmed=True
            )

        assert retry_event != first_event
        assert retry_event.endswith("_retry1234")
        assert duplicate == retry

    def test_status_rejects_unauthorised_and_invalid_requests_before_reading(
        self,
    ) -> None:
        with patch.object(orchestrator, "frappe") as api:
            api.only_for.side_effect = frappe.PermissionError
            with self.assertRaises(frappe.PermissionError):
                orchestrator.get_reporting_currency_sync_status(
                    "rc_gle_sync_abcdef123456"
                )
            api.cache.get_value.assert_not_called()
            api.only_for.side_effect = None
            api.throw.side_effect = _raise_validation
            with self.assertRaises(frappe.ValidationError):
                orchestrator.get_reporting_currency_sync_status("another-site||job")
            api.cache.get_value.assert_not_called()
