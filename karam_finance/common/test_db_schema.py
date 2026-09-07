"""Schema capacity regressions; all database operations are mocked."""

import threading
from functools import partial
from typing import Any, override
from unittest import TestCase
from unittest.mock import MagicMock, patch

from frappe import _dict

from karam_finance.common import db_schema

JOURNAL_TOTALS = {
    "total_debit",
    "total_credit",
    "difference",
    "total_amount",
    "write_off_amount",
}


class TestSchemaCapacity(TestCase):
    @override  # noqa: V105 - unittest and Frappe test lifecycle callback.
    def setUp(self) -> None:
        self.mock_frappe = MagicMock()
        self.mock_frappe.local.site = "first.test"
        self.mock_frappe.db.sql.return_value = list[dict[str, Any]]()
        self.enterContext(patch.object(db_schema, "frappe", self.mock_frappe))
        self.enterContext(
            patch.object(db_schema, "_currency_columns_verified", threading.local())
        )
        self.enterContext(patch.object(db_schema.click, "echo"))

    def test_each_site_is_verified_once(self) -> None:
        with patch.object(db_schema, "_ensure_property_setters") as setters:
            db_schema.ensure_currency_columns_capacity()
            first_queries = self.mock_frappe.db.sql.call_count
            self.mock_frappe.local.site = "second.test"
            db_schema.ensure_currency_columns_capacity()
            self.mock_frappe.local.site = "first.test"
            db_schema.ensure_currency_columns_capacity()
        assert setters.call_count == 2
        assert self.mock_frappe.db.sql.call_count == 2 * first_queries
        assert first_queries == 1

    def test_failed_site_check_is_retried(self) -> None:
        self.mock_frappe.db.sql.side_effect = RuntimeError("DDL failed")
        with patch.object(db_schema, "_ensure_property_setters") as setters:
            with self.assertRaisesRegex(RuntimeError, "DDL failed"):
                db_schema.ensure_currency_columns_capacity()
            self.mock_frappe.db.sql.side_effect = None
            db_schema.ensure_currency_columns_capacity()
            db_schema.ensure_currency_columns_capacity()
        assert setters.call_count == 2

    def test_journal_parent_columns_are_widened(self) -> None:
        rows = [
            _dict(
                table_name="tabJournal Entry",
                column_name=field,
                column_type="decimal(21,2)",
                is_nullable="NO",
            )
            for field in JOURNAL_TOTALS
        ]

        def query(_sql: str, *args: Any, **_kwargs: Any) -> list[Any]:
            return rows if args and "tabJournal Entry" in args[0]["tables"] else []

        self.mock_frappe.db.sql.side_effect = query
        with patch.object(db_schema, "_ensure_property_setters"):
            db_schema.ensure_currency_columns_capacity()
        alterations = {
            call.args[0]
            for call in self.mock_frappe.db.sql.call_args_list
            if call.args[0].startswith("ALTER TABLE")
        }
        assert alterations == {
            f"ALTER TABLE `tabJournal Entry` MODIFY `{field}` DECIMAL(30,4) NOT NULL DEFAULT 0"
            for field in JOURNAL_TOTALS
        }

    def test_journal_parent_property_setters_preserve_capacity(self) -> None:
        self.mock_frappe.get_all.return_value = list[dict[str, Any]]()
        db_schema._ensure_property_setters()
        insert = self.mock_frappe.db.bulk_insert.call_args.kwargs
        records = [
            dict(zip(insert["fields"], row, strict=True)) for row in insert["values"]
        ]
        overrides = {
            (row["field_name"], row["property"], row["value"])
            for row in records
            if row["doc_type"] == "Journal Entry"
        }
        assert overrides == {
            (field, prop, value)
            for field in JOURNAL_TOTALS
            for prop, value in (("length", "30"), ("precision", "4"))
        }

    def test_property_setters_update_only_changed_values(self) -> None:
        targets = db_schema._get_property_setter_targets()
        changed, missing = targets[:2]
        self.mock_frappe.get_all.return_value = [
            {
                "name": target["name"],
                "value": "21" if target == changed else target["value"],
            }
            for target in targets
            if target != missing
        ]
        db_schema._ensure_property_setters()
        self.mock_frappe.db.bulk_update.assert_called_once_with(
            "Property Setter", {changed["name"]: {"value": changed["value"]}}
        )
        values = self.mock_frappe.db.bulk_insert.call_args.kwargs["values"]
        assert len(values) == 1
        assert values[0][0] == missing["name"]
        self.mock_frappe.db.commit.assert_called_once()

    def test_nullable_column_is_cleaned_and_committed_before_alter(self) -> None:
        rows = [
            _dict(column_name="debit", column_type="decimal(30,4)", is_nullable="YES"),
            _dict(column_name="credit", column_type="decimal(30,4)", is_nullable="NO"),
        ]
        db_schema._ensure_table_columns_capacity(
            "tabGL Entry",
            ["debit", "credit", "missing"],
            {("tabGL Entry", row["column_name"]): row for row in rows},
        )
        calls = self.mock_frappe.db.mock_calls
        assert [item[0] for item in calls] == ["sql", "commit", "sql"]
        assert (
            calls[0].args[0]
            == "UPDATE `tabGL Entry` SET `debit` = 0 WHERE `debit` IS NULL"
        )
        assert calls[2].args[0] == (
            "ALTER TABLE `tabGL Entry` MODIFY `debit` DECIMAL(30,4) NOT NULL DEFAULT 0"
        )

    def test_native_reporting_amounts_have_persistent_column_capacity(self) -> None:
        fields = {"debit_in_reporting_currency", "credit_in_reporting_currency"}
        self.mock_frappe.get_all.return_value = [
            {"name": f"GL Entry-{field}-length", "value": "30"} for field in fields
        ]

        self.mock_frappe.db.sql.side_effect = partial(_native_reporting_columns, fields)
        db_schema.ensure_currency_columns_capacity()
        insert = self.mock_frappe.db.bulk_insert.call_args.kwargs
        records = [
            dict(zip(insert["fields"], row, strict=True)) for row in insert["values"]
        ]
        overrides = {
            (row["field_name"], row["property"], row["value"])
            for row in records
            if row["doc_type"] == "GL Entry" and row["field_name"] in fields
        }
        # Existing manual length overrides are reused; missing precision is created.
        assert overrides == {(field, "precision", "4") for field in fields}
        self.mock_frappe.db.bulk_update.assert_not_called()
        alterations = {
            item.args[0]
            for item in self.mock_frappe.db.sql.call_args_list
            if item.args[0].startswith("ALTER TABLE")
        }
        assert alterations == {
            f"ALTER TABLE `tabGL Entry` MODIFY `{field}` DECIMAL(30,4) NOT NULL DEFAULT 0"
            for field in fields
        }

    def test_batched_metadata_keeps_same_named_columns_separate(self) -> None:
        self.mock_frappe.db.sql.return_value = [
            _dict(
                table_name="tabGL Entry",
                column_name="debit",
                column_type="decimal(30,4)",
                is_nullable="NO",
            ),
            _dict(
                table_name="tabJournal Entry Account",
                column_name="debit",
                column_type="decimal(21,2)",
                is_nullable="NO",
            ),
        ]
        with patch.object(db_schema, "_ensure_property_setters"):
            db_schema.ensure_currency_columns_capacity()
        calls = self.mock_frappe.db.sql.call_args_list
        assert len(calls) == 2
        assert len(calls[0].args[1]["tables"]) == 5
        assert calls[1].args[0] == (
            "ALTER TABLE `tabJournal Entry Account` MODIFY `debit` DECIMAL(30,4) NOT NULL DEFAULT 0"
        )


def _native_reporting_columns(
    fields: set[str], _sql: str, *args: Any, **_kwargs: Any
) -> list[Any]:
    if not args or "tabGL Entry" not in args[0]["tables"]:
        return []
    return [
        _dict(
            table_name="tabGL Entry",
            column_name=field,
            column_type="decimal(21,2)",
            is_nullable="NO",
        )
        for field in fields
        if field in args[0]["columns"]
    ]
