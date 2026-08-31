"""Letter Reconciliation Settings controller and historical rebuild actions."""

from __future__ import annotations

from typing import TypedDict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from .historical_gl_rebuild import (
    ClassifiedVoucher,
    PreviewBucket,
    ReasonSummaryGroup,
    RebuildFilters,
    RebuildPreview,
    backfill_reference_detail_no_bulk,
    build_reason_summary_groups,
    build_rebuild_preview,
    get_validated_rebuild_filters,
    rebuild_single_voucher,
)


class LetterReconciliationSettings(Document):
    """Single DocType controlling GL merge prevention behaviour."""


_REBUILD_CACHE_KEY = "historical_gl_rebuild_running"
_REBUILD_STATE_PREFIX = "historical_gl_rebuild_state:"
_CACHE_TTL = 4 * 60 * 60
_REBUILD_BATCH_SIZE = 200

_STATUS_SUCCESS = "success"
_STATUS_PARTIAL_SUCCESS = "partial_success"
_STATUS_ERROR = "error"
_STATUS_FATAL_ERROR = "fatal_error"


class PublicPreviewBucket(TypedDict):
    """Preview bucket returned to the settings UI."""

    count: int
    summary_reason: str
    samples: list[str]
    groups: list[ReasonSummaryGroup]


class PublicRebuildPreview(TypedDict):
    """Subset preview payload returned to the settings UI."""

    filters: RebuildFilters
    total_submitted_vouchers: int
    eligible: PublicPreviewBucket
    blocked: PublicPreviewBucket
    already_correct: PublicPreviewBucket


class RebuildFailure(TypedDict):
    """One failed voucher attempt recorded in the batched run state."""

    voucher_no: str
    reason: str


class EnqueueRebuildResponse(TypedDict):
    """Response payload returned when a rebuild job is queued."""

    progress_event: str
    done_event: str


class RebuildRunState(TypedDict):
    """Cache-backed state shared across chained rebuild batches."""

    run_id: str
    user: str
    progress_event: str
    done_event: str
    filters: RebuildFilters
    total_submitted_vouchers: int
    eligible_vouchers: list[str]
    next_index: int
    rebuilt_count: int
    blocked_count: int
    already_correct_count: int
    failures: list[RebuildFailure]


@frappe.whitelist()
def preview_historical_gl_rebuild() -> PublicRebuildPreview:
    """Preview the selected historical subset before any repost occurs."""
    frappe.only_for(["System Manager", "Accounts Manager"])

    filters = get_validated_rebuild_filters()
    preview = build_rebuild_preview(filters)
    _update_preview_audit(frappe.session.user)
    return _public_preview(preview)


@frappe.whitelist()
def enqueue_historical_gl_rebuild() -> EnqueueRebuildResponse:
    """Queue the subset-based repost rebuild and return realtime event names."""
    frappe.only_for(["System Manager", "Accounts Manager"])

    if frappe.cache.get_value(_REBUILD_CACHE_KEY):
        frappe.throw(_running_rebuild_message())

    filters = get_validated_rebuild_filters()
    preview = build_rebuild_preview(filters)
    eligible_vouchers = [
        voucher["voucher_no"] for voucher in preview["eligible"]["items"]
    ]
    if not eligible_vouchers:
        frappe.throw(_no_eligible_vouchers_message())

    run_id = frappe.generate_hash(length=12)
    progress_event = f"historical_gl_rebuild_progress_{frappe.generate_hash(length=8)}"
    done_event = f"historical_gl_rebuild_done_{frappe.generate_hash(length=8)}"
    state: RebuildRunState = {
        "run_id": run_id,
        "user": frappe.session.user,
        "progress_event": progress_event,
        "done_event": done_event,
        "filters": filters,
        "total_submitted_vouchers": int(preview["total_submitted_vouchers"]),
        "eligible_vouchers": eligible_vouchers,
        "next_index": 0,
        "rebuilt_count": 0,
        "blocked_count": int(preview["blocked"]["count"]),
        "already_correct_count": int(preview["already_correct"]["count"]),
        "failures": [],
    }

    frappe.cache.set_value(_REBUILD_CACHE_KEY, run_id, expires_in_sec=_CACHE_TTL)
    _save_rebuild_state(state)

    try:
        _enqueue_rebuild_batch(run_id)
    except Exception:
        _clear_rebuild_state(run_id)
        raise

    return {
        "progress_event": progress_event,
        "done_event": done_event,
    }


def run_historical_gl_rebuild_job(run_id: str) -> None:
    """Process one chained background batch of the historical rebuild."""
    state = _get_rebuild_state(run_id)
    if not state:
        _clear_orphaned_rebuild_lock(run_id)
        return

    try:
        total = len(state["eligible_vouchers"])
        if not total:
            frappe.throw(_no_eligible_vouchers_message())

        start_index = state["next_index"]
        end_index = min(start_index + _REBUILD_BATCH_SIZE, total)
        batch_vouchers = state["eligible_vouchers"][start_index:end_index]
        backfill_reference_detail_no_bulk(batch_vouchers)

        for absolute_index, voucher_no in enumerate(
            batch_vouchers, start=start_index + 1
        ):
            percent = int(((absolute_index - 1) / total) * 100)
            _publish_progress(
                state["progress_event"],
                state["user"],
                percent,
                _("Rebuilding {0} ({1} of {2}, batch {3} of {4})…").format(
                    voucher_no,
                    absolute_index,
                    total,
                    _get_batch_number(absolute_index),
                    _get_total_batches(total),
                ),
            )

            savepoint = f"historical_gl_rebuild_{absolute_index}"
            frappe.db.savepoint(savepoint)

            try:
                rebuild_single_voucher(
                    voucher_no,
                    reference_detail_backfilled=True,
                )
            except Exception as exc:
                frappe.db.rollback(save_point=savepoint)
                frappe.log_error(
                    frappe.get_traceback(),
                    f"Historical GL rebuild failed for {voucher_no}",
                )
                state["failures"].append(
                    {
                        "voucher_no": voucher_no,
                        "reason": str(exc),
                    }
                )
            else:
                state["rebuilt_count"] += 1
                _commit_rebuild_progress()

            state["next_index"] = absolute_index
            _save_rebuild_state(state)

        if state["next_index"] < total:
            _publish_progress(
                state["progress_event"],
                state["user"],
                int((state["next_index"] / total) * 100),
                _("Queued the next rebuild batch…"),
            )
            _enqueue_rebuild_batch(run_id)
            return

        _finalise_rebuild_run(state)
    except Exception:
        frappe.db.rollback()
        frappe.log_error(frappe.get_traceback(), "Historical GL rebuild failed")
        _publish_rebuild_failure(state)
        _clear_rebuild_state(run_id)


@frappe.whitelist()
def enqueue_gl_entry_migration() -> None:
    """Block the retired split-based migration entry point."""
    frappe.only_for(["System Manager", "Accounts Manager"])
    frappe.throw(
        _(
            "The split-based GL Entry migration has been retired. "
            "Use Preview Historical Rebuild and Run Historical Rebuild instead."
        )
    )


@frappe.whitelist()
def run_gl_split_diagnostic() -> None:
    """Block the retired split-diagnostic entry point."""
    frappe.only_for(["System Manager", "Accounts Manager"])
    frappe.throw(
        _(
            "The split-migration diagnostic has been retired. "
            "Use Preview Historical Rebuild for subset classification instead."
        )
    )


def _publish_progress(event: str, user: str, current: int, message: str) -> None:
    frappe.publish_realtime(
        event,
        {"current": current, "total": 100, "message": message},
        user=user,
    )


def _update_preview_audit(user: str) -> None:
    settings = frappe.get_single("Letter Reconciliation Settings")
    settings.last_preview_run = now_datetime()
    settings.last_preview_user = user
    settings.save(ignore_permissions=True)


def _update_rebuild_audit(user: str, status: str) -> None:
    settings = frappe.get_single("Letter Reconciliation Settings")
    settings.last_migration_run = now_datetime()
    settings.last_migration_status = status
    settings.last_migration_user = user
    settings.save(ignore_permissions=True)
    _commit_rebuild_progress()


def _audit_status_label(status: str) -> str:
    labels = {
        _STATUS_SUCCESS: _("Success"),
        _STATUS_PARTIAL_SUCCESS: _("Partial Success"),
        _STATUS_ERROR: _("Failed"),
    }
    return labels.get(status, _("Failed"))


def _running_rebuild_message() -> str:
    return _(
        "A historical GL rebuild is already running. Please wait for it to finish."
    )


def _no_eligible_vouchers_message() -> str:
    return _(
        "No eligible historical Journal Entries were found for the selected scope."
    )


def _commit_rebuild_progress() -> None:
    frappe.db.commit()  # nosemgrep: frappe-manual-commit


def _enqueue_rebuild_batch(run_id: str) -> None:
    frappe.enqueue(
        "karam_finance.letter_reconciliation.doctype"
        ".letter_reconciliation_settings"
        ".letter_reconciliation_settings"
        ".run_historical_gl_rebuild_job",
        queue="long",
        timeout=4 * 60 * 60,
        run_id=run_id,
    )


def _get_rebuild_state(run_id: str) -> RebuildRunState | None:
    state = frappe.cache.get_value(_get_rebuild_state_key(run_id))
    return state or None


def _save_rebuild_state(state: RebuildRunState) -> None:
    frappe.cache.set_value(
        _get_rebuild_state_key(state["run_id"]),
        state,
        expires_in_sec=_CACHE_TTL,
    )


def _clear_rebuild_state(run_id: str) -> None:
    frappe.cache.delete_value(_REBUILD_CACHE_KEY)
    frappe.cache.delete_value(_get_rebuild_state_key(run_id))


def _clear_orphaned_rebuild_lock(run_id: str) -> None:
    locked_run_id = frappe.cache.get_value(_REBUILD_CACHE_KEY)
    if locked_run_id == run_id:
        frappe.cache.delete_value(_REBUILD_CACHE_KEY)
    frappe.cache.delete_value(_get_rebuild_state_key(run_id))


def _get_rebuild_state_key(run_id: str) -> str:
    return f"{_REBUILD_STATE_PREFIX}{run_id}"


def _get_batch_number(absolute_index: int) -> int:
    return ((absolute_index - 1) // _REBUILD_BATCH_SIZE) + 1


def _get_total_batches(total: int) -> int:
    return max(1, (total + _REBUILD_BATCH_SIZE - 1) // _REBUILD_BATCH_SIZE)


def _finalise_rebuild_run(state: RebuildRunState) -> None:
    """Publish the final run summary once all batches are processed."""
    failures = state["failures"]
    rebuilt = state["rebuilt_count"]

    status = _STATUS_PARTIAL_SUCCESS if failures else _STATUS_SUCCESS
    failure_groups = _build_failure_groups(failures)

    _publish_progress(state["progress_event"], state["user"], 100, _("Complete"))
    _update_rebuild_audit(state["user"], _audit_status_label(status))

    frappe.publish_realtime(
        state["done_event"],
        {
            "status": status,
            "rebuilt_count": rebuilt,
            "blocked_count": state["blocked_count"],
            "already_correct_count": state["already_correct_count"],
            "failed_count": len(failures),
            "failed_groups": failure_groups,
        },
        user=state["user"],
    )
    _clear_rebuild_state(state["run_id"])


def _publish_rebuild_failure(state: RebuildRunState) -> None:
    """Publish a fatal rebuild failure for the current run state."""
    try:
        _update_rebuild_audit(state["user"], _("Failed"))
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "Historical GL rebuild audit update failed",
        )

    frappe.publish_realtime(
        state["done_event"],
        {
            "status": _STATUS_FATAL_ERROR,
            "message": _("Historical rebuild failed. Check the Error Log for details."),
        },
        user=state["user"],
    )


def _build_failure_groups(
    failures: list[RebuildFailure],
) -> list[ReasonSummaryGroup]:
    if not failures:
        return []

    failure_items: list[ClassifiedVoucher] = [
        {
            "voucher_no": item["voucher_no"],
            "posting_date": "",
            "reason": item["reason"],
        }
        for item in failures
    ]
    return build_reason_summary_groups(
        failure_items,
        group_limit=None,
        sample_limit=None,
        include_full_lists=True,
    )


def _public_preview(preview: RebuildPreview) -> PublicRebuildPreview:
    """Return the UI preview without the raw per-voucher item payloads."""
    return {
        "filters": preview["filters"],
        "total_submitted_vouchers": preview["total_submitted_vouchers"],
        "eligible": _strip_bucket_items(preview["eligible"]),
        "blocked": _strip_bucket_items(preview["blocked"]),
        "already_correct": _strip_bucket_items(preview["already_correct"]),
    }


def _strip_bucket_items(bucket: PreviewBucket) -> PublicPreviewBucket:
    """Drop server-only item lists before returning preview data to the client."""
    return {
        "count": bucket["count"],
        "summary_reason": bucket["summary_reason"],
        "samples": bucket["samples"],
        "groups": list(bucket["groups"]),
    }
