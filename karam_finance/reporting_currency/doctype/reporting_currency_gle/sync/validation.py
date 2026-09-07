"""Validation functions for Reporting Currency GLE sync.

This module handles settings validation, currency coverage checks,
and date range building for the sync process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import frappe
from frappe import _

if TYPE_CHECKING:
    from karam_finance.reporting_currency.doctype.reporting_currency_settings.reporting_currency_settings import (
        ReportingCurrencySettings,
    )

# ============================================================================
# MODULE CONSTANTS
# ============================================================================

# DocType names
DOCTYPE_CURRENCY_EXCHANGE = "Currency Exchange"
DOCTYPE_RC_SETTINGS = "Reporting Currency Settings"
DOCTYPE_COMPANY = "Company"

# Error display limits
SAMPLE_ENTRIES_LIMIT = 10
ERROR_TABLE_SAMPLE_LIMIT = 5


# ============================================================================
# SETTINGS VALIDATION
# ============================================================================


def validate_settings() -> dict[str, Any]:
    """Validate that Reporting Currency Settings are properly configured.

    Validates:
    1. reporting_currency field is set
    2. rc_parameters table has at least one row

    Returns settings dict with reporting_currency and sync settings.
    """
    ledger = frappe.qb.DocType("GL Entry")
    companies = (
        frappe.qb.from_(ledger)
        .select(ledger.company)
        .where(ledger.docstatus == 1)
        .distinct()
        .limit(2)
        .run(pluck=True)
    )
    if len(companies) > 1:
        frappe.throw(
            _(
                "Reporting Currency sync currently supports one company per site. Configure company-specific generation before synchronising multiple companies."
            )
        )
    settings = cast("ReportingCurrencySettings", frappe.get_single(DOCTYPE_RC_SETTINGS))

    if not settings.reporting_currency:
        frappe.throw(
            _("Reporting Currency is not set in Reporting Currency Settings."),
            title=_("Configuration Error"),
        )

    if not settings.rc_parameters or len(settings.rc_parameters) == 0:
        frappe.throw(
            _("No Reporting Currency Parameters defined. Please add at least one row."),
            title=_("Configuration Error"),
        )

    return {
        "reporting_currency": settings.reporting_currency,
        "last_sync_timestamp": settings.last_sync_timestamp,
        "rc_parameters": settings.rc_parameters,
    }


def get_company_default_currency(company: str) -> str:
    """Get the default currency for a company."""
    currency = frappe.db.get_value(DOCTYPE_COMPANY, company, "default_currency")
    if not currency:
        frappe.throw(
            _("Company {0} does not have a default currency configured.").format(
                company
            ),
            title=_("Configuration Error"),
        )
    return currency


# ============================================================================
# CURRENCY EXCHANGE VALIDATION
# ============================================================================


def validate_currency_exchange_coverage(
    gl_entries: list[dict[str, Any]], reporting_currency: str, default_currency: str
) -> dict[str, bool]:
    """Validate Currency Exchange records exist for the currency pair.

    Checks for default_currency ↔ reporting_currency exchange records.
    The conversion is ALWAYS from company's default_currency to reporting_currency.
    account_currency is only used to determine if conversion is needed.

    Returns dict: {"direct": bool, "inverse": bool}
    """
    # Check if any GL entries need conversion (account_currency != reporting_currency)
    needs_conversion = False
    accounts_needing_conversion = set()

    for gle in gl_entries:
        acc_currency = gle.get("account_currency")
        if acc_currency and acc_currency != reporting_currency:
            needs_conversion = True
            accounts_needing_conversion.add(acc_currency)

    # If no conversions needed, return early
    if not needs_conversion:
        return {"direct": False, "inverse": False}

    # Check for Currency Exchange records: default_currency ↔ reporting_currency

    # Check direct: default_currency → reporting_currency
    direct_exists = frappe.db.exists(
        DOCTYPE_CURRENCY_EXCHANGE,
        {
            "from_currency": default_currency,
            "to_currency": reporting_currency,
        },
    )

    # Check inverse: reporting_currency → default_currency
    inverse_exists = frappe.db.exists(
        DOCTYPE_CURRENCY_EXCHANGE,
        {
            "from_currency": reporting_currency,
            "to_currency": default_currency,
        },
    )

    # If neither direction exists, raise detailed error
    if not direct_exists and not inverse_exists:
        _throw_missing_exchange_error(
            gl_entries,
            accounts_needing_conversion,
            default_currency,
            reporting_currency=reporting_currency,
        )

    return {
        "direct": bool(direct_exists),
        "inverse": bool(inverse_exists),
    }


def get_reporting_company() -> str | None:
    """Refuse ambiguous company selection in site-wide DOE settings."""
    ledger = frappe.qb.DocType("Reporting Currency GLE")
    companies = (
        frappe.qb.from_(ledger)
        .select(ledger.company)
        .where(ledger.reporting_doe == 0)
        .distinct()
        .limit(2)
        .run(pluck=True)
    )
    if len(companies) > 1:
        frappe.throw(_("Reporting DOE currently supports one company per site."))
    return companies[0] if companies else None


def _throw_missing_exchange_error(
    gl_entries: list[dict[str, Any]],
    accounts_needing_conversion: set[str],
    default_currency: str,
    *,
    reporting_currency: str,
) -> None:
    pair_key = f"{default_currency}-{reporting_currency}"
    # Collect sample GL entries for error message
    sample_entries = []
    for gle in gl_entries[:SAMPLE_ENTRIES_LIMIT]:  # First N entries for error display
        if gle.get("account_currency") != reporting_currency:
            sample_entries.append(  # noqa: PERF401
                {
                    "gle": gle.get("name"),
                    "date": gle.get("posting_date"),
                    "account": gle.get("account"),
                    "voucher": gle.get("voucher_no"),
                    "account_currency": gle.get("account_currency"),
                }
            )

    # Build error message
    currencies_str = ", ".join(sorted(accounts_needing_conversion))
    error_parts = [
        f"<p><strong>Missing Currency Exchange: {pair_key}</strong></p>",
        f"<p>Your company's default currency is <strong>{default_currency}</strong> and you're converting to <strong>{reporting_currency}</strong>.</p>",
        f"<p>Affected accounts use currencies: <strong>{currencies_str}</strong></p>",
        "<p><strong>Sample GL Entries:</strong></p>",
        '<table class="table table-bordered table-sm" style="font-size: 90%;">',
        "<thead><tr>",
        "<th>GL Entry</th><th>Date</th><th>Account</th>",
        "<th>Account Currency</th><th>Voucher</th>",
        "</tr></thead><tbody>",
    ]

    for entry in sample_entries[:ERROR_TABLE_SAMPLE_LIMIT]:
        error_parts.extend(
            [
                "<tr>",
                f"<td>{entry['gle']}</td>",
                f"<td>{entry['date']}</td>",
                f"<td>{entry['account']}</td>",
                f"<td><strong>{entry['account_currency']}</strong></td>",
                f"<td>{entry['voucher']}</td>",
                "</tr>",
            ]
        )

    error_parts.extend(
        [
            "</tbody></table>",
            '<p style="margin-top: 15px;">',
            f"<strong>Action Required:</strong> Create Currency Exchange records for <strong>{default_currency} ↔ {reporting_currency}</strong> ",
            f"(in either direction: {default_currency}→{reporting_currency} or {reporting_currency}→{default_currency}).",
            "</p>",
        ]
    )

    frappe.throw("".join(error_parts), title=_("Missing Currency Exchange Records"))
