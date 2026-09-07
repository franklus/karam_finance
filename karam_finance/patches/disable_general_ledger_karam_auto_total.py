"""Disable Frappe's duplicate automatic total for General Ledger (Karam)."""

from __future__ import annotations

import frappe
from frappe.utils import cint

REPORT_NAME = "General Ledger (Karam)"


def execute() -> None:
    """Keep only the report's own Opening, Total and Closing rows."""
    if not frappe.db.exists("Report", REPORT_NAME):
        return

    add_total_row = frappe.db.get_value("Report", REPORT_NAME, "add_total_row")
    if not cint(add_total_row):
        return

    frappe.db.set_value(
        "Report",
        REPORT_NAME,
        "add_total_row",
        0,
        update_modified=False,
    )
    frappe.clear_cache(doctype="Report")
