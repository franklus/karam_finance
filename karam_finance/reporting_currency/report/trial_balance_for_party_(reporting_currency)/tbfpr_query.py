"""Queries against Reporting Currency GLE for Trial Balance for Party."""

from typing import Any

import frappe
from frappe.query_builder import Case
from frappe.query_builder.functions import Sum
from frappe.utils import flt

from .tbfpr_constants import DOCTYPE_RC_GLE


def get_reporting_currency_balances(filters: Any, account_filter: Any = None) -> Any:
    """Return reporting-currency balances per party from RC GLE.

    Returns nested dict: {party: {"opening_debit", "opening_credit",
    "debit", "credit"}}
    """
    rcgle = frappe.qb.DocType(DOCTYPE_RC_GLE)

    opening_debit = Sum(
        _opening_case(rcgle, filters, rcgle.reporting_debit)  # pyright: ignore[reportArgumentType]
    ).as_("opening_debit")
    opening_credit = Sum(
        _opening_case(rcgle, filters, rcgle.reporting_credit)  # pyright: ignore[reportArgumentType]
    ).as_("opening_credit")
    period_debit = Sum(
        _period_case(rcgle, filters, rcgle.reporting_debit)  # pyright: ignore[reportArgumentType]
    ).as_("debit")
    period_credit = Sum(
        _period_case(rcgle, filters, rcgle.reporting_credit)  # pyright: ignore[reportArgumentType]
    ).as_("credit")

    query = (
        frappe.qb.from_(rcgle)
        .select(
            rcgle.party,
            opening_debit,
            opening_credit,
            period_debit,
            period_credit,
        )
        .where(
            (rcgle.company == filters.company)
            & (rcgle.is_cancelled == 0)
            & (rcgle.party_type == filters.party_type)
            & (rcgle.party != "")
        )
        .groupby(rcgle.party)
    )

    query = _apply_common_filters(query, rcgle, filters, account_filter=account_filter)

    results = {}
    for row in query.run(as_dict=True):
        results[row.party] = {
            "opening_debit": flt(row.opening_debit),
            "opening_credit": flt(row.opening_credit),
            "debit": flt(row.debit),
            "credit": flt(row.credit),
        }
    return results


def _opening_case(rcgle: Any, filters: Any, amount_field: Any) -> Any:
    return (
        Case()
        .when(
            (rcgle.posting_date < filters.from_date)
            | ((rcgle.is_opening == "Yes") & (rcgle.posting_date <= filters.to_date)),
            amount_field,
        )
        .else_(0)
    )


def _period_case(rcgle: Any, filters: Any, amount_field: Any) -> Any:
    return (
        Case()
        .when(
            (rcgle.posting_date >= filters.from_date)
            & (rcgle.posting_date <= filters.to_date)
            & (rcgle.is_opening == "No"),
            amount_field,
        )
        .else_(0)
    )


def _apply_common_filters(
    query: Any, rcgle: Any, filters: Any, *, account_filter: Any
) -> Any:
    if filters.get("party"):
        query = query.where(rcgle.party == filters.party)

    if account_filter:
        query = query.where(rcgle.account.isin(account_filter))

    return query
