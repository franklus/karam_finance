"""Behavioural regressions ported from V15 for the V16 GL currency layers."""

import importlib
from datetime import date
from types import ModuleType
from unittest.mock import patch

from frappe import _dict
from frappe.tests.utils import FrappeTestCase

MODULE_NAME = (
    "karam_finance.karam_general.report.general_ledger_(karam).general_ledger_(karam)"
)


def _load_module() -> ModuleType:
    return importlib.import_module(MODULE_NAME)


class TestGeneralLedgerCurrencyPort(FrappeTestCase):
    def test_currency_balance_columns_follow_their_debit_credit_columns(self) -> None:
        """Keep each currency balance beside its matching debit and credit."""
        module = _load_module()
        filters = _dict(company="Karam", presentation_currency="USD")

        with (
            patch.object(
                importlib.import_module(MODULE_NAME.rsplit(".", 1)[0] + ".gl_columns"),
                "get_company_currency",
                return_value="LBP",
            ),
            patch.object(
                importlib.import_module(
                    MODULE_NAME.rsplit(".", 1)[0] + ".gl_columns"
                ).frappe.db,
                "get_single_value",
                side_effect=["Supplier Name", "Customer Name"],
            ),
        ):
            columns = module.get_columns(filters)

        fieldnames = [column["fieldname"] for column in columns]
        assert fieldnames[7:16] == [
            "debit_in_account_currency",
            "credit_in_account_currency",
            "balance_in_account_currency",
            "debit_in_company_currency",
            "credit_in_company_currency",
            "balance_in_company_currency",
            "debit",
            "credit",
            "balance",
        ]
        columns_by_fieldname = {column["fieldname"]: column for column in columns}
        assert columns_by_fieldname["balance_in_account_currency"]["options"] == (
            "account_currency"
        )
        assert columns_by_fieldname["balance_in_company_currency"]["options"] == (
            "Company:company:default_currency"
        )

    def test_running_balances_are_calculated_for_each_currency_layer(self) -> None:
        """Calculate account, company and presentation balances independently."""
        module = _load_module()
        rows = [
            _dict(
                row_type="opening",
                debit=100.0,
                credit=0.0,
                debit_in_account_currency=10.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=1_000.0,
                credit_in_company_currency=0.0,
            ),
            _dict(
                row_type="entry",
                posting_date=date(2024, 1, 1),
                debit=50.0,
                credit=20.0,
                debit_in_account_currency=5.0,
                credit_in_account_currency=2.0,
                debit_in_company_currency=500.0,
                credit_in_company_currency=200.0,
            ),
            _dict(
                row_type="entry",
                posting_date=date(2024, 1, 2),
                debit=0.0,
                credit=80.0,
                debit_in_account_currency=0.0,
                credit_in_account_currency=8.0,
                debit_in_company_currency=0.0,
                credit_in_company_currency=800.0,
            ),
        ]

        result = module.get_result_as_list(
            rows,
            _dict(company="Selected Company", presentation_currency="USD"),
        )

        assert [row.balance for row in result] == [100.0, 130.0, 50.0]
        assert {row.company for row in result} == {"Selected Company"}
        assert [row.balance_in_account_currency for row in result] == [
            10.0,
            13.0,
            5.0,
        ]
        assert [row.balance_in_company_currency for row in result] == [
            1_000.0,
            1_300.0,
            500.0,
        ]

    def test_flat_mixed_currency_balances_run_per_account(self) -> None:
        """Keep detail balances per account while blanking mixed summaries."""
        module = _load_module()
        rows = [
            _dict(
                row_type="opening",
                debit=0.0,
                credit=0.0,
                debit_in_account_currency=0.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=0.0,
                credit_in_company_currency=0.0,
            ),
            _dict(
                row_type="entry",
                posting_date=date(2024, 1, 1),
                account="USD Bank",
                account_currency="USD",
                _opening_balance_in_account_currency=2.0,
                debit=1_000.0,
                credit=0.0,
                debit_in_account_currency=10.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=1_000.0,
                credit_in_company_currency=0.0,
            ),
            _dict(
                row_type="entry",
                posting_date=date(2024, 1, 2),
                account="EUR Bank",
                account_currency="EUR",
                _opening_balance_in_account_currency=7.0,
                debit=0.0,
                credit=500.0,
                debit_in_account_currency=0.0,
                credit_in_account_currency=5.0,
                debit_in_company_currency=0.0,
                credit_in_company_currency=500.0,
            ),
            _dict(
                row_type="entry",
                posting_date=date(2024, 1, 3),
                account="USD Bank",
                account_currency="USD",
                _opening_balance_in_account_currency=2.0,
                debit=0.0,
                credit=200.0,
                debit_in_account_currency=0.0,
                credit_in_account_currency=2.0,
                debit_in_company_currency=0.0,
                credit_in_company_currency=200.0,
            ),
            _dict(
                row_type="report_total",
                _mixed_account_currency=1,
                debit=1_000.0,
                credit=500.0,
                debit_in_account_currency=10.0,
                credit_in_account_currency=5.0,
                debit_in_company_currency=1_000.0,
                credit_in_company_currency=500.0,
            ),
            _dict(
                row_type="closing",
                _mixed_account_currency=1,
                debit=1_000.0,
                credit=500.0,
                debit_in_account_currency=10.0,
                credit_in_account_currency=5.0,
                debit_in_company_currency=1_000.0,
                credit_in_company_currency=500.0,
            ),
        ]

        result = module.get_result_as_list(
            rows,
            _dict(
                categorize_by="Flat Chronological",
                presentation_currency="LBP",
            ),
        )
        non_separator_rows = [row for row in result if not row.get("is_separator")]
        detail_rows = [row for row in non_separator_rows if row.get("posting_date")]
        summary_rows = [
            row
            for row in non_separator_rows
            if row.get("row_type") in {"opening", "report_total", "closing"}
        ]

        assert [row.balance_in_account_currency for row in detail_rows] == [
            12.0,
            2.0,
            10.0,
        ]
        for row in summary_rows:
            assert row.account_currency == ""
            assert row.debit_in_account_currency == ""
            assert row.credit_in_account_currency == ""
            assert row.balance_in_account_currency == ""
        assert non_separator_rows[-1].balance_in_company_currency == 500.0

    def test_flat_rows_receive_per_account_currency_openings(self) -> None:
        """Carry each account's opening account-currency balance into flat rows."""
        module = _load_module()
        entries = [
            _dict(account="USD Bank", account_currency="USD"),
            _dict(account="EUR Bank", account_currency="EUR"),
        ]
        opening_balances = {
            ("USD Bank", "USD"): 11.0,
            ("EUR Bank", "EUR"): -5.0,
        }

        module._attach_flat_account_currency_openings(
            entries,
            opening_balances,
        )

        assert [row._opening_balance_in_account_currency for row in entries] == [
            11.0,
            -5.0,
        ]

    def test_flat_single_currency_summaries_include_historical_opening(self) -> None:
        """Build Opening, Total and Closing from history plus visible movement."""
        module = _load_module()
        rows = [
            _dict(row_type="opening"),
            _dict(
                row_type="entry",
                posting_date=date(2024, 1, 1),
                account="USD Supplier",
                account_currency="USD",
                debit_in_account_currency=30.0,
                credit_in_account_currency=0.0,
            ),
            _dict(row_type="report_total"),
            _dict(row_type="closing"),
        ]

        module._apply_flat_account_currency_summaries(
            rows,
            {
                ("USD Supplier", "USD"): -100.0,
                ("USD Bank", "USD"): 20.0,
                ("Zero EUR", "EUR"): 0.0,
            },
        )

        opening, _detail, report_total, closing = rows
        assert (
            opening.account_currency,
            opening.debit_in_account_currency,
            opening.credit_in_account_currency,
            opening.balance_in_account_currency,
        ) == ("USD", 20.0, 100.0, -80.0)
        assert (
            report_total.account_currency,
            report_total.debit_in_account_currency,
            report_total.credit_in_account_currency,
            report_total.balance_in_account_currency,
        ) == ("USD", 30.0, 0.0, 30.0)
        assert (
            closing.account_currency,
            closing.debit_in_account_currency,
            closing.credit_in_account_currency,
            closing.balance_in_account_currency,
        ) == ("USD", 50.0, 100.0, -50.0)

    def test_flat_summary_detects_currency_present_only_in_history(self) -> None:
        """Blank summaries when history and movement use different currencies."""
        module = _load_module()
        rows = [
            _dict(row_type="opening"),
            _dict(
                row_type="entry",
                posting_date=date(2024, 1, 1),
                account="USD Supplier",
                account_currency="USD",
                debit_in_account_currency=30.0,
                credit_in_account_currency=0.0,
            ),
            _dict(row_type="report_total"),
            _dict(row_type="closing"),
        ]

        module._apply_flat_account_currency_summaries(
            rows,
            {("EUR Supplier", "EUR"): -100.0},
        )

        for row in (rows[0], rows[2], rows[3]):
            assert row._mixed_account_currency == 1

    def test_account_grouping_retains_per_currency_running_balances(self) -> None:
        """Reset and calculate account-currency balances for each account group."""
        module = _load_module()
        rows = [
            _dict(is_separator=1, row_type="separator"),
            _dict(
                row_type="opening",
                account_currency="USD",
                debit=100.0,
                credit=0.0,
                debit_in_account_currency=10.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=100.0,
                credit_in_company_currency=0.0,
            ),
            _dict(
                row_type="entry",
                posting_date=date(2024, 1, 1),
                account="USD Bank",
                account_currency="USD",
                debit=0.0,
                credit=30.0,
                debit_in_account_currency=0.0,
                credit_in_account_currency=3.0,
                debit_in_company_currency=0.0,
                credit_in_company_currency=30.0,
            ),
            _dict(is_separator=1, row_type="separator"),
            _dict(
                row_type="opening",
                account_currency="EUR",
                debit=200.0,
                credit=0.0,
                debit_in_account_currency=20.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=200.0,
                credit_in_company_currency=0.0,
            ),
            _dict(
                row_type="entry",
                posting_date=date(2024, 1, 2),
                account="EUR Bank",
                account_currency="EUR",
                debit=50.0,
                credit=0.0,
                debit_in_account_currency=5.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=50.0,
                credit_in_company_currency=0.0,
            ),
        ]

        result = module.get_result_as_list(
            rows,
            _dict(
                categorize_by="Categorise by Account",
                presentation_currency="LBP",
            ),
        )
        balances = [
            row.balance_in_account_currency
            for row in result
            if not row.get("is_separator")
        ]

        assert balances == [10.0, 7.0, 20.0, 25.0]

    def test_account_currency_tracking_marks_mixed_aggregates(self) -> None:
        """Track one aggregate currency and reject a second currency."""
        target = _dict()

        importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".gl_currency"
        )._track_account_currency(
            target,
            _dict(
                account_currency="USD",
                debit_in_account_currency=1.0,
                credit_in_account_currency=0.0,
            ),
        )
        assert target.account_currency == "USD"
        assert not target.get("_mixed_account_currency")

        importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".gl_currency"
        )._track_account_currency(
            target,
            _dict(
                account_currency="EUR",
                debit_in_account_currency=1.0,
                credit_in_account_currency=0.0,
            ),
        )
        assert target.account_currency == ""
        assert target._mixed_account_currency == 1

    def test_nonzero_amount_without_currency_marks_aggregate_unknown(self) -> None:
        """Never label an unproven account-currency amount with another currency."""
        target = _dict(account_currency="USD")

        importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".gl_currency"
        )._track_account_currency(
            target,
            _dict(
                account_currency=None,
                debit_in_account_currency=0.001,
                credit_in_account_currency=0.0,
            ),
        )

        assert target.account_currency == ""
        assert target._mixed_account_currency == 1

    def test_zero_amount_without_currency_does_not_change_provenance(self) -> None:
        """A blank currency with no contribution should not invalidate a total."""
        target = _dict(account_currency="USD")

        importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".gl_currency"
        )._track_account_currency(
            target,
            _dict(
                account_currency="EUR",
                debit_in_account_currency=0.0,
                credit_in_account_currency=0.0,
            ),
        )

        assert target.account_currency == "USD"
        assert not target.get("_mixed_account_currency")

    def test_cancelled_history_retains_currency_provenance(self) -> None:
        """Historical reversals must not erase mixed or unknown currencies."""
        currency_module = importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".gl_currency"
        )
        for currency in ("USD", "EUR", None):
            with self.subTest(currency=currency):
                target = _dict(account_currency="USD")
                currency_module._track_account_currency(
                    target,
                    _dict(
                        account_currency=currency,
                        debit_in_account_currency=0,
                        credit_in_account_currency=0,
                        _account_currency_contribution=True,
                    ),
                )
                assert bool(target.get("_mixed_account_currency")) == (
                    currency != "USD"
                )
                assert target.account_currency == ("USD" if currency == "USD" else "")

    def test_each_currency_layer_is_netted_by_its_own_sign(self) -> None:
        """Opposing account/company balances must remain on their correct sides."""
        row = _dict(
            debit=100.0,
            credit=40.0,
            debit_in_account_currency=1.0,
            credit_in_account_currency=2.0,
            debit_in_company_currency=4_690_919.989,
            credit_in_company_currency=0.0,
        )

        importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".gl_currency"
        )._net_currency_layers(row)

        assert (row.debit, row.credit) == (60.0, 0)
        assert (
            row.debit_in_account_currency,
            row.credit_in_account_currency,
        ) == (0, 1.0)
        assert (
            row.debit_in_company_currency,
            row.credit_in_company_currency,
        ) == (4_690_919.989, 0)

    def test_flat_summary_blanks_unresolved_nonzero_opening_currency(self) -> None:
        """Do not omit or mislabel a historical amount with unknown currency."""
        module = _load_module()
        rows = [
            _dict(row_type="opening"),
            _dict(row_type="report_total"),
            _dict(row_type="closing"),
        ]

        module._apply_flat_account_currency_summaries(
            rows,
            {
                ("USD Bank", "USD"): 10.0,
                ("Unresolved Account", None): -0.001,
            },
        )

        for row in rows:
            assert row._mixed_account_currency == 1
