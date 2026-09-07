"""Currency conversion functions for Reporting Currency GLE sync.

This module handles amount conversion and GL Entry processing
for the reporting currency sync.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils.data import cint, flt

from .exchange_rates import get_applicable_rate
from .utils import get_currency_precision, get_gl_entry_stable_hash

# ============================================================================
# MODULE CONSTANTS
# ============================================================================

# DocType names
DOCTYPE_RC_GLE = "Reporting Currency GLE"


# ============================================================================
# AMOUNT CONVERSION
# ============================================================================


def convert_amounts(  # noqa: PLR0917 - retain positional sync conversion callers.
    gle_record: dict[str, Any], rate: float, direction: str, reporting_currency: str
) -> tuple[float, float]:
    """Convert debit/credit amounts (in company currency) to reporting currency.

    Logic:
    - If direct rate (default_currency -> reporting_currency): amount * rate
    - If inverse rate (reporting_currency -> default_currency): amount / rate

    The debit/credit fields are ALWAYS in company's default currency.

    Args:
        gle_record: GL Entry record with debit/credit amounts
        rate: Exchange rate value
        direction: "direct" or "inverse"
        reporting_currency: Currency code for precision lookup

    Returns (reporting_debit, reporting_credit) tuple.
    """
    # Get amounts in company currency (debit/credit fields)
    debit_company_curr = flt(gle_record.get("debit", 0))
    credit_company_curr = flt(gle_record.get("credit", 0))

    # Apply exchange rate based on direction
    if direction == "direct":
        # Direct rate: default_currency -> reporting_currency (multiply)
        conversion_rate = flt(rate)
    else:
        # Inverse rate: reporting_currency -> default_currency (divide)
        conversion_rate = 1 / flt(rate) if flt(rate) != 0 else 0

    # Get currency-specific precision instead of hard-coded 2 decimals
    # This prevents data loss for 3-decimal currencies (KWD, BHD) and
    # 0-decimal currencies (JPY, KRW)
    precision = get_currency_precision(reporting_currency)

    reporting_debit = flt(debit_company_curr * conversion_rate, precision)
    reporting_credit = flt(credit_company_curr * conversion_rate, precision)

    return (reporting_debit, reporting_credit)


# ============================================================================
# GL ENTRY PROCESSING
# ============================================================================


def process_gl_entry(  # noqa: PLR0913, PLR0917 - retain the sync conversion compatibility signature.
    gle: dict[str, Any],
    rate_timeline: list[dict[str, Any]],
    rate_dates: list[Any],
    default_currency: str,
    reporting_currency: str,
    date_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Process a single GL Entry and prepare its Reporting Currency GLE record.

    Steps:
    1. Check if account_currency == reporting_currency (optimisation - direct copy)
    2. Otherwise, get applicable exchange rate for default_currency ↔ reporting_currency
    3. Convert debit/credit (company currency) to reporting_debit/reporting_credit
    4. Capture exchange metadata (Currency Exchange doc, date, effective rate)
    5. Build complete RC GLE record dict

    Returns dict ready for insertion.

    Args:
        gle: GL Entry dictionary
        rate_timeline: Sorted exchange rate timeline
        rate_dates: Pre-extracted dates from timeline for binary search
        default_currency: Company's default currency
        reporting_currency: Target reporting currency
        date_cache: Optional cache for date parsing optimisation
    """
    account_currency = gle.get("account_currency")
    posting_date = gle.get("posting_date", "")

    # Optimisation: If account is already in reporting currency, direct copy
    currency_exchange_name = None
    currency_exchange_date = None
    exchange_rate_used = 1 if account_currency == reporting_currency else None

    if account_currency == reporting_currency:
        # Direct transfer - no conversion needed
        # Copy from account currency fields
        reporting_debit = flt(gle.get("debit_in_account_currency", 0))
        reporting_credit = flt(gle.get("credit_in_account_currency", 0))
    else:
        # Need currency conversion from company currency to reporting currency
        # Get applicable rate from timeline (default_currency ↔ reporting_currency)
        rate_info = get_applicable_rate(
            posting_date,
            default_currency,
            reporting_currency,
            rate_timeline,
            rate_dates,
            date_cache,
        )

        rate = flt(rate_info.get("rate", 0))
        direction = str(rate_info.get("direction", "direct"))
        currency_exchange_name = rate_info.get("currency_exchange")
        currency_exchange_date = rate_info.get("date")

        # Convert debit/credit (in company currency) to reporting currency
        reporting_debit, reporting_credit = convert_amounts(
            gle, rate, direction, reporting_currency
        )

        # For display, show the effective rate applied to convert default → reporting
        exchange_rate_used = _effective_exchange_rate(rate, direction)

    # Ensure date is serialized as ISO string for DB insert
    if currency_exchange_date and not isinstance(currency_exchange_date, str):
        currency_exchange_date = currency_exchange_date.isoformat()

    # Build RC GLE record
    return {
        "doctype": DOCTYPE_RC_GLE,
        "name": None,  # Will be deterministically generated during insertion phase
        "gl_entry": gle.get("name"),
        "gl_entry_hash": get_gl_entry_stable_hash(gle),
        "gl_entry_modified": gle.get("modified"),
        "posting_date": gle.get("posting_date"),
        "transaction_date": gle.get("transaction_date"),
        "fiscal_year": gle.get("fiscal_year"),
        "due_date": gle.get("due_date"),
        "account": gle.get("account"),
        "account_currency": gle.get("account_currency"),
        "against": gle.get("against"),
        "party_type": gle.get("party_type"),
        "party": gle.get("party"),
        "voucher_type": gle.get("voucher_type"),
        "voucher_no": gle.get("voucher_no"),
        "voucher_subtype": gle.get("voucher_subtype"),
        "transaction_currency": gle.get("transaction_currency"),
        "against_voucher_type": gle.get("against_voucher_type"),
        "against_voucher": gle.get("against_voucher"),
        "voucher_detail_no": gle.get("voucher_detail_no"),
        "transaction_exchange_rate": gle.get("transaction_exchange_rate"),
        "debit_amount_in_account_currency": gle.get("debit_in_account_currency"),
        "debit": flt(gle.get("debit") or 0, 9),
        "debit_amount_in_transaction_currency": gle.get(
            "debit_in_transaction_currency"
        ),
        "credit_amount_in_account_currency": gle.get("credit_in_account_currency"),
        "credit": flt(gle.get("credit") or 0, 9),
        "credit_in_transaction_currency": gle.get("credit_in_transaction_currency"),
        "reporting_debit": reporting_debit,
        "reporting_credit": reporting_credit,
        "reporting_currency": reporting_currency,
        "currency_exchange": currency_exchange_name,
        "date": currency_exchange_date,
        "exchange_rate": exchange_rate_used,
        "cost_center": gle.get("cost_center"),
        "project": gle.get("project"),
        "finance_book": gle.get("finance_book"),
        "company": gle.get("company"),
        "is_opening": gle.get("is_opening"),
        "is_advance": gle.get("is_advance"),
        "to_rename": gle.get("to_rename"),
        "is_cancelled": gle.get("is_cancelled"),
        "remarks": gle.get("remarks"),
        "account_details": 0,  # GL Entry doesn't have this field, set to 0
        "docstatus": cint(gle.get("docstatus", 0)),
        "manual_entry": 0,  # Sync-created records are never manual
        # Standard Frappe fields (not auto-populated by bulk_insert)
        "creation": frappe.utils.now(),
        "modified": frappe.utils.now(),
        "owner": frappe.session.user,
        "modified_by": frappe.session.user,
    }


def _effective_exchange_rate(rate: float, direction: str) -> float:
    if direction == "direct":
        return flt(rate)
    return flt(1 / rate) if rate else 0
