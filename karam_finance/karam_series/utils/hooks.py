"""Document lifecycle hooks for Karam Series values.

The hooks keep the document-level Series/Translation pair canonical and
provide explicit adapters for the two ERPNext workflows that create Journal
Entries automatically.  They do not define naming patterns or guess a source
document from arbitrary references.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import frappe
from frappe import _

from karam_finance.karam_series.constants.constants import KARAM_DOCTYPES

if TYPE_CHECKING:
    from frappe.model.document import Document


_ERR_JOURNAL_VOUCHER_TYPES = frozenset(
    {
        "Exchange Gain Or Loss",
        "Exchange Rate Revaluation",
    }
)


def populate_karam_series_fields(
    doc: Document,
    _method: str | None = None,
) -> None:
    """Resolve canonical Translation from the document's selected Series.

    This runs for manually selected values and for programmatically inherited
    values.  Clearing Series also clears Translation, preventing stale text
    from surviving a change to the paired Link field.
    """

    del _method

    if not _document_has_field(doc, "karam_series"):
        return

    karam_series = _get_document_value(doc, "karam_series")
    if not karam_series:
        _set_document_value(doc, "translation", "")
        return

    translation = frappe.db.get_value("Karam Series", karam_series, "translation")
    _set_document_value(doc, "translation", translation or "")


def validate_karam_series_applicability(
    doc: Document,
    _method: str | None = None,
) -> None:
    """Ensure the selected Series is configured for the current DocType."""

    del _method

    if doc.doctype not in KARAM_DOCTYPES:
        return

    karam_series = _get_document_value(doc, "karam_series")
    if not karam_series:
        return

    applicability_field = _applicability_field(doc.doctype)
    if not applicability_field:
        return

    applicable = frappe.db.get_value(
        "Karam Series",
        karam_series,
        applicability_field,
    )
    if applicable is None:
        frappe.throw(_("Karam Series '{0}' does not exist").format(karam_series))
    if not applicable:
        frappe.throw(
            _("Karam Series '{0}' is not applicable for {1}").format(
                karam_series,
                doc.doctype,
            )
        )


def populate_karam_series_from_source_document(
    doc: Document,
    _method: str | None = None,
) -> None:
    """Enrich an automatically created Journal Entry before it is named.

    Supported adapters are deliberately narrow:

    * both ERPNext Exchange Rate Revaluation Journal Entry voucher types use
      exactly one distinct Exchange Rate Revaluation reference;
    * depreciation Journal Entries use exactly one distinct Asset reference.

    A source without Series remains blank.  Frappe's ordinary mandatory
    validation then decides whether the target document may be saved/submitted.
    Every generated child runs this adapter independently, so one source can
    produce multiple children without shared mutable state.
    """

    del _method

    if not _document_has_field(doc, "karam_series"):
        return

    # A mapped draft or user-created JE may already contain a deliberate
    # selection. Preserve it and only refresh its canonical Translation.
    if _get_document_value(doc, "karam_series"):
        populate_karam_series_fields(doc)
        return

    if _populate_depreciation_series(doc):
        return

    _populate_exchange_rate_revaluation_series(doc)


def _populate_depreciation_series(doc: Document) -> bool:
    """Copy Series from the one Asset referenced by a depreciation JE."""

    if _get_document_value(doc, "voucher_type") != "Depreciation Entry":
        return False

    asset_name = _get_single_reference_name(doc, "Asset")
    if not asset_name:
        return False

    karam_series = frappe.db.get_value("Asset", asset_name, "karam_series")
    if not karam_series:
        # Leave both fields blank and allow the target's ordinary policy to
        # determine whether the generated document can be saved.
        populate_karam_series_fields(doc)
        return False

    _set_karam_series(doc, karam_series)
    return True


def _populate_exchange_rate_revaluation_series(doc: Document) -> bool:
    """Copy Series from the one ERR referenced by an automatic JE."""

    voucher_type = _get_document_value(doc, "voucher_type")
    if voucher_type not in _ERR_JOURNAL_VOUCHER_TYPES:
        populate_karam_series_fields(doc)
        return False

    err_name = _get_single_reference_name(doc, "Exchange Rate Revaluation")
    if not err_name:
        populate_karam_series_fields(doc)
        return False

    karam_series = frappe.db.get_value(
        "Exchange Rate Revaluation",
        err_name,
        "karam_series",
    )
    if not karam_series:
        populate_karam_series_fields(doc)
        return False

    _set_karam_series(doc, karam_series)
    return True


def _set_karam_series(doc: Document, karam_series: str) -> None:
    """Set Series and resolve Translation from the canonical Series record."""

    _set_document_value(doc, "karam_series", karam_series)
    populate_karam_series_fields(doc)


def _get_single_reference_name(doc: Document, reference_type: str) -> str | None:
    """Return a reference only when all matching rows identify one document."""

    rows = _get_document_value(doc, "accounts")
    if not isinstance(rows, list):
        return None

    reference_names = {
        reference_name
        for row in rows
        if _get_row_value(row, "reference_type") == reference_type
        if (reference_name := _get_row_value(row, "reference_name"))
    }
    if len(reference_names) != 1:
        return None
    return next(iter(reference_names))


def _get_row_value(row: object, fieldname: str) -> str | None:
    """Read a child-row field from Frappe rows and small test stand-ins."""

    value: object | None
    if isinstance(row, Mapping):
        value = row.get(fieldname)
    else:
        getter = getattr(row, "get", None)
        value = getter(fieldname) if callable(getter) else getattr(row, fieldname, None)

    return value if isinstance(value, str) and value else None


def _document_has_field(doc: Document, fieldname: str) -> bool:
    """Check a live document's Meta, with support for lightweight doubles."""

    meta = getattr(doc, "meta", None)
    has_field = getattr(meta, "has_field", None)
    if callable(has_field):
        return bool(has_field(fieldname))
    return hasattr(doc, fieldname)


def _get_document_value(doc: Document, fieldname: str) -> object | None:
    """Read a field through the Frappe API or a test stand-in."""

    getter = getattr(doc, "get", None)
    if callable(getter):
        return getter(fieldname)
    return getattr(doc, fieldname, None)


def _set_document_value(doc: Document, fieldname: str, value: object) -> None:
    """Set a field through the Frappe API or a test stand-in."""

    setter = getattr(doc, "set", None)
    if callable(setter):
        setter(fieldname, value)
    else:
        setattr(doc, fieldname, value)


def _applicability_field(doctype: str) -> str | None:
    """Convert a supported DocType name to its Karam Series flag field."""

    return doctype.replace(" ", "_").lower() if doctype in KARAM_DOCTYPES else None


# Backward-compatible names used by existing callers and focused tests.
_populate_karam_series_from_depreciation_asset = _populate_depreciation_series
_populate_karam_series_from_exchange_rate_revaluation = (
    _populate_exchange_rate_revaluation_series
)


def _set_karam_series_and_translation(
    doc: Document,
    karam_series: str,
    _translation: str | None = None,
) -> None:
    """Compatibility wrapper that still resolves canonical Translation."""

    _set_karam_series(doc, karam_series)
