"""Focused lifecycle tests for Karam Series and generated Journal Entries."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, call, patch

import pytest
from frappe.model.document import Document

from karam_finance import hooks as app_hooks
from karam_finance.karam_general.utils import date_fields
from karam_finance.karam_series.doctype.karam_series_settings import (
    karam_series_settings,
)
from karam_finance.karam_series.utils import hooks as series_hooks

DATE_FIELDS_HOOK = (
    "karam_finance.karam_general.utils.date_fields.populate_karam_date_fields"
)
SOURCE_DOCUMENT_SERIES_HOOK = (
    "karam_finance.karam_series.utils.hooks.populate_karam_series_from_source_document"
)


def _doc(doctype: str = "Journal Entry", **values: object) -> Document:
    defaults: dict[str, Any] = {
        "doctype": doctype,
        "karam_series": "",
        "translation": "stale",
        "accounts": [],
        "voucher_type": "",
        "posting_date": date(2026, 3, 19),
        "transaction_date": date(2026, 1, 2),
        "purchase_date": date(2025, 12, 31),
        "planned_start_date": date(2026, 2, 3),
        "naming_series": "site-owned-pattern",
    }
    defaults.update(values)
    return cast(Document, SimpleNamespace(**defaults))


def _run_handlers(handlers: list[str], doc: Document) -> None:
    """Execute a hook list in the same order Frappe composes it."""

    for handler in handlers:
        if handler == DATE_FIELDS_HOOK:
            date_fields.populate_karam_date_fields(doc)
        elif handler == SOURCE_DOCUMENT_SERIES_HOOK:
            series_hooks.populate_karam_series_from_source_document(doc)
        else:
            raise AssertionError(f"Unexpected test hook: {handler}")


def _db_patch(
    *,
    return_value: object | None = None,
    side_effect: list[object] | None = None,
) -> Any:
    """Bind a minimal Frappe DB proxy for tests without a site context."""

    return patch.object(
        series_hooks.frappe,
        "db",
        SimpleNamespace(
            get_value=Mock(return_value=return_value, side_effect=side_effect)
        ),
    )


def _value(doc: Document, fieldname: str) -> object:
    """Read dynamic custom fields without requiring generated type stubs."""

    return getattr(doc, fieldname)


@pytest.mark.parametrize(
    ("doctype", "expected"),
    [
        ("Sales Order", "20260102"),
        ("Purchase Order", "20260102"),
        ("Asset Movement", "20260102"),
        ("Period Closing Voucher", "20260102"),
        ("Asset", "20251231"),
        ("Work Order", "20260203"),
        ("Journal Entry", "20260319"),
    ],
)
def test_date_fields_use_the_explicit_business_date_mapping(
    doctype: str,
    expected: str,
) -> None:
    doc = _doc(doctype)

    with patch.object(date_fields, "_has_karam_date_fields", return_value=True):
        date_fields.populate_karam_date_fields(doc)

    assert _value(doc, "karam_posting_date") == expected
    assert _value(doc, "karam_posting_year") == expected[:4]
    assert _value(doc, "karam_posting_month") == expected[4:6]
    assert _value(doc, "karam_posting_day") == expected[6:8]


def test_date_fields_clear_atomically_when_source_is_missing_or_invalid() -> None:
    doc = _doc("Sales Order", transaction_date="not-a-date")

    with patch.object(date_fields, "_has_karam_date_fields", return_value=True):
        date_fields.populate_karam_date_fields(doc)

    assert [
        getattr(doc, fieldname) for fieldname in date_fields.KARAM_DATE_FIELD_NAMES
    ] == [
        "",
        "",
        "",
        "",
    ]


def test_translation_is_resolved_from_the_selected_series() -> None:
    doc = _doc(karam_series="FXREV", translation="old text")

    with _db_patch(return_value="Exchange Rate Revaluation") as db:
        series_hooks.populate_karam_series_fields(doc)

    assert _value(doc, "translation") == "Exchange Rate Revaluation"
    db.get_value.assert_called_once_with("Karam Series", "FXREV", "translation")


def test_clearing_series_also_clears_stale_translation() -> None:
    doc = _doc(karam_series="", translation="old text")

    series_hooks.populate_karam_series_fields(doc)

    assert _value(doc, "translation") == ""


def test_applicability_rejects_a_series_not_enabled_for_the_doctype() -> None:
    doc = _doc("Sales Invoice", karam_series="FXREV")

    with (
        _db_patch(return_value=0),
        patch.object(series_hooks, "_", lambda message: message),
        patch.object(series_hooks.frappe, "throw") as throw,
    ):
        series_hooks.validate_karam_series_applicability(doc)

    throw.assert_called_once()
    assert "Sales Invoice" in str(throw.call_args.args[0])


def test_mandatory_policy_updates_saved_settings_not_custom_field_directly() -> None:
    row = SimpleNamespace(doctype_name="Sales Invoice", karam_series_mandatory=0)
    settings = Mock()
    settings.get.return_value = [row]
    db = Mock()

    with (
        patch.object(karam_series_settings.frappe, "has_permission"),
        patch.object(karam_series_settings.frappe, "get_single", return_value=settings),
        patch.object(karam_series_settings.frappe, "db", db),
    ):
        result = karam_series_settings.update_field_mandatory_status("Sales Invoice", 1)

    assert result["success"] is True
    assert row.karam_series_mandatory == 1
    settings.save.assert_called_once_with()
    db.set_value.assert_not_called()


def test_saved_settings_projection_clears_each_installed_doctype_cache_once() -> None:
    rows = [
        SimpleNamespace(doctype_name="Sales Invoice", karam_series_mandatory=1),
        SimpleNamespace(doctype_name="Asset", karam_series_mandatory=0),
        SimpleNamespace(doctype_name="Sales Invoice", karam_series_mandatory=0),
    ]
    project = Mock()
    settings = object.__new__(karam_series_settings.KaramSeriesSettings)
    settings.__dict__.update(doctype_list=rows, hide_fields=0)
    db = Mock()
    db.get_list.return_value = ["Sales Invoice", "Asset", "Sales Invoice"]
    clear_cache = Mock()

    with (
        patch.object(karam_series_settings.frappe, "db", db),
        patch.object(karam_series_settings.frappe, "clear_cache", clear_cache),
        patch.object(settings, "_execute_bulk_field_updates", project),
    ):
        settings.on_update()

    assert project.call_count == 2
    db.get_list.assert_called_once_with(
        "DocType",
        filters={"name": ["in", ["Asset", "Sales Invoice"]]},
        fields=["name"],
        pluck="name",
        ignore_permissions=True,
    )
    clear_cache.assert_has_calls([call(doctype="Asset"), call(doctype="Sales Invoice")])
    assert clear_cache.call_count == 2


@pytest.mark.parametrize(
    "voucher_type", ["Exchange Gain Or Loss", "Exchange Rate Revaluation"]
)
def test_err_journal_entries_inherit_series_before_naming(voucher_type: str) -> None:
    doc = _doc(
        voucher_type=voucher_type,
        accounts=[
            SimpleNamespace(
                reference_type="Exchange Rate Revaluation",
                reference_name="ERR-0001",
            ),
            SimpleNamespace(
                reference_type="Exchange Rate Revaluation",
                reference_name="ERR-0001",
            ),
        ],
    )

    with _db_patch(side_effect=["FXREV", "Exchange Rate Revaluation"]):
        series_hooks.populate_karam_series_from_source_document(doc)

    assert _value(doc, "karam_series") == "FXREV"
    assert _value(doc, "translation") == "Exchange Rate Revaluation"
    assert _value(doc, "naming_series") == "site-owned-pattern"


def test_depreciation_journal_entry_inherits_asset_series() -> None:
    doc = _doc(
        voucher_type="Depreciation Entry",
        accounts=[
            SimpleNamespace(reference_type="Asset", reference_name="AST-0001"),
            SimpleNamespace(reference_type="Asset", reference_name="AST-0001"),
        ],
    )

    with _db_patch(side_effect=["CAP", "Capital Assets"]):
        series_hooks.populate_karam_series_from_source_document(doc)

    assert _value(doc, "karam_series") == "CAP"
    assert _value(doc, "translation") == "Capital Assets"


def test_ambiguous_source_references_are_not_guessed() -> None:
    doc = _doc(
        voucher_type="Exchange Rate Revaluation",
        accounts=[
            SimpleNamespace(
                reference_type="Exchange Rate Revaluation",
                reference_name="ERR-0001",
            ),
            SimpleNamespace(
                reference_type="Exchange Rate Revaluation",
                reference_name="ERR-0002",
            ),
        ],
    )

    with _db_patch() as db:
        series_hooks.populate_karam_series_from_source_document(doc)

    assert _value(doc, "karam_series") == ""
    assert _value(doc, "translation") == ""
    db.get_value.assert_not_called()


def test_multiple_generated_children_each_inherit_the_parent_series() -> None:
    children = [
        _doc(
            voucher_type="Exchange Gain Or Loss",
            accounts=[
                SimpleNamespace(
                    reference_type="Exchange Rate Revaluation",
                    reference_name="ERR-0001",
                )
            ],
        )
        for _ in range(2)
    ]

    with _db_patch(side_effect=["FXREV", "FX", "FXREV", "FX"]):
        for child in children:
            series_hooks.populate_karam_series_from_source_document(child)

    assert [_value(child, "karam_series") for child in children] == ["FXREV", "FXREV"]
    assert [_value(child, "translation") for child in children] == ["FX", "FX"]


def test_blank_source_leaves_generated_series_blank() -> None:
    doc = _doc(
        voucher_type="Exchange Gain Or Loss",
        accounts=[
            SimpleNamespace(
                reference_type="Exchange Rate Revaluation",
                reference_name="ERR-0001",
            )
        ],
    )

    with _db_patch(return_value=""):
        series_hooks.populate_karam_series_from_source_document(doc)

    assert _value(doc, "karam_series") == ""
    assert _value(doc, "translation") == ""


def test_journal_entry_hook_populates_tokens_before_name_assignment() -> None:
    handlers = cast(list[str], app_hooks.doc_events["Journal Entry"]["before_insert"])
    assert handlers == [DATE_FIELDS_HOOK, SOURCE_DOCUMENT_SERIES_HOOK]

    doc = _doc(
        voucher_type="Exchange Gain Or Loss",
        karam_series="",
        accounts=[
            SimpleNamespace(
                reference_type="Exchange Rate Revaluation",
                reference_name="ERR-0001",
            )
        ],
    )

    with (
        patch.object(date_fields, "_has_karam_date_fields", return_value=True),
        _db_patch(side_effect=["FXREV", "FX"]),
    ):
        _run_handlers(handlers, doc)

    assert _value(doc, "karam_posting_date") == "20260319"
    assert _value(doc, "karam_posting_year") == "2026"
    assert _value(doc, "karam_posting_month") == "03"
    assert _value(doc, "karam_posting_day") == "19"
    assert _value(doc, "karam_series") == "FXREV"
    assert _value(doc, "translation") == "FX"
