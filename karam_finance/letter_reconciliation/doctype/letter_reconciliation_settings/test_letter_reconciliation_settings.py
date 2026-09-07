"""Tests for Letter Reconciliation Settings and historical rebuild helpers."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from karam_finance.letter_reconciliation.utils.doc_events import (
    _is_merge_prevention_enabled,
    gl_entry_before_insert,
    je_before_submit,
    journal_entry_on_update_after_submit,
)

from . import letter_reconciliation_settings as settings_module

_GUARD_PATH = (
    "karam_finance.letter_reconciliation.utils.doc_events._is_merge_prevention_enabled"
)
_SETTINGS_MODULE = (
    "karam_finance.letter_reconciliation.doctype.letter_reconciliation_settings"
    ".letter_reconciliation_settings"
)
_REBUILD_MODULE = (
    "karam_finance.letter_reconciliation.doctype.letter_reconciliation_settings"
    ".historical_gl_rebuild"
)


class TestLetterReconciliationSettings(FrappeTestCase):
    """Settings metadata, toggle and hook gating contracts."""

    def test_default_toggle_is_off(self) -> None:
        """The prevent_gl_merge field metadata should default to off (0)."""
        field = frappe.get_meta("Letter Reconciliation Settings").get_field(
            "prevent_gl_merge"
        )
        assert field is not None
        assert field.default in (0, "0", None)

    def test_helper_returns_false_when_off(self) -> None:
        """_is_merge_prevention_enabled returns False when off."""
        settings = cast(
            "settings_module.LetterReconciliationSettings",
            frappe.get_single("Letter Reconciliation Settings"),
        )
        settings.prevent_gl_merge = 0  # noqa: V101 - saved DocType field exercised by the settings helper.
        settings.save(ignore_permissions=True)

        assert not _is_merge_prevention_enabled()

    def test_helper_returns_true_when_on(self) -> None:
        """_is_merge_prevention_enabled returns True when on."""
        settings = cast(
            "settings_module.LetterReconciliationSettings",
            frappe.get_single("Letter Reconciliation Settings"),
        )
        settings.prevent_gl_merge = 1  # noqa: V101 - saved DocType field exercised by the settings helper.
        settings.save(ignore_permissions=True)

        assert _is_merge_prevention_enabled()

        settings.prevent_gl_merge = 0  # noqa: V101 - saved DocType field exercised by the settings helper.
        settings.save(ignore_permissions=True)

    def test_gl_entry_before_insert_gated(self) -> None:
        """gl_entry_before_insert returns early when off."""

        class _Doc:
            voucher_type = "Journal Entry"
            voucher_no = "JV-00001"
            account = "Test Account"
            letter = ""

        doc = _Doc()

        with patch(_GUARD_PATH, return_value=False):
            gl_entry_before_insert(doc)

        assert doc.letter == ""

    def test_je_before_submit_gated(self) -> None:
        """je_before_submit returns early when off."""
        row = MagicMock()
        row.reference_detail_no = None
        row.name = "row-001"

        doc = MagicMock()
        doc.accounts = [row]

        with patch(_GUARD_PATH, return_value=False):
            je_before_submit(doc)

        assert row.reference_detail_no is None

    def test_journal_entry_on_update_after_submit_gated(
        self,
    ) -> None:
        """journal_entry_on_update_after_submit returns early when off."""
        doc = MagicMock()
        doc.name = "JV-00001"

        with patch(_GUARD_PATH, return_value=False) as mock_check:
            journal_entry_on_update_after_submit(doc)
            mock_check.assert_called_once()
