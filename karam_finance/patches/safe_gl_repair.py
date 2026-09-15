"""Conservative, transaction-scoped replacement of provable merged GL postings."""

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast

import frappe
from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry
from erpnext.accounts.general_ledger import get_merge_properties, process_gl_map
from frappe import _
from frappe.query_builder.functions import Coalesce

from karam_finance.common.db_schema import DECIMAL_PRECISION
from karam_finance.letter_reconciliation.doctype.letter_reconciliation_settings.historical_gl_rebuild import (
    rebuild_single_voucher,
)
from karam_finance.reporting_currency.ledger_lock import hold_ledger_lock

_AMOUNTS = (
    "debit",
    "credit",
    "debit_in_account_currency",
    "credit_in_account_currency",
    "debit_in_transaction_currency",
    "credit_in_transaction_currency",
)
# Karam's owned source-ledger storage contract is DECIMAL(30,4).
_QUANTUM = Decimal(1).scaleb(-DECIMAL_PRECISION)


def repair_merged_vouchers(voucher_nos: list[str] | None = None) -> None:
    frappe.only_for("System Manager")
    if voucher_nos is not None and (
        not isinstance(voucher_nos, list)
        or any(not isinstance(name, str) or not name for name in voucher_nos)
    ):
        frappe.throw(_("Supply a list of Journal Entry names."))
    hold_ledger_lock()
    savepoint = "legacy_gl_repair_" + frappe.generate_hash(length=10)
    frappe.db.savepoint(savepoint)
    try:
        for name in _candidates(voucher_nos):
            _repair_voucher(name)
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise


def _candidates(voucher_nos: list[str] | None) -> list[str]:
    gl = frappe.qb.DocType("GL Entry")
    query = (
        frappe.qb.from_(gl)
        .select(gl.voucher_no)
        .distinct()
        .where(
            (gl.voucher_type == "Journal Entry")
            & (gl.is_cancelled == 0)
            & (Coalesce(gl.voucher_detail_no, "") == "")
        )
    )
    if voucher_nos is not None:
        if not voucher_nos:
            return []
        query = query.where(gl.voucher_no.isin(voucher_nos))
    return query.orderby(gl.voucher_no).run(pluck=True)


def _rows(voucher_no: str) -> list[Any]:
    gl = frappe.qb.DocType("GL Entry")
    return (
        frappe.qb.from_(gl)
        .select("*")
        .where(
            (gl.voucher_type == "Journal Entry")
            & (gl.voucher_no == voucher_no)
            & (gl.is_cancelled == 0)
        )
        .for_update()
        .run(as_dict=True)
    )


def _repair_voucher(voucher_no: str) -> None:
    doc = cast(
        JournalEntry, frappe.get_doc("Journal Entry", voucher_no, for_update=True)
    )
    doc.check_permission("write")
    if doc.docstatus != 1:
        frappe.throw(_("Only submitted Journal Entries can be repaired."))
    original = _rows(voucher_no)
    _check_reporting_references(original)
    for child in doc.get("accounts"):
        if child.reference_detail_no and child.reference_detail_no != child.name:
            frappe.throw(
                _("Review the Journal Entry's existing row references before repair.")
            )
        child.reference_detail_no = child.name
    planned = process_gl_map(doc.build_gl_map())
    # Native repost omits disabled dimensions. Include them in the comparison
    # so it cannot silently discard historical values still stored on the GL.
    dimension = frappe.qb.DocType("Accounting Dimension")
    dimensions = frappe.qb.from_(dimension).select(dimension.fieldname).run(pluck=True)
    identity = list(
        dict.fromkeys(
            [
                *get_merge_properties(dimensions),
                "company",
                "posting_date",
                "account_currency",
                "transaction_currency",
                "is_opening",
            ]
        )
    )
    identity.remove("voucher_detail_no")
    _assert_equivalent(original, planned, identity, voucher_no=voucher_no)
    rebuild_single_voucher(voucher_no)
    _assert_equivalent(
        _rows(voucher_no),
        planned,
        [*identity, "voucher_detail_no"],
        voucher_no=voucher_no,
    )


def _check_reporting_references(original: list[Any]) -> None:
    # An aggregate source row has no unique replacement for a manual/DOE link.
    rc = frappe.qb.DocType("Reporting Currency GLE")
    protected = (
        frappe.qb.from_(rc)
        .select(rc.name)
        .where(
            rc.gl_entry.isin([row.name for row in original])
            & ((rc.manual_entry == 1) | (rc.reporting_doe == 1))
        )
        .limit(1)
        .run(pluck=True)
    )
    if protected:
        frappe.throw(
            _(
                "Review reporting entry {0}'s source link before repairing this voucher."
            ).format(protected[0]),
            frappe.LinkExistsError,
        )


def _assert_equivalent(
    before: list[Any], after: list[Any], identity: list[str], *, voucher_no: str
) -> None:
    if _totals(before, identity) != _totals(after, identity):
        frappe.throw(
            _(
                "Journal Entry {0}: posting dimensions or currency amounts do not match; repair was rolled back."
            ).format(voucher_no)
        )


def _totals(
    rows: list[Any], identity: list[str]
) -> dict[tuple[str, ...], tuple[Decimal, ...]]:
    totals = defaultdict(lambda: [Decimal(0) for _ in _AMOUNTS])
    for row in rows:
        key = tuple(
            str(row.get(field) or ("No" if field == "is_opening" else ""))
            for field in identity
        )
        for index, field in enumerate(_AMOUNTS):
            amount = Decimal(str(row.get(field) or 0)).quantize(
                _QUANTUM, rounding=ROUND_HALF_UP
            )
            totals[key][index] += amount
    return {key: tuple(amounts) for key, amounts in totals.items() if any(amounts)}
