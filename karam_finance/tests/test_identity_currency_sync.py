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

    def _assert_persisted_rows(self, source_names: set[str], manual_name: str) -> None:
        assert frappe.db.count("Reporting Currency GLE", {"company": self.token}) == 4
        persisted = frappe.db.get_all(
            "Reporting Currency GLE",
            filters={"company": self.token},
            fields=["name", "gl_entry", "manual_entry"],
            limit=0,
        )
        assert {
            row.gl_entry for row in persisted if not row.manual_entry
        } == source_names
        assert [row.name for row in persisted if row.manual_entry] == [manual_name]

    def _generated_snapshot(self, source_names: set[str]) -> dict[str, dict[str, Any]]:
        return {
            name: {
                field: row.get(field)
                for field in (
                    "reporting_debit",
                    "reporting_credit",
                    "debit",
                    "credit",
                    "exchange_rate",
                    "source_exchange_rate",
                    "exchange_rate_application",
                    "reporting_currency",
                    "manual_entry",
                )
            }
            for name, row in self._reporting_rows().items()
            if name in source_names
        }

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

    def test_repeated_full_sync_preserves_precision_and_manual_rows(
        self,
    ) -> None:
        sync_reporting_currency_entries(self.token, "Administrator")
        manual_name = self.token + "manual"
        self._insert(
            "Reporting Currency GLE",
            manual_name,
            company=self.token,
            account=self.token + "EUR",
            account_currency="EUR",
            posting_date="2025-06-30",
            reporting_currency="EUR",
            debit=10,
            reporting_debit=10,
            transaction_exchange_rate=1,
            manual_entry=1,
        )
        assert (
            frappe.db.get_value("Reporting Currency GLE", manual_name, "manual_entry")
            == 1
        )
        manual_fields = [
            "company",
            "account",
            "account_currency",
            "posting_date",
            "reporting_currency",
            "debit",
            "reporting_debit",
            "transaction_exchange_rate",
            "manual_entry",
            "gl_entry",
        ]
        manual_before = frappe.db.get_value(
            "Reporting Currency GLE", manual_name, manual_fields, as_dict=True
        )
        frappe.db.set_value("GL Entry", self.token + "debit", "debit", 100.1234)
        frappe.db.set_value(
            "GL Entry",
            self.token + "domestic",
            {"credit": 60.1234, "credit_in_account_currency": 60.1234},
        )
        result = sync_reporting_currency_entries(self.token, "Administrator")
        assert result["sync_mode"] == "Full Sync"
        row = self._reporting_rows()[self.token + "debit"]
        assert row.get("reporting_debit") == row.get("debit") == 100.1234
        assert row.get("debit_amount_in_account_currency") == 120
        source_names = {
            self.token + suffix for suffix in ("debit", "credit", "domestic")
        }

        first_generated = self._generated_snapshot(source_names)
        rows = self._reporting_rows()
        assert set(rows) == source_names | {None}
        assert len(rows) == 4
        self._assert_persisted_rows(source_names, manual_name)
        frappe.db.set_single_value(
            "Reporting Currency Settings", "last_sync_timestamp", None
        )
        for _ in range(2):
            result = sync_reporting_currency_entries(self.token, "Administrator")
            assert result["sync_mode"] == "Full Sync"
            rows = self._reporting_rows()
            assert set(rows) == source_names | {None}
            assert len(rows) == 4
            self._assert_persisted_rows(source_names, manual_name)
            generated = self._generated_snapshot(source_names)
            assert generated == first_generated
            manual = frappe.db.get_value(
                "Reporting Currency GLE",
                manual_name,
                manual_fields,
                as_dict=True,
            )
            assert manual and dict(manual) == dict(manual_before)
            assert generated[self.token + "debit"]["reporting_debit"] == 100.1234
            assert generated[self.token + "domestic"]["reporting_credit"] == 60.1234
            assert generated[self.token + "credit"]["reporting_credit"] == 40
            assert generated[self.token + "debit"]["exchange_rate"] == 1

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
