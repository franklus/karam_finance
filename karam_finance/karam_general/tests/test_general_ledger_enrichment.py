"""Regression coverage for General Ledger translation and voucher enrichment."""

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

from frappe import _dict
from frappe.tests.utils import FrappeTestCase

from .test_general_ledger_report import _load_module


class TestGeneralLedgerEnrichment(FrappeTestCase):
    def test_repeated_display_values_are_translated_once_per_report(self) -> None:
        """Avoid repeating Frappe translation cache reads for identical values."""
        module = _load_module()
        translation_cache: dict[str, str] = {}
        entries = [
            _dict(
                voucher_subtype="Journal Entry",
                against_voucher_type="Purchase Invoice",
                remarks="Repeated remark",
                party_type="Supplier",
            )
            for _ in range(2)
        ]

        with patch.object(
            module._gl_aggregation,
            "_",
            side_effect="translated:{}".format,
        ) as translate:
            for entry in entries:
                module._gl_aggregation._prepare_gle_for_output(entry, translation_cache)

        assert translate.call_count == 4
        assert entries[0].voucher_subtype == "translated:Journal Entry"
        assert entries[1].remarks == "translated:Repeated remark"

    def test_attach_series_translation_populates_fields(self) -> None:
        """Ensure translation and series are hydrated for curated doctypes."""
        module = _load_module()
        hydrate: Callable[[list[_dict[str, Any]]], None] = (
            module._attach_series_translation
        )
        entries: list[_dict[str, Any]] = [
            _dict(
                voucher_type="Sales Invoice",
                voucher_no="SINV-0001",
                karam_series=None,
                translation=None,
            )
        ]

        with patch.object(
            module._gl_enrichment,
            "_fetch_voucher_data",
            return_value={
                ("Sales Invoice", "SINV-0001"): {
                    "name": "SINV-0001",
                    "karam_series": "SER-1",
                    "translation": "Arabic",
                }
            },
        ) as mock_fetch:
            hydrate(entries)

        assert entries[0].karam_series == "SER-1"
        assert entries[0].translation == "Arabic"
        mock_fetch.assert_called_once_with({"Sales Invoice": {"SINV-0001"}})

    def test_attach_series_translation_skips_non_curated_doctype(self) -> None:
        """Ensure non-curated doctypes are ignored."""
        module = _load_module()
        hydrate: Callable[[list[_dict[str, Any]]], None] = (
            module._attach_series_translation
        )
        entries: list[_dict[str, Any]] = [
            _dict(
                voucher_type="Quotation",
                voucher_no="QTN-0001",
                karam_series=None,
                translation=None,
            )
        ]

        with (
            patch.object(module.frappe, "get_meta") as mock_get_meta,
            patch.object(module.frappe.db, "get_all") as mock_get_all,
        ):
            hydrate(entries)

        mock_get_meta.assert_not_called()
        mock_get_all.assert_not_called()

    def test_attach_series_translation_includes_journal_entry_children(self) -> None:
        """Hydrate every GL row sharing a supported parent voucher."""
        module = _load_module()
        entries: list[_dict[str, Any]] = [
            _dict(
                voucher_type="Journal Entry",
                voucher_no="JV-0001",
                karam_series=None,
                translation=None,
            ),
            _dict(
                voucher_type="Journal Entry",
                voucher_no="JV-0001",
                karam_series=None,
                translation=None,
            ),
        ]

        with patch.object(
            module._gl_enrichment,
            "_fetch_voucher_data",
            return_value={
                ("Journal Entry", "JV-0001"): {
                    "name": "JV-0001",
                    "karam_series": "JV",
                    "translation": "Journal",
                }
            },
        ) as mock_fetch:
            module._attach_series_translation(entries)

        assert [entry.karam_series for entry in entries] == ["JV", "JV"]
        assert [entry.translation for entry in entries] == ["Journal", "Journal"]
        mock_fetch.assert_called_once_with({"Journal Entry": {"JV-0001"}})
