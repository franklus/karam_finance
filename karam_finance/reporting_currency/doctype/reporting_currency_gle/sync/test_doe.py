"""Tests for party-wise DOE grouping."""

from typing import Any, override
from unittest import TestCase
from unittest.mock import Mock, patch

from frappe import _dict
from frappe.tests.utils import FrappeTestCase

from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync import doe


class TestPartyWiseDoe(FrappeTestCase):
    """Keep party-wise DOE calculation and carry-forward isolated per party."""

    def test_receivable_groups_create_party_pairs_and_isolate_carry_forward(
        self,
    ) -> None:
        """A prior DOE for one customer must not affect another customer's DOE."""
        customer_a = _party_group("CUST-A")
        customer_b = _party_group("CUST-B")
        prior_doe_by_group: dict[
            tuple[str, str, str | None, str | None], dict[str, float]
        ] = {}
        name_counter = {"year": 2025, "counter": 1}

        with patch.object(doe.frappe.db, "get_value", return_value="1100"):
            first_records, first_processed = doe._create_doe_records_for_groups(
                account_groups=[customer_a],
                prior_doe_by_group=prior_doe_by_group,
                name_counter=name_counter,
                **_DOE_ARGUMENTS,
            )

            second_records, second_processed = doe._create_doe_records_for_groups(
                account_groups=[customer_a, customer_b],
                prior_doe_by_group=prior_doe_by_group,
                name_counter=name_counter,
                **_DOE_ARGUMENTS,
            )

        assert first_processed == 1
        assert len(first_records) == 2
        assert first_records[0]["party"] == "CUST-A"
        assert first_records[0]["party_type"] == "Customer"
        assert first_records[1].get("party") is None
        assert first_records[1].get("party_type") is None
        assert all(record["is_opening"] == "No" for record in first_records)
        assert prior_doe_by_group[("Trade Debtors", "LBP", "Customer", "CUST-A")] == {
            "reporting_debit": 10.0,
            "reporting_credit": 0.0,
        }

        # Customer A is already brought to the DOE rate by the first pair. Only
        # Customer B needs a fresh pair in the next DOE parameter row.
        assert second_processed == 1
        assert len(second_records) == 2
        assert second_records[0]["party"] == "CUST-B"
        assert second_records[0]["party_type"] == "Customer"
        assert second_records[1].get("party") is None
        assert second_records[1].get("party_type") is None

    def test_same_party_name_in_different_doctypes_is_not_combined(self) -> None:
        customer = _party_group("SHARED-NAME")
        supplier = _party_group("SHARED-NAME")
        supplier.party_type = "Supplier"
        with patch.object(doe.frappe.db, "get_value", return_value="1100"):
            records, count = doe._create_doe_records_for_groups(
                account_groups=[customer, supplier],
                prior_doe_by_group={},
                name_counter={"year": 2025, "counter": 1},
                **_DOE_ARGUMENTS,
            )
        assert count == 2
        assert len(records) == 4
        assert (
            sum(row["reporting_debit"] - row["reporting_credit"] for row in records)
            == 0
        )
        assert {row.get("party_type") for row in records} == {
            "Customer",
            "Supplier",
            None,
        }

    def test_query_splits_only_receivable_and_payable_accounts_by_party(self) -> None:
        """The SQL grouping preserves account-level DOE for all other account types."""
        with patch.object(doe.frappe.db, "sql", return_value=[]) as sql:
            doe._get_accounts_with_totals(
                company="Karam",
                reporting_currency="USD",
                excluded_accounts_condition="",
                until_date="2025-12-31",
            )

        query = sql.call_args.args[0]
        assert "INNER JOIN `tabAccount` account" in query
        assert "account.account_type IN ('Receivable', 'Payable')" in query
        assert "THEN COALESCE(rc.party, '')" in query


_DOE_ARGUMENTS: dict[str, Any] = {
    "company": "Karam",
    "reporting_currency": "USD",
    "exchange_rate": 83.0,
    "doe_posting_date": "2025-12-31",
    "profit_account": "DOE Profit",
    "loss_account": "DOE Loss",
    "fiscal_year": "2025",
    "profit_loss_currency_map": {"DOE Profit": "USD", "DOE Loss": "USD"},
}


def _party_group(party: str) -> _dict[str, Any]:
    """Return a Receivable DOE group whose USD adjustment is +10."""
    return _dict(
        account="Trade Debtors",
        account_currency="LBP",
        account_type="Receivable",
        party=party,
        party_type="Customer",
        total_debit=8300.0,
        total_credit=0.0,
        total_reporting_debit=90.0,
        total_reporting_credit=0.0,
    )


class TestDoeWorkflow(TestCase):
    """Both entry points retain sorted cumulative processing and transaction ownership."""

    @override  # noqa: V105 - unittest lifecycle callback.
    def setUp(self) -> None:
        self.frappe_mock = Mock()
        settings = self.frappe_mock.get_single.return_value
        settings.reporting_currency = "USD"
        settings.rc_parameters = [
            _dict(
                idx=2,
                doe_posting_date="2025-12-31",
                exchange_rate=166,
                profit_account="DOE Profit",
                loss_account="DOE Loss",
            ),
            _dict(
                idx=1,
                doe_posting_date="2025-06-30",
                exchange_rate=83,
                profit_account="DOE Profit",
                loss_account="DOE Loss",
            ),
        ]
        self.frappe_mock.db.count.return_value = 1
        excluded_accounts: list[str] = []
        self.frappe_mock.db.sql_list.return_value = excluded_accounts
        self.frappe_mock.db.get_value.return_value = "1100"
        self.enterContext(patch.object(doe, "frappe", self.frappe_mock))
        self.enterContext(
            patch.object(doe, "get_reporting_company", return_value="Karam")
        )
        self.enterContext(patch.object(doe, "_publish_progress"))
        self.enterContext(
            patch("erpnext.accounts.utils.get_fiscal_year", return_value=("2025",))
        )
        self.enterContext(
            patch.object(
                doe,
                "_get_profit_loss_currency_map",
                return_value={"DOE Profit": "USD", "DOE Loss": "USD"},
            )
        )
        self.enterContext(
            patch.object(
                doe,
                "_get_starting_doe_number",
                return_value={"year": 2025, "counter": 1},
            )
        )
        self.enterContext(
            patch.object(
                doe, "_get_accounts_with_totals", return_value=[_party_group("CUST-A")]
            )
        )
        self.insert = self.enterContext(patch.object(doe, "_bulk_insert_doe_records"))

    def test_inline_preserves_sorted_cumulative_rows_without_commit(self) -> None:
        result = doe.compute_doe_inline()
        self._assert_cumulative_rows(result)
        self.frappe_mock.db.savepoint.assert_called_once_with("inline_doe_compute")
        self.frappe_mock.db.commit.assert_not_called()
        self.frappe_mock.get_single.return_value.db_set.assert_not_called()

    def test_background_preserves_rows_and_completion_commit(self) -> None:
        result = doe._compute_doe_background()
        self._assert_cumulative_rows(result)
        assert self.frappe_mock.db.commit.call_count == 2
        self.frappe_mock.get_single.return_value.db_set.assert_called_once()
        assert (
            self.frappe_mock.get_single.return_value.db_set.call_args.args[0]
            == "last_sync_timestamp"
        )

    def test_inline_insertion_failure_rolls_back_to_its_savepoint(self) -> None:
        self.insert.side_effect = RuntimeError("simulated insertion failure")
        with self.assertRaisesRegex(RuntimeError, "simulated insertion failure"):
            doe.compute_doe_inline()
        self.frappe_mock.db.rollback.assert_called_once_with(
            save_point="inline_doe_compute"
        )
        self.frappe_mock.db.commit.assert_not_called()

    def _assert_cumulative_rows(self, result: dict[str, Any]) -> None:
        assert result["accounts_processed"] == 2
        assert result["records_created"] == 4
        self.insert.assert_called_once()
        rows = self.insert.call_args.args[0]
        assert [row["posting_date"] for row in rows] == ["2025-06-30"] * 2 + [
            "2025-12-31"
        ] * 2
        assert [row["name"] for row in rows] == [
            f"KE-RCDOE-GLE-2025-{number:05d}" for number in range(1, 5)
        ]
        assert rows[0]["reporting_debit"] == 10
        assert rows[2]["reporting_credit"] == 50
        assert [row["party"] for row in rows[::2]] == ["CUST-A", "CUST-A"]
        assert [row["party"] for row in rows[1::2]] == [None, None]
        assert (
            sum(row["reporting_debit"] - row["reporting_credit"] for row in rows) == 0
        )
        self.frappe_mock.db.sql.assert_called_once()
        assert "DELETE" in self.frappe_mock.db.sql.call_args.args[0]
