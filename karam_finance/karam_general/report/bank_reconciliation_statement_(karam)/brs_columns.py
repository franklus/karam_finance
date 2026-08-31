from frappe import _


def get_columns():
    return [
        {
            "fieldname": "posting_date",
            "label": _("Posting Date"),
            "fieldtype": "Date",
            "width": 90,
        },
        {
            "fieldname": "payment_document",
            "label": _("Payment Document Type"),
            "fieldtype": "Data",
            "width": 220,
        },
        {
            "fieldname": "payment_entry",
            "label": _("Payment Document"),
            "fieldtype": "Dynamic Link",
            "options": "payment_document",
            "width": 220,
        },
        {
            "fieldname": "debit",
            "label": _("Debit"),
            "fieldtype": "Currency",
            "options": "account_currency",
            "width": 120,
        },
        {
            "fieldname": "credit",
            "label": _("Credit"),
            "fieldtype": "Currency",
            "options": "account_currency",
            "width": 120,
        },
        {
            "fieldname": "against_account",
            "label": _("Against Account"),
            "fieldtype": "Link",
            "options": "Account",
            "width": 200,
        },
        {
            "fieldname": "party_type",
            "label": _("Party Type"),
            "fieldtype": "Link",
            "options": "DocType",
            "width": 100,
        },
        {
            "fieldname": "party",
            "label": _("Party"),
            "fieldtype": "Dynamic Link",
            "options": "party_type",
            "width": 150,
        },
        {
            "fieldname": "party_name",
            "label": _("Party Name"),
            "fieldtype": "Data",
            "width": 180,
        },
        {
            "fieldname": "reference_no",
            "label": _("Reference"),
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "fieldname": "ref_date",
            "label": _("Ref Date"),
            "fieldtype": "Date",
            "width": 110,
        },
        {
            "fieldname": "clearance_date",
            "label": _("Clearance Date"),
            "fieldtype": "Date",
            "width": 110,
        },
        {
            "fieldname": "account_currency",
            "label": _("Currency"),
            "fieldtype": "Link",
            "options": "Currency",
            "width": 100,
        },
    ]
