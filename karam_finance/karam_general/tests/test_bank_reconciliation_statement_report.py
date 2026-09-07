"""Regression tests for the Karam Bank Reconciliation Statement report."""

from __future__ import annotations

import importlib
from datetime import date
from typing import TYPE_CHECKING
from unittest.mock import patch

from frappe import _dict
from frappe.tests.utils import FrappeTestCase

if TYPE_CHECKING:
    from types import ModuleType


MODULE_NAME = (
    "karam_finance.karam_general.report.bank_reconciliation_statement_(karam)."
    "bank_reconciliation_statement_(karam)"
)


def _load_module() -> ModuleType:
    return importlib.import_module(MODULE_NAME)


class TestBankReconciliationStatementReport(FrappeTestCase):
    """Regression tests for the v16 report contract and corrections."""

    def test_execute_without_account_returns_empty_data(self) -> None:
        module = _load_module()

        columns, data = module.execute(_dict())

        assert data == []
        assert any(column["fieldname"] == "party_name" for column in columns)

    def test_get_entries_sorts_and_enriches_additive_entries(self) -> None:
        module = _load_module()
        filters = _dict(
            account="Bank - TC", company="Test Company", report_date=date(2024, 5, 31)
        )
        entries = [
            _dict(
                posting_date=date(2024, 5, 3),
                payment_document="Journal Entry",
                payment_entry="JV-003",
            ),
            _dict(
                posting_date=date(2024, 5, 1),
                payment_document="Payment Entry",
                payment_entry="PE-001",
            ),
            _dict(
                posting_date=date(2024, 5, 2),
                payment_document="Purchase Invoice",
                payment_entry="PI-002",
            ),
        ]

        with (
            patch.object(
                module.brs_aggregation.brs_queries,
                "get_entries_for_bank_reconciliation_statement",
                return_value=entries,
            ),
            patch.object(module.brs_aggregation, "_extension_entries", return_value=[]),
            patch.object(
                module.brs_aggregation.brs_enrichment, "enrich_je_party"
            ) as enrich,
            patch.object(
                module.brs_aggregation.brs_enrichment,
                "populate_missing_party_names",
            ) as names,
        ):
            result = module.get_entries(filters)

        assert [row.payment_entry for row in result] == ["PE-001", "PI-002", "JV-003"]
        enrich.assert_called_once_with([])
        names.assert_called_once_with(entries)

    def test_journal_entry_query_hydrates_first_party_in_source_query(self) -> None:
        module = _load_module()
        filters = _dict(
            account="Bank - TC", company="Test Company", report_date=date(2024, 5, 31)
        )

        sql = module.brs_queries._journal_entry_query(
            filters, outstanding=True
        ).get_sql()

        assert "ROW_NUMBER() OVER" in sql
        assert "journal_entry_party" in sql
        assert "party_row_number" in sql
        assert "`tabJournal Entry`.`company`='Test Company'" in sql

    def test_execute_includes_signed_incorrect_clearance_in_calculated_balance(
        self,
    ) -> None:
        module = _load_module()
        filters = _dict(
            account="Bank - TC",
            company="Test Company",
            report_date="2024-05-31",
        )
        outstanding = [
            _dict(
                posting_date=date(2024, 5, 1),
                payment_entry="PE-001",
                debit=100,
                credit=0,
            ),
            _dict(
                posting_date=date(2024, 5, 2),
                payment_entry="PI-001",
                debit=0,
                credit=40,
            ),
        ]

        with (
            patch.object(module, "get_entries", return_value=outstanding),
            patch.object(module, "get_balance_on", return_value=1000),
            patch.object(
                module, "get_amounts_not_reflected_in_system", return_value=-25
            ),
            patch.object(module.frappe, "get_cached_value", return_value="USD"),
        ):
            _, rows = module.execute(filters)

        calculated = rows[-1]
        assert calculated["payment_entry"] == "Calculated Bank Statement balance"
        assert calculated["credit"] == 0
        assert calculated["debit"] == 915

    def test_get_amounts_combines_builtin_and_additive_hooks(self) -> None:
        module = _load_module()

        with (
            patch.object(
                module.brs_aggregation.brs_queries,
                "get_amounts_not_reflected_in_system",
                return_value=-125.5,
            ),
            patch.object(
                module.brs_aggregation,
                "_extension_incorrect_clearance_amount",
                return_value=10.5,
            ),
        ):
            amount = module.get_amounts_not_reflected_in_system(_dict())

        assert amount == -115.0

    def test_extension_hooks_do_not_replay_erpnext_base_implementation(self) -> None:
        module = _load_module()

        with patch.object(
            module.brs_aggregation.brs_queries.frappe,
            "get_hooks",
            return_value=["erpnext.base", "custom.additive"],
        ):
            hooks = module.brs_aggregation.brs_queries.extension_hook_names(
                "get_entries_for_bank_reconciliation_statement", "erpnext.base"
            )

        assert hooks == ["custom.additive"]

    def test_get_balance_row_routes_negative_to_credit(self) -> None:
        module = _load_module()

        row = module.get_balance_row("Balance", -25.0, "USD")

        assert row["debit"] == 0
        assert row["credit"] == 25.0
        assert row["account_currency"] == "USD"
