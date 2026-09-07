"""Explain the materialised RC ledger and the scope of the displayed balances."""

from typing import Any

import frappe
from frappe import _
from frappe.utils import format_datetime

from .gl_money import decimal_amount


def attach_report_context(rows: list[dict[str, Any]], filters: Any) -> None:
    if not rows:
        return
    timestamp = frappe.db.get_single_value(
        "Reporting Currency Settings", "last_sync_timestamp"
    )
    parts = [
        _(
            "Reporting currency: {0}. Stored RC ledger; refresh does not sync or recompute DOE."
        ).format(filters.presentation_currency),
        _("Recorded sync: {0}.").format(timestamp or _("Not recorded")),
    ]
    if filters.get("disable_opening_balance_calculation"):
        parts.append(_("Period movements only: opening history is excluded."))
        for row in rows:
            if row.get("row_type") == "closing":
                row["account"] = _("Period net movement")
    if filters.get("entry_type") != "All":
        parts.append(
            _("Balances include only the selected entry type: {0}.").format(
                filters.entry_type
            )
        )
    _append_entry_exclusions(parts, filters)
    if any(
        filters.get(key)
        for key in (
            "cost_center",
            "project",
            "finance_book",
            "party",
            "party_type",
            "karam_series",
            "translation",
        )
    ):
        parts.append(
            _(
                "Filtered RC slice: unallocated or unclassified manual/DOE entries may be excluded."
            )
        )
    parts.append(
        _(
            "Footer totals and exports cover the complete report result, across all pages."
        )
    )
    rows[0]["_report_context_details"] = {
        "currency": filters.presentation_currency,
        "sync": format_datetime(timestamp) if timestamp else _("Not recorded"),
        "notes": [
            _("Refresh reads stored entries; use Sync to update conversions and DOE."),
            *parts[2:],
        ],
    }
    _append_ledger_difference(parts, rows, filters)
    rows[0]["_report_context"] = " ".join(parts)


def _append_ledger_difference(
    parts: list[str], rows: list[dict[str, Any]], filters: Any
) -> None:
    total = next(
        (row for row in reversed(rows) if row.get("row_type") == "closing"), {}
    )
    scope_filters = (
        "account",
        "party",
        "party_type",
        "cost_center",
        "project",
        "finance_book",
        "voucher_no",
        "voucher_type",
        "rc_entry",
        "karam_series",
        "translation",
        "ignore_err",
        "ignore_cr_dr_notes",
        "disable_opening_balance_calculation",
        "show_cancelled_entries",
        "exclude_reporting_doe",
        "exclude_manual_entries",
    )
    if filters.get("entry_type") == "All" and not any(
        filters.get(key) for key in scope_filters
    ):
        display: dict[str, Any] = total.get("_display_amounts", {})
        balance = decimal_amount(display.get("balance", total.get("balance")))
        if balance:
            rows[0]["_report_context_details"]["closing"] = f"{abs(balance):,.2f} " + (
                _("Cr") if balance < 0 else _("Dr")
            )
            parts.append(
                _(
                    "Selected ledger closing difference: {0} {1}. Matching RCGLE does not certify a balanced ledger."
                ).format(filters.presentation_currency, balance)
            )


def _append_entry_exclusions(parts: list[str], filters: Any) -> None:
    if filters.get("exclude_reporting_doe"):
        parts.append(
            _("Reporting DOE entries are excluded from movements and opening balances.")
        )
    if filters.get("exclude_manual_entries"):
        parts.append(
            _("Manual entries are excluded from movements and opening balances.")
        )
