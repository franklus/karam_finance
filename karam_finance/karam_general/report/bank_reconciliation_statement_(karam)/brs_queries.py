"""Query Builder accessors for the Karam bank reconciliation report."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import frappe
from erpnext.accounts.utils import get_balance_on as _upstream_get_balance_on
from erpnext.accounts.utils import get_currency_precision
from frappe.query_builder import Case
from frappe.query_builder.custom import ConstantColumn
from frappe.query_builder.functions import Coalesce, Round, Sum
from frappe.utils import flt
from pypika.analytics import RowNumber
from pypika.terms import Field, NullValue

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date


_ENTRY_FIELDS = (
    "payment_document",
    "payment_entry",
    "posting_date",
    "debit",
    "credit",
    "against_account",
    "party_type",
    "party",
    "party_name",
    "reference_no",
    "ref_date",
    "clearance_date",
    "account_currency",
)
_UPSTREAM_ENTRIES_HOOK = (
    "erpnext.accounts.report.bank_reconciliation_statement."
    "bank_reconciliation_statement.get_entries_for_bank_reconciliation_statement"
)
_UPSTREAM_AMOUNT_HOOK = (
    "erpnext.accounts.report.bank_reconciliation_statement."
    "bank_reconciliation_statement."
    "get_amounts_not_reflected_in_system_for_bank_reconciliation_statement"
)


def _coerce_filters(filters: dict[str, Any]) -> frappe._dict[str, Any]:
    return (
        cast(frappe._dict[str, Any], filters)
        if hasattr(filters, "account")
        else frappe._dict(filters or {})
    )


def _clearance_is_after(field: Any, report_date: str | date | None) -> Any:
    return field.isnull() | (field > report_date)


def _clearance_is_on_or_before(field: Any, report_date: str | date | None) -> Any:
    return field.notnull() & (field <= report_date)


def _entry_projection(*fields: Any) -> list[Any]:
    """Return the stable output projection shared by all source queries."""
    if len(fields) != len(_ENTRY_FIELDS):
        message = "BRS source query must provide every output field"
        raise ValueError(message)
    return [field.as_(name) for field, name in zip(fields, _ENTRY_FIELDS, strict=True)]


def _journal_entry_party_first() -> Any:
    """Build a one-row-per-Journal-Entry party relation for the JE source."""
    party = frappe.qb.DocType("Journal Entry Account")

    return (
        frappe.qb.from_(party)
        .select(
            party.parent,
            party.party_type,
            party.party,
            RowNumber().over(party.parent).orderby(party.idx).as_("party_row_number"),
        )
        .where(party.party.notnull() & (party.party != ""))
        .as_("journal_entry_party")
    )


def _journal_entry_query(filters: frappe._dict[str, Any], *, outstanding: bool) -> Any:
    je = frappe.qb.DocType("Journal Entry")
    jea = frappe.qb.DocType("Journal Entry Account")

    conditions = (
        (je.docstatus == 1)
        & (jea.account == filters.account)
        & (je.company == filters.company)
        & ((je.is_opening.isnull()) | (je.is_opening == "No"))
    )
    if outstanding:
        conditions &= (je.posting_date <= filters.report_date) & _clearance_is_after(
            je.clearance_date, filters.report_date
        )
    else:
        conditions &= (
            je.posting_date > filters.report_date
        ) & _clearance_is_on_or_before(je.clearance_date, filters.report_date)

    movement = jea.debit_in_account_currency - jea.credit_in_account_currency
    if outstanding:
        party_first = _journal_entry_party_first()
        return (
            frappe.qb.from_(jea)
            .join(je)
            .on(jea.parent == je.name)
            .left_join(party_first)
            .on((party_first.parent == je.name) & (party_first.party_row_number == 1))
            .select(
                *_entry_projection(
                    ConstantColumn("Journal Entry"),
                    je.name,
                    je.posting_date,
                    jea.debit_in_account_currency,
                    jea.credit_in_account_currency,
                    jea.against_account,
                    party_first.party_type,
                    party_first.party,
                    NullValue(),
                    je.cheque_no,
                    je.cheque_date,
                    je.clearance_date,
                    jea.account_currency,
                )
            )
            .where(conditions)
        )

    return (
        frappe.qb.from_(jea)
        .join(je)
        .on(jea.parent == je.name)
        .select(movement.as_("movement"))
        .where(conditions)
    )


def _payment_entry_query(filters: frappe._dict[str, Any], *, outstanding: bool) -> Any:
    pe = frappe.qb.DocType("Payment Entry")

    conditions = (
        (pe.docstatus == 1)
        & ((pe.paid_from == filters.account) | (pe.paid_to == filters.account))
        & (pe.company == filters.company)
    )
    if outstanding:
        conditions &= (pe.posting_date <= filters.report_date) & _clearance_is_after(
            pe.clearance_date, filters.report_date
        )
    else:
        conditions &= (
            pe.posting_date > filters.report_date
        ) & _clearance_is_on_or_before(pe.clearance_date, filters.report_date)

    received = (
        Case()
        .when(pe.paid_to == filters.account, pe.received_amount_after_tax)
        .else_(0)
    )
    paid = (
        Case().when(pe.paid_from == filters.account, pe.paid_amount_after_tax).else_(0)
    )
    if outstanding:
        return (
            frappe.qb.from_(pe)
            .select(
                *_entry_projection(
                    ConstantColumn("Payment Entry"),
                    pe.name,
                    pe.posting_date,
                    received,
                    paid,
                    Coalesce(
                        pe.party,
                        Case()
                        .when(pe.paid_from == filters.account, pe.paid_to)
                        .else_(pe.paid_from),
                    ),
                    pe.party_type,
                    pe.party,
                    pe.party_name,
                    pe.reference_no,
                    pe.reference_date,
                    pe.clearance_date,
                    Case()
                    .when(pe.paid_to == filters.account, pe.paid_to_account_currency)
                    .else_(pe.paid_from_account_currency),
                )
            )
            .where(conditions)
        )

    return (
        frappe.qb.from_(pe).select((received - paid).as_("movement")).where(conditions)
    )


def _purchase_invoice_query(
    filters: frappe._dict[str, Any], *, outstanding: bool
) -> Any:
    pi = frappe.qb.DocType("Purchase Invoice")
    account = frappe.qb.DocType("Account")

    conditions = (
        (pi.docstatus == 1)
        & (pi.is_paid == 1)
        & (pi.cash_bank_account == filters.account)
        & (pi.company == filters.company)
    )
    if outstanding:
        conditions &= (pi.posting_date <= filters.report_date) & _clearance_is_after(
            pi.clearance_date, filters.report_date
        )
    else:
        conditions &= (
            pi.posting_date > filters.report_date
        ) & _clearance_is_on_or_before(pi.clearance_date, filters.report_date)

    debit = Case().when(pi.paid_amount < 0, -pi.paid_amount).else_(0)
    credit = Case().when(pi.paid_amount > 0, pi.paid_amount).else_(0)
    query = frappe.qb.from_(pi).join(account).on(pi.cash_bank_account == account.name)
    if outstanding:
        return query.select(
            *_entry_projection(
                ConstantColumn("Purchase Invoice"),
                pi.name,
                pi.posting_date,
                debit,
                credit,
                pi.supplier,
                ConstantColumn("Supplier"),
                pi.supplier,
                pi.supplier_name,
                pi.bill_no,
                pi.posting_date,
                pi.clearance_date,
                account.account_currency,
            )
        ).where(conditions)

    return query.select((-pi.paid_amount).as_("movement")).where(conditions)


def _pos_query(filters: frappe._dict[str, Any], *, outstanding: bool) -> Any:
    si = frappe.qb.DocType("Sales Invoice")
    sip = frappe.qb.DocType("Sales Invoice Payment")
    account = frappe.qb.DocType("Account")

    conditions = (
        (sip.account == filters.account)
        & (sip.parent == si.name)
        & (si.docstatus == 1)
        & (si.is_pos == 1)
        & (si.company == filters.company)
    )
    if outstanding:
        conditions &= (si.posting_date <= filters.report_date) & _clearance_is_after(
            sip.clearance_date, filters.report_date
        )
    else:
        conditions &= (
            si.posting_date > filters.report_date
        ) & _clearance_is_on_or_before(sip.clearance_date, filters.report_date)

    query = (
        frappe.qb.from_(sip)
        .join(si)
        .on(sip.parent == si.name)
        .join(account)
        .on(sip.account == account.name)
    )
    if outstanding:
        debit = Case().when(sip.amount > 0, sip.amount).else_(0)
        credit = Case().when(sip.amount < 0, -sip.amount).else_(0)
        return query.select(
            *_entry_projection(
                ConstantColumn("Sales Invoice"),
                si.name,
                si.posting_date,
                debit,
                credit,
                si.debit_to,
                ConstantColumn("Customer"),
                si.customer,
                si.customer_name,
                NullValue(),
                NullValue(),
                sip.clearance_date,
                account.account_currency,
            )
        ).where(conditions)

    return query.select(sip.amount.as_("movement")).where(conditions)


def _union_all(queries: Iterable[Any]) -> Any:
    iterator = iter(queries)
    query = next(iterator)
    for other in iterator:
        query = query.union_all(other)
    return query


def get_entries_for_bank_reconciliation_statement(
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fetch built-in outstanding sources with source-local query plans."""
    filters = _coerce_filters(filters)
    queries = [
        _journal_entry_query(filters, outstanding=True),
        _payment_entry_query(filters, outstanding=True),
        _purchase_invoice_query(filters, outstanding=True),
    ]
    if filters.get("include_pos_transactions"):
        queries.append(_pos_query(filters, outstanding=True))
    entries: list[dict[str, Any]] = []
    for query in queries:
        entries.extend(query.run(as_dict=True))
    return entries


def get_journal_entries(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Compatibility accessor for Journal Entry outstanding rows."""
    filters = _coerce_filters(filters)
    return _journal_entry_query(filters, outstanding=True).run(as_dict=True)


def get_payment_entries(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Compatibility accessor for Payment Entry outstanding rows."""
    filters = _coerce_filters(filters)
    return _payment_entry_query(filters, outstanding=True).run(as_dict=True)


def get_purchase_invoices(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Compatibility accessor for paid Purchase Invoice outstanding rows."""
    filters = _coerce_filters(filters)
    return _purchase_invoice_query(filters, outstanding=True).run(as_dict=True)


def get_pos_entries(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Compatibility accessor for POS parent Sales Invoice rows."""
    filters = _coerce_filters(filters)
    return _pos_query(filters, outstanding=True).run(as_dict=True)


def _get_builtin_incorrect_clearance_query(filters: frappe._dict[str, Any]) -> Any:
    queries = [
        _journal_entry_query(filters, outstanding=False),
        _payment_entry_query(filters, outstanding=False),
        _purchase_invoice_query(filters, outstanding=False),
    ]
    if filters.get("include_pos_transactions"):
        queries.append(_pos_query(filters, outstanding=False))
    return _union_all(queries)


def get_amounts_not_reflected_in_system(filters: dict[str, Any]) -> float:
    """Return signed movements cleared after the report date.

    Receipts and invoice refunds are positive; payments and invoice payments
    are negative.  POS movements are included only when the report option is
    enabled, matching the outstanding section.
    """
    query = _get_builtin_incorrect_clearance_query(_coerce_filters(filters))
    result = (
        frappe.qb.from_(query)
        .select(Sum(query.movement).as_("amount"))
        .run(as_dict=True)
    )
    amount = result[0].get("amount") if result else 0
    return flt(amount)


def get_balance_on(
    account: str, report_date: str | date | None, company: str | None = None
) -> float | None:
    """Return a ledger-account balance with one bounded GL query.

    Bank reconciliation only accepts ledger accounts.  Keep ERPNext's
    behaviour for the compatibility cases where the company is unavailable
    or a group account is supplied, while avoiding its metadata/fiscal-year
    round trips for the normal ledger-account path.
    """
    if not company or frappe.get_cached_value("Account", account, "is_group"):
        return _upstream_get_balance_on(account, report_date, company=company)

    gl_entry = frappe.qb.DocType("GL Entry")
    precision = get_currency_precision()
    balance = (
        Sum(cast(Field, Round(gl_entry.debit_in_account_currency, precision)))
        - Sum(cast(Field, Round(gl_entry.credit_in_account_currency, precision)))
    ).as_("balance")
    result = (
        frappe.qb.from_(gl_entry)
        .select(balance)
        .where(
            (gl_entry.is_cancelled == 0)
            & (gl_entry.posting_date <= report_date)
            & (gl_entry.account == account)
            & (gl_entry.company == company)
        )
        .run(as_dict=True)
    )
    return flt(result[0].get("balance")) if result else 0.0


def extension_hook_names(hook_name: str, built_in_name: str) -> list[str]:
    """Return additive report hooks without replaying ERPNext's base hook."""
    return [
        method_name
        for method_name in frappe.get_hooks(hook_name)
        if method_name != built_in_name
    ]


__all__ = [
    "_ENTRY_FIELDS",
    "_UPSTREAM_AMOUNT_HOOK",
    "_UPSTREAM_ENTRIES_HOOK",
    "extension_hook_names",
    "get_amounts_not_reflected_in_system",
    "get_balance_on",
    "get_entries_for_bank_reconciliation_statement",
    "get_journal_entries",
    "get_payment_entries",
    "get_pos_entries",
    "get_purchase_invoices",
]
