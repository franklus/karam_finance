"""Karam Series settings controller."""

from __future__ import annotations

from typing import Any, cast

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.query_builder import DocType

from karam_finance.karam_series.constants.constants import KARAM_DOCTYPES
from karam_finance.karam_series.utils.doctype_list_sync import reconcile_doctype_rows


class KaramSeriesSettings(Document):
    """Settings controller for the Karam Series feature.

    Owns cross-doctype configuration such as mandatory flags and visibility
    for the `karam_series` and `translation` fields.
    """

    def on_update(self) -> None:
        """Apply configuration to target doctypes on save."""
        self._update_field_properties_bulk()

    # ----- internal helpers -------------------------------------------------

    def _update_field_properties_bulk(self) -> None:
        """Batch update visibility and mandatory properties for target doctypes."""
        rows = cast(list[Any], self.get("doctype_list") or [])
        if not rows:
            return

        visibility_updates: list[dict[str, Any]] = []
        mandatory_updates: list[dict[str, Any]] = []

        hide_fields_flag = (
            1 if frappe.utils.cint(getattr(self, "hide_fields", 0)) else 0
        )

        for row in rows:
            hidden = hide_fields_flag
            required = 1 if row.karam_series_mandatory else 0

            visibility_updates.extend(
                [
                    {
                        "doctype": "Custom Field",
                        "filters": {
                            "fieldname": "karam_series",
                            "dt": row.doctype_name,
                        },
                        "field": "hidden",
                        "value": hidden,
                    },
                    {
                        "doctype": "Custom Field",
                        "filters": {
                            "fieldname": "translation",
                            "dt": row.doctype_name,
                        },
                        "field": "hidden",
                        "value": hidden,
                    },
                ]
            )

            mandatory_updates.append(
                {
                    "doctype": "Custom Field",
                    "filters": {
                        "fieldname": "karam_series",
                        "dt": row.doctype_name,
                    },
                    "field": "reqd",
                    "value": required,
                }
            )

        self._execute_bulk_field_updates(visibility_updates)
        self._execute_bulk_field_updates(mandatory_updates)
        self._clear_impacted_doctype_caches(rows)

    def _clear_impacted_doctype_caches(self, rows: list[Any]) -> None:
        """Refresh metadata for each installed target exactly once."""
        requested_doctypes: set[str] = set()
        for row in rows:
            doctype_name = _row_value(row, "doctype_name")
            if isinstance(doctype_name, str) and doctype_name.strip():
                requested_doctypes.add(doctype_name.strip())
        if not requested_doctypes:
            return

        installed_doctypes = frappe.db.get_list(
            "DocType",
            filters={"name": ["in", sorted(requested_doctypes)]},
            fields=["name"],
            pluck="name",
            ignore_permissions=True,
        )
        installed_doctype_names = {
            str(doctype_name) for doctype_name in installed_doctypes if doctype_name
        }
        for doctype_name in sorted(installed_doctype_names):
            frappe.clear_cache(doctype=doctype_name)

    def _execute_bulk_field_updates(self, updates: list[dict[str, Any]]) -> None:
        if not updates:
            return

        # Group updates by (field, value) for fewer statements
        grouped: dict[str, dict[str, Any]] = {}
        for upd in updates:
            key = f"{upd['field']}_{upd['value']}"
            grouped.setdefault(
                key,
                {"field": upd["field"], "value": upd["value"], "filters": []},
            )
            grouped[key]["filters"].append(upd["filters"])

        for spec in grouped.values():
            self._batch_update_custom_fields(
                spec["field"],
                spec["value"],
                spec["filters"],
            )

    def _batch_update_custom_fields(
        self,
        field: str,
        value: int,
        filter_list: list[dict[str, str]],
    ) -> None:
        if not filter_list:
            return

        cf = DocType("Custom Field")

        # Build OR-of-ANDs condition with Query Builder
        condition = None
        for filters in filter_list:
            clause = None
            for key, val in filters.items():
                expr = cf[key] == val
                clause = expr if clause is None else (clause & expr)
            condition = clause if condition is None else (condition | clause)

        if condition is not None:
            (frappe.qb.update(cf).set(cf[field], value).where(condition)).run()


# ----- whitelisted utilities for the Settings UI ----------------------------


@frappe.whitelist()  # nosemgrep: frappe-missing-permission-check
def get_doctype_list() -> list[str]:
    """Return the curated list of doctypes that support Karam Series.

    Permission: Controlled by Karam Series Settings doctype role permissions.
    """
    doctypes = frappe.db.get_list(
        "DocType",
        filters={"name": ["in", KARAM_DOCTYPES]},
        fields=["name"],
        pluck="name",
        ignore_permissions=True,
    )
    return sorted(doctypes)


def sync_doctype_list() -> None:
    """Synchronise the settings child-table with the curated doctypes.

    - Adds missing doctypes
    - Removes extraneous doctypes
    - Sorts rows alphabetically
    - Preserves existing mandatory flags
    """
    settings = frappe.get_single("Karam Series Settings")
    curated = get_doctype_list()
    settings_rows = cast(list[Any], settings.get("doctype_list") or [])
    existing_flags: dict[str, int] = {
        row.doctype_name: int(row.karam_series_mandatory or 0) for row in settings_rows
    }
    rows = reconcile_doctype_rows(curated, existing_flags)
    settings.set("doctype_list", rows)
    settings.save()


@frappe.whitelist()  # nosemgrep: frappe-missing-permission-check  # noqa: V103 - whitelisted method called by Settings client JS.
def populate_doctype_list() -> None:
    """Public action to sync doctypes from the UI button.

    Permission: Controlled by Karam Series Settings doctype role permissions.
    """
    sync_doctype_list()
    frappe.msgprint(_("Doctype list updated"))


@frappe.whitelist()  # nosemgrep: frappe-missing-permission-check
def update_field_mandatory_status(
    doctype_name: str,
    is_mandatory: int | str,
) -> dict[str, Any]:
    """Persist a mandatory-policy change through Karam Series Settings.

    This endpoint remains as a compatibility surface for older clients, but it
    never writes ``Custom Field`` directly. The singleton child table remains
    the policy source of truth and ``on_update`` projects its saved values.
    """

    frappe.has_permission("Karam Series Settings", ptype="write", throw=True)

    try:
        mandatory_value = int(is_mandatory)
        if doctype_name not in KARAM_DOCTYPES:
            return {
                "success": False,
                "message": f"Unsupported Karam Series doctype: {doctype_name}",
            }

        settings = frappe.get_single("Karam Series Settings")
        settings_rows = cast(list[Any], settings.get("doctype_list") or [])
        row = next(
            (
                candidate
                for candidate in settings_rows
                if candidate.doctype_name == doctype_name
            ),
            None,
        )
        if row is None:
            return {
                "success": False,
                "message": f"Doctype is not present in Karam Series Settings: {doctype_name}",
            }

        row.karam_series_mandatory = 1 if mandatory_value else 0
        settings.save()
    except Exception:  # noqa: BLE001 - compatibility API returns failure after logging any save error.
        frappe.log_error(frappe.get_traceback(), "Update Field Mandatory Status Error")
        return {
            "success": False,
            "message": _("Unexpected error while updating mandatory status"),
        }
    else:
        return {
            "success": True,
            "message": f"Saved mandatory status for {doctype_name}",
        }


def _row_value(row: Any, fieldname: str) -> Any:
    """Read a child row from either a Frappe document or a mapping."""
    if isinstance(row, dict):
        return row.get(fieldname)
    return getattr(row, fieldname, None)
