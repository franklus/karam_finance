"""Sync phase functions for Reporting Currency GLE.

This module contains the five main phases of the sync process:
1. Validation phase - settings and GL entry validation
2. Deletion phase - handle cancelled entries and orphans
3. Temporal reconciliation phase - build exchange rate timeline
4. Conversion phase - process and convert GL entries
5. Insertion phase - bulk insert RC GLE records
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

from .context import InsertionContext
from .conversion import process_gl_entry
from .data_fetch import (
    cleanup_orphaned_rc_gle_records,
    delete_rc_gle_for_cancelled_gl_entries,
    fetch_cancelled_gl_entries,
    fetch_gl_entries,
)
from .exchange_rates import build_exchange_rate_timeline, validate_temporal_coverage
from .naming import get_synced_rc_gle_name
from .utils import publish_sync_progress
from .validation import (
    get_company_default_currency,
    validate_currency_exchange_coverage,
)

# ============================================================================
# MODULE CONSTANTS
# ============================================================================

# DocType names
DOCTYPE_RC_GLE = "Reporting Currency GLE"
DOCTYPE_RC_SETTINGS = "Reporting Currency Settings"

# Progress percentages
PROGRESS_PHASE1_START = 10
PROGRESS_PHASE1_END = 20
PROGRESS_PHASE2_START = 30
PROGRESS_PHASE2_VALIDATION = 35
PROGRESS_PHASE3_START = 40
PROGRESS_PHASE3_END = 80
PROGRESS_PHASE4_START = 85
PROGRESS_PHASE4_DELETE = 87
PROGRESS_PHASE4_INSERT = 90
PROGRESS_COMPLETE = 100

# Processing constants
PROGRESS_UPDATE_FREQUENCY = 100


# ============================================================================
# PHASE 1: VALIDATION
# ============================================================================


# Retain the existing positional phase API for sync callers.
def run_validation_phase(  # noqa: PLR0913, PLR0917
    progress_event: str,
    user: str | None,
    reporting_currency: str,
    last_sync_timestamp: str | None,
    cached_currency_coverage: dict[str, Any] | None = None,
    cached_default_currency: str | None = None,
) -> tuple[
    list[dict[str, Any]],
    str,
    bool,
    str,
    dict[str, bool],
    str | None,
]:
    """Phase 1: Validation & Parameter Establishment.

    Validates settings, fetches all GL entries for a full sync, and
    validates currency coverage.

    Args:
        progress_event: Event ID for realtime progress updates
        user: User ID for realtime messaging
        reporting_currency: Target reporting currency
        last_sync_timestamp: Retained for caller compatibility; never limits a sync
        cached_currency_coverage: Pre-computed coverage from foreground
        cached_default_currency: Company default currency from foreground

    Returns:
        Tuple of (gl_entries, default_currency, is_incremental, sync_mode,
        currency_coverage, effective_last_sync)
    """
    # Every normal sync must refresh historical amounts after rate edits or deletions.
    # Retain the phase signature and result shape for existing callers.
    del last_sync_timestamp
    is_incremental = False
    sync_mode = _("Full Sync")
    effective_last_sync = None

    # Fetch all GL entries with full schema (35 fields) for processing
    publish_sync_progress(
        progress_event,
        PROGRESS_PHASE1_START,
        _("Fetching GL Entries ({0})...").format(sync_mode),
        user,
    )

    gl_entries = fetch_gl_entries(last_sync_timestamp=effective_last_sync)

    if not gl_entries:
        publish_sync_progress(
            progress_event, PROGRESS_COMPLETE, "No GL Entries to process.", user
        )
        # Return empty result tuple to signal early exit
        return ([], "", is_incremental, sync_mode, {}, effective_last_sync)

    _validate_manual_source_links(gl_entries)

    # Use cached default currency if available, otherwise get from company
    if cached_default_currency is not None:
        default_currency = cached_default_currency
    else:
        default_currency = _default_currency_for_entries(gl_entries)

    # Use cached currency coverage if available, otherwise validate
    if cached_currency_coverage is not None:
        currency_coverage = cached_currency_coverage
        publish_sync_progress(
            progress_event,
            PROGRESS_PHASE1_END,
            "Using cached currency exchange coverage...",
            user,
        )
    else:
        publish_sync_progress(
            progress_event,
            PROGRESS_PHASE1_END,
            "Validating currency exchange coverage...",
            user,
        )
        currency_coverage = validate_currency_exchange_coverage(
            gl_entries, reporting_currency, default_currency
        )

    return (
        gl_entries,
        default_currency,
        is_incremental,
        sync_mode,
        currency_coverage,
        effective_last_sync,
    )


def _validate_manual_source_links(gl_entries: list[dict[str, Any]]) -> None:
    """A manual row owns its link; generation must never replace it implicitly."""
    names = frappe.get_all(
        "Reporting Currency GLE",
        filters={
            "manual_entry": 1,
            "gl_entry": ["in", [row["name"] for row in gl_entries]],
        },
        pluck="name",
        limit=5,
    )
    if names:
        frappe.throw(
            _(
                "Manual reporting entries already link to source GL entries: {0}. "
                "Resolve these manual links separately before synchronising."
            ).format(", ".join(names))
        )


# ============================================================================
# PHASE 2: DELETION
# ============================================================================


# Retain the existing positional phase API for sync callers.
def run_deletion_phase(  # noqa: PLR0917
    progress_event: str,
    user: str | None,
    last_sync_timestamp: str | None,
    is_incremental: bool,
) -> dict[str, int]:
    """Phase 2: Handle deletions (cancelled GL entries and orphaned RC GLE records).

    This phase ensures data integrity by:
    1. Removing RC GLE records for cancelled GL entries
    2. Cleaning up remaining snapshots of deleted or cancelled GL entries

    Args:
        progress_event: Event name for realtime progress updates
        user: User who initiated the sync (for progress updates)
        last_sync_timestamp: Timestamp of last successful sync (for incremental)
        is_incremental: Whether this is an incremental sync

    Returns:
        stats dict with deletion counts
    """
    stats = {"deleted_cancelled": 0, "deleted_orphaned": 0}

    publish_sync_progress(
        progress_event,
        15,  # Progress percentage
        "Phase 2: Handling deletions (cancelled entries and orphans)...",
        user,
    )

    # Step 1: Handle cancelled GL entries
    cancelled_gl_entries = fetch_cancelled_gl_entries(last_sync_timestamp)

    if cancelled_gl_entries:
        deleted_count = delete_rc_gle_for_cancelled_gl_entries(
            cancelled_gl_entries, is_incremental
        )
        stats["deleted_cancelled"] = deleted_count

        publish_sync_progress(
            progress_event,
            20,
            f"Deleted {deleted_count} RC GLE records for cancelled GL entries...",
            user,
        )

    # Physical deletions leave no modified source; also remove old cancellations
    # that predate the incremental cutoff. Retain the existing stats key for callers.
    orphaned_count = cleanup_orphaned_rc_gle_records()
    stats["deleted_orphaned"] = orphaned_count

    publish_sync_progress(
        progress_event,
        25,
        f"Cleaned up {orphaned_count} obsolete RC GLE records...",
        user,
    )

    return stats


# ============================================================================
# PHASE 3: TEMPORAL RECONCILIATION
# ============================================================================


# Retain the existing positional phase API for sync callers.
def run_temporal_reconciliation_phase(  # noqa: PLR0913, PLR0917
    progress_event: str,
    user: str | None,
    gl_entries: list[dict[str, Any]],
    default_currency: str,
    reporting_currency: str,
    currency_coverage: dict[str, bool],
    cached_rate_timeline: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[Any]]:
    """Phase 3: Temporal Reconciliation.

    Builds exchange rate timeline and validates temporal coverage.

    Args:
        progress_event: Event ID for realtime progress updates
        user: User ID for realtime messaging
        gl_entries: List of GL Entry records
        default_currency: Company's default currency
        reporting_currency: Target reporting currency
        currency_coverage: Dict indicating direct/inverse rate availability
        cached_rate_timeline: Pre-built exchange rate timeline from foreground

    Returns:
        Tuple of (rate_timeline, rate_dates) - timeline and pre-extracted
        dates for binary search
    """
    # Use cached rate timeline if available, otherwise build from database
    if cached_rate_timeline is not None:
        rate_timeline = cached_rate_timeline
        publish_sync_progress(
            progress_event,
            PROGRESS_PHASE2_START,
            "Phase 3: Using cached exchange rate timeline...",
            user,
        )
    else:
        publish_sync_progress(
            progress_event,
            PROGRESS_PHASE2_START,
            "Phase 3: Building exchange rate timeline...",
            user,
        )
        rate_timeline = build_exchange_rate_timeline(
            default_currency, reporting_currency, currency_coverage
        )

        # Validate temporal coverage: ensure all GL entries have applicable rates
        # (Skip validation if using cached timeline - already validated in foreground)
        publish_sync_progress(
            progress_event,
            PROGRESS_PHASE2_VALIDATION,
            "Validating date coverage...",
            user,
        )
        validate_temporal_coverage(
            gl_entries, rate_timeline, default_currency, reporting_currency
        )

    # Pre-extract dates for binary search optimisation (avoid O(n*m) rebuild in loop)
    rate_dates = [entry["date"] for entry in rate_timeline]

    return rate_timeline, rate_dates


# ============================================================================
# PHASE 4: CONVERSION
# ============================================================================


# Retain the existing positional phase API for sync callers.
def run_conversion_phase(  # noqa: PLR0913, PLR0917
    progress_event: str,
    user: str | None,
    gl_entries: list[dict[str, Any]],
    rate_timeline: list[dict[str, Any]],
    rate_dates: list[Any],
    default_currency: str,
    reporting_currency: str,
) -> list[dict[str, Any]]:
    """Phase 4: Currency Conversion Computation.

    Processes GL entries and converts amounts to reporting currency.

    Args:
        progress_event: Event ID for realtime progress updates
        user: User ID for realtime messaging
        gl_entries: List of GL Entry records
        rate_timeline: Sorted exchange rate timeline
        rate_dates: Pre-extracted dates from timeline for binary search
        default_currency: Company's default currency
        reporting_currency: Target reporting currency

    Returns:
        rc_gle_records: List of Reporting Currency GLE records ready for insertion
    """
    publish_sync_progress(
        progress_event,
        PROGRESS_PHASE3_START,
        _("Phase 4: Processing {0} GL Entries...").format(len(gl_entries)),
        user,
    )

    rc_gle_records = []
    total_entries = len(gl_entries)

    # Initialise date cache for optimal performance (avoids repeated date parsing)
    date_cache = {}

    for idx, gle in enumerate(gl_entries):
        # Fail-fast: any error will propagate up and trigger transaction rollback
        rc_record = process_gl_entry(
            gle,
            rate_timeline,
            rate_dates,
            default_currency,
            reporting_currency,
            date_cache,
        )
        rc_gle_records.append(rc_record)

        # Progress updates every N records
        if (idx + 1) % PROGRESS_UPDATE_FREQUENCY == 0 or (idx + 1) == total_entries:
            progress = PROGRESS_PHASE3_START + int(
                ((idx + 1) / total_entries)
                * (PROGRESS_PHASE3_END - PROGRESS_PHASE3_START)
            )
            publish_sync_progress(
                progress_event,
                progress,
                _("Processing GL Entry {0} of {1}...").format(idx + 1, total_entries),
                user,
            )

    return rc_gle_records


# ============================================================================
# PHASE 5: INSERTION
# ============================================================================


# Retain the existing positional phase API for sync callers.
def run_insertion_phase(  # noqa: PLR0917
    progress_event: str,
    user: str | None,
    rc_gle_records: list[dict[str, Any]],
    gl_entries: list[dict[str, Any]],
    insertion_context: InsertionContext,
) -> dict[str, int]:
    """Phase 5: Bulk Insertion.

    Deletes existing records and bulk inserts new RC GLE records.

    Args:
        progress_event: Event ID for realtime progress updates
        user: User ID for realtime messaging
        rc_gle_records: List of RC GLE records ready for insertion
        gl_entries: Original GL Entry records (for incremental deletion)
        insertion_context: Sync mode paired with its safe source-acquisition
            cutoff.

    Returns:
        stats: Dict with inserted count
    """
    sync_cutoff = insertion_context.cutoff
    is_incremental = insertion_context.is_incremental

    stats = {"inserted": 0}

    publish_sync_progress(
        progress_event,
        PROGRESS_PHASE4_START,
        "Phase 5: Preparing data for insertion...",
        user,
    )

    # Delete existing records based on sync mode
    if is_incremental:
        # Incremental: Delete only RC GLE records for GL entries being updated
        gl_entry_names = [gle.get("name") for gle in gl_entries]
        if gl_entry_names:
            _delete_incremental_records(gl_entry_names, progress_event, user)
    else:
        # Full sync: Delete sync-generated records only (preserve manual_entry=1)
        frappe.db.sql(
            "DELETE FROM `tabReporting Currency GLE` "
            "WHERE manual_entry = 0 OR manual_entry IS NULL"
        )
        publish_sync_progress(
            progress_event,
            PROGRESS_PHASE4_DELETE,
            "Deleted all existing RC GLE records (manual entries preserved)...",
            user,
        )

    publish_sync_progress(
        progress_event,
        PROGRESS_PHASE4_INSERT,
        _("Inserting {0} Reporting Currency GLE records...").format(
            len(rc_gle_records)
        ),
        user,
    )

    # Bulk insert - use efficient bulk operation with chunking to reduce memory
    if rc_gle_records:
        stats["inserted"] = _insert_rc_gle_records(rc_gle_records, progress_event, user)

    # Retain both timestamps as acquisition metadata for existing consumers.
    # Normal syncs always revisit all source entries regardless of these values.
    current_time = sync_cutoff
    frappe.db.set_single_value(
        DOCTYPE_RC_SETTINGS,
        {"last_sync_timestamp": current_time, "last_ce_sync_timestamp": current_time},
        update_modified=False,
    )

    # NOTE: No commit here - orchestrator handles transaction commit/rollback

    return stats


def _default_currency_for_entries(gl_entries: list[dict[str, Any]]) -> str:
    # Get company's default currency (needed for conversion)
    company = str(gl_entries[0].get("company", ""))

    # Validate all GL entries are from the same company
    companies = {str(gle.get("company", "")) for gle in gl_entries}
    if len(companies) > 1:
        frappe.throw(
            _(
                "GL Entries span multiple companies: {0}. Please ensure "
                "all GL Entries are from a single company before syncing."
            ).format(", ".join(companies)),
            title=_("Multi-Company Data Detected"),
        )

    return get_company_default_currency(company)


def _assign_rc_gle_name(record: dict[str, Any]) -> None:
    gl_entry_name = record.get("gl_entry")  # e.g., "ACC-GLE-2021-00005"

    if gl_entry_name:
        record["name"] = get_synced_rc_gle_name(gl_entry_name)
    else:
        # Should never happen - gl_entry is required field
        frappe.throw(_("GL Entry name is missing for record during RC GLE sync"))


def _insert_rc_gle_records(
    rc_gle_records: list[dict[str, Any]], progress_event: str, user: str | None
) -> int:
    # Process in chunks to avoid holding double memory (dicts + lists)
    # For 100K records: reduces peak memory from ~359 MB to ~36 MB per chunk
    chunk_size = 10000
    total_inserted = 0

    # Get field list from first record
    fieldnames = [k for k in rc_gle_records[0] if k != "doctype"]

    for chunk_start in range(0, len(rc_gle_records), chunk_size):
        chunk_end = min(chunk_start + chunk_size, len(rc_gle_records))
        chunk = rc_gle_records[chunk_start:chunk_end]

        # Pre-generate names for this chunk
        chunk_values = _prepare_insert_chunk(chunk, fieldnames)

        # Bulk insert this chunk
        frappe.db.bulk_insert(DOCTYPE_RC_GLE, fields=fieldnames, values=chunk_values)

        total_inserted += len(chunk)

        # Update progress for large inserts
        if len(rc_gle_records) > chunk_size:
            progress_pct = PROGRESS_PHASE4_INSERT + int(
                (total_inserted / len(rc_gle_records)) * 5
            )
            publish_sync_progress(
                progress_event,
                progress_pct,
                f"Inserting records... ({total_inserted}/{len(rc_gle_records)})",
                user,
            )

        # chunk_values released here for garbage collection

    return total_inserted


def _delete_incremental_records(
    gl_entry_names: list[Any], progress_event: str, user: str | None
) -> None:
    # Chunk deletions to avoid MySQL max_allowed_packet issues
    # Large DELETE IN queries can exceed packet size limit (default 64MB)
    chunk_size = 10000  # Safe for all MySQL configs
    total_deleted = 0

    for i in range(0, len(gl_entry_names), chunk_size):
        chunk = gl_entry_names[i : i + chunk_size]
        placeholders = ", ".join(["%s"] * len(chunk))
        # Chunked parameterised DELETE, not an N+1 read.
        # nosemgrep: frappe-n-plus-one-read-in-loop
        frappe.db.sql(
            f"DELETE FROM `tabReporting Currency GLE` "  # noqa: S608
            f"WHERE gl_entry IN ({placeholders}) "
            "AND COALESCE(manual_entry, 0) = 0 AND COALESCE(reporting_doe, 0) = 0",
            tuple(chunk),
        )
        total_deleted += len(chunk)

        # Update progress for large deletes
        if len(gl_entry_names) > chunk_size:
            progress_pct = (
                int((total_deleted / len(gl_entry_names)) * 3) + PROGRESS_PHASE4_START
            )
            total = len(gl_entry_names)
            publish_sync_progress(
                progress_event,
                progress_pct,
                f"Deleting existing records... ({total_deleted}/{total})",
                user,
            )

    publish_sync_progress(
        progress_event,
        PROGRESS_PHASE4_DELETE,
        _("Deleted {0} existing RC GLE records for update...").format(
            len(gl_entry_names)
        ),
        user,
    )


def _prepare_insert_chunk(
    chunk: list[dict[str, Any]], fieldnames: list[str]
) -> list[list[Any]]:
    for record in chunk:
        _assign_rc_gle_name(record)
    return [[record.get(field) for field in fieldnames] for record in chunk]
