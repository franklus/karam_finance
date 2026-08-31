# ruff: noqa: D100, D103

from frappe import _
from frappe.utils import flt

from .tbr_calc import prepare_opening_closing
from .tbr_constants import VALUE_FIELDS, ZERO_THRESHOLD


def prepare_data(
    accounts: list[dict],
    filters: dict,
    reporting_currency: str,
) -> list[dict]:
    data = []

    for account in accounts:
        prepare_opening_closing(account)

        has_value = False
        row = {
            "account": account.name,
            "is_group": account.is_group,
            "parent_account": account.parent_account,
            "indent": account.indent,
            "from_date": filters.from_date,
            "to_date": filters.to_date,
            "currency": reporting_currency,
            "account_name": (
                f"{account.account_number} - {account.account_name}"
                if account.account_number
                else account.account_name
            ),
        }

        for key in VALUE_FIELDS:
            row[key] = flt(account.get(key, 0.0), 3)
            if abs(row[key]) >= ZERO_THRESHOLD:
                has_value = True

        row["has_value"] = has_value
        data.append(row)

    blank_row = {
        "account": "",
        "account_name": "",
        "is_group": None,
        "opening_debit": None,
        "opening_credit": None,
        "debit": None,
        "credit": None,
        "closing_debit": None,
        "closing_credit": None,
        "currency": reporting_currency,
        "has_value": True,
    }
    total_row = calculate_total_row(accounts, reporting_currency)
    data.extend([blank_row, total_row])

    return data


def calculate_total_row(accounts: list[dict], reporting_currency: str) -> dict:
    total_row = {
        "account": "'" + _("Total") + "'",
        "account_name": "'" + _("Total") + "'",
        "is_group": None,
        "warn_if_negative": True,
        "opening_debit": 0.0,
        "opening_credit": 0.0,
        "debit": 0.0,
        "credit": 0.0,
        "closing_debit": 0.0,
        "closing_credit": 0.0,
        "parent_account": None,
        "indent": 0,
        "has_value": True,
        "currency": reporting_currency,
    }

    for account in accounts:
        if not account.parent_account:
            for field in VALUE_FIELDS:
                total_row[field] += account[field]

    return total_row


def get_columns() -> list[dict]:
    return [
        {
            "fieldname": "account",
            "label": _("Account"),
            "fieldtype": "Link",
            "options": "Account",
            "width": 300,
        },
        {
            "fieldname": "is_group",
            "label": _("Is Group"),
            "fieldtype": "Check",
            "width": 80,
        },
        {
            "fieldname": "currency",
            "label": _("Currency"),
            "fieldtype": "Link",
            "options": "Currency",
            "hidden": 1,
        },
        {
            "fieldname": "opening_debit",
            "label": _("Opening (Dr)"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 180,
        },
        {
            "fieldname": "opening_credit",
            "label": _("Opening (Cr)"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 180,
        },
        {
            "fieldname": "debit",
            "label": _("Reporting Debit"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 180,
        },
        {
            "fieldname": "credit",
            "label": _("Reporting Credit"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 180,
        },
        {
            "fieldname": "closing_debit",
            "label": _("Closing (Dr)"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 180,
        },
        {
            "fieldname": "closing_credit",
            "label": _("Closing (Cr)"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 180,
        },
    ]
