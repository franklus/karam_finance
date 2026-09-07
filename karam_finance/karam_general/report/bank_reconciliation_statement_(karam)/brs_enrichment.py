"""Bulk party enrichment for Bank Reconciliation Statement rows."""

from __future__ import annotations

from typing import Any

import frappe


def enrich_je_party(entries: list[dict[str, Any]]) -> None:
    """Populate Journal Entry party fields with one child-table query."""
    journal_entry_names = {
        entry.get("payment_entry")
        for entry in entries
        if entry.get("payment_document") == "Journal Entry"
        and entry.get("payment_entry")
    }
    if not journal_entry_names:
        return

    jea = frappe.qb.DocType("Journal Entry Account")
    rows = (
        frappe.qb.from_(jea)
        .select(jea.parent, jea.party_type, jea.party)
        .where(
            (jea.parent.isin(journal_entry_names))
            & jea.party.notnull()
            & (jea.party != "")
        )
        .orderby(jea.parent)
        .orderby(jea.idx)
        .run(as_dict=True)
    )
    party_by_entry: dict[str | None, Any] = {}
    for row in rows:
        party_by_entry.setdefault(row.parent, row)

    for entry in entries:
        if entry.get("payment_document") != "Journal Entry":
            continue
        party = party_by_entry.get(entry.get("payment_entry"))
        if party:
            entry["party_type"] = party.party_type
            entry["party"] = party.party


def populate_missing_party_names(entries: list[dict[str, Any]]) -> None:
    """Resolve missing party names in bounded Query Builder batches."""
    parties_by_type: dict[str, set[str]] = {}
    for entry in entries:
        party_type = entry.get("party_type")
        party = entry.get("party")
        if party_type and party and not entry.get("party_name"):
            parties_by_type.setdefault(party_type, set()).add(party)

    name_map: dict[tuple[str, str], str] = {}
    for party_type, parties in parties_by_type.items():
        name_map.update(_party_type_names(party_type, parties))

    for entry in entries:
        party_type = entry.get("party_type")
        party = entry.get("party")
        if party_type and party and not entry.get("party_name"):
            entry["party_name"] = name_map.get((party_type, party), party)


def _party_type_names(party_type: str, parties: set[str]) -> dict[tuple[str, str], str]:
    meta = frappe.get_meta(party_type)
    title_field = meta.get_title_field() or "name"
    if title_field == "name":
        return {(party_type, party): party for party in parties}

    party_doc = frappe.qb.DocType(party_type)
    rows = (
        frappe.qb.from_(party_doc)
        .select(party_doc.name, party_doc[title_field])
        .where(party_doc.name.isin(parties))
        .run(as_dict=True)
    )
    return {(party_type, row.name): row.get(title_field) or row.name for row in rows}
