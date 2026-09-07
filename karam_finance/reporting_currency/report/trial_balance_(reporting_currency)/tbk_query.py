"""Database-side aggregation for the Karam Trial Balance report."""

from __future__ import annotations

from typing import Any

import frappe

from .tbk_company_currency import COMPANY_CCY_VALUE_FIELDS, add_company_sums
from .tbk_conditions import apply_gl_filters
from .tbk_money import decimal_amount, sum_amount

ACCOUNT_CURRENCY_FIELDS = (
    "opening_debit_in_account_currency",
    "opening_credit_in_account_currency",
    "debit_in_account_currency",
    "credit_in_account_currency",
)
COMPANY_FIELDS = ("opening_debit", "opening_credit", "debit", "credit")


def get_period_balances(
    filters: Any,
    ignore_is_opening: Any = 0,
    *,
    finance_books: Any = None,
    accounting_dimensions: Any = None,
) -> dict[str, dict[str, Any]]:
    """Aggregate the requested period in one bounded query per currency group.

    Rows remain grouped by ``account_currency`` until after presentation
    currency conversion.  That preserves ERPNext's conversion rules while
    allowing the report layer to blank mixed-currency group and total values.
    """

    gl_entry = frappe.qb.DocType("Reporting Currency GLE")
    query = (
        frappe.qb.from_(gl_entry)
        .select(
            gl_entry.account,
            gl_entry.account_currency,
            sum_amount(gl_entry, "debit").as_("debit"),
            sum_amount(gl_entry, "credit").as_("credit"),
            sum_amount(gl_entry, "debit_in_account_currency").as_(
                "debit_in_account_currency"
            ),
            sum_amount(gl_entry, "credit_in_account_currency").as_(
                "credit_in_account_currency"
            ),
        )
        .where(gl_entry.company == filters.company)
        .where(gl_entry.is_cancelled == 0)
        .where(gl_entry.posting_date >= filters.from_date)
        .where(gl_entry.posting_date <= filters.to_date)
    )

    if not ignore_is_opening:
        query = query.where(
            gl_entry.is_opening.isnull() | (gl_entry.is_opening == "No")
        )

    if not decimal_amount(filters.get("with_period_closing_entry_for_current_period")):
        query = query.where(
            gl_entry.voucher_type.isnull()
            | (gl_entry.voucher_type != "Period Closing Voucher")
        )

    query = apply_gl_filters(
        query,
        gl_entry,
        filters,
        finance_books=finance_books,
        accounting_dimensions=accounting_dimensions,
    )
    query = query.groupby(gl_entry.account, gl_entry.account_currency)

    # This is the same selective index used by ERPNext's v16 GL pipeline.
    # Let RCGLE use its own available indexes.
    rows = add_company_sums(query, gl_entry).run(as_dict=True)

    return _group_currency_rows(rows)


def _group_currency_rows(rows: Any) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        account = row.account
        account_data = grouped.setdefault(
            account,
            {
                "account_currencies": set(),
                **dict[str, Any].fromkeys(COMPANY_FIELDS + COMPANY_CCY_VALUE_FIELDS, 0),
                **dict[str, Any].fromkeys(ACCOUNT_CURRENCY_FIELDS, 0),
            },
        )
        currency = row.get("account_currency") or ""
        if currency and any(
            decimal_amount(row.get(field)) != 0 for field in ACCOUNT_CURRENCY_FIELDS
        ):
            account_data["account_currencies"].add(currency)

        for field in (
            COMPANY_FIELDS + ACCOUNT_CURRENCY_FIELDS + COMPANY_CCY_VALUE_FIELDS
        ):
            account_data[field] += decimal_amount(row.get(field))

    return grouped


def get_gl_data_optimised(filters: Any) -> dict[str, dict[str, Any]]:
    """Compatibility alias for the former unused optimisation entry point."""

    ignore_is_opening = frappe.db.get_single_value(
        "Accounts Settings", "ignore_is_opening_check_for_reporting"
    )
    return get_period_balances(filters, ignore_is_opening)
