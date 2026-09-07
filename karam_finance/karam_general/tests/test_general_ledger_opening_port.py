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


class TestGeneralLedgerOpeningPort(FrappeTestCase):
    def test_ignore_is_opening_classifies_in_period_row_as_movement(self) -> None:
        """Treat an in-period opening-flag row as activity when flags are ignored."""
        module = _load_module()
        filters = _dict(
            categorize_by="Flat Chronological",
            show_net_values_in_party_account=0,
            include_dimensions=0,
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            show_opening_entries=0,
            _ignore_is_opening=1,
            company="Karam",
        )
        opening_flagged_entry = _dict(
            posting_date=date(2024, 1, 1),
            voucher_type="Journal Entry",
            voucher_no="JV-OPEN",
            account="Debtors - K",
            account_currency="USD",
            party_type="Customer",
            party="CUST-0001",
            against_voucher=None,
            is_opening="Yes",
            creation="2023-12-31 09:00:00",
            debit=10.0,
            credit=0.0,
            debit_in_account_currency=10.0,
            credit_in_account_currency=0.0,
            debit_in_company_currency=10.0,
            credit_in_company_currency=0.0,
            cost_center=None,
            project=None,
        )

        totals_template = module._get_totals_dict(total_row_type="report_total")
        gle_map = module._init_gle_map(
            [opening_flagged_entry],
            filters,
            module._get_totals_dict(),
        )
        totals, entries = module._get_account_wise_gle(
            filters,
            [],
            [opening_flagged_entry],
            gle_map=gle_map,
            totals=totals_template,
        )

        assert totals.opening.debit == 0
        assert totals.total.debit == 10.0
        assert totals.closing.debit == 10.0
        assert entries == [opening_flagged_entry]

    def test_disable_opening_calculation_classifies_flagged_row_as_movement(
        self,
    ) -> None:
        """Treat in-period opening rows as activity when openings are disabled."""
        module = _load_module()
        filters = _dict(
            categorize_by="Flat Chronological",
            show_net_values_in_party_account=0,
            include_dimensions=0,
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            show_opening_entries=0,
            disable_opening_balance_calculation=1,
            _ignore_is_opening=0,
            company="Karam",
        )
        opening_flagged_entry = _dict(
            posting_date=date(2024, 1, 1),
            voucher_type="Journal Entry",
            voucher_no="JV-OPEN",
            account="Debtors - K",
            account_currency="USD",
            party_type="Customer",
            party="CUST-0001",
            against_voucher=None,
            is_opening="Yes",
            creation="2023-12-31 09:00:00",
            debit=10.0,
            credit=0.0,
            debit_in_account_currency=10.0,
            credit_in_account_currency=0.0,
            debit_in_company_currency=10.0,
            credit_in_company_currency=0.0,
            cost_center=None,
            project=None,
        )

        totals_template = module._get_totals_dict(total_row_type="report_total")
        gle_map = module._init_gle_map(
            [opening_flagged_entry],
            filters,
            module._get_totals_dict(),
        )
        totals, entries = module._get_account_wise_gle(
            filters,
            [],
            [opening_flagged_entry],
            gle_map=gle_map,
            totals=totals_template,
        )

        assert totals.opening.debit == 0
        assert totals.total.debit == 10.0
        assert totals.closing.debit == 10.0
        assert entries == [opening_flagged_entry]

    def test_blank_categorisation_returns_one_ungrouped_entry_list(self) -> None:
        """Blank categorisation must not insert voucher group summaries."""
        module = _load_module()
        filters = _dict(
            categorize_by="",
            show_net_values_in_party_account=0,
            include_dimensions=0,
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            show_opening_entries=0,
            company="Karam",
        )
        gl_entries = [
            _dict(
                posting_date=date(2024, 1, day),
                voucher_type="Journal Entry",
                voucher_no=f"JV-00{day}",
                account=f"Account {day} - K",
                account_currency="USD",
                party_type=None,
                party=None,
                against_voucher=None,
                is_opening="No",
                creation=f"2024-01-0{day} 09:00:00",
                debit=float(day),
                credit=0.0,
                debit_in_account_currency=float(day),
                credit_in_account_currency=0.0,
                debit_in_company_currency=float(day),
                credit_in_company_currency=0.0,
                cost_center=None,
                project=None,
            )
            for day in (1, 2)
        ]

        with patch.object(module.frappe.db, "get_single_value", return_value=0):
            data = module.get_data_with_opening_closing(filters, [], gl_entries)

        assert [row.get("row_type") for row in data] == [
            "opening",
            "entry",
            "entry",
            "report_total",
            "closing",
        ]
        assert not any(row.get("is_separator") for row in data)
        assert not any(row.get("row_type") == "group_total" for row in data)
