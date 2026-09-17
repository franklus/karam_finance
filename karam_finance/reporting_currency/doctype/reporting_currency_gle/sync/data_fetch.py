"""Data fetching functions for Reporting Currency GLE sync.

This module handles fetching GL Entry records, cancelled entries,
and orphan cleanup operations.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.query_builder.functions import Coalesce

# ============================================================================
# MODULE CONSTANTS
# ============================================================================

# DocType names
DOCTYPE_GL_ENTRY = "GL Entry"
DOCTYPE_RC_SETTINGS = "Reporting Currency Settings"


# ============================================================================
# GL ENTRY FETCHING
# ============================================================================


def fetch_gl_entries(
    last_sync_timestamp: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch all non-cancelled GL Entry records, ordered by posting_date (ASC).

    Fetches ALL submitted GL Entries regardless of date. For incremental sync,
    only entries modified after the last sync timestamp are returned.

    Args:
        last_sync_timestamp: If provided, fetch only entries modified after this
                             timestamp (incremental sync)

    Returns:
        List of GL Entry dicts with all required fields.
    """
    fields = [
        "name",
        "posting_date",
        "transaction_date",
        "fiscal_year",
        "due_date",
        "account",
        "account_currency",
        "against",
        "party_type",
        "party",
        "voucher_type",
        "voucher_no",
        "voucher_subtype",
        "transaction_currency",
        "against_voucher_type",
        "against_voucher",
        "voucher_detail_no",
        "transaction_exchange_rate",
        "debit_in_account_currency",
        "debit",
        "debit_in_transaction_currency",
        "credit_in_account_currency",
        "credit",
        "credit_in_transaction_currency",
        "cost_center",
        "project",
        "finance_book",
        "company",
        "is_opening",
        "is_advance",
        "to_rename",
        "is_cancelled",
        "remarks",
        "modified",
        "docstatus",
    ]

    gl_entry = frappe.qb.DocType(DOCTYPE_GL_ENTRY)
    query = (
        frappe.qb.from_(gl_entry)
        .select(*[gl_entry[f] for f in fields])
        .where(gl_entry.docstatus == 1)
        .where(Coalesce(gl_entry.is_cancelled, 0) == 0)
        .orderby(gl_entry.posting_date)
        .orderby(gl_entry.creation)
    )

    if last_sync_timestamp:
        query = query.where(gl_entry.modified > last_sync_timestamp)

    return query.run(as_dict=True)


def fetch_cancelled_gl_entries(
    last_sync_timestamp: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch GL entries that have been cancelled since the last sync.

    These represent deletions that need to be reflected in RC GLE by removing
    the corresponding records.

    Args:
        last_sync_timestamp: Only fetch entries cancelled after this timestamp.
                             If None, fetches all cancelled entries.

    Returns:
        List of cancelled GL entry records with name and gl_entry_hash fields.
    """
    filters: dict[str, Any] = {
        "is_cancelled": 1,
    }

    # Incremental: only fetch entries cancelled since last sync
    if last_sync_timestamp:
        filters["modified"] = [">", last_sync_timestamp]

    fields = [
        "name",
        "voucher_type",
        "voucher_no",
        "account",
        "posting_date",
        "debit",
        "credit",
    ]

    return frappe.db.get_all(
        DOCTYPE_GL_ENTRY,
        filters=filters,
        fields=fields,
    )


# ============================================================================
# DELETION AND CLEANUP
# ============================================================================


def delete_rc_gle_for_cancelled_gl_entries(
    cancelled_gl_entries: list[dict[str, Any]], is_incremental: bool
) -> int:
    """Delete RC GLE records corresponding to cancelled GL entries.

    Args:
        cancelled_gl_entries: List of cancelled GL entry records
        is_incremental: Whether this is an incremental sync

    Returns:
        Number of RC GLE records deleted
    """
    if not cancelled_gl_entries:
        return 0

    gl_entry_names = [gle.get("name") for gle in cancelled_gl_entries]

    if not gl_entry_names:
        return 0

    # Delete RC GLE records linked to these cancelled GL entries
    placeholders = ", ".join(["%s"] * len(gl_entry_names))

    frappe.db.sql(  # nosemgrep — parameterised values
        f"""
		DELETE FROM `tabReporting Currency GLE`
		WHERE gl_entry IN ({placeholders})
            AND COALESCE(manual_entry, 0) = 0
            AND COALESCE(reporting_doe, 0) = 0
		""",  # noqa: S608
        tuple(gl_entry_names),
    )

    # Get actual row count from the DELETE operation
    deleted_count = frappe.db.sql("SELECT ROW_COUNT()")[0][0] or 0

    mode = "incremental" if is_incremental else "full"
    frappe.logger().info(
        f"Deleted {deleted_count} RC GLE records for cancelled GL entries ({mode} sync)"
    )

    return deleted_count


def cleanup_orphaned_rc_gle_records() -> int:
    """Remove generated snapshots of deleted or cancelled GL entries.

    Check all source links, including cancellations older than the sync cutoff.
    Preserve manual entries and DOE rows, which have separate lifecycles.

    Returns:
        Number of obsolete RC GLE records deleted
    """
    orphan_count = 0
    # Bounded batches of 1000, not one query per ledger row.
    # nosemgrep: frappe-n-plus-one-read-in-loop
    while rows := frappe.db.sql("""
        SELECT rc.name FROM `tabReporting Currency GLE` rc
        LEFT JOIN `tabGL Entry` gle ON rc.gl_entry = gle.name
        WHERE (gle.name IS NULL OR gle.is_cancelled = 1)
            AND (rc.manual_entry = 0 OR rc.manual_entry IS NULL)
            AND (rc.reporting_doe = 0 OR rc.reporting_doe IS NULL)
        ORDER BY rc.name LIMIT 1000 FOR UPDATE
    """):
        names = tuple(row[0] for row in rows)
        # The site mutation lock and row locks keep this bounded batch stable.
        # One parameterised delete per locked batch.
        # nosemgrep: frappe-n-plus-one-read-in-loop
        frappe.db.sql(
            "DELETE FROM `tabReporting Currency GLE` WHERE name IN %(names)s",
            {"names": names},
        )
        orphan_count += len(names)

    if orphan_count > 0:
        frappe.logger().info(
            f"Deleted {orphan_count} obsolete RC GLE records (GL entries removed or cancelled)"
        )

    return orphan_count
