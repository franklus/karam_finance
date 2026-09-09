"""Pure contracts for common SQL and Custom Field helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import frappe
import pytest

from karam_finance.common import custom_fields, sql_utils


def _raise_validation(message: str, exception: type[Exception]) -> None:
    raise exception(message)


def _write_fixture(directory: Path, name: str, payload: Any) -> None:
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


def test_sql_identifier_validation_and_column_lists_reject_unsafe_values() -> None:
    with (
        patch.object(sql_utils.frappe, "_", side_effect=str),
        patch.object(sql_utils.frappe, "throw", side_effect=_raise_validation),
    ):
        assert sql_utils.validate_sql_identifier("  account_currency  ") == (
            "account_currency"
        )
        assert sql_utils.validate_sql_identifiers(["account", "posting_date"]) == [
            "account",
            "posting_date",
        ]
        assert sql_utils.safe_column_list(["account", "posting_date"], "gl") == (
            "gl.account, gl.posting_date"
        )
        assert sql_utils.safe_column_list(["account", "posting_date"]) == (
            "account, posting_date"
        )
        with pytest.raises(frappe.ValidationError):
            sql_utils.validate_sql_identifier("")
        with pytest.raises(frappe.ValidationError):
            sql_utils.validate_sql_identifier("account; DROP TABLE tabGL Entry")
        with pytest.raises(frappe.ValidationError):
            sql_utils.safe_column_list(["account"], "gl; DROP")


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        (None, 7, 7),
        ("-1", 8, 8),
        ("not-a-number", 9, 9),
        ([], 10, 10),
        ("12", 0, 12),
    ],
)
def test_safe_int_uses_defaults_for_absent_invalid_and_negative_values(
    value: Any, default: int, expected: int
) -> None:
    assert sql_utils.safe_int(value, default) == expected


@pytest.mark.parametrize(
    "payload",
    [
        "not an object",
        {"": []},
        {"DocType": {}},
        {"DocType": ["not an object"]},
        {"DocType": [{"fieldname": "", "fieldtype": "Data"}]},
        {"DocType": [{"fieldname": "field", "fieldtype": ""}]},
        {
            "DocType": [
                {"fieldname": "field", "fieldtype": "Data"},
                {"fieldname": "field", "fieldtype": "Data"},
            ]
        },
    ],
)
def test_fixture_shapes_and_field_contracts_are_rejected(
    tmp_path: Path, payload: Any
) -> None:
    _write_fixture(tmp_path, "fixture.json", payload)
    with pytest.raises(custom_fields.CustomFieldFixtureError):
        custom_fields._load_custom_fields_from_dir(tmp_path, "test fixtures")


def test_malformed_json_is_rejected_with_the_fixture_path(tmp_path: Path) -> None:
    fixture = tmp_path / "broken.json"
    fixture.write_text("{", encoding="utf-8")
    with pytest.raises(custom_fields.CustomFieldFixtureError, match=r"broken\.json"):
        custom_fields._load_json_file(fixture, "test fixtures")


def test_loading_valid_first_file_and_invalid_second_file_never_applies_fields(
    tmp_path: Path,
) -> None:
    _write_fixture(
        tmp_path,
        "01-valid.json",
        {"DocType": [{"fieldname": "valid_field", "fieldtype": "Data"}]},
    )
    _write_fixture(
        tmp_path,
        "02-invalid.json",
        {"DocType": [{"fieldname": "", "fieldtype": "Data"}]},
    )
    with (
        patch.object(custom_fields.frappe, "get_app_path", return_value=str(tmp_path)),
        patch.object(custom_fields, "_apply_custom_fields") as apply,
        pytest.raises(custom_fields.CustomFieldFixtureError),
    ):
        custom_fields.ensure_custom_fields_from_dir("fixtures", label="test fixtures")
    apply.assert_not_called()


def test_missing_and_empty_fixture_directories_skip_database_writes(
    tmp_path: Path,
) -> None:
    database = SimpleNamespace(savepoint=Mock(), rollback=Mock())
    with (
        patch.object(
            custom_fields.frappe,
            "get_app_path",
            side_effect=[str(tmp_path / "missing"), str(tmp_path)],
        ),
        patch.object(custom_fields.frappe, "db", database),
    ):
        custom_fields.ensure_custom_fields_from_dir("missing", label="missing")
        custom_fields.ensure_custom_fields_from_dir("empty", label="empty")
    database.savepoint.assert_not_called()
    database.rollback.assert_not_called()


def test_optional_missing_doctypes_skip_application_and_available_fields_apply(
    tmp_path: Path,
) -> None:
    _write_fixture(
        tmp_path,
        "fixture.json",
        {"Installed": [{"fieldname": "custom_flag", "fieldtype": "Check"}]},
    )
    database = SimpleNamespace(savepoint=Mock(), rollback=Mock())
    with (
        patch.object(custom_fields.frappe, "get_app_path", return_value=str(tmp_path)),
        patch.object(custom_fields.frappe, "db", database),
        patch.object(custom_fields.frappe, "get_all", side_effect=[[], ["Installed"]]),
        patch.object(custom_fields, "frappe_create_custom_fields") as create,
    ):
        custom_fields.ensure_custom_fields_from_dir("fixtures", label="missing target")
        custom_fields.ensure_custom_fields_from_dir(
            "fixtures", label="installed target"
        )
    create.assert_called_once_with(
        {"Installed": [{"fieldname": "custom_flag", "fieldtype": "Check"}]},
        ignore_validate=True,
        update=True,
    )
    database.savepoint.assert_called_once_with("karam_finance_custom_fields")


def test_savepoint_failure_does_not_roll_back_and_application_failure_rolls_back() -> (
    None
):
    fields = {"DocType": [{"fieldname": "custom_flag", "fieldtype": "Check"}]}
    savepoint_failure = SimpleNamespace(
        savepoint=Mock(side_effect=RuntimeError("savepoint failed")), rollback=Mock()
    )
    with (
        patch.object(custom_fields.frappe, "db", savepoint_failure),
        pytest.raises(custom_fields.CustomFieldApplicationError) as savepoint_error,
    ):
        custom_fields._apply_custom_fields(fields, "savepoint")
    assert isinstance(savepoint_error.value.__cause__, RuntimeError)
    savepoint_failure.rollback.assert_not_called()

    application_failure = SimpleNamespace(savepoint=Mock(), rollback=Mock())
    with (
        patch.object(custom_fields.frappe, "db", application_failure),
        patch.object(
            custom_fields,
            "frappe_create_custom_fields",
            side_effect=RuntimeError("creation failed"),
        ),
        pytest.raises(custom_fields.CustomFieldApplicationError) as application_error,
    ):
        custom_fields._apply_custom_fields(fields, "application")
    assert isinstance(application_error.value.__cause__, RuntimeError)
    application_failure.rollback.assert_called_once_with(
        save_point="karam_finance_custom_fields"
    )
