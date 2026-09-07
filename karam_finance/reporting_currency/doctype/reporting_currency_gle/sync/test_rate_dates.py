"""Invalid date sentinels must fail before rate lookup or name allocation."""

from datetime import date
from unittest import TestCase
from unittest.mock import patch

import frappe

from karam_finance.reporting_currency.doctype.reporting_currency_gle import (
    reporting_currency_gle as controller,
)

from . import exchange_rates


class TestRateDates(TestCase):
    def test_invalid_lookup_date_does_not_enter_the_date_cache(self) -> None:
        cache: dict[str, date] = {}
        with (
            patch.object(exchange_rates, "_", side_effect=str),
            self.assertRaisesRegex(frappe.ValidationError, "Invalid date"),
        ):
            exchange_rates.get_applicable_rate(
                "0000-00-00", "LBP", "USD", [], [], cache
            )
        assert cache == {}

    def test_valid_lookup_still_uses_the_latest_applicable_rate(self) -> None:
        dates = [date(2026, 1, 1), date(2026, 2, 1)]
        timeline = [{"date": dates[0], "rate": 1}, {"date": dates[1], "rate": 2}]
        cache: dict[str, date] = {}
        result = exchange_rates.get_applicable_rate(
            "2026-02-15", "LBP", "USD", timeline, dates, cache
        )
        assert result is timeline[1]
        assert cache == {"2026-02-15": date(2026, 2, 15)}

    def test_invalid_manual_date_fails_before_database_access(self) -> None:
        with (
            patch.object(controller.frappe, "_", side_effect=str),
            patch.object(controller.frappe, "db") as database,
            self.assertRaisesRegex(frappe.ValidationError, "Invalid Posting Date"),
        ):
            controller._generate_manual_entry_name("0000-00-00")
        database.sql.assert_not_called()
