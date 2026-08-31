"""Delete obsolete Reporting Currency General Ledger report."""

from __future__ import annotations

import frappe

STALE_REPORT = "General Ledger (Reporting Currency)"


def execute() -> None:
    """Remove the stale report left by the previous report name."""
    if not frappe.db.exists("Report", STALE_REPORT):
        return

    frappe.delete_doc(
        "Report",
        STALE_REPORT,
        force=True,
        ignore_permissions=True,
    )
    frappe.clear_cache(doctype="Report")
