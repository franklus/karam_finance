"""Focused tests for owned schema installation and migration seams."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# Frappe's default logger writes to ``../logs``; focused seam tests do not need
# site logging and should remain runnable without a configured bench site.
os.environ.setdefault("FRAPPE_STREAM_LOGGING", "1")

from karam_finance.common import custom_fields
from karam_finance.karam_series.utils import custom_fields as series_custom_fields


def _write_fixture(
    base_path: Path,
    payload: object,
    filename: str = "fixture.json",
) -> None:
    base_path.mkdir(parents=True, exist_ok=True)
    (base_path / filename).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def _patch_fixture_path(monkeypatch: pytest.MonkeyPatch, base_path: Path) -> None:
    monkeypatch.setattr(
        custom_fields.frappe,
        "get_app_path",
        MagicMock(return_value=str(base_path)),
    )


def test_schema_integrity_skips_missing_optional_doctype(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A fixture for an unavailable optional DocType must not block migration."""
    _write_fixture(
        tmp_path,
        {
            "Optional ERPNext Feature": [
                {"fieldname": "optional_value", "fieldtype": "Data"}
            ]
        },
    )
    _patch_fixture_path(monkeypatch, tmp_path)

    db = MagicMock()
    monkeypatch.setattr(custom_fields.frappe, "db", db)
    monkeypatch.setattr(custom_fields.frappe, "get_all", MagicMock(return_value=[]))
    apply_fields = MagicMock()
    monkeypatch.setattr(custom_fields, "frappe_create_custom_fields", apply_fields)

    custom_fields.ensure_custom_fields_from_dir("optional", label="test")

    apply_fields.assert_not_called()
    db.savepoint.assert_not_called()


def test_schema_integrity_rejects_malformed_fixture_before_writes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Malformed JSON must fail before DocType lookup or field application."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "broken.json").write_text('{"Journal Entry": [', encoding="utf-8")
    _patch_fixture_path(monkeypatch, tmp_path)

    db = MagicMock()
    monkeypatch.setattr(custom_fields.frappe, "db", db)
    apply_fields = MagicMock()
    monkeypatch.setattr(custom_fields, "frappe_create_custom_fields", apply_fields)

    with pytest.raises(custom_fields.CustomFieldFixtureError, match=r"broken\.json"):
        custom_fields.ensure_custom_fields_from_dir("malformed", label="test")

    db.exists.assert_not_called()
    apply_fields.assert_not_called()


def test_schema_integrity_rolls_back_and_raises_on_apply_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failure on an installed DocType cannot be silently reported as success."""
    _write_fixture(
        tmp_path,
        {"Journal Entry": [{"fieldname": "owned_value", "fieldtype": "Data"}]},
    )
    _patch_fixture_path(monkeypatch, tmp_path)

    db = MagicMock()
    monkeypatch.setattr(custom_fields.frappe, "db", db)
    monkeypatch.setattr(
        custom_fields.frappe,
        "get_all",
        MagicMock(return_value=["Journal Entry"]),
    )

    def fail_to_apply(_fields: dict[str, list[dict[str, Any]]], **_kwargs: Any) -> None:
        message = "simulated Custom Field failure"
        raise RuntimeError(message)

    monkeypatch.setattr(custom_fields, "frappe_create_custom_fields", fail_to_apply)

    with pytest.raises(
        custom_fields.CustomFieldApplicationError,
        match="Unable to apply Custom Field fixtures",
    ):
        custom_fields.ensure_custom_fields_from_dir("owned", label="test")

    db.savepoint.assert_called_once_with("karam_finance_custom_fields")
    db.rollback.assert_called_once_with(save_point="karam_finance_custom_fields")


def test_schema_integrity_custom_field_application_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Repeated fixture application converges through Frappe's update helper."""
    _write_fixture(
        tmp_path,
        {"Journal Entry": [{"fieldname": "owned_value", "fieldtype": "Data"}]},
    )
    _patch_fixture_path(monkeypatch, tmp_path)

    db = MagicMock()
    monkeypatch.setattr(custom_fields.frappe, "db", db)
    monkeypatch.setattr(
        custom_fields.frappe,
        "get_all",
        MagicMock(return_value=["Journal Entry"]),
    )
    apply_fields = MagicMock()
    monkeypatch.setattr(custom_fields, "frappe_create_custom_fields", apply_fields)

    custom_fields.ensure_custom_fields_from_dir("owned", label="test")
    custom_fields.ensure_custom_fields_from_dir("owned", label="test")

    assert apply_fields.call_count == 2
    assert all(
        call.kwargs == {"ignore_validate": True, "update": True}
        for call in apply_fields.call_args_list
    )


def test_schema_integrity_requirement_projection_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Saved Settings flags project only changed requirements on each run."""
    settings = MagicMock()
    settings.doctype_list = [
        {"doctype_name": "Journal Entry", "karam_series_mandatory": 1},
        {"doctype_name": "Optional ERPNext Feature", "karam_series_mandatory": 1},
    ]
    monkeypatch.setattr(
        series_custom_fields.frappe, "get_single", MagicMock(return_value=settings)
    )

    db = MagicMock()
    monkeypatch.setattr(series_custom_fields.frappe, "db", db)
    get_all = MagicMock(
        side_effect=[
            ["Journal Entry"],
            [{"name": "Journal Entry-karam_series", "dt": "Journal Entry", "reqd": 0}],
            ["Journal Entry"],
            [{"name": "Journal Entry-karam_series", "dt": "Journal Entry", "reqd": 1}],
        ]
    )
    monkeypatch.setattr(series_custom_fields.frappe, "get_all", get_all)
    monkeypatch.setattr(series_custom_fields.frappe.utils, "cint", _integer_value)
    clear_cache = MagicMock()
    monkeypatch.setattr(series_custom_fields.frappe, "clear_cache", clear_cache)

    # First run changes 0 -> 1. Second run sees the persisted 1 and is a no-op.
    series_custom_fields.project_requirement_policy()
    series_custom_fields.project_requirement_policy()

    db.bulk_update.assert_called_once_with(
        "Custom Field", {"Journal Entry-karam_series": {"reqd": 1}}
    )
    clear_cache.assert_called_once_with(doctype="Journal Entry")


def _integer_value(value: Any) -> int:
    return int(value or 0)
