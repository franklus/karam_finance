"""Read-only evidence for merged legacy GL rows without journal row links."""

from __future__ import annotations

from functools import reduce
from operator import and_
from typing import Any, cast

import frappe
from frappe.query_builder.functions import Coalesce, Count, Max


def legacy_letter_mappings(*, after: str = "", limit: int = 500) -> list[Any]:
    """Compare all matching source letters, including blanks, in bounded batches.

    Shared Link fields include disabled accounting dimensions too: disabling a
    dimension does not erase its historical posting identity.
    """
    gl = frappe.qb.DocType("GL Entry")
    row = frappe.qb.DocType("Journal Entry Account")
    journal = frappe.qb.DocType("Journal Entry")
    identity = reduce(
        and_,
        (
            Coalesce(gl[field], "") == Coalesce(row[field], "")
            for field in _shared_posting_fields()
        ),
        (gl.voucher_no == row.parent)
        & (row.parenttype == "Journal Entry")
        & (row.parentfield == "accounts")
        & (row.docstatus == 1),
    )
    query = (
        frappe.qb.from_(gl)
        .left_join(row)
        .on(identity)
        .left_join(journal)
        .on(
            (journal.name == row.parent)
            & (journal.company == gl.company)
            & (journal.posting_date == gl.posting_date)
            & (journal.docstatus == 1)
        )
        .select(
            gl.name,
            gl.letter,
            Max(row.letter).as_("source_letter"),
            cast("Any", Count)(Coalesce(row.letter, "")).distinct().as_("letter_count"),
            Count(journal.name).as_("source_count"),
        )
        .where(
            (gl.voucher_type == "Journal Entry")
            & (Coalesce(gl.voucher_detail_no, "") == "")
            & (gl.name > after)
        )
        .groupby(gl.name, gl.letter)
        .orderby(gl.name)
        .limit(limit)
    )
    return query.run(as_dict=True)


def _shared_posting_fields() -> list[str]:
    gl_fields = {field.fieldname for field in frappe.get_meta("GL Entry").fields}
    row_fields = frappe.get_meta("Journal Entry Account").fields
    fields = {
        field.fieldname
        for field in row_fields
        if field.fieldtype == "Link" and field.fieldname in gl_fields
    }
    return sorted(fields | {"account", "party_type", "party", "cost_center", "project"})
