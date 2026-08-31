# N999: Frappe report naming convention (parentheses)

from __future__ import annotations

from importlib import import_module

import frappe
from frappe import _
from frappe.utils import flt

brs_aggregation = import_module(f"{__package__}.brs_aggregation")
brs_columns = import_module(f"{__package__}.brs_columns")
brs_enrichment = import_module(f"{__package__}.brs_enrichment")
brs_queries = import_module(f"{__package__}.brs_queries")
brs_rows = import_module(f"{__package__}.brs_rows")


def execute(filters=None):
    filters = frappe._dict(filters or {})

    columns = get_columns()

    account = filters.get("account")
    if not account:
        return columns, []

    account_currency = frappe.get_cached_value("Account", account, "account_currency")

    data = get_entries(filters)

    balance_as_per_system = get_balance_on(
        account, filters["report_date"], filters.get("company")
    )

    total_debit, total_credit = 0, 0
    for d in data:
        total_debit += flt(d.debit)
        total_credit += flt(d.credit)

    amounts_not_reflected_in_system = get_amounts_not_reflected_in_system(filters)

    bank_bal = (
        flt(balance_as_per_system)
        - flt(total_debit)
        + flt(total_credit)
        + amounts_not_reflected_in_system
    )

    data += [
        get_balance_row(
            _("Bank Statement balance as per General Ledger"),
            balance_as_per_system,
            account_currency,
        ),
        {},
        {
            "payment_entry": _("Outstanding Cheques and Deposits to clear"),
            "debit": total_debit,
            "credit": total_credit,
            "account_currency": account_currency,
        },
        get_balance_row(
            _("Cheques and Deposits incorrectly cleared"),
            amounts_not_reflected_in_system,
            account_currency,
        ),
        {},
        get_balance_row(
            _("Calculated Bank Statement balance"), bank_bal, account_currency
        ),
    ]

    return columns, data


def get_columns():
    return brs_columns.get_columns()


def get_entries(filters):
    return brs_aggregation.get_entries(filters)


def get_balance_on(account, report_date, company=None):
    return brs_queries.get_balance_on(account, report_date, company)


def get_entries_for_bank_reconciliation_statement(filters):
    return brs_queries.get_entries_for_bank_reconciliation_statement(filters)


def get_journal_entries(filters):
    return brs_queries.get_journal_entries(filters)


def get_payment_entries(filters):
    return brs_queries.get_payment_entries(filters)


def get_purchase_invoices(filters):
    return brs_queries.get_purchase_invoices(filters)


def get_pos_entries(filters):
    return brs_queries.get_pos_entries(filters)


def _enrich_je_party(entries):
    return brs_enrichment.enrich_je_party(entries)


def _populate_party_names(entries):
    return brs_enrichment.populate_missing_party_names(entries)


def get_amounts_not_reflected_in_system(filters):
    return brs_aggregation.get_amounts_not_reflected_in_system(filters)


def get_balance_row(label, amount, account_currency):
    return brs_rows.get_balance_row(label, amount, account_currency)
