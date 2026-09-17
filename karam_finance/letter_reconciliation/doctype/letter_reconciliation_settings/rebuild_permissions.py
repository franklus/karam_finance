"""Access boundaries for historical journal previews and rebuilds."""

from collections.abc import Generator, Mapping
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, cast

import frappe
from frappe import _
from frappe.utils import create_batch, getdate

if TYPE_CHECKING:
    from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry


def check_rebuild_scope(company: str, *, write: bool = False) -> None:
    """Require access to both shared settings and the selected company."""
    settings = frappe.get_single("Letter Reconciliation Settings")
    settings.check_permission("write" if write else "read")
    frappe.get_doc("Company", company).check_permission("read")


def check_rebuild_journals(
    voucher_names: list[str], *, write: bool = False
) -> list[JournalEntry]:
    """Reject partial visibility, including query hooks and child-row permissions."""
    checked = []
    for batch in create_batch(voucher_names, 200):
        # One permission-filtered query per 200 names, rather than per voucher.
        # nosemgrep: frappe-n-plus-one-read-in-loop
        visible = frappe.get_list(
            "Journal Entry", filters={"name": ["in", batch]}, pluck="name", limit=0
        )
        if set(visible) != set(batch):
            _deny_journal_access()
        checked.extend(_check_journal_document(name, write=write) for name in batch)
    return checked


def _check_journal_document(name: str, *, write: bool) -> JournalEntry:
    # Native document checks include child-account permissions and custom hooks.
    doc = cast("JournalEntry", frappe.get_doc("Journal Entry", name))
    if not doc.has_permission("read") or (write and not doc.has_permission("write")):
        _deny_journal_access()
    return doc


def check_rebuild_voucher(voucher_no: str) -> None:
    """Authorise a direct repost before its row-reference backfill."""
    frappe.only_for(["System Manager", "Accounts Manager"])
    doc = check_rebuild_journals([voucher_no], write=True)[0]
    check_rebuild_scope(doc.company, write=True)
    if doc.docstatus != 1:
        frappe.throw(_("Only submitted Journal Entries can be rebuilt."))
    doc.validate_for_repost()


@contextmanager
def as_rebuild_user(user: str) -> Generator[None]:
    """Execute queued work with the initiator's current access, restoring the caller."""
    if not user or user == "Guest" or not frappe.db.get_value("User", user, "enabled"):
        frappe.throw(
            _("The user who requested this rebuild is no longer enabled."),
            frappe.PermissionError,
        )
    previous_user = frappe.session.user
    # Redis run state records the server-side initiating user; never a request override.
    frappe.set_user(user)  # nosemgrep: frappe-setuser
    try:
        frappe.only_for(["System Manager", "Accounts Manager"])
        yield
    finally:
        # Restore the caller even when permission checks or the rebuild fail.
        frappe.set_user(previous_user)  # nosemgrep: frappe-setuser


def check_rebuild_batch(voucher_names: list[str], scope: Mapping[str, Any]) -> None:
    """Revalidate the queued selection before any batch-wide reference updates."""
    check_rebuild_scope(scope["company"], write=True)
    for doc in check_rebuild_journals(voucher_names, write=True):
        if doc.company != scope["company"] or doc.docstatus != 1:
            frappe.throw(
                _("The queued Journal Entry no longer matches the rebuild scope.")
            )
        _check_queued_dates(doc.posting_date, scope)
        doc.validate_for_repost()


def _check_queued_dates(posting_date: Any, scope: Mapping[str, Any]) -> None:
    if scope["whole_history"]:
        return
    start, end, posted = (
        getdate(scope["from_posting_date"]),
        getdate(scope["to_posting_date"]),
        getdate(posting_date),
    )
    if not (start and end and posted and start <= posted <= end):
        frappe.throw(
            _("The queued Journal Entry is outside the selected posting dates.")
        )


def _deny_journal_access() -> None:
    frappe.throw(
        _(
            "You do not have permission to access every Journal Entry in the selected rebuild scope."
        ),
        frappe.PermissionError,
    )
