"""RC Trial Balance regression cases without writing accounting data."""

import importlib
from decimal import Decimal
from typing import Any
from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe.utils import getdate

ROOT = "karam_finance.reporting_currency.report.trial_balance_(reporting_currency)."
aggregation = importlib.import_module(ROOT + "tbk_aggregation")
data = importlib.import_module(ROOT + "tbk_data")
conditions = importlib.import_module(ROOT + "tbk_conditions")
filters_module = importlib.import_module(ROOT + "tbk_filters")
money = importlib.import_module(ROOT + "tbk_money")


class TestReportingTrialBalanceCorrectness(TestCase):
    def test_exact_display_keeps_half_cent(self) -> None:
        rows: list[dict[str, Any]] = [{"debit": Decimal("51309440814079.5450")}]
        money.prepare_display_amounts(rows)
        self.assertEqual(rows[0]["_display_amounts"]["debit"], "51309440814079.55")

    def test_reporting_only_source_values_are_blank(self) -> None:
        account = frappe._dict(account_currency="LBP", _account_currencies=set())
        aggregation.finalize_account_currency_values([account])
        self.assertIsNone(account.debit_in_account_currency)
        self.assertEqual(account.account_currency, "")

    def test_checkbox_strings_are_normalised(self) -> None:
        f = frappe._dict(
            exclude_reporting_doe="0",
            exclude_manual_entries="1",
            show_group_accounts="0",
        )
        filters_module._normalise_checkboxes(f)
        self.assertEqual(f.exclude_reporting_doe, 0)
        self.assertEqual(f.exclude_manual_entries, 1)
        self.assertEqual(f.show_group_accounts, 0)

    def test_group_checkbox_omission_means_unchecked(self) -> None:
        # Frappe omits unchecked controls from the browser request.
        cases: tuple[tuple[dict[str, int], int], ...] = (
            ({}, 0),
            ({"show_group_accounts": 1}, 1),
        )
        for supplied, expected in cases:
            f = frappe._dict(supplied)
            filters_module._normalise_checkboxes(f)
            self.assertEqual(f.show_group_accounts, expected)

    def test_unrestricted_history_does_not_require_a_fiscal_year(self) -> None:
        f = frappe._dict(
            company="Example",
            from_date="2000-01-01",
            to_date="2026-12-31",
            show_unclosed_fy_pl_balances=1,
        )
        with patch.object(filters_module, "get_fiscal_year") as lookup:
            filters_module.validate_date_range_filters(f)
        lookup.assert_not_called()
        self.assertEqual(f.from_date, getdate("2000-01-01"))

    def test_unsupported_dimension_fails_explicitly(self) -> None:
        ledger = frappe.qb.DocType("Reporting Currency GLE")
        query = frappe.qb.from_(ledger).select(ledger.name)
        with patch.object(frappe, "get_meta") as meta:
            meta.return_value.has_field.return_value = False
            with self.assertRaises(frappe.ValidationError):
                conditions._apply_dimension_filters(
                    query,
                    ledger,
                    {"auxiliary": ["A"]},
                    accounting_dimensions=[
                        frappe._dict(fieldname="auxiliary", label="Auxiliary")
                    ],
                )

    def test_opening_query_is_bounded_by_to_date(self) -> None:
        captured: list[str] = []

        def capture(query: Any, *_args: Any, **_kwargs: Any) -> list[Any]:
            captured.append(str(query))
            return []

        builder = type(frappe.qb.from_(frappe.qb.DocType("Reporting Currency GLE")))
        f = frappe._dict(
            company="Example",
            from_date="2026-01-01",
            to_date="2026-12-31",
            show_unclosed_fy_pl_balances=1,
            with_period_closing_entry_for_opening=1,
        )
        with (
            patch.object(data, "apply_gl_filters", side_effect=identity_query),
            patch.object(builder, "run", capture),
        ):
            data._get_gl_opening_currency_rows(f, 0)
        self.assertIn("`posting_date`<='2026-12-31'", captured[0])
        self.assertIn("CONCAT(SUM", captured[0])


def identity_query(query: Any, *_args: Any, **_kwargs: Any) -> Any:
    return query


class TestReportingTrialBalanceCompanyCurrency(TestCase):
    def test_company_currency_hierarchy_and_netting(self) -> None:
        rows_module = importlib.import_module(ROOT + "tbk_rows")
        accounts = [
            frappe._dict(
                name="Assets", parent_account=None, root_type="Asset", is_group=1
            ),
            frappe._dict(
                name="Bank", parent_account="Assets", root_type="Asset", is_group=0
            ),
        ]
        opening = {
            "Bank": {
                "opening_debit_in_company_currency": "100.1250",
                "opening_credit_in_company_currency": "30.0000",
            }
        }
        period = {
            "Bank": {
                "debit_in_company_currency": "20.0000",
                "credit_in_company_currency": "10.0000",
            }
        }
        aggregation.apply_balances_to_accounts(accounts, opening, period)
        aggregation.accumulate_values_into_parents(
            accounts, {a.name: a for a in accounts}
        )
        company = importlib.import_module(ROOT + "tbk_company_currency")
        for account in accounts:
            company.net_company_balances(account)
            self.assertEqual(
                account.closing_debit_in_company_currency, Decimal("80.1250")
            )
            self.assertEqual(account.closing_credit_in_company_currency, 0)
            self.assertEqual(account.debit, 0)
        for groups in (True, False):
            total = rows_module.calculate_total_row(accounts, "USD", groups)
            self.assertEqual(
                total["closing_debit_in_company_currency"], Decimal("80.1250")
            )

    def test_currency_columns_are_always_visible(self) -> None:
        report = importlib.import_module(ROOT + "trial_balance_(reporting_currency)")
        with (
            patch.object(report, "prepare_filters", side_effect=frappe._dict),
            patch.object(report, "validate_filters"),
            patch.object(report, "get_data", return_value=[]),
        ):
            columns, _ = report.execute({})
            legacy, _ = report.execute(
                {
                    "show_company_currency_columns": 0,
                    "show_source_currency_columns": 0,
                }
            )
        self.assertEqual(columns, legacy)
        amounts = [c for c in columns if c["fieldtype"] == "Currency"]
        self.assertEqual(len(amounts), 18)
        self.assertEqual(
            [c["options"] for c in amounts],
            ["currency"] * 6 + ["company_currency"] * 6 + ["account_currency"] * 6,
        )
        self.assertTrue(all(c["precision"] == 2 for c in amounts))
