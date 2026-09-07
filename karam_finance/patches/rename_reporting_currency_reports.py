"""Expand Reporting report suffixes while retaining linked references."""

import frappe

REPORTS = ("General Ledger", "Trial Balance", "Trial Balance for Party")


def execute() -> None:
    for report in REPORTS:
        old_name = f"{report} (Reporting)"
        new_name = f"{report} (Reporting Currency)"
        if not frappe.db.exists("Report", old_name):
            continue
        # Never merge distinct report definitions if a conflicting target exists.
        frappe.rename_doc("Report", old_name, new_name, force=True)
        frappe.db.set_value("Report", new_name, "report_name", new_name)
        for doctype in ("Workspace Link", "Workspace Shortcut", "Workspace Sidebar Item"):
            frappe.db.set_value(
                doctype, {"link_to": new_name, "label": old_name}, "label", new_name
            )
    frappe.clear_cache(doctype="Report")
