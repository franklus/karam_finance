"""Tests for stale Reporting Currency report cleanup patch."""

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
    path = Path(__file__).with_name(
        "delete_stale_general_ledger_reporting_currency_report.py"
    )
    spec = importlib.util.spec_from_file_location("stale_report_patch", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestDeleteStaleGeneralLedgerReportingCurrencyReport(TestCase):
    def test_execute_is_a_noop(self) -> None:
        """Keep the new live report safe when this pending patch runs."""
        patch_module = _load_patch()
        self.assertIsNone(patch_module.execute())

    def test_pending_rename_then_cleanup_preserves_live_report(self) -> None:
        """Model both pending patches without touching a site database."""
        rename = _load_patch_file("rename_reporting_currency_reports.py")
        cleanup = _load_patch()
        reports = {"General Ledger (Reporting)"}
        frappe_stub = MagicMock()
        cleanup_frappe = MagicMock()

        def report_exists(_doctype: str, name: str) -> bool:
            return name in reports

        frappe_stub.db.exists.side_effect = report_exists

        def rename_doc(
            _doctype: str, old_name: str, new_name: str, **_kwargs: object
        ) -> None:
            reports.remove(old_name)
            reports.add(new_name)

        def rename_report(old: str, new: str, _module: str) -> None:
            rename_doc("Report", old, new)

        frappe_stub.rename_doc.side_effect = rename_doc

        with (
            patch.object(rename, "frappe", frappe_stub),
            patch.object(
                rename,
                "rename_report",
                side_effect=rename_report,
            ),
            patch.object(cleanup, "frappe", cleanup_frappe, create=True),
        ):
            rename.execute()
            cleanup.execute()

        self.assertEqual(reports, {"General Ledger (Reporting Currency)"})
        frappe_stub.db.set_value.assert_any_call(
            "Workspace Link",
            {
                "link_to": "General Ledger (Reporting Currency)",
                "label": "General Ledger (Reporting)",
            },
            "label",
            "General Ledger (Reporting Currency)",
        )
        cleanup_frappe.db.exists.assert_not_called()
        cleanup_frappe.db.set_value.assert_not_called()
        cleanup_frappe.delete_doc.assert_not_called()
        cleanup_frappe.clear_cache.assert_not_called()


def _load_patch_file(filename: str) -> ModuleType:
    """Load a sibling patch without relying on Bench package discovery."""
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
