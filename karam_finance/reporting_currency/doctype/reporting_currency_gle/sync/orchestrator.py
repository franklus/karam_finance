"""Sync engine for Reporting Currency GL Entries.

This module serves as the main orchestrator for synchronising GL Entry records
to the Reporting Currency GLE table, converting amounts using Currency Exchange rates.

Architecture:
- utils.py: Progress publishing, column capacity, hash generation, CSV export
- validation.py: Settings validation, currency coverage checks, date range utilities
- data_fetch.py: GL Entry fetching, cancelled entries, orphan cleanup
- exchange_rates.py: Timeline building, rate lookup, temporal validation
- conversion.py: Amount conversion, GL Entry processing
- phases.py: The five sync phase functions
"""

from __future__ import annotations

import time
from typing import Any

import frappe
from frappe import _
from frappe.exceptions import PermissionError as FrappePermissionError
from frappe.utils import now
from frappe.utils.background_jobs import JobStatus, get_job_status

from karam_finance.common.db_schema import ensure_currency_columns_capacity
from karam_finance.reporting_currency.ledger_lock import (
    hold_ledger_lock,
    ledger_operation,
)

# ============================================================================
# IMPORTS FROM SUBMODULES (relative imports within sync package)
# ============================================================================
from .context import InsertionContext, SyncSnapshot
from .exchange_rates import (
    build_exchange_rate_timeline,
    validate_temporal_coverage,
)
from .naming import get_synced_rc_gle_name
from .phases import (
    run_conversion_phase,
    run_deletion_phase,
    run_insertion_phase,
    run_temporal_reconciliation_phase,
    run_validation_phase,
)
from .utils import (
    export_missing_currency_gl_entries_csv,
    export_temporal_validation_entries_csv,
    get_gl_entry_stable_hash,
    publish_sync_progress,
)
from .validation import (
    get_company_default_currency,
    validate_currency_exchange_coverage,
    validate_settings,
)

# ============================================================================
# MODULE CONSTANTS
# ============================================================================

# DocType names
DOCTYPE_GL_ENTRY = "GL Entry"
DOCTYPE_RC_GLE = "Reporting Currency GLE"
DOCTYPE_RC_SETTINGS = "Reporting Currency Settings"

# Progress percentages
PROGRESS_START = 0
PROGRESS_COMPLETE = 100

# Caching
PROGRESS_EVENT_HASH_LENGTH = 12

# Silence unused import warnings - these are re-exported for backwards compatibility
__all__ = [
    "build_exchange_rate_timeline",
    "ensure_currency_columns_capacity",
    "export_missing_currency_gl_entries_csv",
    "export_temporal_validation_entries_csv",
    "get_company_default_currency",
    "get_gl_entry_stable_hash",
    "publish_sync_progress",
    "validate_currency_exchange_coverage",
    "validate_settings",
    "validate_temporal_coverage",
]


# ============================================================================
# MAIN ORCHESTRATOR
# ============================================================================


# Public sync API retains its positional calling convention.
def sync_reporting_currency_entries(  # noqa: PLR0913, PLR0917 - retained positional API plus an atomic worker cutoff.
    progress_event: str,
    user: str | None = None,
    cached_currency_coverage: dict[str, Any] | SyncSnapshot | None = None,
    cached_rate_timeline: list[dict[str, Any]] | None = None,
    cached_default_currency: str | None = None,
    *,
    source_cutoff: str | None = None,
) -> dict[str, Any]:
    """Main orchestrator for Reporting Currency sync.

    Delegates work to 5 phase functions:
    1. run_validation_phase() - Validates settings and fetches GL entries
    2. run_deletion_phase() - Handles cancelled/deleted GL entries and orphans
    3. run_temporal_reconciliation_phase() - Builds exchange rate timeline
    4. run_conversion_phase() - Processes and converts GL entries
    5. run_insertion_phase() - Bulk inserts RC GLE records

    Args:
        progress_event: Event name for realtime progress updates
        user: User to send realtime updates to
        cached_currency_coverage: Queue-time snapshot used only to detect a
            changed reporting currency. The worker rereads coverage at its
            transaction boundary.
        cached_rate_timeline: Legacy queue argument, ignored by the worker.
        cached_default_currency: Legacy queue argument, ignored by the worker.

    Returns stats dict with counts and timing.
    """
    hold_ledger_lock()
    start_time = time.monotonic()
    snapshot = (
        cached_currency_coverage
        if isinstance(cached_currency_coverage, SyncSnapshot)
        else None
    )
    # Queue-time values were read outside this transaction. Keep only the
    # optional snapshot for the currency-identity check below; all rate and GL
    # inputs are reread together at the worker boundary.
    sync_cutoff = source_cutoff or now()
    cached_currency_coverage = None
    cached_rate_timeline = None
    cached_default_currency = None
    stats: dict[str, Any] = {"inserted": 0, "skipped": 0, "errors": 0, "deleted": 0}

    # Initial progress update
    publish_sync_progress(
        progress_event,
        PROGRESS_START,
        "Phase 1: Validating settings and parameters...",
        user,
    )

    # Get settings and prepare for Phase 1
    settings = validate_settings()
    reporting_currency = settings["reporting_currency"]
    last_sync_timestamp = settings["last_sync_timestamp"]

    if snapshot is not None:
        validate_snapshot_currency(snapshot, reporting_currency)

    # ===== PHASE 1: VALIDATION =====
    (
        gl_entries,
        default_currency,
        is_incremental,
        sync_mode,
        currency_coverage,
        effective_last_sync,
    ) = run_validation_phase(
        progress_event,
        user,
        reporting_currency,
        last_sync_timestamp,
        cached_currency_coverage,
        cached_default_currency,
    )

    # ===== PHASE 3: TEMPORAL RECONCILIATION =====
    rate_timeline: list[dict[str, Any]] = []
    rate_dates: list[Any] = []
    if gl_entries:
        rate_timeline, rate_dates = run_temporal_reconciliation_phase(
            progress_event,
            user,
            gl_entries,
            default_currency,
            reporting_currency,
            currency_coverage,
            cached_rate_timeline,
        )

    # ===== PHASE 2: DELETION =====
    # Handle cancelled/deleted GL entries and orphaned RC GLE records
    # This runs before processing new/modified entries to ensure clean state
    deletion_stats = run_deletion_phase(
        progress_event, user, effective_last_sync, is_incremental
    )
    stats["deleted"] = deletion_stats.get("deleted_cancelled", 0) + deletion_stats.get(
        "deleted_orphaned", 0
    )

    # Early exit if no GL entries to process
    if not gl_entries:
        stats.update(
            run_insertion_phase(
                progress_event,
                user,
                [],
                [],
                InsertionContext(is_incremental, sync_cutoff),
            )
        )
        return stats

    # ===== PHASE 4: CURRENCY CONVERSION =====
    rc_gle_records = run_conversion_phase(
        progress_event,
        user,
        gl_entries,
        rate_timeline,
        rate_dates,
        default_currency,
        reporting_currency,
    )

    # ===== PHASE 5: BULK INSERTION =====
    stats.update(
        run_insertion_phase(
            progress_event,
            user,
            rc_gle_records,
            gl_entries,
            InsertionContext(is_incremental, sync_cutoff),
        )
    )

    # Final stats calculation
    stats.update(
        {
            "skipped": len(gl_entries) - len(rc_gle_records),
            "duration_seconds": round(time.monotonic() - start_time, 2),
            "sync_mode": sync_mode,
        }
    )

    publish_sync_progress(
        progress_event,
        PROGRESS_COMPLETE,
        _("Sync completed successfully! ({0})").format(sync_mode),
        user,
    )

    return stats


# ============================================================================
# BACKGROUND JOB WRAPPERS
# ============================================================================


def validate_snapshot_currency(snapshot: SyncSnapshot, reporting_currency: str) -> None:
    """Reject cached rates whose target is unknown or no longer configured."""
    snapshot_currency = getattr(snapshot, "reporting_currency", None)
    if not snapshot_currency:
        frappe.throw(
            _(
                "This queued sync has no target currency. Run Sync again to refresh its rates."
            ),
            title=_("Sync stopped: outdated snapshot"),
        )
    if snapshot_currency != reporting_currency:
        frappe.throw(
            _(
                "Reporting currency changed from {0} to {1} after this sync was queued. "
                "Run Sync again to refresh its rates."
            ).format(snapshot_currency, reporting_currency),
            title=_("Sync stopped: reporting currency changed"),
        )


@frappe.whitelist()
def enqueue_reporting_currency_sync() -> dict[str, str]:
    """Queue the RC GLE sync job and return identifiers for realtime updates.

    Performs lightweight pre-flight checks and critical validation that
    requires HTML dialog rendering. Heavy processing is deferred to the
    background job for better user experience.
    """
    frappe.only_for("System Manager")
    if not frappe.has_permission(DOCTYPE_RC_GLE, "write"):
        frappe.throw(
            _("You do not have permission to sync Reporting Currency GLE records."),
            FrappePermissionError,
        )

    # Capture the source-acquisition cutoff before any foreground reads. The
    # background job uses this same cutoff when advancing the sync watermark;
    # cached rate snapshots therefore cannot hide changes made after capture.
    sync_cutoff = now()

    # Lightweight validation - settings only
    settings = validate_settings()
    reporting_currency = settings["reporting_currency"]

    # CRITICAL PRE-FLIGHT VALIDATION (must be in foreground for HTML rendering):
    # Fetch minimal data needed for temporal validation only
    gl_date_data = frappe.db.sql(  # nosemgrep — no user input
        f"""
		SELECT name, posting_date, account, account_currency, voucher_no, company
		FROM `tab{DOCTYPE_GL_ENTRY}`
		WHERE docstatus = 1 AND COALESCE(is_cancelled, 0) = 0
		ORDER BY posting_date ASC
		""",  # noqa: S608
        as_dict=True,
    )

    # An empty source still needs the locked worker to remove generated snapshots
    # and DOE. Do not freeze an empty foreground result: the worker reads afresh.
    snapshot = None
    if gl_date_data:
        # Get company's default currency
        company = gl_date_data[0].get("company")
        default_currency = get_company_default_currency(company)

        # Validate currency exchange coverage
        currency_coverage = validate_currency_exchange_coverage(
            gl_date_data, reporting_currency, default_currency
        )

        # Build exchange rate timeline
        rate_timeline = build_exchange_rate_timeline(
            default_currency, reporting_currency, currency_coverage
        )

        # CRITICAL: Validate temporal coverage - must show HTML dialog if errors
        validate_temporal_coverage(
            gl_date_data, rate_timeline, default_currency, reporting_currency
        )

        snapshot = SyncSnapshot(
            currency_coverage=currency_coverage,
            rate_timeline=rate_timeline,
            default_currency=default_currency,
            cutoff=sync_cutoff,
            reporting_currency=reporting_currency,
        )

    # All validations passed - queue background job
    progress_event = (
        f"rc_gle_sync_{frappe.generate_hash(length=PROGRESS_EVENT_HASH_LENGTH)}"
    )
    done_event = f"{progress_event}_done"

    # Pass the pre-flight only as an advisory currency identity. The worker
    # deliberately rebuilds all rates and coverage in its fresh transaction.
    job = frappe.enqueue(
        "karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.run_reporting_currency_sync_job",
        queue="long",
        job_id=progress_event,
        progress_event=progress_event,
        done_event=done_event,
        user=frappe.session.user,
        # Foreground validation is advisory; the worker rereads rates and GL
        # inputs together in its fresh transaction.
        cached_currency_coverage=snapshot,
    )

    return {
        "job_id": job.id if job else progress_event,
        "progress_event": progress_event,
        "done_event": done_event,
    }


# Queued entry point retains its existing serialised argument contract.
def run_reporting_currency_sync_job(  # noqa: PLR0913, PLR0917
    progress_event: str,
    done_event: str,
    user: str | None = None,
    cached_currency_coverage: dict[str, Any] | SyncSnapshot | None = None,
    cached_rate_timeline: list[dict[str, Any]] | None = None,
    cached_default_currency: str | None = None,
) -> None:
    """Entry point for the background job. Handles errors and sends realtime updates.

    Implements fail-fast policy with automatic transaction rollback on any error.
    Ensures data integrity - either all records sync successfully or none do.

    Args:
        progress_event: Event name for realtime progress updates
        done_event: Event name for completion notification
        user: User to send realtime updates to
        cached_currency_coverage: Queue-time snapshot used only to detect a
            changed reporting currency; worker inputs are reread.
        cached_rate_timeline: Legacy queue argument, ignored by the worker.
        cached_default_currency: Legacy queue argument, ignored by the worker.
    """
    start = time.monotonic()
    try:
        with ledger_operation():
            frappe.only_for("System Manager")
            frappe.has_permission(DOCTYPE_RC_GLE, "write", throw=True)
            ensure_currency_columns_capacity()
            frappe.db.begin()
            stats = sync_reporting_currency_entries(
                progress_event=progress_event,
                user=user,
                cached_currency_coverage=cached_currency_coverage,
                cached_rate_timeline=cached_rate_timeline,
                cached_default_currency=cached_default_currency,
            )
            # Ordinary sync retains its committed GL result if the later DOE fails.
            frappe.db.commit()  # nosemgrep — background job
            _attach_post_sync_doe(stats, progress_event, user)
            frappe.db.commit()  # nosemgrep — finish DOE before releasing the lock
    except Exception as exc:
        _publish_job_error(done_event, user, exc)
        raise
    stats["duration_seconds"] = round(time.monotonic() - start, 2)
    _publish_job_completion(done_event, user, stats)


def _attach_post_sync_doe(
    stats: dict[str, Any], progress_event: str, user: str | None
) -> None:
    from .doe import compute_doe_inline  # noqa: PLC0415

    try:
        result = compute_doe_inline(progress_event, user)
        if result.get("success"):
            stats["doe_accounts_processed"] = result.get("accounts_processed", 0)
            stats["doe_records_created"] = result.get("records_created", 0)
    except Exception:  # noqa: BLE001 - preserve the already committed GL sync on DOE failure.
        stats["doe_failed"] = True
        frappe.log_error(
            title="DOE Computation Failed (Post-Sync)", message=frappe.get_traceback()
        )


def _publish_job_error(done_event: str, user: str | None, error: Exception) -> None:
    titles = {
        "ValidationError": _("Validation Error"),
        "MandatoryError": _("Missing Required Data"),
        "DoesNotExistError": _("Record Not Found"),
        "PermissionError": _("Permission Denied"),
    }
    frappe.log_error(
        title="Reporting Currency GLE Sync Failure", message=frappe.get_traceback()
    )
    _publish_sync_result(
        done_event,
        user,
        {
            "status": "error",
            "title": titles.get(type(error).__name__, _("Sync Failed")),
            "message": str(error),
        },
    )


def _publish_job_completion(
    done_event: str, user: str | None, stats: dict[str, Any]
) -> None:
    stats = {"inserted": 0, "skipped": 0, "errors": 0, **stats}
    if stats.pop("doe_failed", False):
        message = {
            "status": "partial_success",
            "title": _("Sync Completed, DOE Failed"),
            "message": _(
                "Reporting Currency entries were synced successfully, but DOE computation failed. Check the Error Log for details."
            ),
            **stats,
        }
    else:
        message = {"status": "success", **stats}
    _publish_sync_result(done_event, user, message)


def _publish_sync_result(
    done_event: str, user: str | None, message: dict[str, Any]
) -> None:
    """Retain terminal results so late or disconnected browsers can recover them."""
    if frappe.cache is None:
        error_message = "Sync status cache is unavailable"
        raise RuntimeError(error_message)
    frappe.cache.set_value(f"rc_sync_result:{done_event}", message, expires_in_sec=3600)
    frappe.publish_realtime(done_event, message=message, user=user)


@frappe.whitelist()
def get_reporting_currency_sync_status(progress_event: str) -> dict[str, Any]:
    """Read an existing site-local sync; never enqueue a replacement job."""
    frappe.only_for("System Manager")
    event_spec = _sync_status_event_spec(progress_event)
    if event_spec is None:
        frappe.throw(_("Invalid reporting currency sync identifier."))
        return {"state": "missing"}
    permission_doctypes, valid = event_spec
    if not valid:
        frappe.throw(_("Invalid reporting currency sync identifier."))
    for permission_doctype in permission_doctypes:
        frappe.has_permission(permission_doctype, "write", throw=True)
    if frappe.cache is None:
        error_message = "Sync status cache is unavailable"
        raise RuntimeError(error_message)
    # Read queue state first: a worker finishing during this request saves its
    # result before RQ reports it finished, so the following cache read sees it.
    status = get_job_status(progress_event)
    if status in (JobStatus.QUEUED, JobStatus.STARTED):
        return {"state": status.value}
    result = frappe.cache.get_value(f"rc_sync_result:{progress_event}_done")
    if result is not None:
        return {"state": "complete", "result": result}
    return {"state": status.value if status else "missing"}


def _sync_status_event_spec(
    progress_event: str,
) -> tuple[tuple[str, ...], bool] | None:
    """Return the permissions and shape check for a site-local job event."""
    event_specs = {
        "rc_gle_sync_": (12, (DOCTYPE_RC_GLE,)),
        "rc_currency_change_": (16, (DOCTYPE_RC_SETTINGS, DOCTYPE_RC_GLE)),
    }
    prefix = next(
        (
            candidate
            for candidate in event_specs
            if progress_event.startswith(candidate)
        ),
        None,
    )
    if prefix is None:
        return None
    expected_length, permission_doctypes = event_specs[prefix]
    suffix = progress_event.removeprefix(prefix)
    valid = len(suffix) == expected_length and suffix.isalnum()
    if prefix == "rc_currency_change_":
        valid = valid or (
            len(suffix) == expected_length + 9
            and suffix[expected_length] == "_"
            and suffix[:expected_length].isalnum()
            and suffix[expected_length + 1 :].isalnum()
        )
    return permission_doctypes, valid


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================


@frappe.whitelist()
@ledger_operation()
def delete_all_entries() -> None:
    """Delete all sync-generated Reporting Currency GLE records.

    Preserves manually created records (manual_entry=1).
    Also resets sync timestamps to NULL, triggering full sync on next run.
    """
    frappe.only_for("System Manager")
    # Delete only sync-generated records, preserve manual entries
    frappe.db.sql(
        "DELETE FROM `tabReporting Currency GLE` "
        "WHERE manual_entry = 0 OR manual_entry IS NULL"
    )

    # Reset both sync timestamps to NULL to trigger full sync next time
    frappe.db.set_single_value(
        DOCTYPE_RC_SETTINGS,
        {"last_sync_timestamp": None, "last_ce_sync_timestamp": None},
        update_modified=False,
    )
    frappe.db.commit()  # nosemgrep — destructive op, immediate persist


# ============================================================================
# GL ENTRY RENAME HOOKS
# ============================================================================


def _update_rc_gle_for_renamed_gl_entry(old: str, new: str) -> None:
    """Core rename logic shared by both hook mechanisms.

    Finds the RC GLE record linked to the old GL Entry name, derives a new
    RC GLE name from the new GL Entry name, and updates both the name and
    the gl_entry link field.

    Args:
        old: Old GL Entry name (e.g., "f55e844ed1")
        new: New GL Entry name (e.g., "ACC-GLE-2025-26822")
    """
    fields = ["name", "manual_entry", "reporting_doe"]
    record = frappe.db.get_value(
        DOCTYPE_RC_GLE, {"gl_entry": old}, fields, as_dict=True
    )
    if not record:
        # Frappe's native rename updates Link fields before after_rename runs.
        # ERPNext's scheduler calls the hook with the old link still present.
        record = frappe.db.get_value(
            DOCTYPE_RC_GLE, {"gl_entry": new}, fields, as_dict=True
        )

    if not record:
        return

    rc_gle_name = record.name
    # A source rename updates protected links, never their independent IDs.
    new_rc_gle_name = (
        rc_gle_name
        if record.manual_entry or record.reporting_doe
        else get_synced_rc_gle_name(new)
    )

    frappe.db.sql(
        """
		UPDATE `tabReporting Currency GLE`
		SET name = %s, gl_entry = %s, modified = %s
		WHERE name = %s
	""",
        (new_rc_gle_name, new, now(), rc_gle_name),
    )


# Frappe rename hook supplies these positional arguments.
def on_gl_entry_rename(  # noqa: PLR0917
    doc: object,
    method: str,
    old: str,
    new: str,
    merge: bool = False,  # noqa: V107, ARG001
) -> None:
    """Doc_event after_rename handler for GL Entry.

    Covers manual renames via frappe.rename_doc() and UI operations.

    Args:
        doc: The GL Entry document (unused)
        method: Hook method name (unused)
        old: Old GL Entry name
        new: New GL Entry name
        merge: Whether this is a merge operation (unused)
    """
    del doc, method  # unused
    hold_ledger_lock()
    _update_rc_gle_for_renamed_gl_entry(old, new)


def on_gl_entry_before_rename(  # noqa: PLR0917
    doc: object,
    method: str,
    old: str,
    new: str,
    merge: bool = False,  # noqa: V107
) -> None:
    """Acquire the reporting-ledger lock before Frappe renames the source row."""
    del doc, method, old, new, merge
    hold_ledger_lock()


def on_gle_rename_hook(newname: str, oldname: str) -> None:  # noqa: V103 - ERPNext scheduler hook registered in hooks.py.
    """Top-level on_gle_rename hook for ERPNext's rename_temporarily_named_docs().

    ERPNext's scheduled job calls frappe.get_hooks("on_gle_rename") with
    keyword args (newname=..., oldname=...) — different from the doc_event
    after_rename signature.

    Args:
        newname: New GL Entry name (e.g., "ACC-GLE-2025-26822")
        oldname: Old GL Entry name (e.g., "f55e844ed1")
    """
    hold_ledger_lock()
    _update_rc_gle_for_renamed_gl_entry(oldname, newname)
