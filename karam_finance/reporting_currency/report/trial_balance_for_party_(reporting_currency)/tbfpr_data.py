"""Data assembly for Trial Balance for Party (Reporting Currency).

Mirrors ERPNext vanilla Trial Balance for Party row semantics, but values are
taken from Reporting Currency GLE and the hidden currency is the configured
reporting currency.
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import chain
from typing import Any

import frappe
from erpnext.accounts.report.general_ledger.general_ledger import (
    get_accounts_with_children,
)
from frappe.utils import cint, flt

from .tbfpr_constants import DOCTYPE_RC_GLE, DOCTYPE_RC_SETTINGS
from .tbfpr_filters import get_party_name_field
from .tbfpr_query import get_reporting_currency_balances
from .tbfpr_rows import (
    TOTAL_FIELDS,
    build_total_row,
    get_blank_row,
    toggle_debit_credit,
)


def get_data(filters: Any, show_party_name: Any) -> list[dict[str, Any]]:
    reporting_currency = _get_reporting_currency(filters)

    party_name_field = get_party_name_field(filters)
    parties = _iter_permitted_parties(filters, party_name_field)
    first_party = next(parties, None)
    if first_party is None:
        return []

    account_filter: list[str] | None = []
    if filters.get("account"):
        account_filter = get_accounts_with_children(filters.get("account"))

    party_balances = get_reporting_currency_balances(filters, account_filter)

    data: list[dict[str, Any]] = []
    total_row = frappe._dict(dict.fromkeys(TOTAL_FIELDS, 0.0))

    for party in chain((first_party,), parties):
        party_name = party.get("name")
        balances = party_balances.get(party_name, {})

        opening_debit, opening_credit = toggle_debit_credit(
            flt(balances.get("opening_debit", 0)),
            flt(balances.get("opening_credit", 0)),
        )
        debit = flt(balances.get("debit", 0))
        credit = flt(balances.get("credit", 0))
        closing_debit, closing_credit = toggle_debit_credit(
            opening_debit + debit,
            opening_credit + credit,
        )

        row = {
            "party": party_name,
            "opening_debit": opening_debit,
            "opening_credit": opening_credit,
            "debit": debit,
            "credit": credit,
            "closing_debit": closing_debit,
            "closing_credit": closing_credit,
            "currency": reporting_currency,
        }

        if show_party_name:
            row["party_name"] = party.get(party_name_field)

        _append_party_if_visible(
            data, total_row, row, show_zero_values=cint(filters.show_zero_values)
        )

    return _append_totals(data, reporting_currency, total_row)


PARTY_PAGE_SIZE = 500


def _iter_permitted_parties(filters: Any, party_name_field: str) -> Iterator[Any]:
    """Read permitted parties in stable name order without retaining every master row."""
    party_filters: dict[str, Any] = (
        {"name": filters.get("party")} if filters.get("party") else {}
    )
    while True:
        # Permission-aware keyset pagination: one bounded page, not one read per party.
        # nosemgrep: frappe-n-plus-one-read-in-loop
        page = frappe.get_list(
            filters.party_type,
            fields=["name", party_name_field],
            filters=dict(party_filters),
            order_by="name asc",
            limit_page_length=PARTY_PAGE_SIZE,
            reference_doctype=DOCTYPE_RC_GLE,
        )
        yield from page
        if len(page) < PARTY_PAGE_SIZE or filters.get("party"):
            return
        party_filters["name"] = [">", page[-1]["name"]]


def _append_totals(
    data: list[dict[str, Any]], reporting_currency: Any, total_row: Any
) -> list[dict[str, Any]]:
    if not data:
        return []

    data.append(get_blank_row())
    data.append(
        build_total_row(
            reporting_currency,
            total_row,
        )
    )

    return data


def _get_reporting_currency(filters: Any) -> Any:
    reporting_currency = frappe.db.get_single_value(
        DOCTYPE_RC_SETTINGS, "reporting_currency"
    )
    if not reporting_currency:
        reporting_currency = frappe.get_cached_value(
            "Company", filters.company, "default_currency"
        )

    return reporting_currency


def _append_party_if_visible(
    data: list[dict[str, Any]],
    total_row: dict[str, Any],
    row: dict[str, Any],
    *,
    show_zero_values: int,
) -> None:
    if show_zero_values or any(row[field] for field in TOTAL_FIELDS):
        data.append(row)
        for field in TOTAL_FIELDS:
            total_row[field] += row[field]
