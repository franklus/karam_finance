"""Exchange rate functions for Reporting Currency GLE sync.

This module handles building exchange rate timelines, rate lookup,
and temporal coverage validation.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from decimal import Decimal
from html import escape as escape_html
from operator import itemgetter
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlencode

if TYPE_CHECKING:
    from datetime import date

    # RedisWrapper is referenced by the quoted runtime-safe cast.
    from frappe.utils.redis_wrapper import RedisWrapper  # noqa: V104

import frappe
from frappe import _
from frappe.utils import formatdate
from frappe.utils.data import flt, getdate

# ============================================================================
# MODULE CONSTANTS
# ============================================================================

# DocType names
DOCTYPE_CURRENCY_EXCHANGE = "Currency Exchange"

# Error display limits
CSV_EXPORT_THRESHOLD = 20
ERROR_TABLE_DISPLAY_LIMIT = 20

# Caching
TEMP_CACHE_KEY_PREFIX = "temporal_validation_entries_"
TEMP_CACHE_KEY_HASH_LENGTH = 8
TEMP_CACHE_EXPIRATION_SEC = 300

# Date formats
DATE_FORMAT_DISPLAY = "dd-mm-yyyy"


# ============================================================================
# EXCHANGE RATE TIMELINE
# ============================================================================


def build_exchange_rate_timeline(
    default_currency: str, reporting_currency: str, coverage: dict[str, bool]
) -> list[dict[str, Any]]:
    """Build a timeline of exchange rates for default_currency ↔ reporting_currency.

    Fetches all Currency Exchange records and sorts by date (ASC).
    When both direct and inverse rates exist for the same date, prefers direct rate
    to avoid inconsistencies from reciprocal rounding differences.

    Returns list: [
        {"date": date, "rate": float, "direction": "direct"|"inverse"},
        ...
    ]
    """
    timeline: list[dict[str, Any]] = []
    if coverage["direct"]:
        timeline = _direction_timeline(
            default_currency,
            reporting_currency,
            direction="direct",
            excluded_dates=set(),
        )
    direct_dates = {rate["date"] for rate in timeline}
    if coverage["inverse"]:
        timeline.extend(
            _direction_timeline(
                reporting_currency,
                default_currency,
                direction="inverse",
                excluded_dates=direct_dates,
            )
        )
    return sorted(timeline, key=itemgetter("date"))


def _direction_timeline(
    from_currency: str,
    to_currency: str,
    *,
    direction: str,
    excluded_dates: set[date],
) -> list[dict[str, Any]]:
    candidates = frappe.db.get_all(
        DOCTYPE_CURRENCY_EXCHANGE,
        filters={"from_currency": from_currency, "to_currency": to_currency},
        fields=["name", "date", "exchange_rate"],
        order_by="date asc, name asc",
    )
    grouped: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        day = _required_rate_date(candidate["date"])
        if day not in excluded_dates:
            grouped[day].append(candidate)
    timeline: list[dict[str, Any]] = []
    for day, records in grouped.items():
        records.sort(key=itemgetter("name"))
        _validate_same_day_rates(records, from_currency, to_currency, day=day)
        timeline.append(
            {
                "date": day,
                "rate": flt(records[0]["exchange_rate"]),
                "direction": direction,
                "currency_exchange": records[0].get("name"),
            }
        )
    return timeline


def _validate_same_day_rates(
    records: list[dict[str, Any]], from_currency: str, to_currency: str, *, day: date
) -> None:
    values = {Decimal(str(record["exchange_rate"])) for record in records}
    if len(values) != 1:
        frappe.throw(
            _(
                "Conflicting exchange rates for {0} to {1} on {2}: {3}. "
                "Resolve these Currency Exchange records before synchronising."
            ).format(
                from_currency,
                to_currency,
                day,
                ", ".join(str(record.get("name")) for record in records),
            )
        )


# ============================================================================
# RATE LOOKUP
# ============================================================================


def get_applicable_rate(  # noqa: PLR0913, PLR0917 - retain the sync lookup compatibility signature.
    posting_date: str | date,
    default_currency: str,
    reporting_currency: str,
    rate_timeline: list[dict[str, Any]],
    rate_dates: list[date],
    date_cache: dict[str, date] | None = None,
) -> dict[str, Any]:
    """Find the most recent exchange rate applicable for the given posting_date.

    Uses step-function logic with binary search for optimal performance.
    Timeline must be sorted by date (ascending).

    Returns dict containing rate metadata (rate, direction, currency_exchange, date).
    Raises error if posting_date precedes all CE records.

    Args:
        posting_date: Date string or date object
        default_currency: Company's default currency
        reporting_currency: Target reporting currency
        rate_timeline: Sorted list of rate entries with 'date', 'rate', 'direction'
        rate_dates: Pre-extracted dates from timeline (avoids O(n*m) rebuild)
        date_cache: Optional cache to avoid repeated getdate() calls
    """
    # Use cached date object if available, otherwise parse
    posting_date_obj: date
    if date_cache is not None and isinstance(posting_date, str):
        cached = date_cache.get(posting_date)
        if cached:
            posting_date_obj = cached
        else:
            posting_date_obj = _required_rate_date(posting_date)
            date_cache[posting_date] = posting_date_obj
    elif isinstance(posting_date, str):
        posting_date_obj = _required_rate_date(posting_date)
    else:
        posting_date_obj = posting_date

    # Binary search: find rightmost rate where date <= posting_date
    # Use pre-extracted dates for O(log n) performance (not O(n*m))

    # bisect_right gives index where posting_date would be inserted to keep list sorted
    # We want the entry BEFORE that position (most recent rate <= posting_date)
    idx = bisect.bisect_right(rate_dates, posting_date_obj)

    if idx == 0:
        # posting_date precedes all exchange rates
        earliest_ce_date = rate_timeline[0]["date"] if rate_timeline else "N/A"
        frappe.throw(
            _(
                "GL Entry with posting date {0} precedes the earliest "
                "Currency Exchange record for currency pair {1}-{2} "
                "(earliest CE date: {3}). "
                "Please create Currency Exchange records for earlier dates."
            ).format(
                posting_date, default_currency, reporting_currency, earliest_ce_date
            ),
            title=_("Temporal Validation Error"),
        )

    # Get the rate entry at idx-1 (most recent rate on or before posting_date)
    return rate_timeline[idx - 1]


# ============================================================================
# TEMPORAL VALIDATION
# ============================================================================


def validate_temporal_coverage(  # noqa: PLR0917 - exported Frappe sync compatibility entrypoint.
    gl_entries: list[dict[str, Any]],
    rate_timeline: list[dict[str, Any]],
    default_currency: str,
    reporting_currency: str,
) -> None:
    """Pre-validate that ALL GL entries have applicable exchange rates.

    This prevents partial insertions by catching temporal errors BEFORE
    processing starts. Raises error if any GL entry precedes the earliest
    Currency Exchange record.
    """
    if not rate_timeline:
        return  # No timeline means no conversion needed

    earliest_ce_date = rate_timeline[0]["date"]
    entries_before_ce: list[dict[str, Any]] = []

    # Check all GL entries that need conversion
    for gle in gl_entries:
        account_currency = gle.get("account_currency")

        # Skip entries that don't need conversion
        if account_currency == reporting_currency:
            continue

        posting_date = _required_rate_date(gle.get("posting_date"))

        # Check if posting_date precedes earliest CE date
        if posting_date < earliest_ce_date:
            entries_before_ce.append(
                {
                    "gle": gle.get("name"),
                    "date": posting_date,
                    "account": gle.get("account"),
                    "voucher": gle.get("voucher_no"),
                }
            )

    # If any entries precede CE records, raise detailed error
    if entries_before_ce:
        _throw_temporal_coverage_error(
            entries_before_ce,
            earliest_ce_date,
            default_currency,
            reporting_currency=reporting_currency,
        )


def _throw_temporal_coverage_error(
    entries_before_ce: list[dict[str, Any]],
    earliest_ce_date: date,
    default_currency: str,
    *,
    reporting_currency: str,
) -> None:
    # Sort by date
    entries_before_ce.sort(key=itemgetter("date"))

    earliest_gle_date = entries_before_ce[0]["date"]
    total_count = len(entries_before_ce)

    # Format dates as dd-mm-yyyy
    earliest_ce_date_formatted = formatdate(earliest_ce_date, DATE_FORMAT_DISPLAY)
    earliest_gle_date_formatted = formatdate(earliest_gle_date, DATE_FORMAT_DISPLAY)

    # Build error message with proper HTML
    entry_word = "entry" if total_count == 1 else "entries"
    error_parts = [
        '<div style="margin-bottom: 15px;">',
        f"<p>Found <strong>{total_count}</strong> GL {entry_word} dated before the earliest Currency Exchange record.</p>",
        "<p>",
        f"<strong>Currency Pair:</strong> {default_currency} ↔ {reporting_currency}<br>",
        f"<strong>Earliest Currency Exchange Date:</strong> {earliest_ce_date_formatted}<br>",
        f"<strong>Earliest GL Entry Date:</strong> {earliest_gle_date_formatted}",
        "</p>",
        "</div>",
    ]

    # Add CSV export button if more than 20 entries
    if total_count > CSV_EXPORT_THRESHOLD:
        # Store entries in frappe cache for CSV export
        cache_key = (
            f"{TEMP_CACHE_KEY_PREFIX}"
            f"{frappe.generate_hash(length=TEMP_CACHE_KEY_HASH_LENGTH)}"
        )
        cast("RedisWrapper", frappe.cache).set_value(
            cache_key, entries_before_ce, expires_in_sec=TEMP_CACHE_EXPIRATION_SEC
        )

        download_query = urlencode(
            {
                "cache_key": cache_key,
                "default_currency": default_currency,
                "reporting_currency": reporting_currency,
            }
        )
        download_url = (
            "/api/method/karam_finance.reporting_currency.doctype."
            "reporting_currency_gle.sync.utils.export_temporal_validation_entries_csv?"
            + download_query
        )
        error_parts.extend(
            [
                '<div style="margin-bottom: 15px;">',
                f'<a class="btn btn-sm btn-primary" href="{escape_html(download_url, quote=True)}">',
                f'<i class="fa fa-download"></i> Download Full List (CSV) - {total_count} entries',
                "</a>",
                "</div>",
            ]
        )

    limit = ERROR_TABLE_DISPLAY_LIMIT
    showing_text = (
        f"(showing first {limit} of {total_count})" if total_count > limit else ""
    )
    error_parts.extend(
        [
            f"<p><strong>GL Entries Without Exchange Rates {showing_text}:</strong></p>",
            '<table class="table table-bordered table-sm" style="font-size: 90%; margin-bottom: 15px;">',
            "<thead><tr>",
            "<th>GL Entry</th><th>Date</th><th>Voucher</th>",
            "</tr></thead>",
            "<tbody>",
        ]
    )

    # Add first 20 entries
    for entry in entries_before_ce[:ERROR_TABLE_DISPLAY_LIMIT]:
        date_formatted = formatdate(entry["date"], DATE_FORMAT_DISPLAY)
        error_parts.extend(
            [
                "<tr>",
                f"<td>{entry['gle']}</td>",
                f"<td><strong>{date_formatted}</strong></td>",
                f"<td>{entry['voucher']}</td>",
                "</tr>",
            ]
        )

    if total_count > CSV_EXPORT_THRESHOLD:
        remaining = total_count - ERROR_TABLE_DISPLAY_LIMIT
        error_parts.append(
            f'<tr><td colspan="3"><em>...and {remaining} more entries '
            'not shown. Click "Download Full List" above to get all '
            "entries.</em></td></tr>"
        )

    error_parts.extend(
        [
            "</tbody>",
            "</table>",
            '<p style="margin-top: 15px;">',
            f"<strong>Action Required:</strong> Create Currency Exchange records for <strong>{default_currency} ↔ {reporting_currency}</strong> ",
            f"with dates starting from <strong>{earliest_gle_date_formatted}</strong> or earlier.",
            "</p>",
        ]
    )
    error_message = "".join(error_parts)

    # Throw validation error with HTML content
    frappe.throw(error_message, title=_("GL Entries Precede Exchange Rate Data"))


def _required_rate_date(value: str | date | None) -> date:
    result = getdate(value)
    if result is None:
        message = _("Invalid date in reporting currency exchange data: {0}").format(
            value
        )
        raise frappe.ValidationError(message)
    return result
