"""Tests for stale Trial Balance report cleanup patch."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING
from unittest import TestCase
from unittest.mock import MagicMock, patch

if TYPE_CHECKING:
    from types import ModuleType


def _load_patch() -> ModuleType:
    """Load the patch module without relying on bench package path setup."""
    path = Path(__file__).with_name("delete_stale_trial_balance_reporting_report.py")
    spec = importlib.util.spec_from_file_location("stale_trial_balance_patch", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestDeleteStaleTrialBalanceReportingReport(TestCase):
    def test_execute_deletes_stale_report_when_present(self) -> None:
        """Delete only the obsolete report row."""
        patch_module = _load_patch()
        frappe = MagicMock()
        frappe.db.exists.return_value = True

        with patch.object(patch_module, "frappe", frappe):
            patch_module.execute()

        frappe.db.exists.assert_called_once_with("Report", patch_module.STALE_REPORT)
        frappe.delete_doc.assert_called_once_with(
            "Report",
            patch_module.STALE_REPORT,
            force=True,
            ignore_permissions=True,
        )
        frappe.clear_cache.assert_called_once_with(doctype="Report")

    def test_execute_is_noop_when_stale_report_is_absent(self) -> None:
        """Leave clean sites untouched."""
        patch_module = _load_patch()
        frappe = MagicMock()
        frappe.db.exists.return_value = False

        with patch.object(patch_module, "frappe", frappe):
            patch_module.execute()

        frappe.db.exists.assert_called_once_with("Report", patch_module.STALE_REPORT)
        frappe.delete_doc.assert_not_called()
        frappe.clear_cache.assert_not_called()
