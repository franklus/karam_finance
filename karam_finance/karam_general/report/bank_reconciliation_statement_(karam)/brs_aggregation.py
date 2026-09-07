"""Assemble and enrich Bank Reconciliation Statement rows."""

from __future__ import annotations

from importlib import import_module
from typing import Any

import frappe
from frappe.utils import flt, getdate

brs_enrichment = import_module(f"{__package__}.brs_enrichment")
brs_queries = import_module(f"{__package__}.brs_queries")


def _extension_entries(filters: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for method_name in brs_queries.extension_hook_names(
        "get_entries_for_bank_reconciliation_statement",
        brs_queries._UPSTREAM_ENTRIES_HOOK,
    ):
        entries.extend(frappe.get_attr(method_name)(filters) or [])
    return entries


def _extension_incorrect_clearance_amount(filters: dict[str, Any]) -> float:
    total = 0.0
    for method_name in brs_queries.extension_hook_names(
        "get_amounts_not_reflected_in_system_for_bank_reconciliation_statement",
        brs_queries._UPSTREAM_AMOUNT_HOOK,
    ):
        total += flt(frappe.get_attr(method_name)(filters) or 0.0)
    return total


def get_entries(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Return built-in and additive-hook entries in stable chronological order."""
    entries: list[dict[str, Any]] = list(
        brs_queries.get_entries_for_bank_reconciliation_statement(filters)
    )
    extension_entries = _extension_entries(filters)
    brs_enrichment.enrich_je_party(extension_entries)
    entries.extend(extension_entries)

    brs_enrichment.populate_missing_party_names(entries)

    return sorted(entries, key=_entry_sort_key)


def _entry_sort_key(row: dict[str, Any]) -> tuple[Any, str]:
    return getdate(row.get("posting_date")), row.get("payment_entry") or ""


def get_amounts_not_reflected_in_system(filters: dict[str, Any]) -> float:
    """Return built-in signed movement plus additive application hooks."""
    return brs_queries.get_amounts_not_reflected_in_system(
        filters
    ) + _extension_incorrect_clearance_amount(filters)


def get_journal_entries(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Compatibility accessor for Journal Entry rows."""
    return brs_queries.get_journal_entries(filters)


def get_payment_entries(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Compatibility accessor for Payment Entry rows."""
    return brs_queries.get_payment_entries(filters)


def get_purchase_invoices(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Compatibility accessor for Purchase Invoice rows."""
    return brs_queries.get_purchase_invoices(filters)


def get_pos_entries(filters: dict[str, Any]) -> list[dict[str, Any]]:
    """Compatibility accessor for POS parent Sales Invoice rows."""
    return brs_queries.get_pos_entries(filters)
