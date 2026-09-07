"""Balance aggregation and currency-aware hierarchy handling."""

from __future__ import annotations

from typing import Any

from frappe.utils import flt

from .tbk_constants import ACCOUNT_CCY_VALUE_FIELDS, VALUE_FIELDS

_RAW_ACCOUNT_CURRENCY_VALUES = "_raw_account_currency_values"
_ACCOUNT_CURRENCIES = "_account_currencies"


def apply_balances_to_accounts(
    accounts: Any, opening_balances: Any, period_balances: Any
) -> Any:
    """Attach company and account-currency balances to account rows."""

    for account in accounts:
        opening = opening_balances.get(account.name, {})
        period = period_balances.get(account.name, {})
        account.update(
            {
                "opening_debit": flt(opening.get("opening_debit", 0)),
                "opening_credit": flt(opening.get("opening_credit", 0)),
                "debit": flt(period.get("debit", 0)),
                "credit": flt(period.get("credit", 0)),
            }
        )
        account["closing_debit"] = account["opening_debit"] + account["debit"]
        account["closing_credit"] = account["opening_credit"] + account["credit"]

        raw_account_currency_values = {
            "opening_debit_in_account_currency": flt(
                opening.get("opening_debit_in_account_currency", 0)
            ),
            "opening_credit_in_account_currency": flt(
                opening.get("opening_credit_in_account_currency", 0)
            ),
            "debit_in_account_currency": flt(
                period.get("debit_in_account_currency", 0)
            ),
            "credit_in_account_currency": flt(
                period.get("credit_in_account_currency", 0)
            ),
        }
        raw_account_currency_values.update(
            {
                "closing_debit_in_account_currency": (
                    raw_account_currency_values["opening_debit_in_account_currency"]
                    + raw_account_currency_values["debit_in_account_currency"]
                ),
                "closing_credit_in_account_currency": (
                    raw_account_currency_values["opening_credit_in_account_currency"]
                    + raw_account_currency_values["credit_in_account_currency"]
                ),
            }
        )
        account[_RAW_ACCOUNT_CURRENCY_VALUES] = raw_account_currency_values

        currencies = set(opening.get("account_currencies", set()))
        currencies.update(period.get("account_currencies", set()))
        account[_ACCOUNT_CURRENCIES] = currencies
        account.update(dict.fromkeys(ACCOUNT_CCY_VALUE_FIELDS, 0.0))


def apply_gl_data_to_accounts(
    accounts: Any, gl_data: Any, show_net_values: Any = False
) -> Any:
    """Compatibility helper for callers that provide already-summed balances."""

    for account in accounts:
        data = gl_data.get(account.name, {})
        account.update(
            {
                field: flt(data.get(field, 0))
                for field in (
                    "opening_debit",
                    "opening_credit",
                    "debit",
                    "credit",
                    "opening_debit_in_account_currency",
                    "opening_credit_in_account_currency",
                    "debit_in_account_currency",
                    "credit_in_account_currency",
                )
            }
        )
        account["closing_debit"] = account["opening_debit"] + account["debit"]
        account["closing_credit"] = account["opening_credit"] + account["credit"]
        account["closing_debit_in_account_currency"] = (
            account["opening_debit_in_account_currency"]
            + account["debit_in_account_currency"]
        )
        account["closing_credit_in_account_currency"] = (
            account["opening_credit_in_account_currency"]
            + account["credit_in_account_currency"]
        )
        account[_ACCOUNT_CURRENCIES] = set(
            data.get("account_currencies", {account.get("account_currency", "")})
        ) - {""}
        account[_RAW_ACCOUNT_CURRENCY_VALUES] = {
            field: account.get(field, 0.0) for field in ACCOUNT_CCY_VALUE_FIELDS
        }
    finalize_account_currency_values(accounts, show_net_values)


def apply_account_currency_data_to_accounts(
    accounts: Any,
    gl_entries_by_account: Any,
    opening_balances_in_account_currency: Any,
    *,
    show_net_values: Any,
    ignore_is_opening: Any = 0,
) -> Any:
    """Compatibility helper retained for older direct callers."""

    for account in accounts:
        opening_data = opening_balances_in_account_currency.get(account.name, {})
        account_currencies = set(opening_data.get("account_currencies", set()))
        period_debit = 0.0
        period_credit = 0.0
        for entry in gl_entries_by_account.get(account.name, []):
            if not ignore_is_opening and entry.get("is_opening") == "Yes":
                continue
            currency = entry.get("account_currency")
            if currency:
                account_currencies.add(currency)
            period_debit += flt(entry.get("debit_in_account_currency"))
            period_credit += flt(entry.get("credit_in_account_currency"))

        account.update(
            {
                "opening_debit_in_account_currency": flt(
                    opening_data.get("opening_debit_in_account_currency", 0)
                ),
                "opening_credit_in_account_currency": flt(
                    opening_data.get("opening_credit_in_account_currency", 0)
                ),
                "debit_in_account_currency": period_debit,
                "credit_in_account_currency": period_credit,
            }
        )
        account["closing_debit_in_account_currency"] = (
            account["opening_debit_in_account_currency"]
            + account["debit_in_account_currency"]
        )
        account["closing_credit_in_account_currency"] = (
            account["opening_credit_in_account_currency"]
            + account["credit_in_account_currency"]
        )
        account[_ACCOUNT_CURRENCIES] = account_currencies
        account[_RAW_ACCOUNT_CURRENCY_VALUES] = {
            field: account.get(field, 0.0) for field in ACCOUNT_CCY_VALUE_FIELDS
        }

    finalize_account_currency_values(accounts, show_net_values)


def accumulate_values_into_parents(accounts: Any, accounts_by_name: Any) -> Any:
    """Accumulate gross balances and currency provenance up the account tree."""

    for account in reversed(accounts):
        if not account.parent_account:
            continue

        parent = accounts_by_name[account.parent_account]
        for key in VALUE_FIELDS:
            parent[key] = parent.get(key, 0) + account.get(key, 0)

        parent_raw = parent.setdefault(
            _RAW_ACCOUNT_CURRENCY_VALUES,
            dict.fromkeys(ACCOUNT_CCY_VALUE_FIELDS, 0.0),
        )
        for key in ACCOUNT_CCY_VALUE_FIELDS:
            parent_raw[key] += account.get(_RAW_ACCOUNT_CURRENCY_VALUES, {}).get(key, 0)
        parent.setdefault(_ACCOUNT_CURRENCIES, set()).update(
            account.get(_ACCOUNT_CURRENCIES, set())
        )


def finalize_account_currency_values(
    accounts: Any, show_net_values: Any = False
) -> Any:
    """Expose account-currency values only when one currency contributes."""

    for account in accounts:
        currencies = account.get(_ACCOUNT_CURRENCIES, set())
        raw_values = account.get(_RAW_ACCOUNT_CURRENCY_VALUES, {})
        if len(currencies) > 1:
            account["account_currency"] = ""
            account.update(dict.fromkeys(ACCOUNT_CCY_VALUE_FIELDS))
            continue

        if currencies:
            account["account_currency"] = next(iter(currencies))
        account.update(
            {field: flt(raw_values.get(field, 0)) for field in ACCOUNT_CCY_VALUE_FIELDS}
        )
        if show_net_values:
            prepare_account_currency_opening_closing(account)


def prepare_opening_closing(row: Any, include_account_currency: Any = True) -> Any:
    root_type = row.get("root_type")
    if not root_type:
        return

    dr_or_cr = "debit" if root_type in ["Asset", "Equity", "Expense"] else "credit"
    reverse_dr_or_cr = "credit" if dr_or_cr == "debit" else "debit"

    for col_type in ["opening", "closing"]:
        valid_col = f"{col_type}_{dr_or_cr}"
        reverse_col = f"{col_type}_{reverse_dr_or_cr}"

        row[valid_col] -= row[reverse_col]
        if row[valid_col] < 0:
            row[reverse_col] = abs(row[valid_col])
            row[valid_col] = 0.0
        else:
            row[reverse_col] = 0.0

    if include_account_currency:
        prepare_account_currency_opening_closing(row)


def prepare_account_currency_opening_closing(row: Any) -> Any:
    root_type = row.get("root_type")
    if not root_type:
        return

    dr_or_cr = "debit" if root_type in ["Asset", "Equity", "Expense"] else "credit"
    reverse_dr_or_cr = "credit" if dr_or_cr == "debit" else "debit"

    for col_type in ["opening", "closing"]:
        valid_col = f"{col_type}_{dr_or_cr}_in_account_currency"
        reverse_col = f"{col_type}_{reverse_dr_or_cr}_in_account_currency"

        row[valid_col] -= row[reverse_col]
        if row[valid_col] < 0:
            row[reverse_col] = abs(row[valid_col])
            row[valid_col] = 0.0
        else:
            row[reverse_col] = 0.0
