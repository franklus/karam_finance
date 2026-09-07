"""Preserve report links when adding the Karam suffix."""

import frappe

OLD_NAME = "Asset Depreciation Ledger Summary"
NEW_NAME = "Asset Depreciation Ledger Summary (Karam)"


def execute() -> None:
    if not frappe.db.exists("Report", OLD_NAME):
        return
    # A conflicting target must be resolved rather than merging report definitions.
    frappe.rename_doc("Report", OLD_NAME, NEW_NAME, force=True)
    frappe.db.set_value("Report", NEW_NAME, "report_name", NEW_NAME)
    for doctype in ("Workspace Link", "Workspace Shortcut", "Workspace Sidebar Item"):
        frappe.db.set_value(
            doctype,
            {"link_to": NEW_NAME, "label": OLD_NAME},
            "label",
            NEW_NAME,
        )
    frappe.clear_cache(doctype="Report")
