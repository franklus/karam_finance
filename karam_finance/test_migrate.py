"""Installation capacity checks with database operations isolated."""

import threading
from importlib import import_module
from typing import override
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from karam_finance import hooks, migrate
from karam_finance.common import db_schema


class TestInstallCapacity(TestCase):
    @override  # noqa: V105 - unittest and Frappe test lifecycle callback.
    def setUp(self) -> None:
        self.database = MagicMock()
        self.database.local.site = "fresh-install.test"
        self.database.get_all.return_value = list[dict[str, str]]()
        self.database.db.sql.return_value = [
            frappe._dict(
                table_name=f"tab{doctype}",
                column_name=field,
                column_type="decimal(21,9)",
                is_nullable="NO",
            )
            for doctype, fields in db_schema.WIDENED_FLOAT_FIELDS.items()
            for field in fields
        ]
        self.enterContext(patch.object(db_schema, "frappe", self.database))
        self.enterContext(
            patch.object(db_schema, "_currency_columns_verified", threading.local())
        )
        self.enterContext(patch.object(db_schema.click, "echo"))
        self.owned_schema = self.enterContext(patch.object(migrate, "after_migrate"))
        self.enterContext(patch.object(migrate, "_logger"))

    def test_registered_install_hook_creates_capacity_without_migration(self) -> None:
        module, function = hooks.after_install.rsplit(".", 1)
        hook = getattr(import_module(module), function)
        hook()

        self.owned_schema.assert_called_once()
        insert = self.database.db.bulk_insert.call_args.kwargs
        records = [
            dict(zip(insert["fields"], row, strict=True)) for row in insert["values"]
        ]
        assert {
            (row["doc_type"], row["field_name"], row["property"], row["value"])
            for row in records
        } == {
            (doctype, field, prop, value)
            for doctype, fields in db_schema.WIDENED_FLOAT_FIELDS.items()
            for field in fields
            for prop, value in (("length", "30"), ("precision", "4"))
        }
        assert {
            call.args[0]
            for call in self.database.db.sql.call_args_list
            if call.args[0].startswith("ALTER TABLE")
        } == {
            f"ALTER TABLE `tab{doctype}` MODIFY `{field}` DECIMAL(30,4) NOT NULL DEFAULT 0"
            for doctype, fields in db_schema.WIDENED_FLOAT_FIELDS.items()
            for field in fields
        }

        queries = self.database.db.sql.call_count
        hook()
        assert self.database.db.sql.call_count == queries
        self.database.db.bulk_insert.assert_called_once()

    def test_capacity_failure_fails_install_and_remains_retryable(self) -> None:
        self.database.db.sql.side_effect = RuntimeError("DDL failed")
        with self.assertRaisesRegex(RuntimeError, "DDL failed"):
            migrate.after_install()
        self.database.db.sql.side_effect = None
        migrate.after_install()
        assert self.database.db.bulk_insert.call_count == 2

    def test_owned_schema_failure_prevents_capacity_setup(self) -> None:
        self.owned_schema.side_effect = RuntimeError("Custom field failed")
        with self.assertRaisesRegex(RuntimeError, "Custom field failed"):
            migrate.after_install()
        self.database.db.bulk_insert.assert_not_called()
        self.database.db.sql.assert_not_called()
