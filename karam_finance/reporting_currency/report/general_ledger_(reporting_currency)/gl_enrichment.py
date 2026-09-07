"""Bulk enrichment helpers for General Ledger report rows."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import frappe
from frappe.query_builder import Criterion, NullValue
from frappe.query_builder.terms import ParameterizedValueWrapper as ValueWrapper
from pypika.queries import QueryBuilder, Table
from pypika.terms import Term

from karam_finance.karam_series.constants.constants import KARAM_DOCTYPES

if TYPE_CHECKING:
    from collections.abc import Iterable

PARTY_LOOKUP_BATCH_SIZE = 1000
VOUCHER_LOOKUP_BATCH_SIZE = 1000
_KARAM_FIELDS_CACHE: dict[str, list[str]] = {}


def get_party_name_map(
    gl_entries: list[frappe._dict[str, Any]],
) -> dict[str, dict[str, str]]:
    """Build a party map using one bounded lookup per supported party type."""
    if not gl_entries:
        return {}

    targets: dict[str, set[str]] = {}
    for entry in gl_entries:
        party_type = entry.get("party_type")
        party = entry.get("party")
        if party_type and party:
            targets.setdefault(party_type, set()).add(party)

    party_map: dict[str, dict[str, str]] = {}
    customer_names = targets.get("Customer")
    if customer_names:
        party_map["Customer"] = _fetch_party_names(
            "Customer", "customer_name", customer_names
        )

    supplier_names = targets.get("Supplier")
    if supplier_names:
        party_map["Supplier"] = _fetch_party_names(
            "Supplier", "supplier_name", supplier_names
        )

    employee_names = targets.get("Employee")
    if employee_names:
        party_map["Employee"] = _fetch_party_names(
            "Employee", "employee_name", employee_names
        )

    return party_map


def _fetch_party_names(doctype: str, fieldname: str, names: set[str]) -> dict[str, str]:
    """Fetch only parties present in the report, with an explicit bound."""
    rows = frappe.get_all(
        doctype,
        filters={"name": ["in", sorted(names)]},
        fields=["name", fieldname],
        limit_page_length=max(1, len(names)),
    )
    return {row["name"]: row.get(fieldname) or "" for row in rows if row.get("name")}


def _attach_series_translation(
    gl_entries: list[frappe._dict[str, Any]],
    include_journal_entries: bool = True,
    preloaded_voucher_data: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> None:
    """Populate Karam fields for every supported voucher type in one query."""
    if not gl_entries:
        return

    targets = _collect_voucher_names(
        gl_entries, include_journal_entries=include_journal_entries
    )
    if not targets:
        return

    voucher_data = preloaded_voucher_data
    if voucher_data is None:
        voucher_data = _fetch_voucher_data(targets)

    _apply_voucher_data_to_gl_entries(gl_entries, voucher_data)


def _collect_voucher_names(
    gl_entries: list[frappe._dict[str, Any]], include_journal_entries: bool = True
) -> dict[str, set[str]]:
    """Collect distinct source names without building a per-row index."""
    targets: dict[str, set[str]] = {}
    for entry in gl_entries:
        identity = _voucher_identity(entry, include_journal_entries)
        if identity is None:
            continue
        voucher_type, voucher_no = identity
        targets.setdefault(voucher_type, set()).add(voucher_no)
    return targets


def _collect_voucher_targets(
    gl_entries: list[frappe._dict[str, Any]], include_journal_entries: bool = True
) -> tuple[dict[str, set[str]], dict[tuple[str, str], list[frappe._dict[str, Any]]]]:
    """Collect voucher names and index all rows sharing each source voucher."""
    targets: dict[str, set[str]] = {}
    entry_index: dict[tuple[str, str], list[frappe._dict[str, Any]]] = {}

    for entry in gl_entries:
        identity = _voucher_identity(entry, include_journal_entries)
        if identity is None:
            continue
        voucher_type, voucher_no = identity
        targets.setdefault(voucher_type, set()).add(voucher_no)
        entry_index.setdefault((voucher_type, voucher_no), []).append(entry)

    return targets, entry_index


def _hydrate_entries_from_doctype(
    entry_index: dict[tuple[str, str], list[frappe._dict[str, Any]]],
    doctype: str,
    names: set[str],
) -> None:
    """Compatibility wrapper for callers that hydrate one doctype."""
    voucher_data = _fetch_voucher_data({doctype: names})
    _apply_voucher_data_to_entries(entry_index, voucher_data)


def _get_voucher_data_for_filters(
    filters: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Return source vouchers matching Series/Translation filters.

    Each supported doctype contributes a SELECT to a single UNION query. The
    table names come only from the application-owned allowlist and field names
    are verified through Frappe metadata before they are inserted into the
    Query Builder expression.
    """
    return _fetch_voucher_data(
        names_by_doctype=None,
        series=filters.get("karam_series"),
        translation=filters.get("translation"),
    )


def _fetch_voucher_data(
    names_by_doctype: dict[str, set[str]] | None,
    series: str | None = None,
    translation: str | None = None,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Fetch Karam fields for many doctypes in one UNION query."""
    doctypes = (
        KARAM_DOCTYPES
        if names_by_doctype is None
        else [doctype for doctype in KARAM_DOCTYPES if doctype in names_by_doctype]
    )
    if names_by_doctype is not None and len(doctypes) == 1:
        doctype = doctypes[0]
        fields = _get_karam_fields_for_doctype(doctype)
        names = names_by_doctype.get(doctype) or set()
        if fields and names:
            return _fetch_single_voucher_data(doctype, fields, names)
        return {}

    return _fetch_union_voucher_data(
        doctypes, names_by_doctype, series=series, translation=translation
    )


def _fetch_union_voucher_data(
    doctypes: list[str],
    names_by_doctype: dict[str, set[str]] | None,
    *,
    series: str | None,
    translation: str | None,
) -> dict[tuple[str, str], dict[str, Any]]:
    queries = [
        query
        for doctype in doctypes
        if (
            query := _build_voucher_query(
                doctype,
                names_by_doctype,
                series=series,
                translation=translation,
            )
        )
    ]

    if not queries:
        return {}

    query = queries[0]
    for additional_query in queries[1:]:
        query = query.union_all(additional_query)

    rows = query.run(as_dict=True)
    return _index_voucher_rows(rows)


def _index_voucher_rows(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (row["_doctype"], row["name"]): {
            "name": row["name"],
            "karam_series": row.get("karam_series"),
            "translation": row.get("translation"),
        }
        for row in rows
        if row.get("_doctype") and row.get("name")
    }


def _fetch_single_voucher_data(
    doctype: str, fields: list[str], names: set[str]
) -> dict[tuple[str, str], dict[str, Any]]:
    """Fetch one source doctype without paying UNION construction overhead."""
    source_fields = [
        fieldname
        for fieldname in ("name", "karam_series", "translation")
        if fieldname in fields
    ]
    rows = frappe.get_all(
        doctype,
        filters={"name": ["in", sorted(names)]},
        fields=source_fields,
        limit_page_length=max(1, len(names)),
    )
    return {
        (doctype, row["name"]): {
            "name": row["name"],
            "karam_series": row.get("karam_series"),
            "translation": row.get("translation"),
        }
        for row in rows
        if row.get("name")
    }


def _build_voucher_query(
    doctype: str,
    names_by_doctype: dict[str, set[str]] | None,
    *,
    series: str | None,
    translation: str | None,
) -> QueryBuilder | None:
    """Build one allowlisted source query for the union hydration path."""
    fields = _get_karam_fields_for_doctype(doctype)
    if not _voucher_query_is_supported(
        doctype, fields, names_by_doctype, series=series, translation=translation
    ):
        return None

    table = frappe.qb.DocType(doctype)
    criteria = _voucher_query_criteria(
        table,
        fields,
        names_by_doctype.get(doctype) if names_by_doctype is not None else None,
        series=series,
        translation=translation,
    )

    return (
        frappe.qb.from_(table)
        .select(
            *_voucher_query_projection(table, fields),
            ValueWrapper(doctype).as_("_doctype"),
        )
        .where(Criterion.all(criteria))
    )


def _voucher_query_is_supported(
    doctype: str,
    fields: list[str],
    names_by_doctype: dict[str, set[str]] | None,
    *,
    series: str | None,
    translation: str | None,
) -> bool:
    if not fields:
        return False
    if names_by_doctype is not None:
        names = names_by_doctype.get(doctype)
        if not names:
            return False
    if series and "karam_series" not in fields:
        return False
    return not translation or "translation" in fields


def _voucher_query_criteria(
    table: Table,
    fields: list[str],
    names: set[str] | None,
    *,
    series: str | None,
    translation: str | None,
) -> list[Term]:
    criteria = []
    if names is not None:
        # Let Query Builder bind the voucher names and execute the UNION itself.
        values = sorted(names)
        criteria.append(table.name.isin(values))
    if series:
        criteria.append(table.karam_series == series)
    if translation:
        escaped = translation.strip().replace("\\", "\\\\")
        escaped = escaped.replace("%", "\\%").replace("_", "\\_")
        criteria.append(table.translation.like(f"%{escaped}%"))
    else:
        value_conditions = []
        if "karam_series" in fields:
            value_conditions.append(
                table.karam_series.notnull() & (table.karam_series != "")
            )
        if "translation" in fields:
            value_conditions.append(
                table.translation.notnull() & (table.translation != "")
            )
        if value_conditions:
            criteria.append(Criterion.any(value_conditions))
    return criteria


def _voucher_query_projection(table: Table, fields: list[str]) -> list[Term]:
    return [
        table.name,
        table.karam_series
        if "karam_series" in fields
        else NullValue().as_("karam_series"),
        table.translation
        if "translation" in fields
        else NullValue().as_("translation"),
    ]


def _get_karam_fields_for_doctype(doctype: str) -> list[str]:
    """Determine Karam fields available on a doctype (cached per process)."""
    if doctype in _KARAM_FIELDS_CACHE:
        return _KARAM_FIELDS_CACHE[doctype]

    try:
        meta = frappe.get_meta(doctype)
    except frappe.DoesNotExistError as exc:
        # Optional source DocTypes may be absent; other failures must surface.
        frappe.logger(__name__).warning(
            "Could not get metadata for %s: %s", doctype, exc
        )
        _KARAM_FIELDS_CACHE[doctype] = []
        return []

    fields = ["name"]
    if meta.has_field("karam_series"):
        fields.append("karam_series")
    if meta.has_field("translation"):
        fields.append("translation")

    if len(fields) == 1:
        fields.clear()
    _KARAM_FIELDS_CACHE[doctype] = fields
    return fields


def _apply_voucher_data_to_entries(
    entry_index: dict[tuple[str, str], list[frappe._dict[str, Any]]],
    voucher_data: dict[tuple[str, str], dict[str, Any]],
    fields: list[str] | None = None,
) -> None:
    """Apply one source row to every GL row for that voucher."""
    del fields  # Retained for compatibility; hydration always uses both Karam fields.
    for (doctype, voucher_no), entries in entry_index.items():
        source_row = voucher_data.get((doctype, voucher_no))
        if not source_row:
            continue
        _apply_missing_karam_fields(entries, source_row)


def _apply_voucher_data_to_gl_entries(
    gl_entries: list[frappe._dict[str, Any]],
    voucher_data: dict[tuple[str, str], dict[str, Any]],
) -> None:
    """Hydrate rows in one pass after the bulk source query."""
    for entry in gl_entries:
        voucher_type = entry.get("voucher_type")
        voucher_no = entry.get("voucher_no")
        if not isinstance(voucher_type, str) or not isinstance(voucher_no, str):
            continue
        source_row = voucher_data.get((voucher_type, voucher_no))
        if not source_row:
            continue
        if not entry.get("karam_series"):
            entry["karam_series"] = source_row.get("karam_series")
        if not entry.get("translation"):
            entry["translation"] = source_row.get("translation")


def _chunked(values: list[str], size: int) -> Iterable[list[str]]:
    """Yield values in chunks for compatibility callers."""
    if size <= 0:
        size = VOUCHER_LOOKUP_BATCH_SIZE
    for idx in range(0, len(values), size):
        yield values[idx : idx + size]


def _voucher_identity(
    entry: frappe._dict[str, Any], include_journal_entries: bool
) -> tuple[str, str] | None:
    if entry.get("karam_series") and entry.get("translation"):
        return None
    voucher_type, voucher_no = entry.get("voucher_type"), entry.get("voucher_no")
    if not voucher_type or not voucher_no:
        return None
    if voucher_type == "Journal Entry" and not include_journal_entries:
        return None
    if voucher_type not in KARAM_DOCTYPES:
        return None
    return voucher_type, voucher_no


def _apply_missing_karam_fields(
    entries: list[frappe._dict[str, Any]], source_row: dict[str, Any]
) -> None:
    for entry in entries:
        for field in ("karam_series", "translation"):
            if not entry.get(field):
                entry[field] = source_row.get(field)
