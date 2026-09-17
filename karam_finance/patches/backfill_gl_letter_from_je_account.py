"""Backfill only provable legacy Journal Entry letter mappings."""

from __future__ import annotations

import frappe

from karam_finance.letter_reconciliation.legacy_mapping import legacy_letter_mappings


def execute() -> None:
    """Leave ambiguous, unlettered and already-lettered legacy rows unchanged."""
    after = ""
    while rows := legacy_letter_mappings(after=after):
        updates = {
            row.name: {"letter": row.source_letter}
            for row in rows
            if not row.letter
            and row.letter_count == 1
            and row.source_letter
            and row.source_count
        }
        if updates:
            frappe.db.bulk_update("GL Entry", updates)
        after = rows[-1].name
