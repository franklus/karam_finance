"""Pure contracts for Karam's Bank Clearance party enrichment override."""

from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import frappe

from karam_finance.overrides import bank_clearance


def _clearance(rows: list[Any]) -> Any:
    document = object.__new__(bank_clearance.KaramBankClearance)
    document.payment_entries = rows
    return document


def _row(document: str, name: str, against_account: str = "") -> Any:
    return SimpleNamespace(
        payment_document=document,
        payment_entry=name,
        against_account=against_account,
    )


def _sql_database(*results: list[frappe._dict[str, Any]]) -> SimpleNamespace:
    return SimpleNamespace(sql=Mock(side_effect=results))


def test_get_payment_entries_calls_parent_before_party_enrichment() -> None:
    document = _clearance([])
    events: list[str] = []

    def record_parent() -> None:
        events.append("parent")

    def record_enrichment() -> None:
        events.append("enrich")

    with (
        patch.object(
            bank_clearance.BankClearance,
            "get_payment_entries",
            side_effect=record_parent,
        ) as parent,
        patch.object(
            bank_clearance.KaramBankClearance,
            "_enrich_with_party_info",
            side_effect=record_enrichment,
        ) as enrich,
    ):
        document.get_payment_entries()
    parent.assert_called_once_with()
    enrich.assert_called_once_with()
    assert events == ["parent", "enrich"]


def test_enrichment_assigns_document_parties_and_populates_actual_rows() -> None:
    journal = _row("Journal Entry", "JE-1")
    payment = _row("Payment Entry", "PE-1")
    sale = _row("Sales Invoice", "SI-1", "Receivable")
    purchase = _row("Purchase Invoice", "PI-1", "Payable")
    rows = [journal, payment, sale, purchase]
    document = _clearance(rows)
    database = _sql_database(
        [frappe._dict(parent="JE-1", party_type="Customer", party="C-1")],
        [
            frappe._dict(
                name="PE-1",
                party_type="Supplier",
                party="S-1",
                party_name="Supplier One",
            )
        ],
    )
    with (
        patch.object(bank_clearance.frappe, "db", database),
        patch.object(bank_clearance, "populate_party_names") as populate,
    ):
        document._enrich_with_party_info()
    assert (journal.party_type, journal.party) == ("Customer", "C-1")
    assert (payment.party_type, payment.party, payment.party_name) == (
        "Supplier",
        "S-1",
        "Supplier One",
    )
    assert (sale.party_type, sale.party) == ("Customer", "Receivable")
    assert (purchase.party_type, purchase.party) == ("Supplier", "Payable")
    populate.assert_called_once_with(rows)


def test_journal_enrichment_uses_contra_party_rows_and_keeps_missing_rows() -> None:
    duplicate = _row("Journal Entry", "JE-1")
    missing = _row("Journal Entry", "JE-2")
    database = _sql_database(
        [
            frappe._dict(parent="JE-1", party_type="Customer", party="First"),
            frappe._dict(parent="JE-1", party_type="Supplier", party="Second"),
        ]
    )
    with patch.object(bank_clearance.frappe, "db", database):
        _clearance([])._enrich_journal_entries([duplicate, missing])
    sql, params = database.sql.call_args.args[:2]
    assert "FROM `tabJournal Entry Account`" in sql
    assert "WHERE parent IN %(names)s" in sql
    assert "ifnull(party, '') != ''" in sql
    where_clause = sql.split("WHERE", maxsplit=1)[1]
    assert re.search(r"\baccount\b", where_clause, flags=re.IGNORECASE) is None
    assert params == {"names": ["JE-1", "JE-2"]}
    assert database.sql.call_args.kwargs == {"as_dict": 1}
    assert database.sql.call_count == 1
    assert (duplicate.party_type, duplicate.party) == ("Customer", "First")
    assert not hasattr(missing, "party_type")
    assert not hasattr(missing, "party")


def test_payment_enrichment_batches_names_and_keeps_missing_rows() -> None:
    present = _row("Payment Entry", "PE-1")
    missing = _row("Payment Entry", "PE-2")
    database = _sql_database(
        [
            frappe._dict(
                name="PE-1",
                party_type="Customer",
                party="C-1",
                party_name="Customer One",
            )
        ]
    )
    with patch.object(bank_clearance.frappe, "db", database):
        _clearance([])._enrich_payment_entries([present, missing])
    sql, params = database.sql.call_args.args[:2]
    assert "FROM `tabPayment Entry`" in sql
    assert "WHERE name IN %(names)s" in sql
    assert "ifnull(party, '') != ''" in sql
    assert params == {"names": ["PE-1", "PE-2"]}
    assert database.sql.call_args.kwargs == {"as_dict": 1}
    assert database.sql.call_count == 1
    assert (present.party_type, present.party, present.party_name) == (
        "Customer",
        "C-1",
        "Customer One",
    )
    assert not hasattr(missing, "party_type")
    assert not hasattr(missing, "party")
    assert not hasattr(missing, "party_name")


def test_enrichment_with_no_rows_skips_queries_and_still_resolves_names() -> None:
    document = _clearance([])
    database = _sql_database()
    with (
        patch.object(bank_clearance.frappe, "db", database),
        patch.object(bank_clearance, "populate_party_names") as populate,
    ):
        document._enrich_with_party_info()
    database.sql.assert_not_called()
    populate.assert_called_once_with([])
