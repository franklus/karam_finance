"""Typed carriers for reporting-currency sync boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SyncSnapshot:
    """Foreground validation/rate inputs and their source cutoff."""

    currency_coverage: dict[str, Any]
    rate_timeline: list[dict[str, Any]]
    default_currency: str
    cutoff: str
    # None also covers snapshots deserialised from jobs queued before this field.
    reporting_currency: str | None = None


@dataclass(frozen=True)
class InsertionContext:
    """Insertion mode paired with the safe source-acquisition watermark."""

    is_incremental: bool
    cutoff: str
