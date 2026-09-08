"""Preserve report references when adding the Karam name suffix."""

import frappe

from karam_finance.patches.report_rename import rename_report

OLD_NAMES = (
    "Profit and Loss Statement by Cost Center",
    "(Karam) Profit and Loss Statement by Cost Center",
)
NEW_NAME = "Profit and Loss Statement by Cost Center (Karam)"


def execute() -> None:
    for old_name in OLD_NAMES:
        if frappe.db.exists("Report", old_name):
            _rename_report(old_name)


def _rename_report(old_name: str) -> None:
    rename_report(old_name, NEW_NAME, "Karam General")
    frappe.db.set_value("Report", NEW_NAME, "report_name", NEW_NAME)
    for doctype in ("Workspace Link", "Workspace Shortcut", "Workspace Sidebar Item"):
        if not frappe.db.table_exists(doctype):
            continue
        frappe.db.set_value(
            doctype,
            {"link_to": NEW_NAME, "label": old_name},
            "label",
            NEW_NAME,
        )
    frappe.clear_cache(doctype="Report")
