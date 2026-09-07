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
        if not _include_in_total(account, show_group_accounts):
            continue
        _add_account_values(total_row, account, VALUE_FIELDS)
        account_currencies.update(account.get("_account_currencies", set()))
        _add_account_values(account_currency_totals, account, ACCOUNT_CCY_VALUE_FIELDS)

    if len(account_currencies) == 1:
        total_row["account_currency"] = next(iter(account_currencies))
        total_row.update(
            {field: flt(value) for field, value in account_currency_totals.items()}
        )
    else:
        total_row.update(dict.fromkeys(ACCOUNT_CCY_VALUE_FIELDS))

    return total_row


def prepare_data(
    accounts: Any, filters: Any, _parent_children_map: Any, *, company_currency: Any
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

        _set_row_values(row, account, company_currency)
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


def filter_out_zero_value_rows(
    data: list[dict[str, Any]],
    parent_children_map: dict[str | None, list[dict[str, Any]]],
    show_zero_values: bool = False,
) -> list[dict[str, Any]]:
    """Keep valued accounts and their ancestors without rescanning the tree."""
    if show_zero_values:
        return list(data)

    parents = {
        child["name"]: parent
        for parent, children in parent_children_map.items()
        for child in children
    }
    accounts_to_show = set()
    for row in data:
        if not row.get("has_value"):
            continue
        _include_account_ancestors(accounts_to_show, row.get("account"), parents)

    return [row for row in data if row.get("account") in accounts_to_show]


def _hide_group_accounts(data: Any) -> Any:
    return [dict(row, indent=0) for row in data if not row.get("is_group_account")]


def _include_in_total(account: Any, show_group_accounts: Any) -> bool:
    return (
        not account.parent_account
        if show_group_accounts
        else not account.get("is_group")
    )


def _set_row_values(row: dict[str, Any], account: Any, company_currency: Any) -> None:
    has_value = False
    for key in VALUE_FIELDS + ACCOUNT_CCY_VALUE_FIELDS:
        value = account.get(key)
        row[key] = flt(value) if value is not None else None

        if (
            key in VALUE_FIELDS
            and row[key] is not None
            and abs(row[key]) >= get_zero_cutoff(company_currency)
        ):
            has_value = True

    row["has_value"] = has_value


def _add_account_values(
    target: dict[str, Any], account: Any, fields: tuple[str, ...]
) -> None:
    for field in fields:
        target[field] += flt(account.get(field, 0))


def _include_account_ancestors(
    accounts_to_show: set[str | None],
    account: str | None,
    parents: dict[str, str | None],
) -> None:
    while account not in accounts_to_show:
        accounts_to_show.add(account)
        account = parents.get(account) if account is not None else None
        if not account:
            break
