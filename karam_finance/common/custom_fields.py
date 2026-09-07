"""Common utilities for applying Karam Finance Custom Field fixtures.

Custom Field fixtures are part of the application's schema contract. A bad
fixture or an application failure must therefore stop installation/migration;
logging the error and continuing leaves a site in a state that is difficult to
diagnose and unsafe to operate.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import frappe
from frappe.custom.doctype.custom_field.custom_field import (
    create_custom_fields as frappe_create_custom_fields,
)

_LOG_PREFIX = "[karam_finance] ensure_custom_fields_from_dir:"
_CUSTOM_FIELDS_SAVEPOINT = "karam_finance_custom_fields"
_logger = frappe.logger("karam_finance.custom_fields", allow_site=True)


class CustomFieldFixtureError(ValueError):
    """Raised when an application-owned Custom Field fixture is malformed."""


class CustomFieldApplicationError(RuntimeError):
    """Raised when an application-owned Custom Field cannot be applied."""


def ensure_custom_fields_from_dir(*path_segments: str, label: str) -> None:
    """Apply all Custom Field fixtures in an app-relative directory.

    A missing target DocType is treated as an optional capability and skipped.
    Fixture loading and validation complete before any write is attempted, so a
    malformed file cannot leave earlier files applied. Writes are enclosed in a
    savepoint and application failures are re-raised after rollback instead of
    being converted into a misleading successful migration.
    """
    base_path = Path(frappe.get_app_path("karam_finance", *path_segments))
    if not base_path.is_dir():
        _logger.info("%s no directory %s", _LOG_PREFIX, base_path)
        return

    _logger.info("%s using fixtures from %s (%s)", _LOG_PREFIX, base_path, label)
    custom_fields = _load_custom_fields_from_dir(base_path, label)

    if not custom_fields:
        _logger.info("%s no fields loaded from fixtures (%s)", _LOG_PREFIX, label)
        return

    available_fields = _select_available_doctypes(custom_fields, label)
    if not available_fields:
        _logger.info("%s no available DocTypes for fixtures (%s)", _LOG_PREFIX, label)
        return

    _apply_custom_fields(available_fields, label)


def _load_custom_fields_from_dir(
    base_path: Path,
    label: str,
) -> dict[str, list[dict[str, Any]]]:
    """Load and validate all JSON fixtures in ``base_path``.

    Validation is deliberately performed for every file before the caller
    starts applying any field. This makes malformed later files fail before a
    valid earlier file can change the database.
    """
    custom_fields: dict[str, list[dict[str, Any]]] = {}

    for file_path in sorted(base_path.glob("*.json")):
        fixture = _load_json_file(file_path, label)
        for doctype, fields in fixture.items():
            custom_fields.setdefault(doctype, []).extend(fields)

    _validate_custom_fields(custom_fields, label)
    return custom_fields


def _load_json_file(file_path: Path, label: str) -> dict[str, list[dict[str, Any]]]:
    """Read and validate the top-level shape of one fixture file."""
    try:
        with file_path.open("r", encoding="utf-8") as fixture_file:
            payload = json.load(fixture_file)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        message = f"Unable to load Custom Field fixture {file_path} ({label})"
        raise CustomFieldFixtureError(message) from exc

    if not isinstance(payload, Mapping):
        message = f"Custom Field fixture {file_path} ({label}) must contain an object"
        raise CustomFieldFixtureError(message)

    return {
        _validate_doctype(doctype, file_path, label): _normalise_fields(
            fields, doctype, file_path, label=label
        )
        for doctype, fields in payload.items()
    }


def _validate_doctype(doctype: Any, file_path: Path, label: str) -> str:
    if not isinstance(doctype, str) or not doctype.strip():
        message = f"Custom Field fixture {file_path} ({label}) has an invalid DocType"
        raise CustomFieldFixtureError(message)
    return doctype


def _normalise_fields(
    fields: Any, doctype: str, file_path: Path, *, label: str
) -> list[dict[str, Any]]:
    if not isinstance(fields, list):
        message = (
            f"Custom Field fixture {file_path} ({label}) must map {doctype!r} to a list"
        )
        raise CustomFieldFixtureError(message)
    normalised: list[dict[str, Any]] = []
    for index, field in enumerate(fields):
        if not isinstance(field, Mapping):
            message = (
                f"Custom Field fixture {file_path} ({label}) has a non-object "
                f"field at {doctype}[{index}]"
            )
            raise CustomFieldFixtureError(message)
        normalised.append(dict(field))
    return normalised


def _validate_custom_fields(
    custom_fields: Mapping[str, list[dict[str, Any]]],
    label: str,
) -> None:
    """Validate the field-level contract shared by all fixture directories."""
    for doctype, fields in custom_fields.items():
        _validate_doctype_fields(doctype, fields, label)


def _validate_doctype_fields(
    doctype: str, fields: list[dict[str, Any]], label: str
) -> None:
    fieldnames: set[str] = set()
    for index, field in enumerate(fields):
        fieldname = field.get("fieldname")
        fieldtype = field.get("fieldtype")
        if not isinstance(fieldname, str) or not fieldname.strip():
            message = (
                f"Custom Field fixture ({label}) has an invalid fieldname "
                f"at {doctype}[{index}]"
            )
            raise CustomFieldFixtureError(message)
        if not isinstance(fieldtype, str) or not fieldtype.strip():
            message = (
                f"Custom Field fixture ({label}) has an invalid fieldtype "
                f"for {doctype}.{fieldname}"
            )
            raise CustomFieldFixtureError(message)
        if fieldname in fieldnames:
            message = (
                f"Custom Field fixture ({label}) defines duplicate field "
                f"{doctype}.{fieldname}"
            )
            raise CustomFieldFixtureError(message)
        fieldnames.add(fieldname)


def _select_available_doctypes(
    custom_fields: Mapping[str, list[dict[str, Any]]],
    label: str,
) -> dict[str, list[dict[str, Any]]]:
    """Keep fields for installed DocTypes and skip optional missing targets."""
    doctype_names = list(custom_fields)
    installed_doctypes = set(
        frappe.get_all(
            "DocType",
            filters={"name": ["in", doctype_names]},
            pluck="name",
            ignore_permissions=True,
        )
    )
    available: dict[str, list[dict[str, Any]]] = {}
    for doctype, fields in custom_fields.items():
        if doctype not in installed_doctypes:
            _logger.warning(
                "%s SKIP missing optional DocType %r (%s)",
                _LOG_PREFIX,
                doctype,
                label,
            )
            continue
        available[doctype] = fields
    return available


def _apply_custom_fields(
    custom_fields: Mapping[str, list[dict[str, Any]]],
    label: str,
) -> None:
    """Apply fields under a savepoint and expose any failure to the caller."""
    savepoint_created = False
    try:
        frappe.db.savepoint(_CUSTOM_FIELDS_SAVEPOINT)
        savepoint_created = True
        for doctype, fields in custom_fields.items():
            frappe_create_custom_fields(
                {doctype: fields},
                ignore_validate=True,
                update=True,
            )
            _logger.info(
                "%s OK %s (+%s) (%s)",
                _LOG_PREFIX,
                doctype,
                len(fields),
                label,
            )
    except Exception as exc:
        if savepoint_created:
            frappe.db.rollback(save_point=_CUSTOM_FIELDS_SAVEPOINT)
        _logger.exception(
            "%s ERROR applying owned fields (%s)",
            _LOG_PREFIX,
            label,
        )
        message = f"Unable to apply Custom Field fixtures ({label})"
        raise CustomFieldApplicationError(message) from exc
