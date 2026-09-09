"""Guard restored report consolidation against definition and permission loss."""

from copy import deepcopy
from typing import Any
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from karam_finance.patches import (
    rename_asset_depreciation_ledger_summary,
    rename_profit_and_loss_by_cost_center,
    rename_reporting_currency_reports,
    report_rename,
)


class TestReportRename(TestCase):
    def test_rename_patches_allow_missing_v16_sidebar_table(self) -> None:
        for module in (
            rename_asset_depreciation_ledger_summary,
            rename_profit_and_loss_by_cost_center,
            rename_reporting_currency_reports,
        ):
            with self.subTest(module=module.__name__):
                stub = MagicMock()

                def table_exists(doctype: str) -> bool:
                    return doctype != "Workspace Sidebar Item"

                stub.db.table_exists.side_effect = table_exists
                with (
                    patch.object(module, "frappe", stub),
                    patch.object(module, "rename_report") as rename,
                ):
                    module.execute()
                self.assertTrue(rename.called)
                updated = {call.args[0] for call in stub.db.set_value.call_args_list}
                self.assertIn("Workspace Link", updated)
                self.assertIn("Workspace Shortcut", updated)
                self.assertNotIn("Workspace Sidebar Item", updated)

    def test_completed_rename_patches_are_noops(self) -> None:
        for module in (
            rename_asset_depreciation_ledger_summary,
            rename_profit_and_loss_by_cost_center,
            rename_reporting_currency_reports,
        ):
            with self.subTest(module=module.__name__):
                stub = MagicMock()
                stub.db.exists.return_value = False
                with (
                    patch.object(module, "frappe", stub),
                    patch.object(module, "rename_report") as rename,
                ):
                    module.execute()
                rename.assert_not_called()
                stub.db.set_value.assert_not_called()

    def prepare_reports(self) -> None:
        self.old: frappe._dict[str, Any] = frappe._dict(
            name="Old",
            report_name="Old",
            module="Reporting Currency",
            is_standard="Yes",
            report_type="Script Report",
            query=None,
            roles=[frappe._dict(role="Accounts User")],
            filters=[],
            columns=[],
        )
        self.new = deepcopy(self.old)
        self.new.update(name="New", report_name="New")
        self.stub = MagicMock()
        self.stub.db.exists.return_value = True
        self.stub.throw.side_effect = ValueError

        def get_doc(_dt: str, name: str) -> MagicMock:
            return self.document(self.old if name == "Old" else self.new)

        self.stub.get_doc.side_effect = get_doc

    @staticmethod
    def document(values: dict[str, Any]) -> MagicMock:
        doc = MagicMock()
        for key, value in values.items():
            setattr(doc, key, value)
        doc.as_dict.return_value = values
        return doc

    def run_guard(self) -> bool:
        with patch.object(report_rename, "frappe", self.stub):
            return report_rename.merge_if_equivalent("Old", "New", "Reporting Currency")

    def test_absent_target_uses_rename(self) -> None:
        self.prepare_reports()
        self.stub.db.exists.return_value = False
        self.assertFalse(self.run_guard())
        self.stub.get_doc.assert_not_called()

    def test_equivalent_reports_allow_target_role_superset(self) -> None:
        self.prepare_reports()
        self.new["roles"].append(frappe._dict(role="System Manager"))
        self.assertTrue(self.run_guard())

    def test_conflicting_definition_is_retained(self) -> None:
        self.prepare_reports()
        for field, value in (
            ("query", "select 1"),
            ("is_standard", "No"),
            ("module", "Accounts"),
            ("filters", [frappe._dict(fieldname="custom")]),
        ):
            with self.subTest(field=field):
                previous = self.new[field]
                self.new[field] = value
                with self.assertRaises(ValueError):
                    self.run_guard()
                self.new[field] = previous

    def test_missing_source_role_blocks_merge(self) -> None:
        self.prepare_reports()
        self.new["roles"].clear()
        with self.assertRaises(ValueError):
            self.run_guard()

    def test_merge_preserves_checkout_and_restores_developer_mode(self) -> None:
        self.prepare_reports()
        self.stub.conf = frappe._dict(developer_mode=1)

        def rename(*_args: object, **kwargs: object) -> None:
            self.assertEqual(self.stub.conf.developer_mode, 0)
            self.assertTrue(kwargs["merge"])

        self.stub.rename_doc.side_effect = rename
        with patch.object(report_rename, "frappe", self.stub):
            report_rename.rename_report("Old", "New", "Reporting Currency")
        self.assertEqual(self.stub.conf.developer_mode, 1)

    def test_failed_merge_restores_developer_mode(self) -> None:
        self.prepare_reports()
        self.stub.conf = frappe._dict(developer_mode=1)
        self.stub.rename_doc.side_effect = RuntimeError("failed")
        with (
            patch.object(report_rename, "frappe", self.stub),
            self.assertRaises(RuntimeError),
        ):
            report_rename.rename_report("Old", "New", "Reporting Currency")
        self.assertEqual(self.stub.conf.developer_mode, 1)
