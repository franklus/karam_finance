"""Delete the obsolete Trial Balance Reporting report."""

from __future__ import annotations

import frappe

STALE_REPORT = "Trial Balance Reporting"


def execute() -> None:
    """Remove the report left behind when the standard report was renamed."""
    if not frappe.db.exists("Report", STALE_REPORT):
        return

    frappe.delete_doc(
        "Report",
        STALE_REPORT,
        force=True,
        ignore_permissions=True,
    )
    frappe.clear_cache(doctype="Report")
