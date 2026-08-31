"""Presentation rows for the Karam Trial Balance report."""

from __future__ import annotations

from typing import Any

from erpnext.accounts.utils import get_zero_cutoff
from frappe import _
from frappe.utils import flt

from .tbk_aggregation import (
    prepare_account_currency_opening_closing,
    prepare_opening_closing,
)
from .tbk_constants import ACCOUNT_CCY_VALUE_FIELDS, VALUE_FIELDS


def get_blank_row() -> dict[str, Any]:
    blank_row: dict[str, Any] = {
        "account": "",
        "account_name": "",
        "is_spacer": True,
        "has_value": True,
    }
    for field in VALUE_FIELDS + ACCOUNT_CCY_VALUE_FIELDS:
        blank_row[field] = None
    return blank_row


def calculate_total_row(
    accounts: Any, company_currency: Any, show_group_accounts: Any = True
) -> dict[str, Any]:
    total_row = {
        "account": "'" + _("Total") + "'",
        "account_name": "'" + _("Total") + "'",
        "warn_if_negative": True,
        "parent_account": None,
        "indent": 0,
        "has_value": True,
        "currency": company_currency,
        "account_currency": "",
        "bold": 1,
        "is_total": True,
    }
    for field in VALUE_FIELDS:
        total_row[field] = 0.0

    account_currency_totals = dict.fromkeys(ACCOUNT_CCY_VALUE_FIELDS, 0.0)
    account_currencies = set()
    for account in accounts:
        if show_group_accounts and account.parent_account:
            continue
        if not show_group_accounts and account.get("is_group"):
            continue
        for field in VALUE_FIELDS:
            total_row[field] += flt(account.get(field, 0))
        account_currencies.update(account.get("_account_currencies", set()))
        for field in ACCOUNT_CCY_VALUE_FIELDS:
            account_currency_totals[field] += flt(account.get(field, 0))

    if len(account_currencies) == 1:
        total_row["account_currency"] = next(iter(account_currencies))
        total_row.update(
            {field: flt(value) for field, value in account_currency_totals.items()}
        )
    else:
        total_row.update({field: None for field in ACCOUNT_CCY_VALUE_FIELDS})

    return total_row


def prepare_data(
    accounts: Any, filters: Any, parent_children_map: Any, company_currency: Any
) -> list[dict[str, Any]]:
    data = []
    show_group_accounts = filters.get("show_group_accounts")
    if show_group_accounts is None:
        show_group_accounts = 1

    for account in accounts:
        if filters.get("show_net_values"):
            prepare_opening_closing(account, include_account_currency=False)
            if account.get("account_currency"):
                prepare_account_currency_opening_closing(account)

        has_value = False
        row: dict[str, Any] = {
            "account": account.name,
            "parent_account": account.parent_account,
            "indent": account.indent,
            "from_date": filters.from_date,
            "to_date": filters.to_date,
            "currency": company_currency,
            "account_currency": account.get("account_currency", ""),
            "is_group": account.get("is_group", 0),
            "is_group_account": account.get("is_group", 0),
            "acc_name": account.account_name,
            "acc_number": account.account_number,
            "account_name": (
                f"{account.account_number} - {account.account_name}"
                if account.account_number
                else account.account_name
            ),
        }

        for key in VALUE_FIELDS + ACCOUNT_CCY_VALUE_FIELDS:
            value = account.get(key)
            row[key] = flt(value, 3) if value is not None else None

            if (
                key in VALUE_FIELDS
                and row[key] is not None
                and abs(row[key]) >= get_zero_cutoff(company_currency)
            ):
                has_value = True

        row["has_value"] = has_value
        data.append(row)

    if not show_group_accounts:
        data = _hide_group_accounts(data)

    total_row = calculate_total_row(
        accounts,
        company_currency,
        show_group_accounts=show_group_accounts,
    )
    data.append(total_row)
    return data


def _hide_group_accounts(data):
    return [dict(row, indent=0) for row in data if not row.get("is_group_account")]
