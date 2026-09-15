"""Source GL visibility for reporting-currency rows before aggregation."""

from typing import Any

import frappe
from frappe.model.document import Document
from frappe.permissions import get_user_permissions
from frappe.query_builder.functions import Coalesce


def source_visibility(ledger: Any, user: str | None = None) -> Any:
    """A linked snapshot must remain readable through its original GL entry."""
    user = user or frappe.session.user
    try:
        source = frappe.qb.get_query(
            "GL Entry",
            fields=["name"],
            ignore_permissions=False,
            user=user,
        )
        linked = ledger.gl_entry.isin(source)
    except frappe.PermissionError:
        linked = ledger.name.isnull()
    standalone = (Coalesce(ledger.gl_entry, "") == "") & (
        (ledger.manual_entry == 1) | (ledger.reporting_doe == 1)
    )
    if has_unrepresented_dimension_restrictions(user):
        return linked
    return linked | standalone


def apply_reporting_permissions(query: Any, ledger: Any) -> Any:
    """Retain native reporting-row permissions and also authorise the source."""
    permitted = frappe.qb.get_query(
        "Reporting Currency GLE",
        fields=["name"],
        ignore_permissions=False,
    )
    return query.where(ledger.name.isin(permitted)).where(source_visibility(ledger))


def permission_query_conditions(user: str | None = None) -> str:  # noqa: V103 - Frappe resolves this function from permission_query_conditions in hooks.py.
    """Apply source visibility to native reporting-entry lists and query APIs."""
    return source_visibility(frappe.qb.DocType("Reporting Currency GLE"), user).get_sql(
        quote_char="`"
    )


def has_permission(doc: Document, user: str | None = None, ptype: str = "read") -> bool:
    """Do not let direct document reads bypass a source GL restriction."""
    if ptype not in ("read", "select", "print", "email", "export"):
        return True
    ledger = frappe.qb.DocType("Reporting Currency GLE")
    allowed = (
        frappe.qb.from_(ledger)
        .select(ledger.name)
        .where(ledger.name == doc.name)
        .where(source_visibility(ledger, user))
        .limit(1)
        .run()
    )
    return bool(allowed)


def has_unrepresented_dimension_restrictions(user: str) -> bool:
    """Source-less rows cannot establish access to a dimension they do not store."""
    if user == "Administrator":
        return False
    permissions = get_user_permissions(user)
    reporting_meta = frappe.get_meta("Reporting Currency GLE")
    missing_links = {
        field.options
        for field in frappe.get_meta("GL Entry").fields
        if field.fieldtype == "Link"
        and not field.ignore_user_permissions
        and not reporting_meta.has_field(field.fieldname)
    }
    return any(
        not permission.get("applicable_for")
        or permission.get("applicable_for") in ("GL Entry", "Reporting Currency GLE")
        for doctype in missing_links
        for permission in permissions.get(doctype, [])
    )
