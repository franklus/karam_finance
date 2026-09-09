"""Pure contracts for the Karam bank-reconciliation report."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, call, patch

import frappe
import pytest
from frappe.query_builder.builder import MariaDB

PACKAGE = "karam_finance.karam_general.report.bank_reconciliation_statement_(karam)"


@pytest.fixture(scope="module")
def queries() -> Any:
    return importlib.import_module(f"{PACKAGE}.brs_queries")


@pytest.fixture
def filters() -> frappe._dict[str, Any]:
    return frappe._dict(account="Bank", company="Karam", report_date="2026-01-31")


@pytest.fixture
def qb() -> SimpleNamespace:
    return SimpleNamespace(DocType=MariaDB.DocType, from_=MariaDB.from_)


@pytest.mark.parametrize(
    "case",
    [
        (
            "_journal_entry_query",
            "Journal Entry",
            True,
            (
                "`tabJournal Entry`.`docstatus`=1",
                "`tabJournal Entry Account`.`account`='Bank'",
                "`tabJournal Entry`.`company`='Karam'",
                "`tabJournal Entry`.`posting_date`<='2026-01-31'",
                "`tabJournal Entry`.`clearance_date` IS NULL",
                "`tabJournal Entry`.`clearance_date`>'2026-01-31'",
                "`tabJournal Entry Account`.`debit_in_account_currency` `debit`",
                "`tabJournal Entry Account`.`credit_in_account_currency` `credit`",
                "`tabJournal Entry`.`is_opening` IS NULL",
                "`tabJournal Entry`.`is_opening`='No'",
            ),
        ),
        (
            "_journal_entry_query",
            "Journal Entry",
            False,
            (
                "`tabJournal Entry`.`posting_date`>'2026-01-31'",
                "NOT `tabJournal Entry`.`clearance_date` IS NULL",
                "`tabJournal Entry`.`clearance_date`<='2026-01-31'",
                "`tabJournal Entry Account`.`debit_in_account_currency`-`tabJournal Entry Account`.`credit_in_account_currency`",
            ),
        ),
        (
            "_payment_entry_query",
            "Payment Entry",
            True,
            (
                "`docstatus`=1",
                "`company`='Karam'",
                "`posting_date`<='2026-01-31'",
                "`clearance_date` IS NULL",
                "`clearance_date`>'2026-01-31'",
                "`paid_to`='Bank' THEN `received_amount_after_tax` ELSE 0 END `debit`",
                "`paid_from`='Bank' THEN `paid_amount_after_tax` ELSE 0 END `credit`",
            ),
        ),
        (
            "_payment_entry_query",
            "Payment Entry",
            False,
            (
                "`posting_date`>'2026-01-31'",
                "NOT `clearance_date` IS NULL",
                "`clearance_date`<='2026-01-31'",
                "CASE WHEN `paid_to`='Bank' THEN `received_amount_after_tax` ELSE 0 END-CASE WHEN `paid_from`='Bank' THEN `paid_amount_after_tax` ELSE 0 END `movement`",
            ),
        ),
        (
            "_purchase_invoice_query",
            "Purchase Invoice",
            True,
            (
                "`docstatus`=1",
                "`is_paid`=1",
                "`cash_bank_account`='Bank'",
                "`company`='Karam'",
                "`posting_date`<='2026-01-31'",
                "`tabPurchase Invoice`.`clearance_date` IS NULL",
                "`tabPurchase Invoice`.`clearance_date`>'2026-01-31'",
                "`tabPurchase Invoice`.`paid_amount`<0 THEN -`tabPurchase Invoice`.`paid_amount` ELSE 0 END `debit`",
                "`tabPurchase Invoice`.`paid_amount`>0 THEN `tabPurchase Invoice`.`paid_amount` ELSE 0 END `credit`",
            ),
        ),
        (
            "_purchase_invoice_query",
            "Purchase Invoice",
            False,
            (
                "`posting_date`>'2026-01-31'",
                "NOT `tabPurchase Invoice`.`clearance_date` IS NULL",
                "-`tabPurchase Invoice`.`paid_amount`",
            ),
        ),
        (
            "_pos_query",
            "Sales Invoice",
            True,
            (
                "`tabSales Invoice`.`docstatus`=1",
                "`tabSales Invoice`.`is_pos`=1",
                "`tabSales Invoice`.`company`='Karam'",
                "`tabSales Invoice Payment`.`account`='Bank'",
                "`tabSales Invoice`.`posting_date`<='2026-01-31'",
                "`tabSales Invoice Payment`.`clearance_date` IS NULL",
                "`tabSales Invoice Payment`.`clearance_date`>'2026-01-31'",
                "`tabSales Invoice Payment`.`amount`>0 THEN `tabSales Invoice Payment`.`amount` ELSE 0 END `debit`",
                "`tabSales Invoice Payment`.`amount`<0 THEN -`tabSales Invoice Payment`.`amount` ELSE 0 END `credit`",
            ),
        ),
        (
            "_pos_query",
            "Sales Invoice",
            False,
            (
                "`tabSales Invoice`.`posting_date`>'2026-01-31'",
                "NOT `tabSales Invoice Payment`.`clearance_date` IS NULL",
                "`tabSales Invoice Payment`.`clearance_date`<='2026-01-31'",
                "`tabSales Invoice Payment`.`amount` `movement`",
            ),
        ),
    ],
)
def test_source_queries_keep_exact_scope_clearance_direction_and_signed_projection(
    queries: Any,
    *,
    filters: frappe._dict[str, Any],
    qb: SimpleNamespace,
    case: tuple[str, str, bool, tuple[str, ...]],
) -> None:
    builder, source, _outstanding, expected = case
    with patch.object(queries.frappe, "qb", qb):
        sql = getattr(queries, builder)(filters, outstanding=_outstanding).get_sql()
    assert f"`tab{source}`" in sql
    for expression in expected:
        assert expression in sql


def test_outstanding_queries_project_signs_and_optional_pos(
    queries: Any, filters: Any, qb: SimpleNamespace
) -> None:
    with patch.object(queries.frappe, "qb", qb):
        payment = queries._payment_entry_query(filters, outstanding=True).get_sql()
        invoice = queries._purchase_invoice_query(filters, outstanding=True).get_sql()
        pos = queries._pos_query(filters, outstanding=True).get_sql()
        no_pos = queries._get_builtin_incorrect_clearance_query(filters).get_sql()
        filters["include_pos_transactions"] = 1
        with_pos = queries._get_builtin_incorrect_clearance_query(filters).get_sql()
    for sql in (payment, invoice, pos):
        assert "CASE" in sql
    assert "received_amount_after_tax" in payment and "paid_amount_after_tax" in payment
    assert "paid_amount" in invoice and "Sales Invoice Payment" in pos
    assert "Sales Invoice" not in no_pos
    assert "Sales Invoice" in with_pos


def test_accessors_run_each_source_and_amount_and_balance_handle_empty_results(
    queries: Any, filters: Any, qb: SimpleNamespace
) -> None:
    query_type = type(MariaDB.from_(MariaDB.DocType("Payment Entry")))
    with (
        patch.object(queries.frappe, "qb", qb),
        patch.object(query_type, "run", return_value=[]),
    ):
        assert queries.get_journal_entries(filters) == []
        assert queries.get_payment_entries(filters) == []
        assert queries.get_purchase_invoices(filters) == []
        assert queries.get_pos_entries(filters) == []
        assert queries.get_amounts_not_reflected_in_system(filters) == 0.0
    with (
        patch.object(queries.frappe, "qb", qb),
        patch.object(queries, "get_currency_precision", return_value=2),
        patch.object(query_type, "run", return_value=[]),
        patch.object(queries.frappe, "get_cached_value", return_value=False),
    ):
        assert queries.get_balance_on("Bank", "2026-01-31", company="Karam") == 0.0


def test_balance_uses_upstream_for_missing_company_or_group_account(
    queries: Any,
) -> None:
    with patch.object(queries, "_upstream_get_balance_on", return_value=9) as upstream:
        assert queries.get_balance_on("Bank", "2026-01-31") == 9
    upstream.assert_called_once()
    with (
        patch.object(queries.frappe, "get_cached_value", return_value=True),
        patch.object(queries, "_upstream_get_balance_on", return_value=8),
    ):
        assert queries.get_balance_on("Bank", "2026-01-31", "Karam") == 8


def test_extension_hook_names_are_additive_and_drop_upstream(queries: Any) -> None:
    with patch.object(
        queries.frappe, "get_hooks", return_value=["base", "custom", "custom2"]
    ):
        assert queries.extension_hook_names("hook", "base") == ["custom", "custom2"]


@pytest.fixture(scope="module")
def enrichment() -> Any:
    return importlib.import_module(f"{PACKAGE}.brs_enrichment")


@pytest.fixture(scope="module")
def aggregation() -> Any:
    return importlib.import_module(f"{PACKAGE}.brs_aggregation")


def test_je_party_enrichment_is_bounded_first_row_only(
    enrichment: Any, qb: SimpleNamespace
) -> None:
    entries = [
        {"payment_document": "Journal Entry", "payment_entry": "JE-1"},
        {"payment_document": "Journal Entry", "payment_entry": "JE-2"},
        {"payment_document": "Payment Entry", "payment_entry": "PE-1"},
    ]
    query_type = type(MariaDB.from_(MariaDB.DocType("Journal Entry Account")))
    rows = [
        SimpleNamespace(parent="JE-1", party_type="Customer", party="First"),
        SimpleNamespace(parent="JE-1", party_type="Supplier", party="Later"),
        SimpleNamespace(parent="JE-2", party_type="Supplier", party="Two"),
    ]
    with (
        patch.object(enrichment.frappe, "qb", qb),
        patch.object(query_type, "run", return_value=rows) as run,
    ):
        enrichment.enrich_je_party(entries)
    assert [(x.get("party_type"), x.get("party")) for x in entries] == [
        ("Customer", "First"),
        ("Supplier", "Two"),
        (None, None),
    ]
    run.assert_called_once_with(as_dict=True)


def test_je_party_enrichment_does_not_query_without_je(enrichment: Any) -> None:
    qb_mock = SimpleNamespace(DocType=MagicMock())
    with patch.object(enrichment.frappe, "qb", qb_mock):
        enrichment.enrich_je_party(
            [{"payment_document": "Payment Entry", "payment_entry": "PE"}]
        )
    qb_mock.DocType.assert_not_called()


def test_party_name_enrichment_preserves_existing_and_batches_missing(
    enrichment: Any, qb: SimpleNamespace
) -> None:
    entries = [
        {"party_type": "Customer", "party": "C1", "party_name": "Stored"},
        {"party_type": "Customer", "party": "C2"},
        {"party_type": "Customer", "party": "C2"},
        {"party_type": "Supplier", "party": "S1"},
        {"party_type": "Supplier", "party": "S2"},
    ]
    customer = SimpleNamespace(get_title_field=lambda: "customer_name")
    supplier = SimpleNamespace(get_title_field=lambda: "name")
    query_type = type(MariaDB.from_(MariaDB.DocType("Customer")))
    rows = [frappe._dict(name="C2", customer_name="Customer Two")]
    with (
        patch.object(enrichment.frappe, "get_meta", side_effect=[customer, supplier]),
        patch.object(enrichment.frappe, "qb", qb),
        patch.object(query_type, "run", return_value=rows) as run,
    ):
        enrichment.populate_missing_party_names(entries)
    assert [x.get("party_name") for x in entries] == [
        "Stored",
        "Customer Two",
        "Customer Two",
        "S1",
        "S2",
    ]
    run.assert_called_once_with(as_dict=True)


def test_aggregation_extensions_are_additive_sorted_and_signed(
    aggregation: Any,
) -> None:
    base = [{"posting_date": "2026-01-02", "payment_entry": "B"}]
    extension = [
        {"posting_date": "2026-01-01", "payment_entry": "Z"},
        {"posting_date": "2026-01-02", "payment_entry": "A"},
    ]
    with (
        patch.object(
            aggregation.brs_queries,
            "get_entries_for_bank_reconciliation_statement",
            return_value=base,
        ),
        patch.object(aggregation, "_extension_entries", return_value=extension),
        patch.object(aggregation.brs_enrichment, "enrich_je_party"),
        patch.object(aggregation.brs_enrichment, "populate_missing_party_names"),
    ):
        assert [x["payment_entry"] for x in aggregation.get_entries({})] == [
            "Z",
            "A",
            "B",
        ]
    with (
        patch.object(
            aggregation.brs_queries,
            "get_amounts_not_reflected_in_system",
            return_value=-5,
        ),
        patch.object(
            aggregation, "_extension_incorrect_clearance_amount", return_value=2.5
        ),
    ):
        assert aggregation.get_amounts_not_reflected_in_system({}) == -2.5


def test_extension_helpers_execute_only_additive_hooks_and_ignore_none(
    aggregation: Any,
) -> None:
    entries_hook = "get_entries_for_bank_reconciliation_statement"
    amount_hook = (
        "get_amounts_not_reflected_in_system_for_bank_reconciliation_statement"
    )

    def no_entries(_filters: dict[str, Any]) -> None:
        return None

    def payment_entries(_filters: dict[str, Any]) -> list[dict[str, str]]:
        return [{"payment_entry": "X"}]

    def no_amount(_filters: dict[str, Any]) -> None:
        return None

    def incorrect_amount(_filters: dict[str, Any]) -> int:
        return -3

    with (
        patch.object(
            aggregation.brs_queries,
            "extension_hook_names",
            side_effect=[["none", "rows"], ["none_amount", "amount"]],
        ) as extension_hook_names,
        patch.object(
            aggregation.frappe,
            "get_attr",
            side_effect=[
                no_entries,
                payment_entries,
                no_amount,
                incorrect_amount,
            ],
        ),
    ):
        assert aggregation._extension_entries({}) == [{"payment_entry": "X"}]
        assert aggregation._extension_incorrect_clearance_amount({}) == -3.0
    assert extension_hook_names.call_args_list == [
        call(entries_hook, aggregation.brs_queries._UPSTREAM_ENTRIES_HOOK),
        call(amount_hook, aggregation.brs_queries._UPSTREAM_AMOUNT_HOOK),
    ]


def test_controller_returns_empty_without_account_and_calculates_balance() -> None:
    controller = importlib.import_module(
        f"{PACKAGE}.bank_reconciliation_statement_(karam)"
    )
    with patch.object(
        controller.brs_columns, "get_columns", return_value=[{"fieldname": "x"}]
    ):
        assert controller.execute({}) == ([{"fieldname": "x"}], [])
    entries = [{"debit": 10, "credit": 2}]
    with (
        patch.object(controller.brs_columns, "get_columns", return_value=[]),
        patch.object(controller.frappe, "get_cached_value", return_value="USD"),
        patch.object(controller.brs_aggregation, "get_entries", return_value=entries),
        patch.object(controller.brs_queries, "get_balance_on", return_value=100),
        patch.object(
            controller.brs_aggregation,
            "get_amounts_not_reflected_in_system",
            return_value=-5,
        ),
    ):
        _columns, rows = controller.execute(
            {"account": "Bank", "company": "Karam", "report_date": "2026-01-31"}
        )
    assert rows[-1]["credit"] == 0 and rows[-1]["debit"] == 87


def test_query_projection_failure_and_balance_nonempty(queries: Any, qb: Any) -> None:
    with pytest.raises(ValueError, match="every output field"):
        queries._entry_projection("only")
    query_type = type(MariaDB.from_(MariaDB.DocType("GL Entry")))
    with (
        patch.object(queries.frappe, "qb", qb),
        patch.object(queries.frappe, "get_cached_value", return_value=False),
        patch.object(queries, "get_currency_precision", return_value=2),
        patch.object(query_type, "run", return_value=[{"balance": 12.5}]),
    ):
        assert queries.get_balance_on("Bank", "2026-01-31", "Karam") == 12.5


def test_compatibility_wrappers_forward_exactly() -> None:
    controller = importlib.import_module(
        f"{PACKAGE}.bank_reconciliation_statement_(karam)"
    )
    aggregation = importlib.import_module(f"{PACKAGE}.brs_aggregation")
    for name in (
        "get_journal_entries",
        "get_payment_entries",
        "get_purchase_invoices",
        "get_pos_entries",
    ):
        with patch.object(aggregation.brs_queries, name, return_value=[name]) as target:
            assert getattr(aggregation, name)({"x": 1}) == [name]
        target.assert_called_once_with({"x": 1})
    for name in (
        "get_entries_for_bank_reconciliation_statement",
        "get_journal_entries",
        "get_payment_entries",
        "get_purchase_invoices",
        "get_pos_entries",
    ):
        with patch.object(controller.brs_queries, name, return_value=[name]):
            assert getattr(controller, name)({}) == [name]


def test_builtin_entries_collect_sources_with_pos_toggle_and_nonempty_amount(
    queries: Any, filters: Any
) -> None:
    source_queries = [
        MagicMock(run=MagicMock(return_value=[{"source": n}])) for n in range(4)
    ]
    with (
        patch.object(queries, "_journal_entry_query", return_value=source_queries[0]),
        patch.object(queries, "_payment_entry_query", return_value=source_queries[1]),
        patch.object(
            queries, "_purchase_invoice_query", return_value=source_queries[2]
        ),
        patch.object(queries, "_pos_query", return_value=source_queries[3]),
    ):
        assert queries.get_entries_for_bank_reconciliation_statement(filters) == [
            {"source": 0},
            {"source": 1},
            {"source": 2},
        ]
        filters["include_pos_transactions"] = 1
        assert queries.get_entries_for_bank_reconciliation_statement(filters) == [
            {"source": 0},
            {"source": 1},
            {"source": 2},
            {"source": 3},
        ]
    union = MagicMock()
    union.movement = MagicMock()
    outer = MagicMock()
    outer.select.return_value.run.return_value = [{"amount": -4.25}]
    with (
        patch.object(
            queries, "_get_builtin_incorrect_clearance_query", return_value=union
        ),
        patch.object(
            queries.frappe, "qb", SimpleNamespace(from_=MagicMock(return_value=outer))
        ),
    ):
        assert queries.get_amounts_not_reflected_in_system(filters) == -4.25


def test_unmatched_je_party_preserves_existing_fields(enrichment: Any, qb: Any) -> None:
    entries = [
        {
            "payment_document": "Journal Entry",
            "payment_entry": "JE-X",
            "party_type": "Customer",
            "party": "Existing",
        }
    ]
    query_type = type(MariaDB.from_(MariaDB.DocType("Journal Entry Account")))
    with (
        patch.object(enrichment.frappe, "qb", qb),
        patch.object(query_type, "run", return_value=[]),
    ):
        enrichment.enrich_je_party(entries)
    assert entries[0]["party_type"] == "Customer" and entries[0]["party"] == "Existing"


def test_controller_enrichment_wrappers_forward() -> None:
    controller = importlib.import_module(
        f"{PACKAGE}.bank_reconciliation_statement_(karam)"
    )
    rows: list[dict[str, Any]] = [{}]
    with (
        patch.object(
            controller.brs_enrichment, "enrich_je_party", return_value=None
        ) as enrich,
        patch.object(
            controller.brs_enrichment, "populate_missing_party_names", return_value=None
        ) as names,
    ):
        assert controller._enrich_je_party(rows) is None
        assert controller._populate_party_names(rows) is None
    enrich.assert_called_once_with(rows)
    names.assert_called_once_with(rows)
