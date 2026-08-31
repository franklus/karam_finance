"""Tests for Letter Reconciliation helpers."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock, patch

import pytest
from frappe.exceptions import ValidationError
from frappe.tests.utils import FrappeTestCase

from karam_finance.letter_reconciliation.doctype.letter_reconciliation import (
    letter_reconciliation as lr,
)
from karam_finance.letter_reconciliation.utils import doc_events as lr_doc_events

if TYPE_CHECKING:
    from collections.abc import Iterable

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
        mock_sql.side_effect = [[], [{"letter": "B"}]]
        mock_get_doc.return_value = MagicMock(insert=MagicMock())

        assert lr._get_letter_for_year_locked(2025) == "B"

    @patch(f"{MODULE_PATH}.get_currency_precision")
    @patch(f"{MODULE_PATH}.frappe.db.get_value")
    def test_validate_sum_precision_within_tolerance(
        self,
        mock_get_value: MagicMock,
        mock_precision: MagicMock,
    ) -> None:
        """Pass when totals match within precision."""
        mock_get_value.return_value = "BHD"
        mock_precision.return_value = 3
        lr.validate_sum_of_credit_and_debit(
            cr_items=[{"credit": 1.111}],
            dt_items=[{"debit": 1.111}],
            account="Test",
        )

    @patch(f"{MODULE_PATH}.get_currency_precision")
    @patch(f"{MODULE_PATH}.frappe.db.get_value")
    def test_validate_sum_precision_raises_on_mismatch(
        self,
        mock_get_value: MagicMock,
        mock_precision: MagicMock,
    ) -> None:
        """Raise when totals differ beyond precision."""
        mock_get_value.return_value = "BHD"
        mock_precision.return_value = 3
        with pytest.raises(ValidationError):
            lr.validate_sum_of_credit_and_debit(
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


class _Cond:
    """Fake condition for query builder interception."""

    def __init__(self, op: str, left: object, right: object | None = None) -> None:
        self.op = op
        self.left = left
        self.right = right

    def __and__(self, other: object) -> _Cond:
        return _Cond("and", self, other)

    def __or__(self, other: object) -> _Cond:
        return _Cond("or", self, other)


class _FakeField:
    """Fake field for query builder interception."""

    def __init__(self, name: str) -> None:
        self.name = name

    def isin(self, values: Iterable[object]) -> _Cond:
        return _Cond("isin", self.name, tuple(values))

    def __eq__(self, other: object) -> _Cond:  # type: ignore[override]
        return _Cond("eq", self.name, other)

    def __hash__(self) -> int:
        return hash(self.name)


class _FakeDocType:
    """Fake doctype for query builder interception."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.voucher_no = _FakeField("voucher_no")
        self.voucher_type = _FakeField("voucher_type")
        self.account = _FakeField("account")
        self.voucher_detail_no = _FakeField("voucher_detail_no")
        self.letter = _FakeField("letter")


class _FakeUpdate:
    """Fake update for query builder interception."""

    def __init__(self, doctype: _FakeDocType) -> None:
        self.doctype = doctype
        self.set_args: tuple[object, object] | None = None
        self.where_arg: _Cond | None = None

    def set(self, field: object, value: object) -> _FakeUpdate:
        self.set_args = (field, value)
        return self

    def where(self, cond: _Cond) -> _FakeUpdate:
        self.where_arg = cond
        return self

    def run(self) -> dict[str, _Cond | None]:
        return {"where": self.where_arg}


class _FakeQB:
    """Fake query builder for interception."""

    def __init__(self) -> None:
        self.last_update: _FakeUpdate | None = None

    def DocType(self, name: str) -> _FakeDocType:  # noqa: N802
        return _FakeDocType(name)

    def update(self, doctype: _FakeDocType) -> _FakeUpdate:
        upd = _FakeUpdate(doctype)
        self.last_update = upd
        return upd


def _cond_contains(cond: _Cond | None, op: str, fieldname: str) -> bool:
    """Recursively check if a condition tree contains field/op."""
    if cond is None:
        return False
    if getattr(cond, "op", None) == op and getattr(cond, "left", None) == fieldname:
        return True
    for child in (getattr(cond, "left", None), getattr(cond, "right", None)):
        if _cond_contains(child, op, fieldname):
            return True
    return False


def test_set_letter_scopes_to_selected_rows() -> None:
    """GL updates should be narrowed by voucher detail rows/accounts."""
    qb = _FakeQB()
    fake_savepoint = MagicMock()
    fake_rollback = MagicMock()
    fake_bulk_update = MagicMock()

    cr_items = [{"account": "Cash", "jv_row_name": "JEA-1", "journal_entry": "JV-1"}]
    dt_items = [{"account": "Bank", "jv_row_name": "JEA-2", "journal_entry": "JV-1"}]
    all_items = cr_items + dt_items

    with (
        patch(f"{MODULE_PATH}.frappe.qb", qb),
        patch(f"{MODULE_PATH}.frappe.db.savepoint", fake_savepoint),
        patch(f"{MODULE_PATH}.frappe.db.rollback", fake_rollback),
        patch(f"{MODULE_PATH}.frappe.db.bulk_update", fake_bulk_update),
        patch(f"{MODULE_PATH}.frappe.db.get_value", return_value=1),
        patch(
            f"{MODULE_PATH}._prepare_letter_items",
            return_value=(cr_items, dt_items, all_items, None),
        ),
        patch(f"{MODULE_PATH}._compute_latest_year", return_value=2024),
        patch(f"{MODULE_PATH}._get_letter_for_year_locked", return_value="B"),
        patch(f"{MODULE_PATH}.increment_string", return_value="C"),
        patch(f"{MODULE_PATH}.update_year_letter"),
        patch(f"{MODULE_PATH}._validate_lettering_enabled_for_accounts"),
    ):
        lr.set_letter(cr_items, dt_items, account=None)

    where_arg = qb.last_update.where_arg if qb.last_update else None
    assert _cond_contains(where_arg, "isin", "voucher_detail_no"), (
        "Voucher detail filter missing"
    )


def test_gl_entry_before_insert_prefers_voucher_detail_no() -> None:
    """Letter should follow the exact Journal Entry Account row when present."""
    doc = cast(
        "lr_doc_events._GLInsertDoc",
        SimpleNamespace(
            voucher_type="Journal Entry",
            voucher_no="JV-1",
            account="Cash",
            voucher_detail_no="ROW-1",
            letter="",
        ),
    )

    with (
        patch(
            "karam_finance.letter_reconciliation.utils.doc_events._is_merge_prevention_enabled",
            return_value=True,
        ),
        patch(
            "karam_finance.letter_reconciliation.utils.doc_events.frappe.get_all",
            return_value=[
                {"name": "ROW-1", "account": "Cash", "letter": "X", "idx": 1},
                {"name": "ROW-2", "account": "Cash", "letter": "Y", "idx": 2},
            ],
        ),
    ):
        lr_doc_events.gl_entry_before_insert(doc)

    assert doc.letter == "X"


def test_journal_entry_update_uses_voucher_detail_no() -> None:
    """Bulk updates should map GL rows by voucher_detail_no when available."""
    doc = cast(
        "lr_doc_events._JournalEntryDoc",
        SimpleNamespace(
            name="JV-1",
            accounts=[
                SimpleNamespace(name="ROW-1", account="Cash", letter="L1"),
                SimpleNamespace(name="ROW-2", account="Cash", letter="L2"),
            ],
        ),
    )

    gl_rows = [
        {"name": "GL-1", "account": "Cash", "letter": "", "voucher_detail_no": "ROW-1"},
        {"name": "GL-2", "account": "Cash", "letter": "", "voucher_detail_no": "ROW-2"},
    ]

    with (
        patch(
            "karam_finance.letter_reconciliation.utils.doc_events._is_merge_prevention_enabled",
            return_value=True,
        ),
        patch(
            "karam_finance.letter_reconciliation.utils.doc_events.frappe.get_all",
            return_value=gl_rows,
        ),
        patch(
            "karam_finance.letter_reconciliation.utils.doc_events."
            "frappe.db.bulk_update"
        ) as mock_bulk,
    ):
        lr_doc_events.journal_entry_on_update_after_submit(doc)

    mock_bulk.assert_called_once()
    call_args = mock_bulk.call_args
    assert call_args
    updates = cast("dict[str, dict[str, str]]", call_args.args[1])
    assert updates == {"GL-1": {"letter": "L1"}, "GL-2": {"letter": "L2"}}
