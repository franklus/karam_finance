"""Data assembly for Trial Balance for Party (Reporting Currency).

Mirrors ERPNext vanilla Trial Balance for Party row semantics, but values are
taken from Reporting Currency GLE and the hidden currency is the configured
reporting currency.
"""

from __future__ import annotations

from typing import Any

import frappe
from erpnext.accounts.report.general_ledger.general_ledger import (
    get_accounts_with_children,
)
from frappe.utils import cint, flt

from .tbfpr_constants import DOCTYPE_RC_SETTINGS
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
    party_filters: dict[str, Any] = (
        {"name": filters.get("party")} if filters.get("party") else {}
    )
    parties = frappe.get_all(
        filters.party_type,
        fields=["name", party_name_field],
        filters=party_filters,
        order_by="name",
    )

    if not parties:
        return []

    account_filter: list[str] | None = []
    if filters.get("account"):
        account_filter = get_accounts_with_children(filters.get("account"))

    party_balances = get_reporting_currency_balances(filters, account_filter)

    data: list[dict[str, Any]] = []
    total_row = frappe._dict(dict.fromkeys(TOTAL_FIELDS, 0.0))

    for party in parties:
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

        has_value = (
            opening_debit
            or opening_credit
            or debit
            or credit
            or closing_debit
            or closing_credit
        )
        if cint(filters.show_zero_values) or has_value:
            data.append(row)
            for field in TOTAL_FIELDS:
                total_row[field] += row[field]

    return _append_totals(data, reporting_currency, total_row)


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
