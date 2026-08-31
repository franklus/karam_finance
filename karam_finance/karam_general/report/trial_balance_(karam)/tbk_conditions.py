"""Query Builder filters shared by the Karam Trial Balance report."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import frappe
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
    get_accounting_dimensions,
    get_dimension_with_children,
)
from erpnext.accounts.report.financial_statements import get_cost_centers_with_children
from frappe import _
from frappe.utils import cstr


def apply_gl_filters(
    query: Any,
    gl_entry: Any,
    filters: Any,
    *,
    finance_books=None,
    accounting_dimensions=None,
):
    """Apply v16 Trial Balance filters to a GL Entry query."""

    if filters.get("cost_center"):
        query = query.where(
            gl_entry.cost_center.isin(
                get_cost_centers_with_children(filters.get("cost_center"))
            )
        )

    if filters.get("project"):
        query = query.where(gl_entry.project.isin(_as_list(filters.get("project"))))

    if accounting_dimensions is None:
        accounting_dimensions = get_accounting_dimensions(as_list=False)

    if finance_books is None:
        finance_books = bool(frappe.db.count("Finance Book"))

    query = _apply_finance_book_filter(query, gl_entry, filters, finance_books)
    query = _apply_dimension_filters(query, gl_entry, filters, accounting_dimensions)

    return query


def _apply_finance_book_filter(
    query: Any, gl_entry: Any, filters: Any, finance_books: Any
):
    if not finance_books:
        return query

    finance_book = cstr(filters.get("finance_book", ""))
    company_finance_book = cstr(
        frappe.get_cached_value("Company", filters.company, "default_finance_book")
        or ""
    )
    if filters.get("include_default_book_entries"):
        if (
            finance_book
            and company_finance_book
            and finance_book != company_finance_book
        ):
            frappe.throw(
                _(
                    "To use a different finance book, please uncheck "
                    "'Include Default FB Entries'"
                )
            )
        allowed_books = [finance_book, company_finance_book, ""]
    else:
        allowed_books = [finance_book, ""]

    return query.where(
        gl_entry.finance_book.isin(allowed_books) | gl_entry.finance_book.isnull()
    )


def _apply_dimension_filters(
    query: Any, gl_entry: Any, filters: Any, accounting_dimensions: Any
):
    for dimension in accounting_dimensions:
        value = filters.get(dimension.fieldname)
        if not value:
            continue
        if frappe.get_cached_value("DocType", dimension.document_type, "is_tree"):
            value = get_dimension_with_children(dimension.document_type, value)
        query = query.where(gl_entry[dimension.fieldname].isin(_as_list(value)))
    return query


def _as_list(value: str | Iterable[str]) -> list[str]:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return list(value)
