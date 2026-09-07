"""Regression coverage for reporting-currency precision and conversion."""

from __future__ import annotations

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from . import conversion, utils


class TestReportingCurrencyPrecision(FrappeTestCase):
    """Currency metadata must be interpreted as fraction units."""

    def test_fraction_units_are_converted_to_decimal_places(self) -> None:
        with patch.object(utils, "frappe") as frappe_mock:
            frappe_mock.db.get_value.return_value = 100
            assert utils.get_currency_precision("USD") == 2

            frappe_mock.db.get_value.return_value = 1000
            assert utils.get_currency_precision("KWD") == 3

            frappe_mock.db.get_value.return_value = 1
            assert utils.get_currency_precision("JPY") == 0

    def test_missing_currency_precision_uses_safe_fallback(self) -> None:
        with patch.object(utils, "frappe") as frappe_mock:
            frappe_mock.db.get_value.return_value = None
            frappe_mock.get_precision.return_value = None

            assert utils.get_currency_precision("UNKNOWN") == 2

    def test_conversion_keeps_nonzero_kwd_and_rounds_usd(self) -> None:
        record = {"debit": 1.23456789, "credit": 0}

        with patch.object(conversion, "get_currency_precision", return_value=3):
            debit, credit = conversion.convert_amounts(record, 1, "direct", "KWD")
        assert debit == 1.235
        assert credit == 0

        with patch.object(conversion, "get_currency_precision", return_value=2):
            debit, credit = conversion.convert_amounts(record, 1, "direct", "USD")
        assert debit == 1.23
        assert credit == 0

    def test_whole_unit_conversion_does_not_use_invalid_precision(self) -> None:
        with patch.object(conversion, "get_currency_precision", return_value=0):
            debit, credit = conversion.convert_amounts(
                {"debit": 1.234, "credit": 0}, 1, "direct", "JPY"
            )

        assert debit == 1.0
        assert credit == 0
