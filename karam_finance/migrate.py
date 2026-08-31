"""Installation and migration hooks for Karam Finance."""

from __future__ import annotations

import frappe

_logger = frappe.logger("karam_finance.migrate", allow_site=True)


def after_migrate() -> None:
    """Apply owned schema and persisted Karam Series policy after migration.

    The hook intentionally lets failures escape. Frappe can then mark the
    migration as failed and the operator receives an actionable traceback;
    swallowing an owned-schema error would report a healthy site with missing
    fields. The database transaction is rolled back before the error is
    re-raised so an application failure cannot be mistaken for success.
    """
    _logger.info("after_migrate: start")

    from karam_finance.karam_general.utils.custom_fields import (
        ensure_custom_fields_general,
    )
    from karam_finance.karam_series.doctype.karam_series_settings.karam_series_settings import (
        sync_doctype_list,
    )
    from karam_finance.karam_series.utils.custom_fields import (
        ensure_custom_fields,
        project_requirement_policy,
    )
    from karam_finance.letter_reconciliation.utils.custom_fields import (
        ensure_custom_fields_letter,
    )

    try:
        _logger.info("Creating karam_series fields...")
        ensure_custom_fields()
        _logger.info("Creating karam_general fields...")
        ensure_custom_fields_general()
        _logger.info("Creating letter_reconciliation fields...")
        ensure_custom_fields_letter()
        _logger.info("Synchronising Karam Series doctype list...")
        sync_doctype_list()
        _logger.info("Projecting Karam Series mandatory policy...")
        project_requirement_policy()
    except Exception:
        frappe.db.rollback()
        _logger.exception("after_migrate: owned schema application failed")
        raise

    _logger.info("after_migrate: done")


def after_install() -> None:
    """Apply the same idempotent owned schema during app installation."""
    _logger.info("after_install: start")
    after_migrate()
    _logger.info("after_install: done")
