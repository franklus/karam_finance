from frappe import _


def get_columns(filters, show_party_name):
    columns = [
        {
            "fieldname": "party",
            "label": _(filters.party_type),
            "fieldtype": "Link",
            "options": filters.party_type,
            "width": 200,
        },
        {
            "fieldname": "account_currency",
            "label": _("Account Currency"),
            "fieldtype": "Link",
            "options": "Currency",
            "width": 80,
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
            "label": _("Debit"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 120,
        },
        {
            "fieldname": "credit",
            "label": _("Credit"),
            "fieldtype": "Currency",
            "options": "currency",
            "width": 120,
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
            "fieldname": "opening_debit_in_account_currency",
            "label": _("Opening Dr (Account Ccy)"),
            "fieldtype": "Currency",
            "options": "account_currency",
            "width": 140,
        },
        {
            "fieldname": "opening_credit_in_account_currency",
            "label": _("Opening Cr (Account Ccy)"),
            "fieldtype": "Currency",
            "options": "account_currency",
            "width": 140,
        },
        {
            "fieldname": "debit_in_account_currency",
            "label": _("Movement Dr (Account Ccy)"),
            "fieldtype": "Currency",
            "options": "account_currency",
            "width": 140,
        },
        {
            "fieldname": "credit_in_account_currency",
            "label": _("Movement Cr (Account Ccy)"),
            "fieldtype": "Currency",
            "options": "account_currency",
            "width": 140,
        },
        {
            "fieldname": "closing_debit_in_account_currency",
            "label": _("Closing Dr (Account Ccy)"),
            "fieldtype": "Currency",
            "options": "account_currency",
            "width": 140,
        },
        {
            "fieldname": "closing_credit_in_account_currency",
            "label": _("Closing Cr (Account Ccy)"),
            "fieldtype": "Currency",
            "options": "account_currency",
            "width": 140,
        },
        {
            "fieldname": "currency",
            "label": _("Currency"),
            "fieldtype": "Link",
            "options": "Currency",
            "hidden": 1,
        },
    ]

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
