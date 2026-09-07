"""Currency-layer contract for reports sourced from Reporting Currency GLE."""

from typing import Any

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.utils import cint

DOCTYPE = "Reporting Currency GLE"
AMOUNT_FIELDS = {
    "debit": "reporting_debit",
    "credit": "reporting_credit",
    "debit_in_account_currency": "debit_amount_in_account_currency",
    "credit_in_account_currency": "credit_amount_in_account_currency",
    "debit_in_company_currency": "debit",
    "credit_in_company_currency": "credit",
    "debit_in_transaction_currency": "debit_amount_in_transaction_currency",
    "credit_in_transaction_currency": "credit_in_transaction_currency",
}


def amount(table: Any, field: str) -> Any:
    source = table[AMOUNT_FIELDS[field]]
    if field in ("debit", "credit"):
        return source
    # DOE and manual reporting adjustments do not represent source-ledger movement.
    return (
        Case()
        .when((table.reporting_doe == 1) | (table.manual_entry == 1), 0)
        .else_(source)
    )


def prepare_filters(filters: Any) -> Any:
    filters = frappe._dict(filters or {})
    currency = frappe.db.get_single_value(
        "Reporting Currency Settings", "reporting_currency"
    )
    if not currency:
        frappe.throw(_("Configure Reporting Currency before running this report."))
    requested = filters.get("presentation_currency")
    if requested and requested != currency:
        frappe.throw(
            _("This report uses the stored reporting currency: {0}").format(currency)
        )
    ledger = frappe.qb.DocType(DOCTYPE)
    inconsistent = (
        frappe.qb.from_(ledger)
        .select(ledger.name)
        .where(ledger.company == filters.get("company"))
        .where(
            (ledger.is_cancelled == 0) | (ledger.is_cancelled == 1)
            if cint(filters.get("show_cancelled_entries"))
            else ledger.is_cancelled == 0
        )
        .where((ledger.reporting_debit != 0) | (ledger.reporting_credit != 0))
        .where(
            ledger.reporting_currency.isnull() | (ledger.reporting_currency != currency)
        )
        .limit(1)
        .run()
    )
    if inconsistent:
        frappe.throw(
            _(
                "Reporting entries contain a missing or different reporting currency. Verify the affected entries and their amounts; a sync does not correct manual entries."
            )
        )
    filters["presentation_currency"] = currency
    return filters
