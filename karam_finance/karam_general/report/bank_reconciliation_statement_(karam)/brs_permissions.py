"""Server-side access checks for Bank Reconciliation source data."""

from collections import defaultdict
from typing import Any

import frappe
from frappe import _


def validate_filters(filters: dict[str, Any]) -> None:
    company = filters.get("company")
    if not company:
        frappe.throw(_("Company is required."))
        return
    frappe.get_doc("Company", company).check_permission("read")
    account = frappe.get_doc("Account", filters["account"])
    account.check_permission("read")
    if account.get("company") != company or account.get("is_group"):
        frappe.throw(_("Select a ledger account belonging to the selected company."))


def source_condition(doctype: str) -> Any:
    """Apply native row permissions in SQL before outstanding or clearance totals."""
    frappe.has_permission(doctype, "read", throw=True)
    permitted = frappe.qb.get_query(doctype, fields=["name"], ignore_permissions=False)
    return frappe.qb.DocType(doctype).name.isin(permitted)


def restrict_source(query: Any, doctype: str) -> Any:
    """Check full source documents, including child dimensions, before aggregation."""
    if frappe.session.user == "Administrator":
        return query
    query = query.where(source_condition(doctype))
    source = frappe.qb.DocType(doctype)
    candidates = query.select(source.name.as_("permission_source")).as_(
        "brs_candidates"
    )
    names = (
        frappe.qb.from_(candidates)
        .select(candidates.permission_source)
        .distinct()
        .run(pluck=True)
    )
    readable: list[str] = []
    for offset in range(0, len(names), 500):
        readable.extend(_readable_documents(doctype, names[offset : offset + 500]))
    return query.where(source.name.isin(readable) if readable else source.name.isnull())


def _readable_documents(doctype: str, names: list[str]) -> list[str]:
    # The candidate query already applied native list permissions. Bulk hydration
    # lets native Document permissions also inspect children without per-row loads.
    parents = frappe.get_all(
        doctype, filters={"name": ["in", list(names)]}, fields=["*"]
    )
    for field in frappe.get_meta(doctype).get_table_fields():
        # One query per metadata child table for the entire batch of up to 500 parents.
        children = frappe.get_all(  # nosemgrep: frappe-n-plus-one-read-in-loop
            field.options,
            filters={
                "parent": ["in", list(names)],
                "parenttype": doctype,
                "parentfield": field.fieldname,
            },
            fields=["*"],
            order_by="idx",
        )
        by_parent = defaultdict(list)
        for child in children:
            by_parent[child.parent].append(child)
        for parent in parents:
            parent[field.fieldname] = by_parent[parent.name]
    readable = []
    for parent in parents:
        # Dictionary construction uses the hydrated data; this does not load a document by name.
        # nosemgrep: frappe-n-plus-one-read-in-loop
        doc = frappe.get_doc({**parent, "doctype": doctype})
        if frappe.has_permission(doctype, "read", doc=doc):
            readable.append(parent.name)
    return readable
