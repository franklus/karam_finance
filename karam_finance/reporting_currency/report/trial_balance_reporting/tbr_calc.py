# ruff: noqa: D100, D103

from frappe.utils import flt

from .tbr_constants import VALUE_FIELDS


def calculate_values(
    accounts: list[dict],
    gl_entries_by_account: dict,
    opening_balances: dict,
) -> None:
    init = {
        "opening_debit": 0.0,
        "opening_credit": 0.0,
        "debit": 0.0,
        "credit": 0.0,
        "closing_debit": 0.0,
        "closing_credit": 0.0,
    }

    for account in accounts:
        account.update(init.copy())

        account["opening_debit"] = opening_balances.get(account.name, {}).get(
            "opening_debit", 0
        )
        account["opening_credit"] = opening_balances.get(account.name, {}).get(
            "opening_credit", 0
        )

        period_entry = gl_entries_by_account.get(account.name, {})
        account["debit"] = flt(period_entry.get("debit", 0))
        account["credit"] = flt(period_entry.get("credit", 0))

        account["closing_debit"] = account["opening_debit"] + account["debit"]
        account["closing_credit"] = account["opening_credit"] + account["credit"]


def accumulate_values_into_parents(
    accounts: list[dict], accounts_by_name: dict
) -> None:
    for account in reversed(accounts):
        if account.parent_account:
            for key in VALUE_FIELDS:
                accounts_by_name[account.parent_account][key] += account[key]


def prepare_opening_closing(row: dict) -> None:
    debit_root_types = ("Asset", "Equity", "Expense")
    dr_or_cr = "debit" if row.get("root_type") in debit_root_types else "credit"
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
