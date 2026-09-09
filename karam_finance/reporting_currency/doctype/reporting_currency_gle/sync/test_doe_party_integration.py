"""MariaDB DOE storage and party report regression; fixtures bypass lifecycle."""

import importlib
from tempfile import TemporaryDirectory
from typing import Any, override
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync import doe
from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync import (
    doe_metadata_repair as repair,
)


class TestDoePartyIntegration(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "doe_port_" + frappe.generate_hash(length=10)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, previous_user)
        self._insert("Company", self.token, default_currency="LBP")
        self._insert(
            "Supplier", self.token, supplier_name=self.token, default_currency="GBP"
        )
        for suffix, kind, currency in (
            ("A", "Payable", "EUR"),
            ("B", "Payable", "EUR"),
            ("Profit", "Income Account", "USD"),
            ("Loss", "Expense Account", "USD"),
        ):
            self._insert(
                "Account",
                self.token + suffix,
                account_number=self.token + suffix,
                account_type=kind,
                account_currency=currency,
                company=self.token,
                is_group=0,
                report_type="Balance Sheet" if kind == "Payable" else "Profit and Loss",
            )
        self.query = importlib.import_module(
            "karam_finance.reporting_currency.report."
            "trial_balance_for_party_(reporting_currency).tbfpr_query"
        )

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()

    def test_stored_pairs_group_by_account_and_roll_forward(self) -> None:
        records = self._records()
        doe._bulk_insert_doe_records(records)
        stored = frappe.get_all(
            "Reporting Currency GLE",
            filters={"company": self.token},
            fields=[
                "name",
                "account",
                "party",
                "party_type",
                "voucher_no",
                "is_opening",
                "reporting_debit",
                "reporting_credit",
            ],
            limit_page_length=0,
        )
        assert len(stored) == 6
        assert len({row.name for row in stored}) == 6
        self._assert_pairs(stored)
        filters = frappe._dict(
            company=self.token,
            party_type="Supplier",
            party=self.token,
            from_date="2025-01-01",
            to_date="2025-12-31",
        )
        current = self.query.get_reporting_currency_balances(filters)[self.token]
        assert [(row["account"], row["debit"], row["credit"]) for row in current] == [
            (self.token + "A", 20, 0),
            (self.token + "B", 0, 10),
        ]
        assert all(row["account_currency"] == "EUR" for row in current)
        filters.update(from_date="2026-01-01", to_date="2026-12-31")
        following = self.query.get_reporting_currency_balances(filters)[self.token]
        assert [(row["opening_debit"], row["opening_credit"]) for row in following] == [
            (20, 0),
            (0, 10),
        ]

    def test_legacy_metadata_repair_restores_exact_database_snapshot(self) -> None:
        records = self._records()[:2]
        sequence = int.from_bytes(self.token.encode()) % 1_000_000_000
        for index, record in enumerate(records):
            record.update(
                name=f"KE-RCDOE-GLE-2025-{sequence + index:05d}",
                voucher_no="DOE-2025-legacy",
                is_opening=None,
                party=self.token,
                party_type="Supplier",
            )
            self._insert("Reporting Currency GLE", record.pop("name"), **record)
        before = repair._read_rows(self.token)
        preview = repair.preview_doe_metadata_repair(self.token)
        assert preview["pair_count"] == 1
        assert preview["skipped"] == []
        with (
            TemporaryDirectory() as directory,
            patch.object(repair.frappe, "get_site_path", return_value=directory),
        ):
            result = repair.apply_doe_metadata_repair(
                self.token, preview["fingerprint"]
            )
            assert result["updated"] == 2
            assert repair.preview_doe_metadata_repair(self.token)["changes"] == []
            assert repair.restore_doe_metadata(result["recovery_file"]) == {
                "restored": 2
            }
        assert repair._read_rows(self.token) == before

    @staticmethod
    def _assert_pairs(stored: list[Any]) -> None:
        vouchers: dict[str, list[Any]] = {}
        for row in stored:
            vouchers.setdefault(row.voucher_no, []).append(row)
            assert row.is_opening == "No"
            if row.account.endswith(("Profit", "Loss")):
                assert not row.party and not row.party_type
        assert len(vouchers) == 3
        for reference, pair in vouchers.items():
            assert len(pair) == 2
            assert reference in {row.name for row in pair}
            assert sum(row.reporting_debit - row.reporting_credit for row in pair) == 0

    def _records(self) -> list[dict[str, Any]]:
        counter = {"year": 2025, "counter": 1}
        records = []
        for suffix, amount in (("A", 90), ("B", 110), ("A", 90)):
            group = frappe._dict(
                account=self.token + suffix,
                account_currency="EUR",
                account_type="Payable",
                party=self.token,
                party_type="Supplier",
                total_debit=8300,
                total_credit=0,
                total_reporting_debit=amount,
                total_reporting_credit=0,
            )
            pair, _ = doe._create_doe_records_for_groups(
                account_groups=[group],
                company=self.token,
                reporting_currency="USD",
                exchange_rate=83,
                doe_posting_date="2025-12-31",
                profit_account=self.token + "Profit",
                loss_account=self.token + "Loss",
                fiscal_year="2025",
                name_counter=counter,
                prior_doe_by_group={},
                profit_loss_currency_map={
                    self.token + "Profit": "USD",
                    self.token + "Loss": "USD",
                },
            )
            records.extend(pair)
        return records
