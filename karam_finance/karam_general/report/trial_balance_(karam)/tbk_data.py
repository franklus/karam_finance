"""Data loading for the v16-compatible Karam Trial Balance report."""

from __future__ import annotations

from typing import Any

import erpnext
import frappe
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
    get_accounting_dimensions,
)
from erpnext.accounts.report.financial_statements import filter_accounts
from erpnext.accounts.report.utils import convert_to_presentation_currency, get_currency
from frappe.query_builder.functions import Sum
from frappe.utils import add_days, flt, getdate

from karam_finance.common.ledger_permissions import (
    apply_gl_permissions,
    has_gl_restrictions,
)

from .tbk_aggregation import (
    accumulate_values_into_parents,
    apply_balances_to_accounts,
    finalize_account_currency_values,
)
from .tbk_conditions import apply_gl_filters
from .tbk_query import get_period_balances
from .tbk_rows import filter_out_zero_value_rows, prepare_data

ACCOUNT_FIELDS = (
    "name",
    "account_number",
    "parent_account",
    "account_name",
    "root_type",
    "report_type",
    "account_type",
    "lft",
    "rgt",
    "is_group",
    "account_currency",
)


def get_data(filters: Any) -> Any:
    accounts = _get_accounts(filters.company)
    if not accounts:
        return None

    company_currency = filters.presentation_currency or erpnext.get_company_currency(
        filters.company
    )
    ignore_is_opening = frappe.db.get_single_value(
        "Accounts Settings", "ignore_is_opening_check_for_reporting"
    )
    finance_books = bool(frappe.db.count("Finance Book"))
    accounting_dimensions = get_accounting_dimensions(as_list=False)

    accounts, accounts_by_name, parent_children_map = filter_accounts(accounts)
    opening_balances = _get_opening_balances(
        filters,
        ignore_is_opening,
        finance_books=finance_books,
        accounting_dimensions=accounting_dimensions,
    )
    period_balances = get_period_balances(
        filters,
        ignore_is_opening,
        finance_books=finance_books,
        accounting_dimensions=accounting_dimensions,
    )
    apply_balances_to_accounts(accounts, opening_balances, period_balances)
    accumulate_values_into_parents(accounts, accounts_by_name)
    # Netting is performed once while rows are prepared, after parent values
    # have been accumulated.
    finalize_account_currency_values(accounts)

    data = prepare_data(
        accounts, filters, parent_children_map, company_currency=company_currency
    )
    return filter_out_zero_value_rows(
        data, parent_children_map, show_zero_values=filters.get("show_zero_values")
    )


def _get_accounts(company: Any) -> Any:
    account = frappe.qb.DocType("Account")
    query = (
        frappe.qb.from_(account)
        .select(*(getattr(account, field) for field in ACCOUNT_FIELDS))
        .where(account.company == company)
        .orderby(account.lft)
    )
    return query.run(as_dict=True)


def _get_opening_balances(
    filters: Any,
    ignore_is_opening: Any,
    *,
    finance_books: Any = None,
    accounting_dimensions: Any = None,
) -> Any:
    opening_entries: list[Any] = []
    ignore_closing_balances = frappe.db.get_single_value(
        "Accounts Settings", "ignore_account_closing_balance"
    )
    last_period_closing_voucher = None

    if not ignore_closing_balances and not has_gl_restrictions():
        last_period_closing_voucher = frappe.db.get_all(
            "Period Closing Voucher",
            filters={
                "docstatus": 1,
                "company": filters.company,
                "period_end_date": ("<", filters.from_date),
            },
            fields=["period_end_date", "name"],
            order_by="period_end_date desc",
            limit=1,
        )

    if last_period_closing_voucher:
        period_end_date = getdate(last_period_closing_voucher[0].period_end_date)
        comparison_date = getdate(add_days(filters.from_date, -1))
        opening_entries.extend(
            _get_account_closing_currency_rows(
                filters,
                last_period_closing_voucher[0].name,
                finance_books=finance_books,
                accounting_dimensions=accounting_dimensions,
            )
        )
        if period_end_date and comparison_date and period_end_date < comparison_date:
            start_date = add_days(period_end_date, 1)
            opening_entries.extend(
                _get_gl_opening_currency_rows(
                    filters,
                    ignore_is_opening,
                    start_date=start_date,
                    finance_books=finance_books,
                    accounting_dimensions=accounting_dimensions,
                )
            )
    else:
        opening_entries = _get_gl_opening_currency_rows(
            filters,
            ignore_is_opening,
            finance_books=finance_books,
            accounting_dimensions=accounting_dimensions,
        )

    return _aggregate_opening_entries(opening_entries, filters)


def _aggregate_opening_entries(opening_entries: Any, filters: Any) -> Any:
    if filters.get("presentation_currency"):
        entries_by_report_type = {}
        for entry in opening_entries:
            entries_by_report_type.setdefault(entry.report_type, []).append(entry)
        for entries in entries_by_report_type.values():
            convert_to_presentation_currency(entries, get_currency(filters), filters)

    opening_map = {}
    for entry in opening_entries:
        account_data = opening_map.setdefault(
            entry.account,
            {
                "account": entry.account,
                "account_currencies": set(),
                "opening_debit": 0.0,
                "opening_credit": 0.0,
                "opening_debit_in_account_currency": 0.0,
                "opening_credit_in_account_currency": 0.0,
            },
        )
        if entry.get("account_currency") and (
            flt(entry.debit_in_account_currency) != 0
            or flt(entry.credit_in_account_currency) != 0
        ):
            account_data["account_currencies"].add(entry.account_currency)
        account_data["opening_debit"] += flt(entry.debit)
        account_data["opening_credit"] += flt(entry.credit)
        account_data["opening_debit_in_account_currency"] += flt(
            entry.debit_in_account_currency
        )
        account_data["opening_credit_in_account_currency"] += flt(
            entry.credit_in_account_currency
        )

    return opening_map


def _get_account_closing_currency_rows(
    filters: Any,
    period_closing_voucher: Any,
    *,
    finance_books: Any = None,
    accounting_dimensions: Any = None,
) -> Any:
    closing_balance = frappe.qb.DocType("Account Closing Balance")
    account = frappe.qb.DocType("Account")
    query = (
        frappe.qb.from_(closing_balance)
        .join(account)
        .on(closing_balance.account == account.name)
        .select(
            closing_balance.account,
            closing_balance.account_currency,
            account.report_type,
            Sum(closing_balance.debit).as_("debit"),
            Sum(closing_balance.credit).as_("credit"),
            Sum(closing_balance.debit_in_account_currency).as_(
                "debit_in_account_currency"
            ),
            Sum(closing_balance.credit_in_account_currency).as_(
                "credit_in_account_currency"
            ),
        )
        .where(closing_balance.company == filters.company)
        .where(closing_balance.period_closing_voucher == period_closing_voucher)
        .where(account.report_type.isin(["Balance Sheet", "Profit and Loss"]))
    )
    if not flt(filters.get("with_period_closing_entry_for_opening")):
        query = query.where(closing_balance.is_period_closing_voucher_entry == 0)
    query = apply_gl_filters(
        query,
        closing_balance,
        filters,
        finance_books=finance_books,
        accounting_dimensions=accounting_dimensions,
    )
    return query.groupby(
        closing_balance.account,
        closing_balance.account_currency,
        account.report_type,
    ).run(as_dict=True)


def _get_gl_opening_currency_rows(
    filters: Any,
    ignore_is_opening: Any,
    start_date: Any = None,
    *,
    finance_books: Any = None,
    accounting_dimensions: Any = None,
) -> Any:
    gl_entry = frappe.qb.DocType("GL Entry")
    account = frappe.qb.DocType("Account")
    query = (
        frappe.qb.from_(gl_entry)
        .join(account)
        .on(gl_entry.account == account.name)
        .select(
            gl_entry.account,
            gl_entry.account_currency,
            account.report_type,
            Sum(gl_entry.debit).as_("debit"),
            Sum(gl_entry.credit).as_("credit"),
            Sum(gl_entry.debit_in_account_currency).as_("debit_in_account_currency"),
            Sum(gl_entry.credit_in_account_currency).as_("credit_in_account_currency"),
        )
        .where(gl_entry.company == filters.company)
        .where(gl_entry.is_cancelled == 0)
    )

    if start_date:
        query = query.where(gl_entry.posting_date >= start_date).where(
            gl_entry.posting_date < filters.from_date
        )
        if not ignore_is_opening:
            query = query.where(gl_entry.is_opening == "No")
    elif ignore_is_opening:
        query = query.where(gl_entry.posting_date < filters.from_date)
    else:
        query = query.where(
            (gl_entry.posting_date < filters.from_date) | (gl_entry.is_opening == "Yes")
        )

    if not filters.get("show_unclosed_fy_pl_balances") and filters.get(
        "year_start_date"
    ):
        query = query.where(
            (account.report_type == "Balance Sheet")
            | (
                (account.report_type == "Profit and Loss")
                & (gl_entry.posting_date >= filters.year_start_date)
            )
        )
    else:
        query = query.where(
            account.report_type.isin(["Balance Sheet", "Profit and Loss"])
        )

    if not flt(filters.get("with_period_closing_entry_for_opening")):
        query = query.where(gl_entry.voucher_type != "Period Closing Voucher")

    query = apply_gl_permissions(query, gl_entry)
    query = apply_gl_filters(
        query,
        gl_entry,
        filters,
        finance_books=finance_books,
        accounting_dimensions=accounting_dimensions,
    )
    return query.groupby(
        gl_entry.account,
        gl_entry.account_currency,
        account.report_type,
    ).run(as_dict=True)
