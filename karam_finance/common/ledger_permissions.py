"""Frappe source permissions for reports that aggregate GL entries."""

from typing import Any

import frappe


def has_gl_restrictions() -> bool:
    """Closing balances are safe only when the reader can access every GL row."""
    frappe.has_permission("GL Entry", "read", throw=True)
    return bool(frappe.build_match_conditions("GL Entry"))


def apply_gl_permissions(query: Any, ledger: Any) -> Any:
    """Restrict source rows before aggregation, including owner and dimension rules."""
    if not has_gl_restrictions():
        return query
    permitted = frappe.qb.get_query(
        "GL Entry", fields=["name"], ignore_permissions=False
    )
    return query.where(ledger.name.isin(permitted))
