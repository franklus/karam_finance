"""Migration hook ordering and failure-boundary contracts without a site."""

from __future__ import annotations

import importlib
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from typing import override
from unittest import TestCase
from unittest.mock import MagicMock, call, patch

import frappe
import pytest


class TestMigrateContracts(TestCase):
    """Keep owned schema operations ordered with explicit rollback on failure."""

    @override
    def setUp(self) -> None:
        with patch.object(frappe, "logger", return_value=MagicMock()):
            self.module = importlib.import_module("karam_finance.migrate")
            self.general_fields = importlib.import_module(
                "karam_finance.karam_general.utils.custom_fields"
            )
            self.series_fields = importlib.import_module(
                "karam_finance.karam_series.utils.custom_fields"
            )
            self.series_settings = importlib.import_module(
                "karam_finance.karam_series.doctype.karam_series_settings.karam_series_settings"
            )
            self.letter_fields = importlib.import_module(
                "karam_finance.letter_reconciliation.utils.custom_fields"
            )

        self.frappe = MagicMock()
        self.enterContext(patch.object(self.module, "frappe", self.frappe))
        self.enterContext(patch.object(self.module, "_logger"))

    @contextmanager
    def _schema_steps(self, calls: MagicMock) -> Generator[None]:
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(self.series_fields, "ensure_custom_fields", calls.series)
            )
            stack.enter_context(
                patch.object(
                    self.general_fields,
                    "ensure_custom_fields_general",
                    calls.general,
                )
            )
            stack.enter_context(
                patch.object(
                    self.letter_fields,
                    "ensure_custom_fields_letter",
                    calls.letter,
                )
            )
            stack.enter_context(
                patch.object(self.series_settings, "sync_doctype_list", calls.sync)
            )
            stack.enter_context(
                patch.object(
                    self.series_fields,
                    "project_requirement_policy",
                    calls.project,
                )
            )
            yield

    def test_after_migrate_dispatches_owned_schema_steps_in_order(self) -> None:
        calls = MagicMock()
        with self._schema_steps(calls):
            self.module.after_migrate()

        assert calls.mock_calls == [
            call.series(),
            call.general(),
            call.letter(),
            call.sync(),
            call.project(),
        ]
        self.frappe.db.rollback.assert_not_called()

    def test_after_migrate_rolls_back_and_reraises_each_step_failure(self) -> None:
        step_names = ["series", "general", "letter", "sync", "project"]
        for failing_step, failing_name in enumerate(step_names):
            with self.subTest(failing_step=failing_name):
                calls = MagicMock()
                getattr(calls, failing_name).side_effect = RuntimeError("schema failed")

                with (
                    self._schema_steps(calls),
                    pytest.raises(
                        RuntimeError,
                        match="schema failed",
                    ),
                ):
                    self.module.after_migrate()

                assert calls.mock_calls == [
                    getattr(call, name)() for name in step_names[: failing_step + 1]
                ]
                self.frappe.db.rollback.assert_called_once_with()
                self.frappe.db.rollback.reset_mock()

    def test_after_install_runs_migration_before_capacity_setup(self) -> None:
        calls = MagicMock()
        with (
            patch.object(self.module, "after_migrate", calls.migrate),
            patch.object(
                self.module,
                "ensure_currency_columns_capacity",
                calls.capacity,
            ),
        ):
            self.module.after_install()

        assert calls.mock_calls == [call.migrate(), call.capacity()]

    def test_after_install_propagates_migration_failure_before_capacity_setup(
        self,
    ) -> None:
        capacity = MagicMock()
        with (
            patch.object(
                self.module,
                "after_migrate",
                side_effect=RuntimeError("migration failed"),
            ),
            patch.object(self.module, "ensure_currency_columns_capacity", capacity),
            pytest.raises(RuntimeError, match="migration failed"),
        ):
            self.module.after_install()

        capacity.assert_not_called()
