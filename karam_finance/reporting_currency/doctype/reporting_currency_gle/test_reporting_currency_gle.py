# Copyright (c) 2026, Noospheric
# See license.txt

"""Tests for Reporting Currency GLE DocType."""

from types import SimpleNamespace
from typing import cast, override
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe
import pytest

from karam_finance.reporting_currency.doctype.reporting_currency_gle import (
    reporting_currency_gle as module,
)


def _raise_validation(message: str, **_: object) -> None:
    raise frappe.ValidationError(message)


def _reporting_doe_value(fieldname: str) -> int | None:
    return 1 if fieldname == "reporting_doe" else None


def _non_doe_value(_fieldname: str) -> int:
    return 0


class TestReportingCurrencyGLE(TestCase):
    """Test cases for Reporting Currency GLE."""

    @override
    def setUp(self) -> None:
        self.frappe = MagicMock()
        self.frappe._ = str
        self.frappe.ValidationError = frappe.ValidationError
        self.frappe.throw.side_effect = _raise_validation
        self.enterContext(patch.object(module, "frappe", self.frappe))

    def test_manual_name_helpers_cover_empty_malformed_and_valid_sequences(
        self,
    ) -> None:
        assert module._next_manual_number([]) == 1
        assert module._next_manual_number([("",)]) == 1
        assert module._next_manual_number([("KE-RCMAN-2026",)]) == 1
        assert module._next_manual_number([("KE-RCMAN-2026-xx",)]) == 1
        assert module._next_manual_number([("KE-RCMAN-2026-00009",)]) == 10

        with patch.object(
            self.frappe.db, "sql", return_value=[("KE-RCMAN-2026-00009",)]
        ) as sql:
            assert (
                module._generate_manual_entry_name("2026-05-01")
                == "KE-RCMAN-2026-00010"
            )
        statement = sql.call_args.args[0]
        assert "KE-RCMAN-2026-%" in statement
        assert "ORDER BY name DESC" in statement
        assert "LIMIT 1" in statement

    def test_manual_name_rejects_invalid_date(self) -> None:
        with (
            patch.object(module, "get_datetime", return_value=None),
            pytest.raises(frappe.ValidationError, match="Invalid Posting Date"),
        ):
            module._generate_manual_entry_name("bad-date")

    def test_lifecycle_marks_manual_entries_and_protects_doe_records(self) -> None:
        manual = cast(
            module.ReportingCurrencyGLE,
            SimpleNamespace(name=None, gl_entry=None, posting_date="2026-05-01"),
        )
        with patch.object(
            module, "_generate_manual_entry_name", return_value="KE-RCMAN-2026-00001"
        ):
            module.ReportingCurrencyGLE.autoname(manual)
        assert manual.name == "KE-RCMAN-2026-00001"
        module.ReportingCurrencyGLE.before_insert(manual)
        assert manual.manual_entry == 1
        linked = cast(module.ReportingCurrencyGLE, SimpleNamespace(gl_entry="GL-1"))
        module.ReportingCurrencyGLE.before_insert(linked)
        assert not hasattr(linked, "manual_entry")
        named = cast(
            module.ReportingCurrencyGLE,
            SimpleNamespace(
                name="RC-EXISTING",
                gl_entry=None,
                posting_date="2026-05-01",
            ),
        )
        module.ReportingCurrencyGLE.autoname(named)
        assert named.name == "RC-EXISTING"

        doe = cast(module.ReportingCurrencyGLE, SimpleNamespace(reporting_doe=1))
        with pytest.raises(frappe.ValidationError, match="cannot be deleted"):
            module.ReportingCurrencyGLE.on_trash(doe)
        module.ReportingCurrencyGLE.on_trash(
            cast(module.ReportingCurrencyGLE, SimpleNamespace(reporting_doe=0))
        )

    def test_before_save_rejects_existing_doe_record(self) -> None:
        record = cast(
            module.ReportingCurrencyGLE,
            SimpleNamespace(name="RC-1", reporting_doe=1, is_new=lambda: False),
        )
        old = SimpleNamespace(get=_reporting_doe_value)
        with (
            patch.object(self.frappe, "get_doc", return_value=old),
            pytest.raises(frappe.ValidationError, match="cannot be edited"),
        ):
            module.ReportingCurrencyGLE.before_save(record)

        unprotected = cast(
            module.ReportingCurrencyGLE,
            SimpleNamespace(name="RC-2", reporting_doe=0, is_new=lambda: False),
        )
        module.ReportingCurrencyGLE.before_save(unprotected)
        old_non_doe = SimpleNamespace(get=_non_doe_value)
        with patch.object(self.frappe, "get_doc", return_value=old_non_doe):
            module.ReportingCurrencyGLE.before_save(record)

    def test_validate_enforces_configured_currency_and_doe_immutability(self) -> None:
        self.frappe.db.get_single_value.return_value = None
        unconfigured = cast(
            module.ReportingCurrencyGLE,
            SimpleNamespace(
                is_new=lambda: True,
                gl_entry=None,
                reporting_doe=0,
                reporting_currency=None,
            ),
        )
        with pytest.raises(
            frappe.ValidationError, match="Configure Reporting Currency"
        ):
            module.ReportingCurrencyGLE.validate(unconfigured)

        self.frappe.db.get_single_value.return_value = "USD"
        manual = cast(
            module.ReportingCurrencyGLE,
            SimpleNamespace(
                is_new=lambda: True,
                gl_entry=None,
                reporting_doe=0,
                reporting_currency="LBP",
            ),
        )
        module.ReportingCurrencyGLE.validate(manual)
        assert manual.reporting_currency == "USD"

        mismatch = cast(
            module.ReportingCurrencyGLE,
            SimpleNamespace(
                is_new=lambda: False,
                gl_entry="GL-1",
                reporting_doe=0,
                reporting_currency="EUR",
            ),
        )
        with pytest.raises(frappe.ValidationError, match="must be USD"):
            module.ReportingCurrencyGLE.validate(mismatch)

        doe = cast(
            module.ReportingCurrencyGLE,
            SimpleNamespace(
                is_new=lambda: False,
                gl_entry="GL-1",
                reporting_doe=1,
                reporting_currency="USD",
            ),
        )
        with pytest.raises(frappe.ValidationError, match="cannot be edited"):
            module.ReportingCurrencyGLE.validate(doe)
