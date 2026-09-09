"""Preserve report links when adding the Karam suffix."""

import frappe

from karam_finance.patches.report_rename import rename_report

OLD_NAME = "Asset Depreciation Ledger Summary"
NEW_NAME = "Asset Depreciation Ledger Summary (Karam)"


def execute() -> None:
    if not frappe.db.exists("Report", OLD_NAME):
        return
    rename_report(OLD_NAME, NEW_NAME, "Karam General")
    frappe.db.set_value("Report", NEW_NAME, "report_name", NEW_NAME)
    for doctype in ("Workspace Link", "Workspace Shortcut", "Workspace Sidebar Item"):
        if not frappe.db.table_exists(doctype):
            continue
        frappe.db.set_value(
            doctype,
            {"link_to": NEW_NAME, "label": OLD_NAME},
            "label",
            NEW_NAME,
        )
    frappe.clear_cache(doctype="Report")
