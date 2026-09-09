"""Queries against Reporting Currency GLE for Trial Balance for Party."""

from typing import Any

import frappe
from frappe.query_builder import Case
from frappe.query_builder.functions import Sum
from frappe.utils import flt

from .tbfpr_constants import DOCTYPE_RC_GLE


def get_reporting_currency_balances(filters: Any, account_filter: Any = None) -> Any:
    """Return reporting-currency balances per party and account from RC GLE."""
    rcgle = frappe.qb.DocType(DOCTYPE_RC_GLE)
    account = frappe.qb.DocType("Account")

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
        .inner_join(account)
        .on(account.name == rcgle.account)
        .select(
            rcgle.party,
            rcgle.account,
            account.account_currency,
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
            & (rcgle.posting_date <= filters.to_date)
        )
        .groupby(rcgle.party, rcgle.account, account.account_currency)
        .orderby(rcgle.party, rcgle.account)
    )

    query = _apply_common_filters(query, rcgle, filters, account_filter=account_filter)
    query = _apply_source_permissions(query, rcgle, filters)

    results: dict[str, list[dict[str, Any]]] = {}
    for row in query.run(as_dict=True):
        results.setdefault(row.party, []).append(
            {
                "account": row.account,
                "account_currency": row.account_currency,
                "opening_debit": flt(row.opening_debit),
                "opening_credit": flt(row.opening_credit),
                "debit": flt(row.debit),
                "credit": flt(row.credit),
            }
        )
    return results


def _apply_source_permissions(query: Any, rcgle: Any, filters: Any) -> Any:
    """Apply source-row and Dynamic Link party permissions before grouping."""
    permitted_rc_gle = frappe.qb.get_query(
        DOCTYPE_RC_GLE,
        fields=["name"],
        ignore_permissions=False,
    )
    query = query.where(rcgle.name.isin(permitted_rc_gle))

    # ``party`` is a Dynamic Link, so Frappe cannot infer its target doctype
    # while applying Reporting Currency GLE user permissions.
    party_filters = {"name": filters.party} if filters.get("party") else None
    permitted_parties = frappe.qb.get_query(
        filters.party_type,
        fields=["name"],
        filters=party_filters,
        ignore_permissions=False,
        reference_doctype=DOCTYPE_RC_GLE,
    )
    return query.where(rcgle.party.isin(permitted_parties))


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
    # Legacy bulk-inserted DOE rows have NULL opening flags.
    return (
        Case()
        .when(
            (rcgle.posting_date >= filters.from_date)
            & (rcgle.posting_date <= filters.to_date)
            & ((rcgle.is_opening == "No") | rcgle.is_opening.isnull()),
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
