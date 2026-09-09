# Copyright (c) 2026, Noospheric
# See license.txt

"""Tests for Reporting Currency Settings DocType."""

from types import SimpleNamespace
from typing import cast, override
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe
import pytest

from karam_finance.reporting_currency.doctype.reporting_currency_settings import (
    reporting_currency_settings as module,
)


def _raise_validation(message: str, **_: object) -> None:
    raise frappe.ValidationError(message)


class TestReportingCurrencySettings(TestCase):
    """Test cases for Reporting Currency Settings."""

    @override
    def setUp(self) -> None:
        self.frappe = MagicMock()
        self.frappe.throw.side_effect = _raise_validation
        self.enterContext(patch.object(module, "frappe", self.frappe))
        self.enterContext(patch.object(module, "_", side_effect=str))

    def test_validate_requires_posting_date_after_validating_rate(self) -> None:
        settings = object.__new__(module.ReportingCurrencySettings)
        settings.__dict__["rc_parameters"] = [
            SimpleNamespace(idx=3, exchange_rate=2, doe_posting_date=None)
        ]
        with pytest.raises(frappe.ValidationError, match="Row 3: DOE Posting Date"):
            module.ReportingCurrencySettings.validate(settings)

    def test_validate_accepts_an_empty_parameter_table(self) -> None:
        settings = object.__new__(module.ReportingCurrencySettings)
        settings.__dict__["rc_parameters"] = cast("list[object]", [])
        module.ReportingCurrencySettings.validate(settings)
        self.frappe.throw.assert_not_called()

    def test_exchange_rate_validator_accepts_a_positive_finite_row(self) -> None:
        module.validate_doe_exchange_rates([SimpleNamespace(idx=1, exchange_rate=1.25)])
        self.frappe.throw.assert_not_called()

    def test_accounts_under_parent_validates_parent_and_returns_empty_list(
        self,
    ) -> None:
        with pytest.raises(frappe.ValidationError, match="select a parent"):
            module.get_accounts_under_parent("")
        self.frappe.only_for.assert_called_once_with("System Manager")
        self.frappe.only_for.reset_mock()

        parent = SimpleNamespace(lft=2, rgt=10, company="Karam")
        with (
            patch.object(self.frappe.db, "get_value", return_value=parent) as get_value,
            patch.object(self.frappe, "get_all", return_value=[]) as get_all,
        ):
            assert module.get_accounts_under_parent("Assets") == []
        self.frappe.only_for.assert_called_once_with("System Manager")
        get_value.assert_called_once_with(
            "Account", "Assets", ["lft", "rgt", "company"], as_dict=True
        )
        get_all.assert_called_once_with(
            "Account",
            fields=["name as account", "is_group"],
            filters={
                "lft": [">", 2],
                "rgt": ["<", 10],
                "company": "Karam",
            },
            order_by="lft asc",
            limit=0,
        )

    def test_accounts_under_parent_rejects_missing_parent(self) -> None:
        with (
            patch.object(self.frappe.db, "get_value", return_value=None),
            pytest.raises(frappe.ValidationError, match=r"Assets.*not found"),
        ):
            module.get_accounts_under_parent("Assets")
        self.frappe.only_for.assert_called_once_with("System Manager")
