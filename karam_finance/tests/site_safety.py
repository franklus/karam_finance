"""Fail-closed guard for native tests that commit reporting-ledger fixtures."""

from __future__ import annotations

from typing import Any

import frappe

DISPOSABLE_SITE_MARKER = "karam_finance_disposable_test_site"
CRITICAL_BUSINESS_DOCTYPES = (
    "Company",
    "Account",
    "Fiscal Year",
    "GL Entry",
    "Reporting Currency GLE",
    "Currency Exchange",
)
_ALLOW_TESTS_ERROR = "Native finance review tests require site config allow_tests=true."
_MARKER_ERROR = (
    f"Native finance review tests require site config {DISPOSABLE_SITE_MARKER}=true."
)


class UnsafeNativeTestSite(RuntimeError):
    """Raised before a committing native test can write to a shared site."""


def require_disposable_test_site() -> None:
    """Require explicit opt-in and an empty business database before fixtures."""
    _require_test_config()

    site = getattr(frappe.local, "site", None)
    if not site:
        message = "Native finance review tests require an initialised site."
        raise UnsafeNativeTestSite(message)

    _require_empty_business_tables(site)


def _require_test_config() -> None:
    if not _enabled_config("allow_tests"):
        raise UnsafeNativeTestSite(_ALLOW_TESTS_ERROR)
    if not _enabled_config(DISPOSABLE_SITE_MARKER):
        raise UnsafeNativeTestSite(_MARKER_ERROR)


def _require_empty_business_tables(site: str) -> None:
    populated: dict[str, int] = {}
    for doctype in CRITICAL_BUSINESS_DOCTYPES:
        try:
            count = frappe.db.count(doctype)
        except Exception as exc:
            message = f"Could not verify that {doctype} is empty on {site}."
            raise UnsafeNativeTestSite(message) from exc
        if count:
            populated[doctype] = count
    if populated:
        details = ", ".join(
            f"{doctype}={count}" for doctype, count in populated.items()
        )
        message = (
            "Native finance review tests require an empty disposable site; "
            f"{site} has {details}."
        )
        raise UnsafeNativeTestSite(message)


def _enabled_config(key: str) -> bool:
    """Accept the bool forms emitted by Bench while rejecting missing config."""
    config: Any = frappe.conf
    value = config.get(key) if config is not None else None
    if value is True or value == 1:
        return True
    return isinstance(value, str) and value.lower() in {"1", "true"}
