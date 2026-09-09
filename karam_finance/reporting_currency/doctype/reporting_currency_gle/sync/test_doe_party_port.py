"""Tests for party-wise DOE grouping."""

from typing import Any, override
from unittest import TestCase
from unittest.mock import Mock, patch

import pytest
from frappe import _dict

from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync import (
    doe,
    doe_legacy_reference,
    doe_naming,
    doe_storage,
)


class TestPartyWiseDoe(TestCase):
    """Keep party-wise DOE calculation and carry-forward isolated per party."""

    @override
    def setUp(self) -> None:
        """Use database doubles; these tests never connect to a site."""
        self.enterContext(patch.object(doe.frappe, "db", Mock()))
        self.enterContext(
            patch.object(
                doe.frappe,
                "get_system_settings",
                return_value="Banker's Rounding (legacy)",
            )
        )
        self.enterContext(patch.object(doe.frappe, "session", _dict(user="test")))
        self.enterContext(
            patch.object(doe_storage, "now", return_value="2025-12-31 00:00:00")
        )

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

    def test_loss_pair_keeps_supplier_only_on_payable_side(self) -> None:
        """A P&L counterentry identifies the opposite party through Against only."""
        supplier = _party_group("VEN-1")
        supplier.update(
            account="Trade Creditors", account_type="Payable", party_type="Supplier"
        )
        supplier["total_reporting_debit"] = 110
        with patch.object(doe.frappe.db, "get_value", return_value="4011"):
            records, count = doe._create_doe_records_for_groups(
                account_groups=[supplier],
                prior_doe_by_group={},
                name_counter={"year": 2025, "counter": 1},
                **_DOE_ARGUMENTS,
            )
        assert count == 1
        assert records[0]["party"] == "VEN-1"
        assert records[0]["reporting_credit"] == 10
        assert records[1]["account"] == "DOE Loss"
        assert records[1]["reporting_debit"] == 10
        assert records[1].get("party") is None
        assert records[1].get("party_type") is None
        assert records[1]["against"] == "VEN-1"
        assert records[0]["against"] == "DOE Loss"
        assert records[0]["voucher_no"] == records[1]["voucher_no"]

    def test_account_party_type_and_currency_keep_carry_forward_separate(self) -> None:
        """Different accounts or party types must not reuse another group's DOE."""
        groups = [_party_group("SHARED") for _ in range(4)]
        groups[1].account = "Other Debtors"
        groups[2].party_type = "Supplier"
        groups[3].account_currency = "EUR"
        with patch.object(doe.frappe.db, "get_value", return_value="1100"):
            records, count = doe._create_doe_records_for_groups(
                account_groups=list(groups),
                prior_doe_by_group={},
                name_counter={"year": 2025, "counter": 1},
                **_DOE_ARGUMENTS,
            )
        assert count == 4
        assert len(records) == 8
        assert all(record["reporting_debit"] == 10 for record in records[::2])
        assert len({record["voucher_no"] for record in records[::2]}) == 4

    def test_non_party_account_has_no_party_on_either_side(self) -> None:
        """Account-level adjustments retain account-only Against context."""
        account = _party_group("CUST-A")
        account.update(
            account="Bank EUR", account_type="Bank", party=None, party_type=None
        )
        with patch.object(doe.frappe.db, "get_value", return_value="5120"):
            records, _ = doe._create_doe_records_for_groups(
                account_groups=[account],
                prior_doe_by_group={},
                name_counter={"year": 2025, "counter": 1},
                **_DOE_ARGUMENTS,
            )
        assert all(
            not row.get("party") and not row.get("party_type") for row in records
        )
        assert records[1]["against"] == "Bank EUR"

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
        assert "AND rc.reporting_doe = 0" in query
        assert "COALESCE(rc.is_cancelled, 0) = 0" in query
        assert "MAX(" not in query

    def test_bulk_insert_preserves_party_context(self) -> None:
        """The DOE writer must persist party fields created by the calculator."""
        records = [
            {
                "name": "RC-DOE-2025-00001",
                "voucher_no": "pair-1",
                "account": "Trade Debtors",
                "posting_date": "2025-12-31",
                "party_type": "Customer",
                "party": "CUST-A",
            },
            {
                "name": "RC-DOE-2025-00002",
                "voucher_no": "pair-1",
                "account": "DOE Profit",
                "posting_date": "2025-12-31",
                "party_type": None,
                "party": None,
            },
        ]

        with (
            patch.object(doe.frappe.db, "bulk_insert") as bulk_insert,
            patch.object(doe.frappe, "get_all", side_effect=[_NAMING_ACCOUNTS, []]),
        ):
            doe._bulk_insert_doe_records(records)

        _, fields, values = bulk_insert.call_args.args
        inserted_rows = [dict(zip(fields, row, strict=True)) for row in values]

        assert inserted_rows[0]["party_type"] == "Customer"
        assert inserted_rows[0]["party"] == "CUST-A"
        assert inserted_rows[1]["party_type"] is None
        assert inserted_rows[1]["party"] is None
        assert all(row["is_opening"] == "No" for row in inserted_rows)
        assert inserted_rows[0]["name"] == "DOE-31122025-1100-CUST-A"
        assert inserted_rows[1]["name"] == "DOE-31122025-7751"
        assert all(
            row["voucher_no"] == "DOE-31122025-1100-CUST-A" for row in inserted_rows
        )

    def test_stored_voucher_uses_primary_id_and_keeps_repeated_pairs_distinct(
        self,
    ) -> None:
        """Remove redundant reference components without merging balanced pairs."""
        counter = {"year": 2025, "counter": 1}
        records = []
        for party in ("CUST-A", "CUST-B", "CUST-A"):
            pair, _ = doe._create_doe_records_for_groups(
                account_groups=[_party_group(party)],
                prior_doe_by_group={},
                name_counter=counter,
                **_DOE_ARGUMENTS,
            )
            records.extend(pair)

        with patch.object(doe.frappe, "get_all", side_effect=[_NAMING_ACCOUNTS, []]):
            doe._bulk_insert_doe_records(records)
        _, fields, values = doe.frappe.db.bulk_insert.call_args.args
        stored = [dict(zip(fields, row, strict=True)) for row in values]
        assert [row["voucher_no"] for row in stored[::2]] == [
            "DOE-31122025-1100-CUST-A-001",
            "DOE-31122025-1100-CUST-B",
            "DOE-31122025-1100-CUST-A-002",
        ]
        for primary, offset in zip(stored[::2], stored[1::2], strict=True):
            assert primary["voucher_no"] == primary["name"] == offset["voucher_no"]
            assert offset["name"] != primary["name"]
            assert primary["reporting_debit"] == offset["reporting_credit"] == 10
            assert primary["reporting_credit"] == offset["reporting_debit"] == 0
            assert offset["party"] is None

    def test_record_ids_number_shared_offsets_and_repeated_parameters(self) -> None:
        """Real generated pairs retain amounts/references while stored IDs change."""
        with patch.object(doe.frappe.db, "get_value", return_value="1100"):
            records, _ = doe._create_doe_records_for_groups(
                account_groups=[_party_group("CUST-A"), _party_group("CUST-B")],
                prior_doe_by_group={},
                name_counter={"year": 2025, "counter": 1},
                **_DOE_ARGUMENTS,
            )
        # A second parameter can generate another adjustment on the same date.
        records.append(dict(records[0]))
        before = [{k: v for k, v in row.items() if k != "name"} for row in records]
        with patch.object(doe.frappe, "get_all", side_effect=[_NAMING_ACCOUNTS, []]):
            doe_naming.assign_doe_record_names(records)
        assert [row["name"] for row in records] == [
            "DOE-31122025-1100-CUST-A-001",
            "DOE-31122025-7751-001",
            "DOE-31122025-1100-CUST-B",
            "DOE-31122025-7751-002",
            "DOE-31122025-1100-CUST-A-002",
        ]
        assert before == [
            {k: v for k, v in row.items() if k != "name"} for row in records
        ]

    def test_record_ids_use_parameter_date_account_type_and_reserve_suffixes(
        self,
    ) -> None:
        """Do not include a non-party account's party or collide with a real suffix."""
        records = [
            {"account": "Trade Creditors", "party": party, "posting_date": date}
            for party, date in [
                ("VEN1", "2023-02-01"),
                ("VEN1", "2023-02-01"),
                ("VEN1-001", "2023-02-01"),
                ("VEN1", "2023-01-02"),
            ]
        ]
        records.append(
            {"account": "DOE Profit", "party": "VEN1", "posting_date": "2023-02-01"}
        )
        with patch.object(
            doe.frappe,
            "get_all",
            side_effect=[_NAMING_ACCOUNTS, ["DOE-01022023-7751"]],
        ):
            doe_naming.assign_doe_record_names(records)
        assert [row["name"] for row in records] == [
            "DOE-01022023-4011-VEN1-002",
            "DOE-01022023-4011-VEN1-003",
            "DOE-01022023-4011-VEN1-001",
            "DOE-02012023-4011-VEN1",
            "DOE-01022023-7751-001",
        ]

    def test_case_insensitive_names_reserve_later_pages(self) -> None:
        first_page = [f"DOE-{index:04d}" for index in range(500)]
        with patch.object(
            doe.frappe, "get_all", side_effect=[first_page, ["DOE-TARGET"]]
        ) as get_all:
            occupied = doe_naming._occupied_names()
        assert get_all.call_count == 2
        assert get_all.call_args.kwargs["filters"][-1] == ["name", ">", first_page[-1]]
        assert doe_naming._allocate_names(
            ["DOE-target", "DOE-TARGET-001"], occupied
        ) == [
            "DOE-target-002",
            "DOE-TARGET-001",
        ]

    def test_overlong_record_id_stops_before_inserting(self) -> None:
        """Never truncate an identifier silently or insert only part of a batch."""
        records = [
            {
                "account": "Trade Debtors",
                "party": "X" * 140,
                "posting_date": "2025-12-31",
            }
        ]
        with (
            patch.object(doe.frappe, "get_all", side_effect=[_NAMING_ACCOUNTS, []]),
            pytest.raises(ValueError, match="exceeds 140 characters"),
        ):
            doe._bulk_insert_doe_records(records)
        doe.frappe.db.bulk_insert.assert_not_called()

    def test_legacy_reference_omits_missing_party_and_hashes_long_unique_values(
        self,
    ) -> None:
        assert (
            doe_legacy_reference.build_doe_voucher_no(
                "1100",
                None,
                None,
                posting_date="2025-12-31",
                pair_name="DOE-pair-001",
            )
            == "DOE-2025-1100-20251231-001"
        )

        first = doe_legacy_reference.build_doe_voucher_no(
            "A" * 130,
            "Customer",
            "P" * 40,
            posting_date="2025-12-31",
            pair_name="DOE-pair-tail-a",
        )
        second = doe_legacy_reference.build_doe_voucher_no(
            "A" * 130,
            "Customer",
            "P" * 40,
            posting_date="2025-12-31",
            pair_name="DOE-pair-tail-b",
        )
        assert len(first) == doe_legacy_reference.VOUCHER_NO_MAX_LENGTH
        assert len(second) == doe_legacy_reference.VOUCHER_NO_MAX_LENGTH
        assert first == doe_legacy_reference.build_doe_voucher_no(
            "A" * 130,
            "Customer",
            "P" * 40,
            posting_date="2025-12-31",
            pair_name="DOE-pair-tail-a",
        )
        assert first != second

    def test_doe_name_helpers_reject_real_invalid_sentinels_before_database_reads(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="Invalid DOE posting date"):
            doe_legacy_reference.build_doe_voucher_no(
                "1100",
                None,
                None,
                posting_date="0000-00-00",
                pair_name="DOE-pair-001",
            )

        with patch.object(doe_naming.frappe, "get_all") as get_all:
            doe_naming.assign_doe_record_names([])
        get_all.assert_not_called()

        account = _dict(
            name="Trade Debtors", account_number="1100", account_type="Receivable"
        )
        with pytest.raises(ValueError, match="Invalid DOE posting date"):
            doe_naming._record_base({"posting_date": "0000-00-00"}, account)


_NAMING_ACCOUNTS = [
    _dict(name="Trade Debtors", account_number="1100", account_type="Receivable"),
    _dict(name="Trade Creditors", account_number="4011", account_type="Payable"),
    _dict(name="DOE Profit", account_number="7751", account_type="Income Account"),
]


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
