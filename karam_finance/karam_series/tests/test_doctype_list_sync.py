"""Tests for the doctype list reconciliation helper."""

from __future__ import annotations

from karam_finance.karam_series.utils.doctype_list_sync import reconcile_doctype_rows


class TestDoctypeListSync:
    """Unit tests for doctype list synchronisation."""

    def test_reconcile_doctype_rows_sorts_and_preserves_flags(self) -> None:
        """Ensure rows are sorted and flags preserved."""
        curated = ["Sales Invoice", "Purchase Order", "Asset"]
        existing_flags = {"Sales Invoice": 1, "Purchase Order": 0, "Ignored": 1}

        rows = reconcile_doctype_rows(curated, existing_flags)

        # Expect alphabetical order by doctype name
        names = [r["doctype_name"] for r in rows]
        assert names == ["Asset", "Purchase Order", "Sales Invoice"]

        # Expect flags preserved where present, default 0 otherwise
        flags = {r["doctype_name"]: r["karam_series_mandatory"] for r in rows}
        assert flags == {"Asset": 0, "Purchase Order": 0, "Sales Invoice": 1}
