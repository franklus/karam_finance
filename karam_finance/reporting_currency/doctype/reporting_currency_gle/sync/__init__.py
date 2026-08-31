"""Reporting Currency GLE sync engine.

This module contains utilities for synchronising GL Entry records to
the Reporting Currency GLE table.

Modules:
    orchestrator: Main sync orchestration and entry points
    data_fetch: GL Entry fetching and cleanup
    phases: The five sync phase functions
    conversion: Amount conversion logic
    validation: Settings and coverage validation
    exchange_rates: Exchange rate timeline and lookup
    utils: Progress publishing, hashing, CSV export
    doe: DOE (Difference of Exchange) computation
    reconcile_gl_entry_links: Fix broken GL Entry links
"""

# Re-export main entry points for backwards compatibility
from karam_finance.common.db_schema import ensure_currency_columns_capacity

from .orchestrator import (
    build_exchange_rate_timeline,
    delete_all_entries,
    enqueue_reporting_currency_sync,
    export_missing_currency_gl_entries_csv,
    export_temporal_validation_entries_csv,
    get_company_default_currency,
    get_gl_entry_stable_hash,
    on_gl_entry_rename,
    publish_sync_progress,
    run_reporting_currency_sync_job,
    sync_reporting_currency_entries,
    validate_currency_exchange_coverage,
    validate_settings,
    validate_temporal_coverage,
)
from .phases import (
    run_conversion_phase,
    run_deletion_phase,
    run_insertion_phase,
    run_temporal_reconciliation_phase,
    run_validation_phase,
)

__all__ = [
    "build_exchange_rate_timeline",
    "delete_all_entries",
    "enqueue_reporting_currency_sync",
    "ensure_currency_columns_capacity",
    "export_missing_currency_gl_entries_csv",
    "export_temporal_validation_entries_csv",
    "get_company_default_currency",
    "get_gl_entry_stable_hash",
    "on_gl_entry_rename",
    "publish_sync_progress",
    "run_conversion_phase",
    "run_deletion_phase",
    "run_insertion_phase",
    "run_reporting_currency_sync_job",
    "run_temporal_reconciliation_phase",
    "run_validation_phase",
    "sync_reporting_currency_entries",
    "validate_currency_exchange_coverage",
    "validate_settings",
    "validate_temporal_coverage",
]
