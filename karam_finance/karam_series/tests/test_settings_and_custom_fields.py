"""Behavioural contracts for Karam Series settings projection."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, call, patch

import frappe
import pytest
from frappe.query_builder.builder import MariaDB
from pypika.queries import QueryBuilder

from karam_finance.karam_series.doctype.karam_series_settings import (
    karam_series_settings as settings_controller,
)
from karam_finance.karam_series.utils import custom_fields


def _settings(
    *rows: object, hide_fields: object = 0
) -> settings_controller.KaramSeriesSettings:
    settings = object.__new__(settings_controller.KaramSeriesSettings)
    settings.__dict__.update(doctype_list=list(rows), hide_fields=hide_fields)
    return settings


def test_settings_projection_groups_field_updates_and_refreshes_installed_metadata() -> (
    None
):
    rows = [
        SimpleNamespace(doctype_name="Sales Invoice", karam_series_mandatory=1),
        SimpleNamespace(doctype_name="Asset", karam_series_mandatory=0),
    ]
    settings = _settings(*rows, hide_fields="1")
    batch_update = Mock()
    db = Mock()
    db.get_list.return_value = ["Sales Invoice"]
    clear_cache = Mock()

    with (
        patch.object(settings, "_batch_update_custom_fields", batch_update),
        patch.object(settings_controller.frappe, "db", db),
        patch.object(settings_controller.frappe, "clear_cache", clear_cache),
    ):
        settings.on_update()

    assert batch_update.call_args_list == [
        call(
            "hidden",
            1,
            [
                {"fieldname": "karam_series", "dt": "Sales Invoice"},
                {"fieldname": "translation", "dt": "Sales Invoice"},
                {"fieldname": "karam_series", "dt": "Asset"},
                {"fieldname": "translation", "dt": "Asset"},
            ],
        ),
        call(
            "reqd",
            1,
            [{"fieldname": "karam_series", "dt": "Sales Invoice"}],
        ),
        call("reqd", 0, [{"fieldname": "karam_series", "dt": "Asset"}]),
    ]
    clear_cache.assert_called_once_with(doctype="Sales Invoice")


def test_settings_projection_skips_empty_rows_and_empty_update_groups() -> None:
    settings = _settings()

    with patch.object(settings, "_execute_bulk_field_updates") as execute:
        settings.on_update()

    execute.assert_not_called()
    settings._execute_bulk_field_updates([])


def test_get_and_sync_doctype_list_only_persist_curated_rows() -> None:
    settings = Mock()
    settings.get.return_value = [
        SimpleNamespace(doctype_name="Sales Invoice", karam_series_mandatory="1"),
        SimpleNamespace(doctype_name="Ignored", karam_series_mandatory=1),
    ]
    db = Mock()
    db.get_list.return_value = ["Sales Invoice", "Asset"]

    with (
        patch.object(settings_controller.frappe, "db", db),
        patch.object(settings_controller.frappe, "get_single", return_value=settings),
    ):
        settings_controller.sync_doctype_list()

    settings.set.assert_called_once_with(
        "doctype_list",
        [
            {"doctype_name": "Asset", "karam_series_mandatory": 0},
            {"doctype_name": "Sales Invoice", "karam_series_mandatory": 1},
        ],
    )
    settings.save.assert_called_once_with()
    db.get_list.assert_called_once_with(
        "DocType",
        filters={"name": ["in", settings_controller.KARAM_DOCTYPES]},
        fields=["name"],
        pluck="name",
        ignore_permissions=True,
    )


def test_populate_doctype_list_announces_completed_sync() -> None:
    with (
        patch.object(settings_controller, "sync_doctype_list") as sync,
        patch.object(settings_controller.frappe, "msgprint") as msgprint,
        patch.object(settings_controller, "_", str),
    ):
        settings_controller.populate_doctype_list()

    sync.assert_called_once_with()
    msgprint.assert_called_once_with("Doctype list updated")


@pytest.mark.parametrize(
    ("doctype_name", "expected_message"),
    [
        ("Unsupported", "Unsupported Karam Series doctype"),
        ("Sales Invoice", "Doctype is not present"),
    ],
)
def test_mandatory_endpoint_rejects_unknown_or_unsynchronised_doctypes(
    doctype_name: str, expected_message: str
) -> None:
    settings = Mock()
    settings.get.return_value = cast(list[object], [])

    with (
        patch.object(settings_controller.frappe, "has_permission"),
        patch.object(settings_controller.frappe, "get_single", return_value=settings),
    ):
        result = settings_controller.update_field_mandatory_status(doctype_name, 1)

    assert result["success"] is False
    assert expected_message in result["message"]
    settings.save.assert_not_called()


def test_mandatory_endpoint_logs_unexpected_save_failure() -> None:
    row = SimpleNamespace(doctype_name="Sales Invoice", karam_series_mandatory=0)
    settings = Mock()
    settings.get.return_value = [row]
    settings.save.side_effect = RuntimeError("database unavailable")

    with (
        patch.object(settings_controller.frappe, "has_permission"),
        patch.object(settings_controller.frappe, "get_single", return_value=settings),
        patch.object(settings_controller.frappe, "get_traceback", return_value="trace"),
        patch.object(settings_controller.frappe, "log_error") as log_error,
        patch.object(settings_controller, "_", str),
    ):
        result = settings_controller.update_field_mandatory_status("Sales Invoice", "1")

    assert result == {
        "success": False,
        "message": "Unexpected error while updating mandatory status",
    }
    log_error.assert_called_once_with("trace", "Update Field Mandatory Status Error")


def test_ensure_custom_fields_delegates_to_shared_fixture_loader() -> None:
    with patch.object(custom_fields, "ensure_custom_fields_from_dir") as ensure:
        custom_fields.ensure_custom_fields()

    ensure.assert_called_once_with(
        "karam_series", "custom_fields", label="karam_series"
    )


def test_project_requirement_policy_updates_changed_installed_fields_once() -> None:
    settings = SimpleNamespace(
        doctype_list=[
            SimpleNamespace(doctype_name="Sales Invoice", karam_series_mandatory=1),
            {"doctype_name": "Asset", "karam_series_mandatory": 0},
            SimpleNamespace(doctype_name="Optional", karam_series_mandatory=1),
        ]
    )
    get_all = Mock(
        side_effect=[
            ["Sales Invoice", "Asset"],
            [
                {"name": "Custom Field-SI", "dt": "Sales Invoice", "reqd": 0},
                {"name": "Custom Field-Asset", "dt": "Asset", "reqd": 0},
            ],
        ]
    )
    db = Mock()
    clear_cache = Mock()

    with (
        patch.object(custom_fields.frappe, "get_single", return_value=settings),
        patch.object(custom_fields.frappe, "get_all", get_all),
        patch.object(custom_fields.frappe, "db", db),
        patch.object(custom_fields.frappe, "clear_cache", clear_cache),
    ):
        custom_fields.project_requirement_policy()

    db.bulk_update.assert_called_once_with(
        "Custom Field", {"Custom Field-SI": {"reqd": 1}}
    )
    clear_cache.assert_called_once_with(doctype="Sales Invoice")


def test_project_requirement_policy_stops_before_schema_lookup_without_installed_targets() -> (
    None
):
    settings = SimpleNamespace(
        doctype_list=[
            SimpleNamespace(doctype_name="Optional", karam_series_mandatory=0)
        ]
    )
    get_all = Mock(return_value=[])

    with (
        patch.object(custom_fields.frappe, "get_single", return_value=settings),
        patch.object(custom_fields.frappe, "get_all", get_all),
    ):
        custom_fields.project_requirement_policy()

    get_all.assert_called_once()


@pytest.mark.parametrize("doctype_name", ["", "  ", None])
def test_requirement_policy_rejects_blank_configured_doctypes(
    doctype_name: object,
) -> None:
    with pytest.raises(ValueError, match="blank DocType"):
        custom_fields._requested_doctypes([{"doctype_name": doctype_name}])


def test_requirement_policy_fails_for_missing_owned_field_on_installed_doctype() -> (
    None
):
    with pytest.raises(RuntimeError, match="Sales Invoice"):
        custom_fields._apply_requirement_changes(
            [SimpleNamespace(doctype_name="Sales Invoice", karam_series_mandatory=1)],
            {"Sales Invoice"},
            {},
        )


def test_settings_batch_update_builds_parameterised_or_of_and_predicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    statements: list[tuple[str, dict[str, object]]] = []
    query_type = type(MariaDB.update(MariaDB.DocType("Custom Field")))

    def run(query: QueryBuilder, **_kwargs: Any) -> None:
        statements.append(query.walk())

    monkeypatch.setattr(settings_controller.frappe.local, "qb", MariaDB, raising=False)
    monkeypatch.setattr(
        settings_controller.frappe.local, "flags", frappe._dict(), raising=False
    )
    with (
        patch.object(settings_controller, "DocType", MariaDB.DocType),
        patch.object(query_type, "run", run),
    ):
        settings._batch_update_custom_fields(
            "reqd",
            1,
            [
                {"fieldname": "karam_series", "dt": "Sales Invoice"},
                {"fieldname": "karam_series", "dt": "Asset"},
            ],
        )

    assert len(statements) == 1
    sql, parameters = statements[0]
    assert "UPDATE `tabCustom Field` SET `reqd`=1" in sql
    assert (
        "(`fieldname`=%(param1)s AND `dt`=%(param2)s) OR "
        "(`fieldname`=%(param3)s AND `dt`=%(param4)s)"
    ) in sql
    assert parameters == {
        "param1": "karam_series",
        "param2": "Sales Invoice",
        "param3": "karam_series",
        "param4": "Asset",
    }


def test_clear_impacted_doctype_caches_skips_blank_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    db = Mock()
    monkeypatch.setattr(settings_controller.frappe.local, "db", db, raising=False)

    settings._clear_impacted_doctype_caches(
        [
            {"doctype_name": ""},
            SimpleNamespace(doctype_name=None),
            {"doctype_name": "   "},
        ]
    )

    db.get_list.assert_not_called()


def test_row_value_supports_mapping_and_document_rows() -> None:
    assert (
        settings_controller._row_value({"doctype_name": "Asset"}, "doctype_name")
        == "Asset"
    )
    assert (
        settings_controller._row_value(
            SimpleNamespace(doctype_name="Asset"), "doctype_name"
        )
        == "Asset"
    )


def test_settings_batch_update_skips_empty_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qb = Mock()
    monkeypatch.setattr(settings_controller.frappe.local, "qb", qb, raising=False)

    _settings()._batch_update_custom_fields("reqd", 1, [])

    qb.update.assert_not_called()


def test_project_requirement_policy_skips_empty_saved_settings() -> None:
    settings = SimpleNamespace(doctype_list=[])

    with (
        patch.object(custom_fields.frappe, "get_single", return_value=settings),
        patch.object(custom_fields.frappe, "get_all") as get_all,
    ):
        custom_fields.project_requirement_policy()

    get_all.assert_not_called()


def test_settings_batch_update_rejects_empty_filter_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qb = Mock()
    monkeypatch.setattr(settings_controller.frappe.local, "qb", qb, raising=False)

    _settings()._batch_update_custom_fields("reqd", 1, [{}])

    qb.update.assert_not_called()


def test_requirement_projection_skips_custom_fields_already_at_saved_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = Mock()
    clear_cache = Mock()
    monkeypatch.setattr(custom_fields.frappe.local, "db", database, raising=False)
    monkeypatch.setattr(
        custom_fields.frappe.local, "clear_cache", clear_cache, raising=False
    )

    custom_fields._apply_requirement_changes(
        [SimpleNamespace(doctype_name="Sales Invoice", karam_series_mandatory=1)],
        {"Sales Invoice"},
        {"Sales Invoice": {"name": "Custom Field-SI", "reqd": 1}},
    )

    database.bulk_update.assert_not_called()
    clear_cache.assert_not_called()
