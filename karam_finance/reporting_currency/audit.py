"""Administrator-only historical evidence. This module never repairs data."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.query_builder.functions import Coalesce, Count

from karam_finance.letter_reconciliation.legacy_mapping import legacy_letter_mappings
from karam_finance.reporting_currency.offset_accounts import validate_offset_accounts


@frappe.whitelist()
def audit_finance_data(sample_limit: int | str = 20) -> dict[str, Any]:
    """Return counts and bounded samples from the caller's database snapshot.

    A missing/stale generated row can reflect pending sync, so it is a review
    candidate. Orphaned rows, conflicting selected rates and non-empty letters
    inconsistent with an unambiguous journal mapping are definite inconsistencies.
    """
    frappe.only_for("System Manager")
    limit = max(1, min(int(sample_limit), 100))
    settings = frappe.get_single("Reporting Currency Settings")
    findings = _ledger_findings(settings.get("reporting_currency"), limit)
    findings.update(_configuration_findings(settings, limit))
    findings.update(_letter_findings(limit))
    return {
        "site": frappe.local.site,
        "as_of": frappe.utils.now(),
        "reporting_currency": settings.get("reporting_currency"),
        "last_sync_timestamp": settings.get("last_sync_timestamp"),
        "sample_limit": limit,
        "read_only": True,
        "findings": findings,
    }


def _finding(confidence: str) -> dict[str, Any]:
    return {"confidence": confidence, "count": 0, "samples": []}


def _record(finding: dict[str, Any], sample: Any, limit: int) -> None:
    finding["count"] += 1
    if len(finding["samples"]) < limit:
        finding["samples"].append(sample)


def _query_finding(
    query: Any, fields: list[Any], *, limit: int, confidence: str
) -> dict[str, Any]:
    return {
        "confidence": confidence,
        "count": query.select(Count("*")).run()[0][0],
        "samples": query.select(*fields)
        .orderby(fields[0])
        .limit(limit)
        .run(as_dict=True),
    }


def _ledger_findings(currency: str | None, limit: int) -> dict[str, Any]:
    gl = frappe.qb.DocType("GL Entry")
    rc = frappe.qb.DocType("Reporting Currency GLE")
    generated = (Coalesce(rc.manual_entry, 0) == 0) & (
        Coalesce(rc.reporting_doe, 0) == 0
    )
    linked = (
        frappe.qb.from_(rc).left_join(gl).on(gl.name == rc.gl_entry).where(generated)
    )
    missing = (
        frappe.qb.from_(gl)
        .left_join(rc)
        .on((rc.gl_entry == gl.name) & generated)
        .where(
            (gl.docstatus == 1) & (Coalesce(gl.is_cancelled, 0) == 0) & rc.name.isnull()
        )
    )
    queries = {
        "missing_generated": (missing, [gl.name, gl.company], "review_candidate"),
        "orphaned_generated": (
            linked.where(gl.name.isnull()),
            [rc.name, rc.gl_entry],
            "definite",
        ),
        "stale_generated": (
            linked.where(gl.name.notnull()).where(
                (gl.modified > rc.gl_entry_modified)
                | rc.gl_entry_modified.isnull()
                | (Coalesce(gl.is_cancelled, 0) != Coalesce(rc.is_cancelled, 0))
                | (gl.docstatus != rc.docstatus)
            ),
            [
                rc.name,
                rc.gl_entry,
                rc.gl_entry_modified,
                gl.modified,
                gl.is_cancelled.as_("source_cancelled"),
                rc.is_cancelled.as_("reporting_cancelled"),
                gl.docstatus.as_("source_status"),
                rc.docstatus.as_("reporting_status"),
            ],
            "review_candidate",
        ),
        "mixed_currencies": (
            frappe.qb.from_(rc).where(
                Coalesce(rc.reporting_currency, "") != (currency or "")
            ),
            [rc.name, rc.reporting_currency, rc.manual_entry],
            "definite" if currency else "review_candidate",
        ),
    }
    return {
        key: _query_finding(query, fields, limit=limit, confidence=confidence)
        for key, (query, fields, confidence) in queries.items()
    }


def _configuration_findings(settings: Any, limit: int) -> dict[str, Any]:
    offsets = _finding("definite")
    for row in settings.rc_parameters or []:
        try:
            validate_offset_accounts([row])
        except frappe.ValidationError as exc:
            _record(offsets, {"row": row.idx, "error": str(exc)}, limit)
    conflicts = _finding("definite")
    currencies = frappe.get_all(
        "Company", pluck="default_currency", distinct=True, limit=0
    )
    for currency in sorted(
        set(currencies) - {settings.get("reporting_currency"), None, ""}
    ):
        for conflict in _rate_conflicts(currency, settings.get("reporting_currency")):
            _record(conflicts, conflict, limit)
    return {"invalid_doe_offsets": offsets, "conflicting_exchange_rates": conflicts}


def _rate_conflicts(default_currency: str, reporting_currency: str | None) -> list[Any]:
    # Only the chosen direction matters: direct rates supersede inverse rates on
    # the same date. Values are bound; the query contains no dynamic identifiers.
    return frappe.db.sql(
        """
        SELECT ce.from_currency, ce.to_currency, ce.date,
               COUNT(*) AS records, MIN(ce.name) AS first_record, MAX(ce.name) AS last_record
        FROM `tabCurrency Exchange` ce
        WHERE (ce.from_currency = %(default)s AND ce.to_currency = %(reporting)s)
           OR (ce.from_currency = %(reporting)s AND ce.to_currency = %(default)s
               AND NOT EXISTS (SELECT 1 FROM `tabCurrency Exchange` direct
                   WHERE direct.from_currency = %(default)s
                     AND direct.to_currency = %(reporting)s AND direct.date = ce.date))
        GROUP BY ce.from_currency, ce.to_currency, ce.date
        HAVING COUNT(DISTINCT ce.exchange_rate) > 1
        ORDER BY ce.date, ce.from_currency, ce.to_currency
    """,
        {"default": default_currency, "reporting": reporting_currency},
        as_dict=True,
    )


def _letter_findings(limit: int) -> dict[str, Any]:
    findings = {
        "ambiguous_legacy_letters": _finding("review_candidate"),
        "inconsistent_legacy_letters": _finding("definite"),
        "missing_legacy_letters": _finding("review_candidate"),
    }
    after = ""
    while rows := legacy_letter_mappings(after=after):
        for row in rows:
            if key := _letter_finding_key(row):
                _record(findings[key], row, limit)
        after = rows[-1].name
    return findings


def _letter_finding_key(row: Any) -> str | None:
    if not row.source_count or row.letter_count != 1:
        return "ambiguous_legacy_letters"
    if (row.letter or "") != (row.source_letter or ""):
        return "inconsistent_legacy_letters" if row.letter else "missing_legacy_letters"
    return None
