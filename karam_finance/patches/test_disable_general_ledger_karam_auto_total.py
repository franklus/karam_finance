"""Tests for the General Ledger (Karam) automatic-total cleanup patch."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

if TYPE_CHECKING:
    from types import ModuleType

    import pytest


def _load_patch() -> ModuleType:
    """Load the patch without depending on Bench package discovery."""
    path = Path(__file__).with_name("disable_general_ledger_karam_auto_total.py")
    spec = importlib.util.spec_from_file_location("disable_gl_auto_total_patch", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_execute_disables_existing_automatic_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disable only the automatic total flag on the target report."""
    patch = _load_patch()
    frappe = MagicMock()
    frappe.db.exists.return_value = True
    frappe.db.get_value.return_value = 1
    monkeypatch.setattr(patch, "frappe", frappe)

    patch.execute()

    frappe.db.set_value.assert_called_once_with(
        "Report",
        patch.REPORT_NAME,
        "add_total_row",
        0,
        update_modified=False,
    )
    frappe.clear_cache.assert_called_once_with(doctype="Report")


def test_execute_leaves_missing_or_disabled_report_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remain idempotent for sites that need no repair."""
    patch = _load_patch()
    frappe = MagicMock()
    monkeypatch.setattr(patch, "frappe", frappe)

    frappe.db.exists.return_value = False
    patch.execute()
    frappe.db.get_value.assert_not_called()
    frappe.db.set_value.assert_not_called()

    frappe.reset_mock()
    frappe.db.exists.return_value = True
    frappe.db.get_value.return_value = 0
    patch.execute()
    frappe.db.set_value.assert_not_called()
    frappe.clear_cache.assert_not_called()
