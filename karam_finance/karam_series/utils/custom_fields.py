"""Custom field helpers for Karam Series."""

from __future__ import annotations

from typing import Any

import frappe

from karam_finance.common.custom_fields import ensure_custom_fields_from_dir


def ensure_custom_fields() -> None:
    """Create or update Karam Series fields from validated fixtures.

    The shared helper makes the operation idempotent while surfacing malformed
    fixtures and failures on installed DocTypes to the migration hook.
    """
    ensure_custom_fields_from_dir("karam_series", "custom_fields", label="karam_series")


def project_requirement_policy() -> None:
    """Project saved Settings mandatory flags onto Karam Series fields.

    ``Karam Series Settings`` remains the source of truth. This function is
    deliberately safe to run repeatedly: it updates only fields whose value
    differs from the persisted policy and clears each affected DocType cache
    once. Missing optional DocTypes are ignored; an installed target without
    its owned ``karam_series`` field is an incomplete schema and fails loudly.
    """
    settings = frappe.get_single("Karam Series Settings")
    rows = list(getattr(settings, "doctype_list", []) or [])
    if not rows:
        return

    requested_doctypes = [_row_value(row, "doctype_name") for row in rows]
    if any(
        not isinstance(doctype_name, str) or not doctype_name.strip()
        for doctype_name in requested_doctypes
    ):
        raise ValueError("Karam Series Settings contains a blank DocType")

    installed_doctypes = set(
        frappe.get_all(
            "DocType",
            filters={"name": ["in", requested_doctypes]},
            pluck="name",
            ignore_permissions=True,
        )
    )
    if not installed_doctypes:
        return
    custom_field_rows = frappe.get_all(
        "Custom Field",
        filters={
            "dt": ["in", list(installed_doctypes)],
            "fieldname": "karam_series",
        },
        fields=["name", "dt", "reqd"],
        ignore_permissions=True,
    )
    custom_fields_by_doctype = {
        str(field_row["dt"]): field_row for field_row in custom_field_rows
    }
    updates: dict[str, dict[str, int]] = {}
    changed_doctypes: set[str] = set()

    for row in rows:
        doctype_name = str(_row_value(row, "doctype_name"))
        if doctype_name not in installed_doctypes:
            continue

        custom_field = custom_fields_by_doctype.get(doctype_name)
        if not custom_field:
            raise RuntimeError(
                f"Karam Series field is missing on existing DocType {doctype_name}"
            )

        required = int(
            bool(frappe.utils.cint(_row_value(row, "karam_series_mandatory")))
        )
        if frappe.utils.cint(custom_field.get("reqd")) == required:
            continue

        updates[str(custom_field["name"])] = {"reqd": required}
        changed_doctypes.add(doctype_name)

    if updates:
        frappe.db.bulk_update("Custom Field", updates)

    for doctype_name in changed_doctypes:
        frappe.clear_cache(doctype=doctype_name)


def _row_value(row: Any, fieldname: str) -> Any:
    """Read a child row from either a Frappe document or a mapping."""
    if isinstance(row, dict):
        return row.get(fieldname)
    return getattr(row, fieldname, None)
