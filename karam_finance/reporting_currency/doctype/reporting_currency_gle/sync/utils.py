"""Utility functions for Reporting Currency GLE sync.

This module provides helper functions for progress reporting, column capacity,
currency precision, hash generation, and CSV export operations.
"""

from __future__ import annotations

import hashlib
from typing import Any

import frappe
from frappe import _
from frappe.utils import formatdate
from frappe.utils.data import cint

# ============================================================================
# MODULE CONSTANTS
# ============================================================================

# DocType names
DOCTYPE_RC_GLE = "Reporting Currency GLE"

# Progress
PROGRESS_COMPLETE = 100

# Date formats
DATE_FORMAT_DISPLAY = "dd-mm-yyyy"

# ============================================================================
# PROGRESS PUBLISHING
# ============================================================================


def publish_sync_progress(
    event: str, current: int, message: str, user: str | None = None
) -> None:
    """Publish sync progress to realtime event.

    Args:
        event: Progress event ID
        current: Current progress value (0-100)
        message: Progress message to display
        user: Optional user ID for targeted messaging
    """
    frappe.publish_realtime(
        event,
        message={"current": current, "total": PROGRESS_COMPLETE, "message": _(message)},
        user=user,
    )


# ============================================================================
# CURRENCY UTILITIES
# ============================================================================


def get_currency_precision(currency: str) -> int:
    """Get the decimal precision for a currency.

    Returns the number of decimal places configured for the currency in the
    Currency doctype. Defaults to 2 if currency not found or precision not set.

    Args:
        currency: Currency code (e.g., "USD", "KWD", "JPY")

    Returns:
        Integer precision (0 for JPY/KRW, 2 for USD/EUR, 3 for KWD/BHD, etc.)

    Examples:
        - USD, EUR: 2 decimals
        - JPY, KRW: 0 decimals (whole numbers)
        - KWD, BHD, JOD, OMR, TND: 3 decimals
        - BTC, ETH: 8+ decimals
    """
    try:
        # Try to get precision from Currency doctype's fraction_units field
        precision = frappe.db.get_value("Currency", currency, "fraction_units")
        if precision is not None:
            return cint(precision)
    except Exception:  # noqa: S110
        pass  # Silently fallback to next method

    # Fallback: try Frappe's precision API for the RC GLE doctype field
    try:
        precision = frappe.get_precision(DOCTYPE_RC_GLE, "reporting_debit", currency)
        if precision is not None:
            return cint(precision)
    except Exception:  # noqa: S110
        pass  # Silently fallback to default

    # Final fallback: standard 2 decimals
    return 2


# ============================================================================
# HASH GENERATION
# ============================================================================


def get_gl_entry_stable_hash(gle: dict[str, Any]) -> str:
    """Generate a stable identifier for a GL Entry.

    Based on immutable business attributes. This hash remains constant even
    when the GL Entry name changes (e.g., from hash-based temporary name to
    naming series). Used to maintain RC GLE → GL Entry linkage across renames.

    Args:
        gle: GL Entry record dict with voucher details

    Returns:
        16-character hexadecimal hash string
    """
    # Use immutable business transaction attributes that uniquely identify this GL Entry
    components = [
        str(gle.get("voucher_type", "")),
        str(gle.get("voucher_no", "")),
        str(gle.get("account", "")),
        str(gle.get("posting_date", "")),
        str(gle.get("debit", 0)),
        str(gle.get("credit", 0)),
    ]

    # Create unique string from components
    hash_input = "|".join(components)

    # Generate SHA256 hash and return first 16 characters
    return hashlib.sha256(hash_input.encode()).hexdigest()[:16]


# ============================================================================
# CSV EXPORT UTILITIES
# ============================================================================


def _generate_csv_download(
    headers: list[str],
    rows: list[list[Any]],
    filename_prefix: str,
    *filename_params: str,
) -> None:
    """Helper to generate CSV file download response.

    Args:
        headers: List of column headers
        rows: List of data rows (each row is a list of values)
        filename_prefix: Prefix for the filename (e.g., "Temporal_Validation_Error")
        *filename_params: Additional parameters to include in filename
    """
    import csv  # noqa: PLC0415
    from io import StringIO  # noqa: PLC0415

    # Generate CSV
    csv_buffer = StringIO()
    csv_writer = csv.writer(csv_buffer)

    # Write header and data
    csv_writer.writerow(headers)
    csv_writer.writerows(rows)

    # Prepare file download
    csv_content = csv_buffer.getvalue()
    csv_buffer.close()

    # Build filename with parameters
    param_str = "_".join(str(p) for p in filename_params)
    timestamp = frappe.utils.now_datetime().strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_prefix}_{param_str}_{timestamp}.csv"

    frappe.local.response.filename = filename
    frappe.local.response.filecontent = csv_content
    frappe.local.response.type = "download"


@frappe.whitelist()
def export_temporal_validation_entries_csv(
    cache_key: str, default_currency: str, reporting_currency: str
) -> None:
    """Export GL entries that precede Currency Exchange records to CSV.

    Retrieves data from cache and generates downloadable CSV.
    """
    frappe.only_for("System Manager")
    # Retrieve entries from cache
    entries = frappe.cache().get_value(cache_key)

    if not entries:
        frappe.throw(_("Validation data expired. Please try the sync operation again."))

    # Prepare CSV headers
    headers = ["GL Entry", "Posting Date", "Account", "Voucher No"]

    # Prepare data rows
    rows = [
        [
            entry.get("gle", ""),
            formatdate(entry.get("date"), DATE_FORMAT_DISPLAY),
            entry.get("account", ""),
            entry.get("voucher", ""),
        ]
        for entry in entries
    ]

    # Generate and download CSV
    _generate_csv_download(
        headers, rows, "Temporal_Validation_Error", default_currency, reporting_currency
    )


@frappe.whitelist()
def export_missing_currency_gl_entries_csv(
    account_currency: str, reporting_currency: str
) -> None:
    """Export GL Entries for a specific currency pair missing Currency Exchange records.

    Returns a CSV file download.
    """
    frappe.only_for("System Manager")
    # Build filters
    filters = {
        "docstatus": 1,
        "account_currency": account_currency,
    }

    # Fetch GL entries for this currency pair
    gl_entries = frappe.db.get_all(
        "GL Entry",
        filters=filters,
        fields=[
            "name",
            "posting_date",
            "account",
            "voucher_no",
            "voucher_type",
            "debit_in_account_currency",
            "credit_in_account_currency",
            "remarks",
        ],
        order_by="posting_date asc, name asc",
    )

    if not gl_entries:
        frappe.throw(
            _("No GL Entries found for currency pair {0}-{1}").format(
                account_currency, reporting_currency
            )
        )

    # Prepare CSV headers
    headers = [
        "GL Entry",
        "Posting Date",
        "Account",
        "Voucher Type",
        "Voucher No",
        "Debit",
        "Credit",
        "Remarks",
    ]

    # Prepare data rows
    rows = [
        [
            gle.get("name", ""),
            gle.get("posting_date", ""),
            gle.get("account", ""),
            gle.get("voucher_type", ""),
            gle.get("voucher_no", ""),
            gle.get("debit_in_account_currency", 0),
            gle.get("credit_in_account_currency", 0),
            gle.get("remarks", ""),
        ]
        for gle in gl_entries
    ]

    # Generate and download CSV
    _generate_csv_download(
        headers, rows, "Missing_Currency_Exchange", account_currency, reporting_currency
    )
