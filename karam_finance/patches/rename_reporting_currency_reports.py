"""Expand Reporting report suffixes while retaining linked references."""

import frappe

from karam_finance.patches.report_rename import rename_report

REPORTS = ("General Ledger", "Trial Balance", "Trial Balance for Party")


def execute() -> None:
    for report in REPORTS:
        old_name = f"{report} (Reporting)"
        new_name = f"{report} (Reporting Currency)"
        if not frappe.db.exists("Report", old_name):
            continue
        rename_report(old_name, new_name, "Reporting Currency")
        frappe.db.set_value("Report", new_name, "report_name", new_name)
        for doctype in (
            "Workspace Link",
            "Workspace Shortcut",
            "Workspace Sidebar Item",
        ):
            if not frappe.db.table_exists(doctype):
                continue
            frappe.db.set_value(
                doctype, {"link_to": new_name, "label": old_name}, "label", new_name
            )
    frappe.clear_cache(doctype="Report")
