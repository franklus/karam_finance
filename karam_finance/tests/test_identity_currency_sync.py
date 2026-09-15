"""Identity-currency Sync preserves source company amounts for foreign accounts."""

from typing import Any, override

import frappe
from frappe.tests import IntegrationTestCase
from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.orchestrator import (
    sync_reporting_currency_entries,
)


class TestIdentityCurrencySync(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "identity_review_" + frappe.generate_hash(length=8)
        previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, previous_user)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        # Sync is site-wide. Isolate its inputs within this test transaction.
        for doctype in ("Reporting Currency GLE", "GL Entry", "Currency Exchange"):
            frappe.db.delete(doctype)
        self._insert("Company", self.token, default_currency="EUR")
        self._insert(
            "Account",
            self.token,
            company=self.token,
            account_currency="USD",
            account_type="Bank",
        )
        self._insert(
            "Account",
            self.token + "EUR",
            company=self.token,
            account_currency="EUR",
            account_type="Income Account",
            root_type="Income",
            report_type="Profit and Loss",
        )
        settings = frappe.get_single("Reporting Currency Settings")
        settings.update(
            {
                "reporting_currency": "EUR",
                "last_sync_timestamp": None,
                "last_ce_sync_timestamp": None,
            }
        )
        settings.set("rc_parameters", [])
        settings.append(
            "rc_parameters",
            {
                "doe_posting_date": "2025-12-31",
                "exchange_rate": 1,
                "profit_account": self.token + "EUR",
                "loss_account": self.token + "EUR",
            },
        )
        settings.save()
        for suffix, currency, debit, credit, account_debit, account_credit in (
            ("debit", "USD", 100, 0, 120, 0),
            ("credit", "USD", 0, 40, 0, 48),
            ("domestic", "EUR", 0, 60, 0, 60),
        ):
            self._insert(
                "GL Entry",
                self.token + suffix,
                company=self.token,
                account=self.token if currency == "USD" else self.token + "EUR",
                account_currency=currency,
                debit=debit,
                credit=credit,
                debit_in_account_currency=account_debit,
                credit_in_account_currency=account_credit,
                posting_date="2025-06-30",
                fiscal_year="2025",
                docstatus=1,
                is_cancelled=0,
                voucher_type="Journal Entry",
                voucher_no=self.token,
                is_opening="No",
            )

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()

    def _reporting_rows(self) -> dict[str, Any]:
        names = frappe.get_list(
            "Reporting Currency GLE",
            filters={"company": self.token},
            pluck="name",
            limit=0,
        )
        rows = [frappe.get_doc("Reporting Currency GLE", name) for name in names]
        return {row.get("gl_entry"): row for row in rows}

    def test_foreign_accounts_copy_company_amounts_without_exchange_records(
        self,
    ) -> None:
        assert not frappe.get_list("Currency Exchange", pluck="name", limit=1)
        result = sync_reporting_currency_entries(self.token, "Administrator")
        assert result["inserted"] == 3 and result["errors"] == 0
        rows = self._reporting_rows()
        assert len(rows) == 3
        assert [
            (
                rows[self.token + suffix].get("reporting_debit"),
                rows[self.token + suffix].get("reporting_credit"),
            )
            for suffix in ("debit", "credit", "domestic")
        ] == [(100, 0), (0, 40), (0, 60)]
        for suffix in ("debit", "credit"):
            row = rows[self.token + suffix]
            assert row.get("reporting_currency") == "EUR"
            assert row.get("exchange_rate") == row.get("source_exchange_rate") == 1
            assert not row.get("currency_exchange") and not row.get("date")
            assert not row.get("exchange_rate_application")

    def test_incremental_and_repeated_full_sync_preserve_company_precision(
        self,
    ) -> None:
        sync_reporting_currency_entries(self.token, "Administrator")
        frappe.db.set_value("GL Entry", self.token + "debit", "debit", 100.1234)
        frappe.db.set_value(
            "GL Entry",
            self.token + "domestic",
            {"credit": 60.1234, "credit_in_account_currency": 60.1234},
        )
        result = sync_reporting_currency_entries(self.token, "Administrator")
        assert result["sync_mode"].startswith("Incremental")
        row = self._reporting_rows()[self.token + "debit"]
        assert row.get("reporting_debit") == row.get("debit") == 100.1234
        assert row.get("debit_amount_in_account_currency") == 120
        frappe.db.set_single_value(
            "Reporting Currency Settings", "last_sync_timestamp", None
        )
        for _ in range(2):
            result = sync_reporting_currency_entries(self.token, "Administrator")
            assert result["sync_mode"].startswith("Full Sync")
            rows = self._reporting_rows()
            assert len(rows) == 3
            assert rows[self.token + "debit"].get("reporting_debit") == 100.1234
            assert rows[self.token + "domestic"].get("reporting_credit") == 60.1234
            assert rows[self.token + "credit"].get("reporting_credit") == 40
            assert rows[self.token + "debit"].get("exchange_rate") == 1

    def test_genuine_company_conversion_still_requires_an_exchange_record(self) -> None:
        frappe.db.set_single_value(
            "Reporting Currency Settings", "reporting_currency", "GBP"
        )
        with self.assertRaisesRegex(
            frappe.ValidationError, "Missing Currency Exchange: EUR-GBP"
        ):
            sync_reporting_currency_entries(self.token, "Administrator")
        assert not self._reporting_rows()

    def test_existing_reporting_account_copy_keeps_its_amounts_and_metadata(
        self,
    ) -> None:
        frappe.db.set_value("Company", self.token, "default_currency", "GBP")
        frappe.db.set_value("Account", self.token, "account_currency", "EUR")
        frappe.db.set_value(
            "GL Entry", {"company": self.token}, "account_currency", "EUR"
        )
        sync_reporting_currency_entries(self.token, "Administrator")
        row = self._reporting_rows()[self.token + "debit"]
        assert row.get("reporting_debit") == 120 and row.get("debit") == 100
        assert row.get("source_exchange_rate") == 1 and row.get("exchange_rate") == 0
        assert not row.get("currency_exchange") and not row.get("date")
        assert not row.get("exchange_rate_application")

    def test_real_direct_and_inverse_conversion_keep_their_rate_evidence(self) -> None:
        frappe.db.set_single_value(
            "Reporting Currency Settings", "reporting_currency", "GBP"
        )
        for application, source, target, rate, amount, multiplier in (
            ("Direct", "EUR", "GBP", 2, 200, 2),
            ("Inverse", "GBP", "EUR", 4, 25, 0.25),
        ):
            with self.subTest(application=application):
                frappe.db.savepoint("real_conversion")
                self._insert(
                    "Currency Exchange",
                    self.token,
                    from_currency=source,
                    to_currency=target,
                    exchange_rate=rate,
                    date="2025-01-01",
                )
                sync_reporting_currency_entries(self.token, "Administrator")
                row = self._reporting_rows()[self.token + "debit"]
                assert row.get("reporting_debit") == amount
                assert row.get("currency_exchange") == self.token
                assert str(row.get("date")) == "2025-01-01"
                assert row.get("exchange_rate_application") == application
                assert row.get("source_exchange_rate") == rate
                assert row.get("exchange_rate") == multiplier
                frappe.db.rollback(save_point="real_conversion")
