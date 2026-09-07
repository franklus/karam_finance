"""Shared party-name resolution utilities."""

from __future__ import annotations

from typing import Any

import frappe


def populate_party_names(entries: list[Any]) -> None:
    """Batch-resolve party_name for entries that have party but no name.

    Works with Document child rows, frappe._dict instances, and plain dicts.
    """
    parties_by_type: dict[str, set[str]] = {}
    unresolved = [entry for entry in entries if _needs_party_name(entry)]
    for entry in unresolved:
        parties_by_type.setdefault(entry.get("party_type"), set()).add(
            entry.get("party")
        )

    name_map: dict[tuple[str, str], str] = {}
    for party_type, parties in parties_by_type.items():
        name_map.update(_names_for_party_type(party_type, parties))

    for entry in unresolved:
        resolved = name_map.get(
            (entry.get("party_type"), entry.get("party")), entry.get("party")
        )
        if isinstance(entry, dict):
            entry["party_name"] = resolved
        else:
            entry.party_name = resolved


def _needs_party_name(entry: Any) -> bool:
    return bool(
        entry.get("party_type") and entry.get("party") and not entry.get("party_name")
    )


def _names_for_party_type(
    party_type: str, parties: set[str]
) -> dict[tuple[str, str], str]:
    """One batch per DocType: parties in different tables cannot share a query."""
    title_field = frappe.get_meta(party_type).get_title_field() or "name"
    if title_field == "name":
        return {(party_type, party): party for party in parties}
    data = frappe.get_all(
        party_type,
        filters={"name": ["in", list(parties)]},
        fields=["name", title_field],
        limit=0,
    )
    return {(party_type, row.name): row.get(title_field) or row.name for row in data}
