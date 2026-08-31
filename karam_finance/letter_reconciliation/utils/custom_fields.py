"""Custom field helpers for Letter Reconciliation."""

from __future__ import annotations

from karam_finance.common.custom_fields import ensure_custom_fields_from_dir


def ensure_custom_fields_letter() -> None:
    """Create or update Letter Reconciliation custom fields (deduplicated)."""
    ensure_custom_fields_from_dir(
        "letter_reconciliation", "custom_fields", label="letter_reconciliation"
    )
