"""Shared party-name resolution utilities."""

from __future__ import annotations

import frappe


def populate_party_names(entries: list) -> None:
    """Batch-resolve party_name for entries that have party but no name.

    Works with Document child rows, frappe._dict instances, and plain dicts.
    """
    parties_by_type: dict[str, set[str]] = {}
    for e in entries:
        if e.get("party_type") and e.get("party") and not e.get("party_name"):
            parties_by_type.setdefault(e.get("party_type"), set()).add(e.get("party"))

    if not parties_by_type:
        return

    name_map: dict[tuple[str, str], str] = {}
    for party_type, parties in parties_by_type.items():
        title_field = frappe.get_meta(party_type).get_title_field() or "name"
        if title_field == "name":
            for p in parties:
                name_map[(party_type, p)] = p
            continue

        data = frappe.get_all(
            party_type,
            filters={"name": ["in", list(parties)]},
            fields=["name", title_field],
            limit=0,
        )
        for d in data:
            name_map[(party_type, d.name)] = d.get(title_field) or d.name

    for e in entries:
        if e.get("party_type") and e.get("party") and not e.get("party_name"):
            resolved = name_map.get(
                (e.get("party_type"), e.get("party")), e.get("party")
            )
            if isinstance(e, dict):
                e["party_name"] = resolved
            else:
                e.party_name = resolved
