"""Confirmed reporting-currency changes publish settings and ledger atomically."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any, cast

import frappe
from frappe import _
from frappe.query_builder.functions import Coalesce
from frappe.utils import flt, get_datetime, now
from frappe.utils.background_jobs import JobStatus, get_job_status

from karam_finance.common.db_schema import ensure_currency_columns_capacity
from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.doe import (
    compute_doe_inline,
)
from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.orchestrator import (
    _publish_sync_result,
    sync_reporting_currency_entries,
)
from karam_finance.reporting_currency.doctype.reporting_currency_settings.reporting_currency_settings import (
    ReportingCurrencySettings,
)
from karam_finance.reporting_currency.ledger_lock import ledger_operation

SETTINGS = "Reporting Currency Settings"
LEDGER = "Reporting Currency GLE"
CURRENCY_CHANGE_ATTEMPT_TTL = 3600


class _SyncStatusCacheUnavailable(RuntimeError):
    """Raised when the site cache cannot track a currency-change attempt."""


@frappe.whitelist()
def enqueue_currency_change(
    requested_currency: str,
    expected_modified: str | None,
    *,
    confirmed: bool = False,
    doe_rates: dict[str, Any] | str | None = None,
) -> dict[str, str]:
    """Queue only an explicitly confirmed change against the displayed revision."""
    _check_permissions()
    if not confirmed:
        frappe.throw(_("Confirm the reporting currency change before rebuilding."))
    settings = _validate_request(
        requested_currency, expected_modified, doe_rates=doe_rates
    )
    rates = {row.name: row.exchange_rate for row in settings.rc_parameters}
    identity = "|".join(
        (
            requested_currency,
            str(settings.modified),
            frappe.session.user,
            json.dumps(rates, sort_keys=True),
        )
    )
    base_event = (
        "rc_currency_change_" + hashlib.sha256(identity.encode()).hexdigest()[:16]
    )
    progress_event = _get_currency_change_attempt(base_event)
    done_event = progress_event + "_done"
    frappe.enqueue(
        "karam_finance.reporting_currency.currency_change.run_currency_change_job",
        queue="long",
        timeout=3600,
        job_id=progress_event,
        deduplicate=True,
        enqueue_after_commit=True,
        requested_currency=requested_currency,
        expected_modified=expected_modified,
        progress_event=progress_event,
        done_event=done_event,
        user=frappe.session.user,
        doe_rates=rates,
    )
    return {
        "job_id": progress_event,
        "progress_event": progress_event,
        "done_event": done_event,
    }


def _currency_change_attempt_key(base_event: str) -> str:
    return f"rc_currency_change_attempt:{base_event}"


def _get_live_currency_change_attempt(cache: Any, attempt_key: str) -> str | None:
    current = cache.get_value(attempt_key)
    if not current:
        return None

    status = get_job_status(current)
    if status in (JobStatus.QUEUED, JobStatus.STARTED):
        return current

    terminal = cache.get_value(f"rc_sync_result:{current}_done")
    if terminal is None and status is None:
        # enqueue_after_commit can leave the pointer visible before RQ does.
        return current
    return None


def _get_new_currency_change_attempt(cache: Any, base_event: str) -> str:
    base_status = get_job_status(base_event)
    base_result = cache.get_value(f"rc_sync_result:{base_event}_done")
    if base_status in (JobStatus.QUEUED, JobStatus.STARTED) or (
        base_status is None and base_result is None
    ):
        return base_event
    return f"{base_event}_{frappe.generate_hash(length=8)}"


def _get_currency_change_attempt(base_event: str) -> str:
    """Reuse only a live attempt; terminal results get a fresh event identity."""
    cache = frappe.cache
    if cache is None:
        raise _SyncStatusCacheUnavailable

    attempt_key = _currency_change_attempt_key(base_event)
    progress_event = _get_live_currency_change_attempt(cache, attempt_key)
    if progress_event is None:
        progress_event = _get_new_currency_change_attempt(cache, base_event)
    cache.set_value(
        attempt_key,
        progress_event,
        expires_in_sec=CURRENCY_CHANGE_ATTEMPT_TTL,
    )
    return progress_event


# Preserve the existing queued-worker arguments when adding explicit DOE rates.
def run_currency_change_job(  # noqa: PLR0913
    requested_currency: str,
    expected_modified: str | None,
    *,
    progress_event: str,
    done_event: str,
    user: str,
    doe_rates: dict[str, Any] | str | None = None,
) -> dict[str, Any]:
    """Re-read and rebuild under one lock and one transaction, then notify the user."""
    started = time.monotonic()
    try:
        with ledger_operation():
            _check_permissions()
            # DDL must finish before the transaction whose atomicity is promised.
            ensure_currency_columns_capacity()
            frappe.db.begin()
            cutoff = now()  # before the first transaction read, including settings
            settings = _validate_request(
                requested_currency, expected_modified, doe_rates=doe_rates
            )
            # Persist replacement rates in the same transaction as the new currency
            # and rebuilt ledger. No old rate is silently carried across currencies.
            settings.save()
            result = _rebuild(requested_currency, progress_event, user, cutoff=cutoff)
            frappe.db.commit()  # nosemgrep — publish the entire currency transition
    except Exception as exc:
        frappe.log_error(
            title="Reporting Currency Change Failed", message=frappe.get_traceback()
        )
        _publish_sync_result(
            done_event,
            user,
            {
                "status": "error",
                "title": _("Currency Change Failed"),
                "message": str(exc),
            },
        )
        raise
    result.update(
        status="success",
        reporting_currency=requested_currency,
        duration_seconds=round(time.monotonic() - started, 2),
    )
    _publish_sync_result(done_event, user, result)
    return result


def _check_permissions() -> None:
    frappe.only_for("System Manager")
    frappe.has_permission(SETTINGS, "write", throw=True)
    frappe.has_permission(LEDGER, "write", throw=True)


def _validate_request(
    requested_currency: str,
    expected_modified: str | None,
    *,
    doe_rates: dict[str, Any] | str | None = None,
) -> ReportingCurrencySettings:
    settings = cast("ReportingCurrencySettings", frappe.get_single(SETTINGS))
    actual = get_datetime(settings.modified) if settings.modified else None
    expected = get_datetime(expected_modified) if expected_modified else None
    if actual != expected:
        frappe.throw(
            _("Reporting Currency Settings changed. Reload the form and confirm again.")
        )
    if not requested_currency or not frappe.db.exists("Currency", requested_currency):
        frappe.throw(_("Select an existing reporting currency."))
    if requested_currency == settings.reporting_currency:
        frappe.throw(
            _(
                "The requested reporting currency is already configured. Reload the form."
            )
        )
    _validate_manual_entries(requested_currency)
    _replace_doe_rates(settings, doe_rates)
    settings.validate()
    return settings


def _replace_doe_rates(
    settings: ReportingCurrencySettings, doe_rates: dict[str, Any] | str | None
) -> None:
    rates = frappe.parse_json(doe_rates) if isinstance(doe_rates, str) else doe_rates
    expected = {row.name for row in settings.rc_parameters}
    if rates is None and not expected:
        return
    if not isinstance(rates, dict) or set(rates) != expected:
        frappe.throw(
            _("Provide replacement DOE rates for every current DOE parameter.")
        )
        return
    for row in settings.rc_parameters:
        value = rates[row.name]
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            frappe.throw(_("Replacement DOE rates must be numeric values."))
        row.exchange_rate = flt(value)


def _validate_manual_entries(requested_currency: str) -> None:
    ledger = frappe.qb.DocType(LEDGER)

    names = (
        frappe.qb.from_(ledger)
        .select(ledger.name)
        .where(
            (ledger.manual_entry == 1)
            & (Coalesce(ledger.reporting_currency, "") != requested_currency)
        )
        .orderby(ledger.name)
        .limit(5)
    ).run(pluck=True)
    if names:
        frappe.throw(
            _(
                "Manual reporting entries have a missing or different currency: {0}. "
                "Resolve these entries separately before changing currency; they will not be converted or deleted."
            ).format(", ".join(names))
        )


def _rebuild(
    requested_currency: str, progress_event: str, user: str, *, cutoff: str
) -> dict[str, Any]:
    # The worker owns the lock. Ordinary Document.save never bypasses its guard.
    frappe.db.set_single_value(
        SETTINGS,
        {
            "reporting_currency": requested_currency,
            "last_sync_timestamp": None,
            "last_ce_sync_timestamp": None,
        },
    )
    result = sync_reporting_currency_entries(progress_event, user, source_cutoff=cutoff)
    doe_result = compute_doe_inline(progress_event, user)
    if not doe_result.get("success") and frappe.db.exists(LEDGER, {}):
        frappe.throw(str(doe_result.get("message") or _("DOE generation failed.")))
    result.update(
        doe_accounts_processed=doe_result.get("accounts_processed", 0),
        doe_records_created=doe_result.get("records_created", 0),
    )
    return result
