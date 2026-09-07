"""Column definitions for the GL report."""

from __future__ import annotations

from typing import Any

import frappe
from erpnext import get_company_currency, get_default_company
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
    get_accounting_dimensions,
)
from frappe import _


def get_columns(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Build column definitions for the report grid."""
    company_currency = get_company_currency(
        filters.get("company") or get_default_company()
    )
    currency = filters.get("presentation_currency") or company_currency
    filters["presentation_currency"] = currency

    columns = [
        {
            "label": _("RC Entry"),
            "fieldname": "gl_entry",
            "fieldtype": "Link",
            "options": "Reporting Currency GLE",
            "width": 170,
        },
        {
            "label": _("Posting Date"),
            "fieldname": "posting_date",
            "fieldtype": "Date",
            "width": 100,
        },
        {
            "label": _("Series"),
            "fieldname": "karam_series",
            "fieldtype": "Data",
            "width": 100,
        },
        {
            "label": _("Translation"),
            "fieldname": "translation",
            "fieldtype": "Data",
            "width": 120,
        },
        {
            "label": _("Letter"),
            "fieldname": "letter",
            "fieldtype": "Data",
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
            "label": _("Account Currency"),
            "fieldname": "account_currency",
            "fieldtype": "Link",
            "options": "Currency",
            "width": 80,
        },
        {
            "label": _("Debit (Account Ccy)"),
            "fieldname": "debit_in_account_currency",
            "fieldtype": "Currency",
            "precision": 2,
            "options": "account_currency",
            "width": 130,
        },
        {
            "label": _("Credit (Account Ccy)"),
            "fieldname": "credit_in_account_currency",
            "fieldtype": "Currency",
            "precision": 2,
            "options": "account_currency",
            "width": 130,
        },
        {
            "label": _("Balance (Account Ccy)"),
            "fieldname": "balance_in_account_currency",
            "fieldtype": "Currency",
            "precision": 2,
            "options": "account_currency",
            "width": 130,
        },
        {
            "label": _("Debit (Company Ccy)"),
            "fieldname": "debit_in_company_currency",
            "fieldtype": "Currency",
            "precision": 2,
            "options": "Company:company:default_currency",
            "width": 130,
        },
        {
            "label": _("Credit (Company Ccy)"),
            "fieldname": "credit_in_company_currency",
            "fieldtype": "Currency",
            "precision": 2,
            "options": "Company:company:default_currency",
            "width": 130,
        },
        {
            "label": _("Balance (Company Ccy)"),
            "fieldname": "balance_in_company_currency",
            "fieldtype": "Currency",
            "precision": 2,
            "options": "Company:company:default_currency",
            "width": 130,
        },
    ]

    columns.extend(
        [
            {
                "label": _("Reporting Debit ({0})").format(currency),
                "fieldname": "debit",
                "fieldtype": "Currency",
                "precision": 2,
                "options": "presentation_currency",
                "width": 130,
            },
            {
                "label": _("Reporting Credit ({0})").format(currency),
                "fieldname": "credit",
                "fieldtype": "Currency",
                "precision": 2,
                "options": "presentation_currency",
                "width": 130,
            },
            {
                "label": _("Reporting Balance ({0})").format(currency),
                "fieldname": "balance",
                "fieldtype": "Currency",
                "precision": 2,
                "options": "presentation_currency",
                "width": 130,
            },
        ]
    )

    if filters.get("add_values_in_transaction_currency"):
        columns += [
            {
                "label": _("Debit (Transaction)"),
                "fieldname": "debit_in_transaction_currency",
                "fieldtype": "Currency",
                "precision": 2,
                "width": 130,
                "options": "transaction_currency",
            },
            {
                "label": _("Credit (Transaction)"),
                "fieldname": "credit_in_transaction_currency",
                "fieldtype": "Currency",
                "precision": 2,
                "width": 130,
                "options": "transaction_currency",
            },
            {
                "label": _("Transaction Currency"),
                "fieldname": "transaction_currency",
                "fieldtype": "Link",
                "options": "Currency",
                "width": 70,
            },
        ]

    columns += [
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

    if filters.get("include_dimensions"):
        columns.extend(_get_dimension_columns(filters))

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
            {
                "label": _("Supplier Invoice No"),
                "fieldname": "bill_no",
                "fieldtype": "Data",
                "width": 100,
            },
        ]
    )

    if filters.get("show_remarks"):
        columns.extend([{"label": _("Remarks"), "fieldname": "remarks", "width": 400}])

    return _order_rc_columns(columns, filters)


def _order_rc_columns(
    columns: list[dict[str, Any]], filters: dict[str, Any]
) -> list[dict[str, Any]]:
    columns = _visible_rc_columns(columns, filters)
    primary = {"debit", "credit", "balance"}
    first = {"gl_entry", "posting_date", "account"}
    ordered = [column for column in columns if column.get("fieldname") in first]
    ordered.append(
        {
            "label": _("Entry Type"),
            "fieldname": "entry_type",
            "fieldtype": "Data",
            "width": 120,
        }
    )
    ordered.extend(column for column in columns if column.get("fieldname") in primary)
    ordered.extend(
        column for column in columns if column.get("fieldname") not in first | primary
    )
    if filters.get("show_cancelled_entries"):
        ordered.append(
            {
                "label": _("Cancelled"),
                "fieldname": "is_cancelled",
                "fieldtype": "Check",
                "width": 85,
            }
        )
    if filters.get("show_exchange_details"):
        ordered.extend(
            [
                {
                    "label": _("Source GL Entry"),
                    "fieldname": "source_gl_entry",
                    "fieldtype": "Link",
                    "options": "GL Entry",
                    "width": 170,
                },
                {
                    "label": _("Conversion Basis"),
                    "fieldname": "conversion_basis",
                    "fieldtype": "Data",
                    "width": 250,
                },
                {
                    "label": _("Stored Conversion Rate"),
                    "fieldname": "exchange_rate",
                    "fieldtype": "Float",
                    "precision": 9,
                    "width": 130,
                },
                {
                    "label": _("Rate Date"),
                    "fieldname": "exchange_rate_date",
                    "fieldtype": "Date",
                    "width": 110,
                },
                {
                    "label": _("Currency Exchange"),
                    "fieldname": "currency_exchange",
                    "fieldtype": "Link",
                    "options": "Currency Exchange",
                    "width": 170,
                },
            ]
        )
    return ordered


def _get_dimension_columns(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the optional project, accounting dimension and cost centre columns."""
    columns = []
    columns.append(
        {
            "label": _("Project"),
            "options": "Project",
            "fieldname": "project",
            "width": 100,
        }
    )
    columns.extend(
        {
            "label": _(dim.label),
            "options": dim.label,
            "fieldname": dim.fieldname,
            "width": 100,
        }
        for dim in (
            filters.get("_dimensions_meta") or get_accounting_dimensions(as_list=False)
        )
        if frappe.get_meta("Reporting Currency GLE").has_field(dim.fieldname)
    )
    columns.append(
        {
            "label": _("Cost Center"),
            "options": "Cost Center",
            "fieldname": "cost_center",
            "width": 100,
        }
    )
    return [column for column in columns if column.get("fieldname") != "letter"]


def _visible_rc_columns(
    columns: list[dict[str, Any]], filters: dict[str, Any]
) -> list[dict[str, Any]]:
    columns = [column for column in columns if column.get("fieldname") != "letter"]
    source = {
        "account_currency",
        "debit_in_account_currency",
        "credit_in_account_currency",
        "balance_in_account_currency",
        "debit_in_company_currency",
        "credit_in_company_currency",
        "balance_in_company_currency",
    }
    if not filters.get("show_source_currency_columns"):
        columns = [
            column for column in columns if column.get("fieldname") not in source
        ]
    return columns
