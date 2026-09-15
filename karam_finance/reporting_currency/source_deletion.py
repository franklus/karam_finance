"""Remove generated source snapshots without exempting reporting master links."""

from typing import override

import frappe
from erpnext.controllers.accounts_controller import AccountsController
from frappe.model.delete_doc import raise_link_exists_exception
from frappe.model.document import Document

from karam_finance.reporting_currency.ledger_lock import hold_ledger_lock


class ReportingSourceDeletionMixin(AccountsController):  # noqa: V102 - Frappe extend_doctype_class registration.
    """Guard manual GL links before ERPNext's voucher-level SQL ledger deletion."""

    @override
    def on_trash(self) -> None:
        hold_ledger_lock()
        ledger = frappe.qb.DocType("Reporting Currency GLE")
        source = frappe.qb.DocType("GL Entry")
        protected = (
            frappe.qb.from_(ledger)
            .join(source)
            .on(source.name == ledger.gl_entry)
            .select(ledger.name)
            .where(
                (source.voucher_type == self.doctype)
                & (source.voucher_no == self.name)
                & ((ledger.manual_entry != 0) | (ledger.reporting_doe != 0))
            )
            .limit(1)
        ).run(pluck=True)
        if protected:
            raise_link_exists_exception(self, "Reporting Currency GLE", protected[0])
        super().on_trash()


# Frappe invokes these functions by their dotted paths in doc_events.
def allow_generated_voucher_cancellation(  # noqa: V103 - Frappe on_cancel hook.
    doc: Document, _method: str | None = None
) -> None:
    """Limit the cancellation exemption to an accounting voucher's generated copies."""
    if not isinstance(doc, AccountsController):
        return
    hold_ledger_lock()
    ledger = frappe.qb.DocType("Reporting Currency GLE")
    source_link = (
        (ledger.voucher_type == doc.doctype) & (ledger.voucher_no == doc.name)
    ) | (
        (ledger.against_voucher_type == doc.doctype)
        & (ledger.against_voucher == doc.name)
    )
    protected = (
        frappe.qb.from_(ledger)
        .select(ledger.name)
        .where(
            (ledger.docstatus == 1)
            & (
                (
                    source_link
                    & ((ledger.manual_entry != 0) | (ledger.reporting_doe != 0))
                )
                | ((ledger.party_type == doc.doctype) & (ledger.party == doc.name))
            )
        )
        .limit(1)
    ).run(pluck=True)
    if protected:
        raise_link_exists_exception(doc, "Reporting Currency GLE", protected[0])
    doc.set(
        "ignore_linked_doctypes",
        (*doc.get("ignore_linked_doctypes", ()), "Reporting Currency GLE"),
    )


def remove_generated_gl_snapshot(doc: Document, _method: str | None = None) -> None:  # noqa: V103 - Frappe on_trash hook.
    """Participate in the source deletion transaction; never delete manual or DOE rows."""
    hold_ledger_lock()
    frappe.db.delete(
        "Reporting Currency GLE",
        {"gl_entry": doc.name, "manual_entry": 0, "reporting_doe": 0},
    )


def remove_generated_voucher_links(doc: Document, _method: str | None = None) -> None:  # noqa: V103 - Frappe on_trash hook.
    """Clean only accounting-voucher snapshots, leaving other vouchers' amounts intact."""
    if not isinstance(doc, AccountsController):
        return
    hold_ledger_lock()
    ledger = frappe.qb.DocType("Reporting Currency GLE")
    generated = (ledger.manual_entry == 0) & (ledger.reporting_doe == 0)
    (
        frappe.qb.from_(ledger)
        .delete()
        .where(
            generated
            & (ledger.voucher_type == doc.doctype)
            & (ledger.voucher_no == doc.name)
        )
    ).run()
    # An against-voucher link does not own this row's amount or source GL entry.
    (
        frappe.qb.update(ledger)
        .set(ledger.against_voucher, None)
        .set(ledger.against_voucher_type, None)
        .where(
            generated
            & (ledger.against_voucher_type == doc.doctype)
            & (ledger.against_voucher == doc.name)
        )
    ).run()
