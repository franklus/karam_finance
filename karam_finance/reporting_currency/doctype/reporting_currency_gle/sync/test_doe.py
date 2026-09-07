"""Tests for party-wise DOE grouping."""

from typing import Any
from unittest.mock import patch

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
        assert all(record["party"] == "CUST-A" for record in first_records)
        assert all(record["party_type"] == "Customer" for record in first_records)
        assert prior_doe_by_group[("Trade Debtors", "LBP", "Customer", "CUST-A")] == {
            "reporting_debit": 10.0,
            "reporting_credit": 0.0,
        }

        # Customer A is already brought to the DOE rate by the first pair. Only
        # Customer B needs a fresh pair in the next DOE parameter row.
        assert second_processed == 1
        assert len(second_records) == 2
        assert all(record["party"] == "CUST-B" for record in second_records)
        assert all(record["party_type"] == "Customer" for record in second_records)

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
        assert {row["party_type"] for row in records} == {"Customer", "Supplier"}

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

    def test_bulk_insert_preserves_party_context(self) -> None:
        """The DOE writer must persist party fields created by the calculator."""
        records = [
            {"name": "RC-DOE-2025-00001", "party_type": "Customer", "party": "CUST-A"},
            {"name": "RC-DOE-2025-00002", "party_type": "Customer", "party": "CUST-A"},
        ]

        with patch.object(doe.frappe.db, "bulk_insert") as bulk_insert:
            doe._bulk_insert_doe_records(records)

        _, fields, values = bulk_insert.call_args.args
        inserted_rows = [dict(zip(fields, row, strict=True)) for row in values]

        assert all(row["party_type"] == "Customer" for row in inserted_rows)
        assert all(row["party"] == "CUST-A" for row in inserted_rows)


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
