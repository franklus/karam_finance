"""DOE posts representable currency adjustments as balanced, attributed pairs."""

from decimal import Decimal
from typing import Any, override

import frappe
from frappe.tests import IntegrationTestCase
from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.doe import (
    compute_doe_inline,
)


class TestDOEPrecision(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "precision_review_" + frappe.generate_hash(length=8)
        previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, previous_user)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        # DOE selects its company from the reporting ledger; isolate that input.
        frappe.db.delete("Reporting Currency GLE")
        self._insert("Company", self.token, default_currency="EUR")
        self._insert("Supplier", self.token, supplier_name=self.token)
        self._insert(
            "Account",
            self.token,
            company=self.token,
            account_currency="EUR",
            account_type="Payable",
            root_type="Liability",
            report_type="Balance Sheet",
        )
        self._insert(
            "Account",
            self.token + "FX",
            company=self.token,
            account_currency="KWD",
            root_type="Income",
            report_type="Profit and Loss",
            account_type="Income Account",
        )
        self._insert(
            "Fiscal Year",
            self.token,
            year="2040",
            year_start_date="2040-01-01",
            year_end_date="2040-12-31",
        )
        frappe.db.set_value("Currency", "KWD", "fraction_units", 1000)
        settings = frappe.get_single("Reporting Currency Settings")
        settings.update(
            {
                "reporting_currency": "KWD",
                "last_sync_timestamp": None,
                "last_ce_sync_timestamp": None,
            }
        )
        settings.set("rc_parameters", [])
        settings.append(
            "rc_parameters",
            {
                "doe_posting_date": "2040-06-30",
                "exchange_rate": 2,
                "profit_account": self.token + "FX",
                "loss_account": self.token + "FX",
            },
        )
        settings.save()
        self._insert(
            "Reporting Currency GLE",
            self.token + "source",
            company=self.token,
            account=self.token,
            account_currency="EUR",
            reporting_currency="KWD",
            posting_date="2040-01-01",
            docstatus=1,
            reporting_doe=0,
            manual_entry=0,
            party_type="Supplier",
            party=self.token,
            debit=0,
            credit=10,
            reporting_debit=0,
            reporting_credit=5.005,
            is_cancelled=0,
        )

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()

    def _doe_rows(self) -> list[Any]:
        names = frappe.get_list(
            "Reporting Currency GLE",
            filters={"company": self.token, "reporting_doe": 1},
            pluck="name",
            limit=0,
        )
        return [frappe.get_doc("Reporting Currency GLE", name) for name in names]

    def test_five_fils_adjustment_is_saved_as_a_balanced_kwd_pair(self) -> None:
        # EUR 10 / rate 2 = KWD 5 owed. KWD 5.005 currently owed needs a .005 debit.
        result = compute_doe_inline()
        assert result["success"] and result["accounts_processed"] == 1
        assert result["records_created"] == 2
        rows = self._doe_rows()
        assert len(rows) == 2
        primary = next(row for row in rows if row.get("account") == self.token)
        offset = next(row for row in rows if row.get("account") == self.token + "FX")
        assert (primary.get("reporting_debit"), primary.get("reporting_credit")) == (
            0.005,
            0,
        )
        assert (offset.get("reporting_debit"), offset.get("reporting_credit")) == (
            0,
            0.005,
        )
        assert (primary.get("party_type"), primary.get("party")) == (
            "Supplier",
            self.token,
        )
        assert not offset.get("party") and not offset.get("party_type")
        assert primary.get("voucher_no") == offset.get("voucher_no")
        assert primary.name != offset.name
        self._assert_reporting_total(rows, total="0.005")

    @staticmethod
    def _assert_reporting_total(rows: list[Any], *, total: str) -> None:
        assert sum(Decimal(str(row.get("reporting_debit"))) for row in rows) == Decimal(
            total
        )
        assert sum(
            Decimal(str(row.get("reporting_credit"))) for row in rows
        ) == Decimal(total)

    def test_negative_small_adjustment_and_repeat_generation_preserve_the_pair(
        self,
    ) -> None:
        frappe.db.set_value(
            "Reporting Currency GLE", self.token + "source", "reporting_credit", 4.995
        )
        for _ in range(2):
            result = compute_doe_inline()
            assert result["accounts_processed"] == 1 and result["records_created"] == 2
            amounts = {
                row.get("account"): (
                    row.get("reporting_debit"),
                    row.get("reporting_credit"),
                )
                for row in self._doe_rows()
            }
            assert amounts == {self.token: (0, 0.005), self.token + "FX": (0.005, 0)}

    def _configure_case(
        self, currency: str, *, units: int, reporting_credit: float
    ) -> None:
        frappe.db.set_value("Currency", currency, "fraction_units", units)
        frappe.db.set_single_value(
            "Reporting Currency Settings", "reporting_currency", currency
        )
        frappe.db.set_value("Account", self.token + "FX", "account_currency", currency)
        frappe.db.set_value(
            "Reporting Currency GLE",
            self.token + "source",
            {
                "reporting_currency": currency,
                "reporting_credit": reporting_credit,
            },
        )

    def test_amounts_rounding_to_zero_create_no_pairs(self) -> None:
        for currency, units, credit in (
            ("KWD", 1000, 5.0004),
            ("KWD", 1000, 4.9996),
            ("USD", 100, 5.004),
            ("USD", 100, 4.996),
            ("JPY", 1, 5.4),
            ("JPY", 1, 4.6),
        ):
            with self.subTest(currency=currency, reporting_credit=credit):
                self._configure_case(currency, units=units, reporting_credit=credit)
                result = compute_doe_inline()
                assert result["success"] and result["accounts_processed"] == 0
                assert result["records_created"] == 0
                assert not self._doe_rows()

    def test_pairs_use_the_rounded_amount_in_both_directions(self) -> None:
        for currency, units, credit, expected in (
            ("KWD", 1000, 5.0006, 0.001),
            ("KWD", 1000, 4.9994, -0.001),
            ("USD", 100, 5.006, 0.01),
            ("USD", 100, 4.994, -0.01),
            ("JPY", 1, 5.6, 1),
            ("JPY", 1, 4.4, -1),
        ):
            with self.subTest(currency=currency, reporting_credit=credit):
                self._configure_case(currency, units=units, reporting_credit=credit)
                result = compute_doe_inline()
                assert (
                    result["accounts_processed"] == 1 and result["records_created"] == 2
                )
                rows = self._doe_rows()
                amounts = {
                    row.get("account"): row.get("reporting_debit")
                    - row.get("reporting_credit")
                    for row in rows
                }
                assert amounts == {self.token: expected, self.token + "FX": -expected}
                assert all(row.get("debit") == row.get("credit") == 0 for row in rows)

    def test_cumulative_parameters_use_rounded_prior_doe_for_each_party(self) -> None:
        frappe.db.set_value(
            "Reporting Currency GLE", self.token + "source", "reporting_credit", 5.0006
        )
        other_party = self.token + "other"
        self._insert("Supplier", other_party, supplier_name=other_party)
        other = frappe.copy_doc(
            frappe.get_doc("Reporting Currency GLE", self.token + "source")
        )
        other.name = self.token + "second"
        other.update({"party": other_party, "reporting_credit": 5.0016})
        other.db_insert()
        settings = frappe.get_single("Reporting Currency Settings")
        settings.append(
            "rc_parameters",
            {
                "doe_posting_date": "2040-07-31",
                "exchange_rate": 2,
                "profit_account": self.token + "FX",
                "loss_account": self.token + "FX",
            },
        )
        settings.save()
        for _ in range(2):
            result = compute_doe_inline()
            assert result["accounts_processed"] == 2 and result["records_created"] == 4
            rows = self._doe_rows()
            primary = {
                row.get("party"): row.get("reporting_debit")
                for row in rows
                if row.get("account") == self.token
            }
            assert primary == {self.token: 0.001, other_party: 0.002}
            assert {str(row.get("posting_date")) for row in rows} == {"2040-06-30"}
            self._assert_reporting_total(rows, total="0.003")
