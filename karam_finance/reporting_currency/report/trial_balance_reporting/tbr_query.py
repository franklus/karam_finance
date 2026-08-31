# ruff: noqa: D100, D103

from typing import Any

import frappe
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
    get_accounting_dimensions,
)
from frappe.query_builder.functions import Sum
from frappe.utils import cstr, flt

from .tbr_constants import DOCTYPE_RC_GLE


def build_base_query(filters: dict) -> tuple[Any, Any]:
    rcgle = frappe.qb.DocType(DOCTYPE_RC_GLE)
    query = (
        frappe.qb.from_(rcgle)
        .select(
            rcgle.account,
            Sum(rcgle.reporting_debit).as_("debit"),
            Sum(rcgle.reporting_credit).as_("credit"),
        )
        .where(rcgle.company == filters.company)
        .groupby(rcgle.account)
    )

    if frappe.db.count("Finance Book"):
        finance_book = cstr(filters.get("finance_book", ""))
        fb_cond = (rcgle.finance_book.isnull()) | (rcgle.finance_book == "")
        if filters.get("include_default_book_entries"):
            company_fb = cstr(
                frappe.get_cached_value(
                    "Company", filters.company, "default_finance_book"
                )
            )
            books = tuple({finance_book, company_fb} - {""})
            if books:
                fb_cond = fb_cond | rcgle.finance_book.isin(books)
        else:
            fb_cond = fb_cond | (rcgle.finance_book == finance_book)
        query = query.where(fb_cond)

    if filters.get("cost_center"):
        query = query.where(rcgle.cost_center.isin(tuple(filters.cost_center)))

    if filters.get("project"):
        query = query.where(rcgle.project.isin(tuple(filters.project)))

    for dimension in get_accounting_dimensions(as_list=False):
        dim_values = filters.get(dimension.fieldname)
        if dim_values:
            query = query.where(rcgle[dimension.fieldname].isin(tuple(dim_values)))

    return query, rcgle


def get_opening_balances(filters: dict) -> dict:
    opening = frappe._dict()

    query, rcgle = build_base_query(filters)
    query = query.where(rcgle.posting_date < filters.from_date)

    if not flt(filters.get("with_period_closing_entry_for_opening")):
        query = query.where(rcgle.voucher_type != "Period Closing Voucher")

    entries = query.run(as_dict=True)

    for entry in entries:
        opening[entry.account] = {
            "opening_debit": flt(entry.debit),
            "opening_credit": flt(entry.credit),
        }

    return opening


def get_gl_entries_by_account(filters: dict) -> dict:
    gl_entries_by_account = {}

    query, rcgle = build_base_query(filters)
    query = query.where(rcgle.posting_date >= filters.from_date)
    query = query.where(rcgle.posting_date <= filters.to_date)

    if not flt(filters.get("with_period_closing_entry_for_current_period")):
        query = query.where(rcgle.voucher_type != "Period Closing Voucher")

    entries = query.run(as_dict=True)

    for entry in entries:
        gl_entries_by_account[entry.account] = {
            "debit": flt(entry.debit),
            "credit": flt(entry.credit),
        }

    return gl_entries_by_account
