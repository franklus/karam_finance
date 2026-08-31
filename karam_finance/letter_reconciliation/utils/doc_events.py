"""Doc events for Letter Reconciliation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypedDict

import frappe

if TYPE_CHECKING:
    from collections.abc import Iterable


class _JournalEntryAccountRowDict(TypedDict):
    name: str
    account: str
    letter: str | None
    idx: int


class _GLEntryRow(TypedDict):
    name: str
    account: str
    letter: str | None
    voucher_detail_no: str | None


_LETTER_CACHE_KEY = "_letter_reconciliation_account_cache"


def _is_merge_prevention_enabled() -> bool:
    """Return True if the GL merge prevention toggle is on in settings."""
    try:
        return bool(
            frappe.db.get_single_value(
                "Letter Reconciliation Settings", "prevent_gl_merge"
            )
        )
    except Exception:
        return False


class _GLInsertDoc(Protocol):
    voucher_type: str
    voucher_no: str
    account: str
    letter: str


class _JournalEntryAccountRow(Protocol):
    account: str
    name: str
    letter: str | None
    reference_detail_no: str | None


class _JournalEntryDoc(Protocol):
    name: str
    accounts: Iterable[_JournalEntryAccountRow]


def gl_entry_before_insert(doc: _GLInsertDoc, _method: object = None) -> None:
    """Copy letter from Journal Entry Account line onto GL Entry at creation.

    Uses voucher_detail_no (= JE Account row name) for exact row-level lookup.
    Falls back to account-level matching for legacy entries without
    voucher_detail_no.
    """
    if not _is_merge_prevention_enabled():
        return
    if getattr(doc, "voucher_type", None) != "Journal Entry":
        return
    if not getattr(doc, "voucher_no", None) or not getattr(doc, "account", None):
        return

    try:
        cache: dict[str, dict[str, str]] | None = getattr(
            frappe.local, _LETTER_CACHE_KEY, None
        )
        if cache is None:
            cache = {}
            setattr(frappe.local, _LETTER_CACHE_KEY, cache)

        letter_map: dict[str, str] | None = cache.get(doc.voucher_no)
        if letter_map is None:
            rows: list[_JournalEntryAccountRowDict] = frappe.get_all(
                "Journal Entry Account",
                filters={"parent": doc.voucher_no},
                fields=["name", "account", "letter", "idx"],
                order_by="idx asc",
                limit=0,
            )
            letter_map = {}
            for row in rows:
                row_name = row.get("name")
                if row_name:
                    letter_map[row_name] = row.get("letter") or ""

            cache[doc.voucher_no] = letter_map

        # Match by voucher_detail_no (JE Account row name)
        detail_no = getattr(doc, "voucher_detail_no", None) or ""
        if detail_no and detail_no in letter_map:
            doc.letter = letter_map[detail_no]
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "gl_entry_before_insert error (letter sync)",
        )


def je_before_submit(doc: _JournalEntryDoc, _method: object = None) -> None:
    """Populate reference_detail_no on each JE Account row to prevent GL merging.

    ERPNext's merge_similar_entries() uses voucher_detail_no (sourced from
    reference_detail_no) as part of the merge key.  When this field is empty,
    rows sharing the same account+party collapse into a single GL Entry —
    losing per-row letter distinctions.  Setting it to the child row's name
    gives each row a unique key, preventing the merge.
    """
    if not _is_merge_prevention_enabled():
        return
    for row in doc.accounts:
        if not row.reference_detail_no:
            row.reference_detail_no = row.name


def journal_entry_on_update_after_submit(
    doc: _JournalEntryDoc,
    _method: object = None,
) -> None:
    """Keep GL Entry.letter in sync after Journal Entry updates.

    Matches GL entries to JE Account rows via voucher_detail_no for
    row-level precision.
    """
    if not _is_merge_prevention_enabled():
        return

    try:
        sync_journal_entry_gl_letters(doc)
    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "journal_entry_on_update_after_submit error (letter sync)",
        )


def sync_journal_entry_gl_letters(
    doc: _JournalEntryDoc,
    *,
    ignore_setting: bool = False,
) -> None:
    """Synchronise GL Entry letters from Journal Entry rows."""
    if not ignore_setting and not _is_merge_prevention_enabled():
        return
    if not getattr(doc, "name", None):
        return

    gl_entries: list[_GLEntryRow] = frappe.get_all(
        "GL Entry",
        filters={"voucher_type": "Journal Entry", "voucher_no": doc.name},
        fields=["name", "voucher_detail_no", "letter"],
        limit=0,
    )
    if not gl_entries:
        return

    jea_letter_map: dict[str, str] = {}
    for row in doc.accounts:
        jea_letter_map[row.name] = row.letter or ""

    updates: dict[str, dict[str, str]] = {}
    for ge in gl_entries:
        detail_no = ge.get("voucher_detail_no") or ""
        if not detail_no or detail_no not in jea_letter_map:
            continue
        desired = jea_letter_map[detail_no]
        if (ge.get("letter") or "") != desired:
            updates[ge["name"]] = {"letter": desired}

    if updates:
        frappe.db.bulk_update("GL Entry", updates)

    cache: dict[str, dict[str, str]] | None = getattr(
        frappe.local, _LETTER_CACHE_KEY, None
    )
    if cache and doc.name in cache:
        cache.pop(doc.name, None)
