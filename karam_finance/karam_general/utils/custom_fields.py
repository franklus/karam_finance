"""Custom field helpers for Karam General."""

from __future__ import annotations

from karam_finance.common.custom_fields import ensure_custom_fields_from_dir
from karam_finance.karam_general.utils.item_price_on_rate_mismatch import (
    sync_stock_settings_rate_mismatch_rows_for_migrate,
)


def ensure_custom_fields_general() -> None:
    """Create or update Karam General custom fields from validated fixtures.

    Run after Karam Series fixtures so anchors like ``karam_series_sb2`` exist.
    Missing optional DocTypes are skipped by the shared helper; malformed
    fixtures and application failures propagate to the migration hook.
    """
    ensure_custom_fields_from_dir(
        "karam_general",
        "custom_fields",
        label="karam_general",
    )
    sync_stock_settings_rate_mismatch_rows_for_migrate()
