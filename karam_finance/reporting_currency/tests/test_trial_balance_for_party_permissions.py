"""Permission-boundary tests for Trial Balance for Party aggregation."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

import frappe
import frappe.permissions
from frappe import _dict
from frappe.tests.utils import FrappeTestCase

MODULE_NAME = (
    "karam_finance.reporting_currency.report.trial_balance_for_party_(reporting_currency)."
    "tbfpr_query"
)


class _Field:
    def __init__(self, name: str) -> None:
        self.name = name

    def isin(self, values: object) -> tuple[str, object]:
        return self.name, values


class TestTrialBalanceForPartyPermissions(FrappeTestCase):
    """Aggregation must receive already-permitted source rows."""

    def test_dynamic_party_and_source_permissions_are_applied_before_grouping(
        self,
    ) -> None:
        module = importlib.import_module(MODULE_NAME)
        query = Mock()
        query.where.return_value = query
        rcgle = SimpleNamespace(name=_Field("name"), party=_Field("party"))
        permitted_source = object()
        permitted_parties = object()
        filters = _dict(party_type="Customer", party=None)

        with patch.object(module, "frappe") as frappe_mock:
            frappe_mock.qb.get_query.side_effect = [
                permitted_source,
                permitted_parties,
            ]
            result = module._apply_source_permissions(query, rcgle, filters)

        assert result is query
        assert frappe_mock.qb.get_query.call_args_list == [
            call(
                module.DOCTYPE_RC_GLE,
                fields=["name"],
                ignore_permissions=False,
            ),
            call(
                "Customer",
                fields=["name"],
                filters=None,
                ignore_permissions=False,
                reference_doctype=module.DOCTYPE_RC_GLE,
            ),
        ]
        assert query.where.call_args_list == [
            call(("name", permitted_source)),
            call(("party", permitted_parties)),
        ]

    def test_real_permission_queries_cover_account_and_scoped_party(self) -> None:
        """The generated source subqueries carry Account and scoped Party limits."""
        module = importlib.import_module(MODULE_NAME)
        rcgle = frappe.qb.DocType(module.DOCTYPE_RC_GLE)
        query = frappe.qb.from_(rcgle).select(rcgle.party)
        filters = _dict(party_type="Customer", party=None)
        permissions = {
            "Account": [{"doc": "Receivable", "applicable_for": None}],
            "Customer": [{"doc": "CUST-001", "applicable_for": module.DOCTYPE_RC_GLE}],
        }

        with patch.object(
            frappe.permissions,
            "get_user_permissions",
            return_value=permissions,
        ):
            sql, values = module._apply_source_permissions(query, rcgle, filters).walk()

        assert "`tabReporting Currency GLE`" in sql
        assert "`tabCustomer`" in sql
        assert "`account`" in sql
        assert "`party`" in sql
        assert "Receivable" in values.values()
        assert "CUST-001" in values.values()

    def test_total_uses_only_rows_left_by_source_permissions(self) -> None:
        """A denied party row cannot contribute to the synthetic totals row."""
        data_module = importlib.import_module(
            MODULE_NAME.rsplit(".", 1)[0] + ".tbfpr_data"
        )
        filters = _dict(
            party_type="Customer",
            party=None,
            account=None,
            company="Karam",
            show_zero_values=0,
        )
        with (
            patch.object(
                data_module,
                "get_reporting_currency_balances",
                return_value={"CUST-001": {"debit": 100.0}},
            ),
            patch.object(
                data_module.frappe,
                "get_list",
                return_value=[{"name": "CUST-001", "customer_name": "Allowed"}],
            ),
            patch.object(
                data_module.frappe.db,
                "get_single_value",
                return_value="USD",
            ),
        ):
            data = data_module.get_data(filters, show_party_name=True)

        assert data[-1]["debit"] == 100.0
        assert data[-1]["credit"] == 0.0
