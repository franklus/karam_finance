"""Tests for trial balance reporting helpers."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from frappe import _dict
from frappe.tests.utils import FrappeTestCase

if TYPE_CHECKING:
    from types import ModuleType

MODULE_NAME = (
    "karam_finance.reporting_currency.report.trial_balance_(reporting_currency)."
    "trial_balance_(reporting_currency)"
)


def _load_module() -> ModuleType:
    return importlib.import_module(MODULE_NAME)


class TestTrialBalanceReporting(FrappeTestCase):
    """Regression tests for modularised reporting-currency trial balance."""

    def test_prepare_opening_closing_nets_to_natural_side(self) -> None:
        module = _load_module()
        row = {
            "root_type": "Expense",
            "opening_debit": 50.0,
            "opening_credit": 30.0,
            "closing_debit": 80.0,
            "closing_credit": 10.0,
        }

        module._prepare_opening_closing(row, include_account_currency=False)

        assert row["opening_debit"] == 20.0
        assert row["opening_credit"] == 0.0
        assert row["closing_debit"] == 70.0
        assert row["closing_credit"] == 0.0

    def test_calculate_total_row_sums_root_accounts(self) -> None:
        module = _load_module()
        accounts = [
            _dict(parent_account=None, opening_debit=4.0, opening_credit=1.0),
            _dict(parent_account="Parent", opening_debit=40.0, opening_credit=10.0),
            _dict(parent_account=None, opening_debit=6.0, opening_credit=2.0),
        ]
        for account in accounts:
            for field in module.VALUE_FIELDS:
                account.setdefault(field, 0.0)

        row = module.calculate_total_row(accounts, "USD")

        assert row["opening_debit"] == 10.0
        assert row["opening_credit"] == 3.0
