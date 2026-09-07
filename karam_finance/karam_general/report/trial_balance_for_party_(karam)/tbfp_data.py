"""Data assembly for Trial Balance for Party (Karam)."""

from __future__ import annotations

from typing import Any

import frappe
from erpnext.accounts.report.general_ledger.general_ledger import (
    get_accounts_with_children,
)
from frappe.utils import cint, flt

from .tbfp_constants import ACCOUNT_CCY_VALUE_FIELDS, VALUE_FIELDS
from .tbfp_filters import get_party_name_field
from .tbfp_query import (
    get_party_currency_balances,
    get_party_currency_balances_with_all_names,
    get_party_currency_balances_with_names,
)
from .tbfp_rows import build_party_row_from_sources, build_total_row, get_blank_row

COMPANY_VALUE_FIELDS = (
    "opening_debit",
    "opening_credit",
    "debit",
    "credit",
    "closing_debit",
    "closing_credit",
)


def get_data(filters: Any, show_party_name: Any) -> Any:
    """Build party/account-currency rows from one grouped GL query."""
    party_name_field = get_party_name_field(filters)

    account_filter: list[str] | None = []
    if filters.get("account"):
        account_filter = get_accounts_with_children(filters.get("account"))

    company_currency = frappe.get_cached_value(
        "Company", filters.company, "default_currency"
    )
    if cint(filters.get("show_zero_values")) and not filters.get(
        "exclude_zero_balance_parties"
    ):
        party_currency_balances, party_names = (
            get_party_currency_balances_with_all_names(
                filters, account_filter, party_name_field
            )
        )
    elif show_party_name:
        party_currency_balances, party_names = get_party_currency_balances_with_names(
            filters, account_filter, party_name_field
        )
    else:
        party_currency_balances = get_party_currency_balances(filters, account_filter)
        party_names: dict[str, Any] = {}

    party_keys = sorted(
        str(party) for party in (party_names or party_currency_balances)
    )
    parties = [
        {"name": party, party_name_field: party_names.get(party)}
        for party in party_keys
    ]

    (
        data,
        total_company_values,
        total_account_currency_values,
        account_currencies_seen,
    ) = _build_party_rows(
        parties,
        party_currency_balances,
        {
            "party_name_field": party_name_field,
            "show_party_name": show_party_name,
            "company_currency": company_currency,
        },
        filters=filters,
    )

    total_account_currency = next(iter(account_currencies_seen), company_currency)
    if len(account_currencies_seen) > 1:
        total_account_currency = ""
        total_account_currency_values = dict.fromkeys(ACCOUNT_CCY_VALUE_FIELDS)

    data.extend(
        [
            get_blank_row(),
            build_total_row(
                company_currency,
                total_company_values,
                total_account_currency_values,
                account_currency=total_account_currency,
            ),
        ]
    )
    return data


def _build_party_rows(
    parties: Any,
    party_currency_balances: Any,
    display: dict[str, Any],
    *,
    filters: Any,
) -> Any:
    data = []
    total_company_values = _zero_values(COMPANY_VALUE_FIELDS)
    total_account_currency_values = _zero_values(ACCOUNT_CCY_VALUE_FIELDS)
    account_currencies_seen = set()

    for party in parties:
        party_name = party.get("name")
        currency_balances = party_currency_balances.get(party_name, {})
        rows = _rows_for_party(party, currency_balances, display, filters=filters)
        for index, row in enumerate(rows):
            data.append(row)
            if not currency_balances:
                continue
            account_currencies_seen.update(_contributing_account_currency(row))

            if index == 0:
                _add_values(total_company_values, row, VALUE_FIELDS)
            _add_values(total_account_currency_values, row, ACCOUNT_CCY_VALUE_FIELDS)

    return (
        data,
        total_company_values,
        total_account_currency_values,
        account_currencies_seen,
    )


def _company_values(currency_balances: Any) -> Any:
    """Aggregate raw company-currency sides and calculate closing values."""
    values = _zero_values(("opening_debit", "opening_credit", "debit", "credit"))
    for account_values in currency_balances:
        _add_values(
            values,
            account_values,
            ("opening_debit", "opening_credit", "debit", "credit"),
        )

    values["opening_debit"], values["opening_credit"] = _net_sides(
        values["opening_debit"], values["opening_credit"]
    )
    values["closing_debit"], values["closing_credit"] = _net_sides(
        values["opening_debit"] + values["debit"],
        values["opening_credit"] + values["credit"],
    )
    return values


def _include_party(company_values: Any, filters: Any) -> Any:
    has_value = any(flt(company_values.get(field)) for field in VALUE_FIELDS)
    if not cint(filters.get("show_zero_values")) and not has_value:
        return False

    return not (
        filters.get("exclude_zero_balance_parties")
        and not (
            company_values.get("closing_debit") or company_values.get("closing_credit")
        )
    )


def _zero_values(fields: Any) -> Any:
    return dict.fromkeys(fields, 0.0)


def _build_zero_party_row(
    party: Any, party_name: Any, show_party_name: Any, *, company_currency: Any
) -> Any:
    row = {
        "party": party,
        "account_currency": company_currency,
        "currency": company_currency,
    }
    if show_party_name:
        row["party_name"] = party_name
    row.update(_zero_values((*VALUE_FIELDS, *ACCOUNT_CCY_VALUE_FIELDS)))
    return row


def _add_values(target: Any, source: Any, fields: Any) -> Any:
    for field in fields:
        target[field] = flt(target.get(field)) + flt(source.get(field))


def _net_sides(debit: Any, credit: Any) -> Any:
    if flt(debit) > flt(credit):
        return flt(debit) - flt(credit), 0.0
    return 0.0, flt(credit) - flt(debit)


def _contributing_account_currency(row: dict[str, Any]) -> set[str]:
    """Ignore zero-only currencies when labelling the total."""
    if any(flt(row.get(field)) != 0 for field in ACCOUNT_CCY_VALUE_FIELDS):
        return {row["account_currency"]}
    return set()


def _rows_for_party(
    party: Any, currency_balances: Any, display: dict[str, Any], *, filters: Any
) -> list[dict[str, Any]]:
    party_name_field = display["party_name_field"]
    show_party_name = display["show_party_name"]
    company_currency = display["company_currency"]
    party_name = party.get("name")
    if not currency_balances:
        if not cint(filters.get("show_zero_values")) or filters.get(
            "exclude_zero_balance_parties"
        ):
            return []
        return [
            _build_zero_party_row(
                party_name,
                party.get(party_name_field),
                show_party_name,
                company_currency=company_currency,
            )
        ]
    company_values = _company_values(currency_balances.values())
    if not _include_party(company_values, filters):
        return []
    party_meta = {
        "party": party_name,
        "party_name": party.get(party_name_field),
        "show_party_name": show_party_name,
        "company_currency": company_currency,
    }
    return [
        build_party_row_from_sources(
            party_meta,
            account_currency,
            company_values if index == 0 else {},
            account_currency_values=currency_balances[account_currency],
            show_party_label=index == 0,
        )
        for index, account_currency in enumerate(sorted(currency_balances))
    ]
