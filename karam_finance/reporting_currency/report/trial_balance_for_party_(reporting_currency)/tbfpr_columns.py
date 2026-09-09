"""Column definitions for Trial Balance for Party (Reporting Currency)."""

from typing import Any

from frappe import _


def get_columns(filters: Any, show_party_name: Any) -> Any:
    columns = [
        {
            "fieldname": "party",
            "label": _(filters.party_type),
            "fieldtype": "Link",
            "options": filters.party_type,
            "width": 200,
        },
        {
            "fieldname": "account",
            "label": _("Account"),
            "fieldtype": "Link",
            "options": "Account",
            "width": 300,
        },
        {
            "fieldname": "account_currency",
            "label": _("Account Currency"),
            "fieldtype": "Link",
            "options": "Currency",
            "width": 100,
        },
        {
            "fieldname": "opening_debit",
            "label": _("Opening (Dr)"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 120,
        },
        {
            "fieldname": "opening_credit",
            "label": _("Opening (Cr)"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 120,
        },
        {
            "fieldname": "debit",
            "label": _("Movement (Dr)"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 130,
        },
        {
            "fieldname": "credit",
            "label": _("Movement (Cr)"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 130,
        },
        {
            "fieldname": "closing_debit",
            "label": _("Closing (Dr)"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 120,
        },
        {
            "fieldname": "closing_credit",
            "label": _("Closing (Cr)"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 120,
        },
        {
            "fieldname": "currency",
            "label": _("Currency"),
            "fieldtype": "Link",
            "options": "Currency",
            "hidden": 1,
        },
    ]

    if filters.party_type in ("Customer", "Supplier"):
        columns.insert(
            3,
            {
                "fieldname": "billing_currency",
                "label": _("Billing Currency"),
                "fieldtype": "Link",
                "options": "Currency",
                "width": 180,
            },
        )

    if show_party_name:
        columns.insert(
            1,
            {
                "fieldname": "party_name",
                "label": _(filters.party_type) + " Name",
                "fieldtype": "Data",
                "width": 200,
            },
        )

    return columns
