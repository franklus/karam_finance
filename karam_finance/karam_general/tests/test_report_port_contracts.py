"""Regression checks for V16 report queries and Trial Balance currency provenance."""

import importlib
from datetime import date
from typing import Any
from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe import _dict
from frappe.query_builder import Criterion

GL_PACKAGE = "karam_finance.karam_general.report.general_ledger_(karam)"
TB_PACKAGE = "karam_finance.karam_general.report.trial_balance_(karam)"


class TestReportPortContracts(TestCase):
    def test_trial_balance_zero_filter_preserves_ancestors_and_order(self) -> None:
        module = importlib.import_module(f"{TB_PACKAGE}.tbk_rows")
        parents = {
            None: [{"name": "Root"}],
            "Root": [{"name": "Branch"}, {"name": "Empty"}],
            "Branch": [{"name": "Leaf"}, {"name": "Other Leaf"}],
        }
        rows = [
            {"account": name, "has_value": name in {"Leaf", "Other Leaf", "Total"}}
            for name in ("Root", "Branch", "Leaf", "Other Leaf", "Empty", "Total")
        ]
        result = module.filter_out_zero_value_rows(rows, parents)
        assert [row["account"] for row in result] == [
            "Root",
            "Branch",
            "Leaf",
            "Other Leaf",
            "Total",
        ]
        assert result[0] is rows[0]
        assert module.filter_out_zero_value_rows(rows, parents, True) == rows
        leaves = [row for row in rows if row["account"] not in {"Root", "Branch"}]
        assert [
            row["account"] for row in module.filter_out_zero_value_rows(leaves, parents)
        ] == ["Leaf", "Other Leaf", "Total"]

    def test_trial_balance_zero_filter_matches_standard_for_sparse_tree(self) -> None:
        module = importlib.import_module(f"{TB_PACKAGE}.tbk_rows")
        standard = importlib.import_module(
            "erpnext.accounts.report.financial_statements"
        )
        parents = {"Root": [{"name": "Branch"}], "Branch": [{"name": "Leaf"}]}
        for visible in (set(), {"Leaf"}, {"Root"}, {None}, {"Leaf", "Branch"}):
            rows = [
                {"account": name, "has_value": name in visible}
                for name in ("Leaf", "Branch", "Root", None)
            ]
            assert module.filter_out_zero_value_rows(
                rows, parents
            ) == standard.filter_out_zero_value_rows(rows, parents)

    def test_optional_voucher_metadata_does_not_hide_unexpected_errors(self) -> None:
        module = importlib.import_module(f"{GL_PACKAGE}.gl_enrichment")
        with (
            patch.dict(module._KARAM_FIELDS_CACHE, {}, clear=True),
            patch.object(module.frappe, "logger"),
            patch.object(
                module.frappe, "get_meta", side_effect=frappe.DoesNotExistError
            ),
        ):
            assert module._get_karam_fields_for_doctype("Missing Voucher") == []
        with (
            patch.dict(module._KARAM_FIELDS_CACHE, {}, clear=True),
            patch.object(module.frappe, "get_meta", side_effect=RuntimeError("failed")),
            self.assertRaises(RuntimeError),
        ):
            module._get_karam_fields_for_doctype("Broken Voucher")

    def test_enrichment_keeps_displayed_opening_entries(self) -> None:
        module = importlib.import_module(f"{GL_PACKAGE}.gl_query")
        history = _dict(posting_date=date(2022, 12, 31), is_opening="No")
        movement = _dict(posting_date=date(2023, 6, 1), is_opening="No")
        opening = _dict(posting_date=date(2023, 6, 1), is_opening="Yes")
        future = _dict(posting_date=date(2024, 1, 1), is_opening="No")
        future_opening = _dict(posting_date=date(2024, 1, 1), is_opening="Yes")
        rows = [history, movement, opening, future, future_opening]
        filters = _dict(from_date="2023-01-01", to_date="2023-12-31")

        assert module._get_display_entries(rows, filters) == [movement]
        for setting in ("show_opening_entries", "_ignore_is_opening"):
            selected = module._get_display_entries(rows, {**filters, setting: 1})
            assert selected == [movement, opening, future_opening]
            assert selected[0] is movement
        assert module._get_display_entries(
            rows, {**filters, "disable_opening_balance_calculation": 1}
        ) == [movement, opening]

    def test_flat_opening_query_keeps_permissions_and_report_filters(self) -> None:
        query_module = importlib.import_module(f"{GL_PACKAGE}.gl_query")
        query_type = type(frappe.qb.from_(frappe.qb.DocType("GL Entry")))
        frappe.get_meta("GL Entry")
        captured = []
        original_run = query_type.run

        def capture(query: Any, **kwargs: Any) -> list[_dict[str, Any]]:
            if "opening_balance" not in query.get_sql():
                return original_run(query, **kwargs)
            captured.append(query.get_sql())
            return [
                _dict(account="Debtors", account_currency="USD", opening_balance=25)
            ]

        filters = _dict(
            company="Company A",
            from_date="2023-01-01",
            to_date="2023-12-31",
            categorize_by="Flat Chronological",
            letter="LETTER-1",
            party_type="Customer",
            party=["Customer A"],
        )
        with (
            patch.object(query_type, "run", capture),
            patch.object(
                query_module,
                "build_match_conditions",
                return_value="`tabGL Entry`.`owner` = 'restricted@example.test'",
            ),
        ):
            result = query_module.get_flat_account_currency_openings(filters)

        assert result == {("Debtors", "USD"): 25.0}
        sql = captured[0]
        for value in ("Company A", "LETTER-1", "Customer A", "restricted@example.test"):
            assert value in sql
        assert "LEFT JOIN `tabAccount`" in sql
        assert "COALESCE(NULLIF(" in sql
        assert "GROUP BY" in sql
        assert "2023-01-01" in sql
        assert "_flat_account_openings" not in filters

    def test_disabled_opening_retains_explicit_opening_entries(self) -> None:
        module = importlib.import_module(f"{GL_PACKAGE}.gl_query")
        gl = frappe.qb.DocType("GL Entry")
        filters = _dict(
            from_date="2023-01-01",
            to_date="2023-12-31",
            disable_opening_balance_calculation=1,
        )
        sql = Criterion.all(module._build_qb_date_conditions(filters, gl)).get_sql()
        assert "OR" in sql
        assert "is_opening" in sql
        filters._ignore_is_opening = 1  # noqa: V101 - report reads the filter through mapping access.
        sql = Criterion.all(module._build_qb_date_conditions(filters, gl)).get_sql()
        assert "is_opening" not in sql

    def test_zero_currency_rows_do_not_blank_trial_balance_totals(self) -> None:
        module = importlib.import_module(f"{TB_PACKAGE}.tbk_query")
        rows = [
            _dict(
                account="Debtors", account_currency="USD", debit_in_account_currency=5
            ),
            _dict(
                account="Debtors", account_currency="EUR", debit_in_account_currency=0
            ),
        ]
        grouped = module._group_currency_rows(rows)
        assert grouped["Debtors"]["account_currencies"] == {"USD"}
        assert grouped["Debtors"]["debit_in_account_currency"] == 5

    def test_trial_balance_rows_keep_four_decimal_amounts(self) -> None:
        module = importlib.import_module(f"{TB_PACKAGE}.tbk_rows")
        account = _dict(
            name="Debtors",
            account_name="Debtors",
            parent_account=None,
            indent=0,
            account_currency="USD",
            _account_currencies={"USD"},
            debit=1.2345,
            debit_in_account_currency=1.2345,
        )
        with patch.object(module, "get_zero_cutoff", return_value=0.005):
            rows = module.prepare_data(
                [account],
                _dict(from_date="2023-01-01", to_date="2023-12-31"),
                {},
                company_currency="USD",
            )
        assert rows[0]["debit"] == 1.2345
        assert rows[0]["debit_in_account_currency"] == 1.2345

    def test_zero_party_does_not_change_foreign_currency_total(self) -> None:
        module = importlib.import_module(
            "karam_finance.karam_general.report.trial_balance_for_party_(karam).tbfp_data"
        )
        parties = [{"name": "Active"}, {"name": "Zero"}]
        balances = {"Active": {"USD": {"debit": 50, "debit_in_account_currency": 5}}}
        rows, _, totals, currencies = module._build_party_rows(
            parties,
            balances,
            {
                "party_name_field": "customer_name",
                "show_party_name": False,
                "company_currency": "LBP",
            },
            filters=_dict(show_zero_values=1),
        )
        assert len(rows) == 2
        assert currencies == {"USD"}
        assert totals["debit_in_account_currency"] == 5
