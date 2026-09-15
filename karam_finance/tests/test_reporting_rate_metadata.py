"""Persist reporting-rate evidence without changing monetary calculations."""

from datetime import date
from decimal import Decimal
from typing import Any, override
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from karam_finance.common import db_schema
from karam_finance.reporting_currency.doctype.reporting_currency_gle.reporting_currency_gle import (
    ReportingCurrencyGLE,
)
from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync import (
    conversion,
    doe,
    phases,
)


class TestReportingRateMetadata(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "rate_" + frappe.generate_hash(length=8)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, previous_user)
        self._insert("Company", self.token, default_currency="LBP")
        for suffix, kind in (("Payable", "Payable"), ("Profit", "Income Account")):
            self._insert(
                "Account",
                self.token + suffix,
                account_number=self.token + suffix,
                account_type=kind,
                account_currency="EUR" if suffix == "Payable" else "LBP",
                company=self.token,
                is_group=0,
            )

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()

    def _converted(self, direction: str, rate: float) -> dict[str, Any]:
        gle = {
            "name": self.token + direction,
            "company": self.token,
            "account": self.token + "Payable",
            "account_currency": "EUR",
            "posting_date": "2025-12-31",
            "debit": 121456125000,
            "credit": 0,
        }
        timeline = [{"date": date(2025, 1, 1), "rate": rate, "direction": direction}]
        return conversion.process_gl_entry(
            gle, timeline, [date(2025, 1, 1)], "LBP", "USD"
        )

    def test_converted_rates_survive_database_storage_without_amount_changes(
        self,
    ) -> None:
        for direction, rate, application in (
            ("inverse", 89500, "Inverse"),
            ("direct", 0.00000001, "Direct"),
        ):
            with self.subTest(direction=direction):
                record = self._converted(direction, rate)
                before = (record["reporting_debit"], record["reporting_credit"])
                self._insert(
                    "Reporting Currency GLE",
                    record.pop("gl_entry"),
                    **{k: v for k, v in record.items() if k not in ("doctype", "name")},
                )
                stored = frappe.db.get_value(
                    "Reporting Currency GLE",
                    self.token + direction,
                    [
                        "exchange_rate",
                        "exchange_rate_application",
                        "source_exchange_rate",
                        "reporting_debit",
                        "reporting_credit",
                    ],
                    as_dict=True,
                )
                expected = Decimal(str(1 / rate if direction == "inverse" else rate))
                assert abs(Decimal(str(stored.exchange_rate)) - expected) < Decimal(
                    "0.000000001"
                )
                assert stored.exchange_rate > 0
                assert stored.exchange_rate_application == application
                assert stored.source_exchange_rate == rate
                assert (stored.reporting_debit, stored.reporting_credit) == before

    def test_account_copy_records_one_without_claiming_company_conversion(self) -> None:
        record = conversion.process_gl_entry(
            {"account_currency": "USD", "debit": 89500, "debit_in_account_currency": 1},
            [],
            [],
            "LBP",
            "USD",
        )
        assert record["reporting_debit"] == record["source_exchange_rate"] == 1
        assert record["exchange_rate"] == 0
        assert record["exchange_rate_application"] == ""
        assert record["currency_exchange"] is None

    def test_doe_pairs_store_source_rate_application_and_multiplier(self) -> None:
        self._insert(
            "Reporting Currency GLE",
            self.token + "source",
            company=self.token,
            account=self.token + "Payable",
            account_currency="EUR",
            party_type="Supplier",
            party="Supplier fixture",
            posting_date="2025-12-31",
            reporting_currency="USD",
            debit=895000,
            reporting_debit=5,
            reporting_doe=0,
            is_cancelled=0,
        )
        parameter = frappe._dict(
            idx=1,
            doe_posting_date="2025-12-31",
            exchange_rate=89500,
            profit_account=self.token + "Profit",
            loss_account=self.token + "Profit",
        )
        with patch("erpnext.accounts.utils.get_fiscal_year", return_value=("2025",)):
            records, count, _ = doe._process_doe_parameter(
                parameter,
                self.token,
                "USD",
                context=doe._DOEComputation(excluded_accounts_condition=""),
            )
        assert count == 1
        doe._bulk_insert_doe_records(records)
        stored = frappe.get_all(
            "Reporting Currency GLE",
            filters={"company": self.token, "reporting_doe": 1},
            fields=[
                "exchange_rate",
                "source_exchange_rate",
                "exchange_rate_application",
                "date",
                "reporting_debit",
                "reporting_credit",
                "party",
            ],
        )
        assert len(stored) == 2
        assert sum(r.reporting_debit - r.reporting_credit for r in stored) == 0
        assert sum(r.reporting_debit for r in stored) == 5
        for row in stored:
            assert row.exchange_rate > 0
            assert row.source_exchange_rate == 89500
            assert row.exchange_rate_application == "Inverse"
            assert row.date == date(2025, 12, 31)

    def test_manual_rate_is_not_inferred_from_amounts(self) -> None:
        doc = ReportingCurrencyGLE(
            {"doctype": "Reporting Currency GLE", "debit": 89500, "reporting_debit": 1}
        )
        with (
            patch(
                "karam_finance.reporting_currency.doctype.reporting_currency_gle.reporting_currency_gle.hold_ledger_lock"
            ),
            patch.object(frappe.db, "get_single_value", return_value="USD"),
        ):
            doc.validate()
            assert not doc.get("exchange_rate") and not doc.get("source_exchange_rate")
            assert not doc.get("exchange_rate_application")
            doc.source_exchange_rate = 89500
            doc.exchange_rate_application = "Inverse"
            doc.validate()
            assert doc.exchange_rate == 1 / 89500
            assert doc.get("reporting_debit") == 1 and doc.get("debit") == 89500
            doc.exchange_rate_application = "Direct"
            doc.validate()
            assert doc.exchange_rate == 89500
            doc.exchange_rate_application = ""
            doc.validate()
            assert not doc.exchange_rate and doc.source_exchange_rate == 89500
            doc.exchange_rate_application = "Inverse"
            for rate in (0, -1, float("inf"), float("nan")):
                doc.source_exchange_rate = rate
                with self.assertRaises(frappe.ValidationError):
                    doc.validate()

    def test_schema_keeps_rate_precision_and_integer_capacity(self) -> None:
        meta = frappe.get_meta("Reporting Currency GLE")
        assert not meta.has_field("rate_basis") and not meta.has_field("rate_quote")
        application = meta.get_field("exchange_rate_application")
        assert application is not None and application.options == "\nDirect\nInverse"
        fields = ["exchange_rate", "source_exchange_rate"]
        columns = db_schema._get_capacity_columns({"tabReporting Currency GLE": fields})
        for name in fields:
            field = meta.get_field(name)
            assert field is not None
            assert int(field.precision) == db_schema.REPORTING_RATE_PRECISION == 9
            assert field.length == db_schema.REPORTING_RATE_WIDTH == 35
            assert (
                columns[("tabReporting Currency GLE", name)].column_type
                == "decimal(35,9)"
            )
        with patch.object(frappe.db, "sql") as sql:
            db_schema._ensure_table_columns_capacity(
                "tabReporting Currency GLE", fields, columns
            )
        sql.assert_not_called()

    def test_native_fetch_copies_source_rate_and_date_without_refreshing_saved_values(
        self,
    ) -> None:
        self._insert(
            "Currency Exchange",
            self.token,
            from_currency="USD",
            to_currency="LBP",
            exchange_rate=89500,
            date="2025-01-01",
        )
        doc = ReportingCurrencyGLE(
            {"doctype": "Reporting Currency GLE", "currency_exchange": self.token}
        )
        assert doc.get_invalid_links() == ([], [])
        assert doc.source_exchange_rate == 89500
        assert str(doc.get("date")) == "2025-01-01"
        frappe.db.set_value(
            "Currency Exchange",
            self.token,
            {"exchange_rate": 90000, "date": "2025-02-01"},
        )
        fresh = ReportingCurrencyGLE(
            {"doctype": "Reporting Currency GLE", "currency_exchange": self.token}
        )
        assert fresh.get_invalid_links() == ([], [])
        assert fresh.source_exchange_rate == 90000
        assert doc.get_invalid_links() == ([], [])
        assert doc.source_exchange_rate == 89500
        assert str(doc.get("date")) == "2025-01-01"

    def test_full_sync_replaces_legacy_generated_rows_and_preserves_manual_rows(
        self,
    ) -> None:
        # The test savepoint restores the isolated CI site's original ledger.
        frappe.db.delete("Reporting Currency GLE")
        self._insert("Reporting Currency GLE", self.token + "manual", manual_entry=1)
        self._insert("Reporting Currency GLE", self.token + "old")
        record = self._converted("direct", 0.5)
        with patch.object(phases, "publish_sync_progress"):
            phases.run_insertion_phase(
                "event",
                None,
                [record],
                [{"name": record["gl_entry"]}],
                phases.InsertionContext(is_incremental=False, cutoff="2026-01-01"),
            )
        assert frappe.db.exists("Reporting Currency GLE", self.token + "manual")
        assert not frappe.db.exists("Reporting Currency GLE", self.token + "old")
        stored = frappe.db.get_value(
            "Reporting Currency GLE",
            {"gl_entry": self.token + "direct"},
            ["source_exchange_rate", "exchange_rate_application", "reporting_debit"],
            as_dict=True,
        )
        assert stored.source_exchange_rate == 0.5
        assert stored.exchange_rate_application == "Direct"
        assert stored.reporting_debit == record["reporting_debit"]
