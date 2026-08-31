import frappe
from frappe import _


def get_result_as_list(data, filters):
    from erpnext.accounts.report.general_ledger.general_ledger import (
        get_result_as_list as base_get_result_as_list,
    )

    result = base_get_result_as_list(data, filters)
    result = inject_reporting_currency(result)
    return insert_footer_separator(result)


def inject_reporting_currency(result: list[dict]) -> list[dict]:
    if not result:
        return result

    reporting_currency = frappe.db.get_single_value(
        "Reporting Currency Settings", "reporting_currency"
    )
    if not reporting_currency:
        return result

    for row in result:
        if not row.get("reporting_currency"):
            row["reporting_currency"] = reporting_currency

    return result


def insert_footer_separator(result: list[dict]) -> list[dict]:
    if not result:
        return result

    footer_start_idx = None
    for idx, row in enumerate(result):
        account_value = row.get("account", "")
        if isinstance(account_value, str) and (
            account_value.strip().strip("'").startswith("Total")
            or account_value.strip().strip("'").startswith("Closing")
        ):
            footer_start_idx = idx
            break

    if footer_start_idx is None:
        return result

    data_rows = result[:footer_start_idx]
    footer_rows = result[footer_start_idx:]
    for row in footer_rows:
        row["is_report_footer"] = True

    reporting_currency = frappe.db.get_single_value(
        "Reporting Currency Settings", "reporting_currency"
    )
    blank_row = {
        "posting_date": None,
        "account": None,
        "debit": None,
        "credit": None,
        "debit_in_account_currency": None,
        "credit_in_account_currency": None,
        "balance": None,
        "voucher_type": None,
        "voucher_no": None,
        "party_type": None,
        "party": None,
        "reporting_currency": reporting_currency,
        "is_spacer": True,
    }

    return [*data_rows, blank_row, *footer_rows]


def get_columns(filters: dict) -> list[dict]:
    columns = [
        {
            "label": _("GL Entry"),
            "fieldname": "gl_entry",
            "fieldtype": "Link",
            "options": "Reporting Currency GLE",
            "hidden": 1,
        },
        {
            "label": _("Posting Date"),
            "fieldname": "posting_date",
            "fieldtype": "Date",
            "width": 100,
        },
        {
            "label": _("Account"),
            "fieldname": "account",
            "fieldtype": "Link",
            "options": "Account",
            "width": 180,
        },
        {
            "label": _("Reporting Debit"),
            "fieldname": "debit",
            "fieldtype": "Currency",
            "options": "reporting_currency",
            "width": 130,
        },
        {
            "label": _("Reporting Credit"),
            "fieldname": "credit",
            "fieldtype": "Currency",
            "options": "reporting_currency",
            "width": 130,
        },
        {
            "label": _("Balance"),
            "fieldname": "balance",
            "fieldtype": "Currency",
            "options": "reporting_currency",
            "width": 130,
        },
        {"label": _("Voucher Type"), "fieldname": "voucher_type", "width": 120},
        {
            "label": _("Voucher Subtype"),
            "fieldname": "voucher_subtype",
            "fieldtype": "Data",
            "width": 180,
        },
        {
            "label": _("Voucher No"),
            "fieldname": "voucher_no",
            "fieldtype": "Dynamic Link",
            "options": "voucher_type",
            "width": 180,
        },
        {"label": _("Against Account"), "fieldname": "against", "width": 120},
        {"label": _("Party Type"), "fieldname": "party_type", "width": 100},
        {"label": _("Party"), "fieldname": "party", "width": 100},
    ]

    supplier_master_name = frappe.db.get_single_value(
        "Buying Settings", "supp_master_name"
    )
    customer_master_name = frappe.db.get_single_value(
        "Selling Settings", "cust_master_name"
    )
    if (
        supplier_master_name != "Supplier Name"
        or customer_master_name != "Customer Name"
    ):
        columns.append(
            {
                "label": _("Party Name"),
                "fieldname": "party_name",
                "fieldtype": "Data",
                "width": 150,
            }
        )

    columns.extend(
        [
            {
                "label": _("Project"),
                "options": "Project",
                "fieldname": "project",
                "width": 100,
            },
            {
                "label": _("Cost Center"),
                "options": "Cost Center",
                "fieldname": "cost_center",
                "width": 100,
            },
        ]
    )

    columns.extend(
        [
            {
                "label": _("Against Voucher Type"),
                "fieldname": "against_voucher_type",
                "width": 100,
            },
            {
                "label": _("Against Voucher"),
                "fieldname": "against_voucher",
                "fieldtype": "Dynamic Link",
                "options": "against_voucher_type",
                "width": 100,
            },
        ]
    )

    if filters.get("show_remarks"):
        columns.extend([{"label": _("Remarks"), "fieldname": "remarks", "width": 400}])

    return columns
