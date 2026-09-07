"""Preserve report references when adding the Karam name suffix."""

import frappe

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
    # Do not merge reports: an existing target needs explicit resolution.
    frappe.rename_doc("Report", old_name, NEW_NAME, force=True)
    frappe.db.set_value("Report", NEW_NAME, "report_name", NEW_NAME)
    for doctype in ("Workspace Link", "Workspace Shortcut", "Workspace Sidebar Item"):
        frappe.db.set_value(
            doctype,
            {"link_to": NEW_NAME, "label": old_name},
            "label",
            NEW_NAME,
        )
    frappe.clear_cache(doctype="Report")
