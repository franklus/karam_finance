"""Populate the document-level Karam posting date tokens.

The four Karam date fields are deliberately derived from the current document
only. They are inputs to a user-owned naming pattern; this module never
chooses, rewrites, or allocates a naming series.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

import frappe
from frappe.model.document import Document

if TYPE_CHECKING:
    from frappe.model.meta import Meta


KARAM_DATE_FIELD_NAMES = (
    "karam_posting_date",
    "karam_posting_year",
    "karam_posting_month",
    "karam_posting_day",
)

# This is an explicit business-date contract. Do not fall back to another
# date field: several supported DocTypes intentionally do not expose a
# posting_date field, and a fallback can silently produce a wrong identifier.
_BUSINESS_DATE_FIELDS: dict[str, str] = {
    "Sales Order": "transaction_date",
    "Purchase Order": "transaction_date",
    "Asset Movement": "transaction_date",
    "Period Closing Voucher": "transaction_date",
    "Asset": "purchase_date",
    "Work Order": "planned_start_date",
    "Delivery Note": "posting_date",
    "Exchange Rate Revaluation": "posting_date",
    "Expense Claim": "posting_date",
    "Journal Entry": "posting_date",
    "Landed Cost Voucher": "posting_date",
    "Payment Entry": "posting_date",
    "Payroll Entry": "posting_date",
    "Purchase Invoice": "posting_date",
    "Purchase Receipt": "posting_date",
    "Salary Slip": "posting_date",
    "Sales Invoice": "posting_date",
    "Stock Entry": "posting_date",
    "Stock Reconciliation": "posting_date",
}


def populate_karam_date_fields(doc: Document, method: str | None = None) -> None:
    """Populate all four date tokens before Frappe assigns a document name.

    The function is safe to register on ``before_insert`` and ``before_save``.
    If a supported DocType does not have the complete destination field set,
    it is left untouched; schema installation owns adding those fields.
    Missing or malformed source dates clear the complete token set together so
    that a naming pattern cannot observe a partially refreshed date.
    """

    del method

    if not _has_karam_date_fields(doc.doctype):
        return

    source_field = _BUSINESS_DATE_FIELDS.get(doc.doctype)
    source_value = _get_doc_value(doc, source_field) if source_field else None
    date_string = _normalise_date(source_value)
    if not date_string:
        _clear_karam_date_fields(doc)
        return

    # Build the complete result before assigning any value. This keeps the
    # contract atomic even for document stand-ins used by focused tests.
    values = {
        "karam_posting_date": date_string.replace("-", ""),
        "karam_posting_year": date_string[:4],
        "karam_posting_month": date_string[5:7],
        "karam_posting_day": date_string[8:10],
    }
    _set_doc_values(doc, values)


def _normalise_date(value: object) -> str | None:
    """Return a valid ISO date string for supported Frappe date values."""

    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text:
        return None

    # Frappe normally gives us an ISO date, while import and API paths may
    # provide an ISO datetime string. Keep malformed values non-fatal so the
    # four derived fields are cleared together instead of exposing partial
    # tokens or triggering locale-dependent parsing.
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        return None


def _has_karam_date_fields(doctype: str) -> bool:
    """Return whether a DocType has the complete four-field date contract."""

    meta = frappe.get_meta(doctype)
    return all(_meta_has_field(meta, fieldname) for fieldname in KARAM_DATE_FIELD_NAMES)


def _meta_has_field(meta: Meta, fieldname: str) -> bool:
    """Support both live Frappe Meta and small test doubles."""

    has_field = getattr(meta, "has_field", None)
    if callable(has_field):
        return bool(has_field(fieldname))

    fields = getattr(meta, "fields", ())
    return any(getattr(field, "fieldname", None) == fieldname for field in fields)


def _clear_karam_date_fields(doc: Document) -> None:
    """Clear all derived date fields as one operation."""

    _set_doc_values(doc, dict.fromkeys(KARAM_DATE_FIELD_NAMES, ""))


def _set_doc_values(doc: Document, values: dict[str, str]) -> None:
    """Set fields on a Frappe document or a lightweight test stand-in."""

    setter = getattr(doc, "set", None)
    if callable(setter):
        for fieldname, value in values.items():
            setter(fieldname, value)
        return

    for fieldname, value in values.items():
        setattr(doc, fieldname, value)


def _get_doc_value(doc: Document, fieldname: str | None) -> object:
    """Read a document field without assuming every DocType has it."""

    if not fieldname:
        return None

    getter = getattr(doc, "get", None)
    if callable(getter):
        return getter(fieldname)
    return getattr(doc, fieldname, None)


def _get_source_date(doc: Document) -> date | datetime | str | None:  # noqa: V103 - retained business-date compatibility wrapper.
    """Return the explicitly mapped business date for compatibility callers."""

    value = _get_doc_value(doc, _BUSINESS_DATE_FIELDS.get(doc.doctype))
    return value if isinstance(value, (date, datetime, str)) else None
