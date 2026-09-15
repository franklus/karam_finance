"""Tests for Letter Reconciliation helpers."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock, patch

import pytest
from frappe.exceptions import ValidationError
from frappe.tests.utils import FrappeTestCase

from karam_finance.letter_reconciliation.doctype.letter_reconciliation import (
    letter_reconciliation as lr,
)

MODULE_PATH = (
    "karam_finance.letter_reconciliation.doctype.letter_reconciliation."
    "letter_reconciliation"
)


class TestLetterReconciliation(FrappeTestCase):
    """Unit tests for letter reconciliation utilities."""

    def test_increment_string_simple(self) -> None:
        """Increment basic sequences."""
        assert lr.increment_string("") == "A"
        assert lr.increment_string("A") == "B"
        assert lr.increment_string("Z") == "AA"
        assert lr.increment_string("ZZ") == "AAA"

    def test_increment_string_cap(self) -> None:
        """Stop at six Zs."""
        six_z = "Z" * 6
        assert lr.increment_string(six_z) == six_z

    def test_compute_latest_year(self) -> None:
        """Pick latest posting year."""
        items = [
            {"posting_date": "2023-01-01"},
            {"posting_date": "2024-12-31"},
            {"posting_date": "2022-05-05"},
        ]
        assert lr._compute_latest_year(items) == 2024

    @patch(f"{MODULE_PATH}.frappe.db.sql")
    @patch(f"{MODULE_PATH}.frappe.get_doc")
    def test_get_letter_for_year_locked_inserts_missing_record(
        self,
        mock_get_doc: MagicMock,
        mock_sql: MagicMock,
    ) -> None:
        """Insert missing year row and return it."""
        # First call returns nothing -> forces insert; then returns the new letter
        mock_sql.side_effect = [list[dict[str, str]](), [{"letter": "B"}]]
        mock_get_doc.return_value = MagicMock(insert=MagicMock())

        assert lr._get_letter_for_year_locked(2025) == "B"

    @patch(f"{MODULE_PATH}.frappe.db.get_value")
    def test_validate_sum_precision_within_tolerance(
        self,
        mock_get_value: MagicMock,
    ) -> None:
        """Pass when totals match within precision."""
        mock_get_value.side_effect = ["BHD", 1000]
        lr._validate_totals(
            cr_items=[{"credit": 1.111}],
            dt_items=[{"debit": 1.111}],
            account="Test",
        )

    @patch(f"{MODULE_PATH}.frappe.db.get_value")
    def test_validate_sum_precision_raises_on_mismatch(
        self,
        mock_get_value: MagicMock,
    ) -> None:
        """Raise when totals differ beyond precision."""
        mock_get_value.side_effect = ["BHD", 1000]
        with pytest.raises(ValidationError):
            lr._validate_totals(
                cr_items=[{"credit": 1.111}],
                dt_items=[{"debit": 1.112}],
                account="Test",
            )

    def test_update_year_letter_inserts_with_supplied_letter(self) -> None:
        """Ensure new Letter Settings rows respect the requested letter."""
        with (
            patch(f"{MODULE_PATH}.frappe.db.exists", return_value=False),
            patch(f"{MODULE_PATH}.frappe.get_doc") as mock_get_doc,
        ):
            mock_doc = MagicMock()
            mock_get_doc.return_value = mock_doc

            lr.update_year_letter(2025, "C")

        mock_get_doc.assert_called_once()
        call_args = mock_get_doc.call_args
        assert call_args is not None
        args, _kwargs = cast(
            "tuple[tuple[dict[str, object], ...], dict[str, object]]",
            call_args,
        )
        assert args[0]["letter"] == "C"
