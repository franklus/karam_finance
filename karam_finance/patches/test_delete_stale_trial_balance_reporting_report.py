"""Tests for stale Trial Balance report cleanup patch."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from types import ModuleType

    import pytest


def _load_patch() -> ModuleType:
    """Load the patch module without relying on bench package path setup."""
    path = Path(__file__).with_name("delete_stale_trial_balance_reporting_report.py")
    spec = importlib.util.spec_from_file_location("stale_trial_balance_patch", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_execute_deletes_stale_report_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delete only the obsolete report row."""
    patch = _load_patch()
    frappe = MagicMock()
    frappe.db.exists.return_value = True
    monkeypatch.setattr(patch, "frappe", frappe)

    patch.execute()

    frappe.db.exists.assert_called_once_with("Report", patch.STALE_REPORT)
    frappe.delete_doc.assert_called_once_with(
        "Report",
        patch.STALE_REPORT,
        force=True,
        ignore_permissions=True,
    )
    frappe.clear_cache.assert_called_once_with(doctype="Report")


def test_execute_is_noop_when_stale_report_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave clean sites untouched."""
    patch = _load_patch()
    frappe = MagicMock()
    frappe.db.exists.return_value = False
    monkeypatch.setattr(patch, "frappe", frappe)

    patch.execute()

    frappe.db.exists.assert_called_once_with("Report", patch.STALE_REPORT)
    frappe.delete_doc.assert_not_called()
    frappe.clear_cache.assert_not_called()
