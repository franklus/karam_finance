"""Permission-filtered SQL must feed every Party Trial Balance output path."""

import importlib
import re
import sqlite3
from itertools import product
from typing import Any, override
from unittest import TestCase
from unittest.mock import Mock, patch

from frappe import _dict
from frappe.query_builder.builder import MariaDB
from pypika.queries import QueryBuilder

PREFIX = "karam_finance.karam_general.report.trial_balance_for_party_(karam)."
query_module = importlib.import_module(PREFIX + "tbfp_query")
data_module = importlib.import_module(PREFIX + "tbfp_data")


class TestPartyBalancePermissions(TestCase):
    @override  # noqa: V105 - unittest and Frappe test lifecycle callback.
    def setUp(self) -> None:
        self.database = sqlite3.connect(":memory:")
        self.addCleanup(self.database.close)
        self.database.row_factory = sqlite3.Row  # noqa: V101 - sqlite3 consumes this connection option.
        self.database.execute(
            "CREATE TABLE `tabGL Entry` (name TEXT, party TEXT, account TEXT, "
            "account_currency TEXT, debit REAL, credit REAL DEFAULT 0, "
            "debit_in_account_currency REAL, credit_in_account_currency REAL DEFAULT 0, "
            "company TEXT DEFAULT 'Karam', party_type TEXT DEFAULT 'Customer', "
            "posting_date TEXT DEFAULT '2026-01-15', is_cancelled INTEGER DEFAULT 0, "
            "is_opening TEXT DEFAULT 'No')"
        )
        self.database.execute(
            "CREATE TABLE `tabCustomer` (name TEXT, customer_name TEXT)"
        )
        self.database.executemany(
            "INSERT INTO `tabCustomer` VALUES (?, ?)",
            [
                ("A", "Allowed"),
                ("B", "Denied"),
                ("C", "Allowed zero"),
                ("Z", "Denied zero"),
            ],
        )
        self.database.executemany(
            "INSERT INTO `tabGL Entry` (name,party,account,account_currency,debit,debit_in_account_currency) VALUES (?,?,?,?,?,?)",
            [
                ("1", "A", "Allowed", "USD", 100, 100),
                ("2", "A", "Secret", "USD", 400, 400),
                ("3", "A", "Secret", "EUR", 600, 500),
                ("4", "B", "Allowed", "USD", 900, 900),
            ],
        )
        self.restricted = True
        frappe_mock = Mock(qb=MariaDB)
        frappe_mock.get_cached_value.return_value = "USD"
        for module in (query_module, data_module):
            self.enterContext(patch.object(module, "frappe", frappe_mock))
        self.enterContext(
            patch.object(MariaDB, "get_query", side_effect=self.permitted_names)
        )

        def run(query: QueryBuilder, **_kwargs: object) -> list[Any]:
            sql = re.sub(r" FORCE INDEX \([^)]*\)", "", query.get_sql())
            return [_dict(dict(row)) for row in self.database.execute(sql)]

        self.enterContext(patch.object(QueryBuilder, "run", new=run))
        rows_module = importlib.import_module(PREFIX + "tbfp_rows")
        self.enterContext(patch.object(rows_module, "_", side_effect=str))

    def permitted_names(self, doctype: str, **kwargs: Any) -> QueryBuilder:
        assert kwargs["ignore_permissions"] is False
        table = MariaDB.DocType(doctype)
        query = MariaDB.from_(table).select(table.name)
        if doctype == "Customer":
            assert kwargs["reference_doctype"] == "GL Entry"
            return (
                query.where(table.name.isin(["A", "C"])) if self.restricted else query
            )
        assert doctype == "GL Entry"
        return query.where(table.account == "Allowed") if self.restricted else query

    def result(
        self, *, names: bool, zeros: bool, party: str | None = None
    ) -> list[Any]:
        return data_module.get_data(
            _dict(
                company="Karam",
                party_type="Customer",
                party=party,
                from_date="2026-01-01",
                to_date="2026-01-31",
                show_zero_values=int(zeros),
                exclude_zero_balance_parties=0,
            ),
            show_party_name=names,
        )

    def test_restricted_rows_and_totals_exclude_other_parties_and_account_amounts(
        self,
    ) -> None:
        for names, zeros in product((False, True), repeat=2):
            with self.subTest(names=names, zeros=zeros):
                data = self.result(names=names, zeros=zeros)
                assert [row["party"] for row in data[:-2]] == (
                    ["A", "C"] if zeros else ["A"]
                )
                assert data[0]["debit"] == data[0]["closing_debit"] == 100
                assert data[-1]["debit"] == data[-1]["closing_debit"] == 100
                assert data[-1]["account_currency"] == "USD"
                assert data[-1]["debit_in_account_currency"] == 100

    def test_explicit_denied_party_cannot_reappear_as_a_zero_row(self) -> None:
        data = self.result(names=True, zeros=True, party="B")
        assert not data[:-2]
        assert data[-1]["debit"] == 0

    def test_unrestricted_totals_retain_all_contributions_and_mixed_currencies(
        self,
    ) -> None:
        self.restricted = False
        for names, zeros in product((False, True), repeat=2):
            with self.subTest(names=names, zeros=zeros):
                data = self.result(names=names, zeros=zeros)
                assert data[-1]["debit"] == data[-1]["closing_debit"] == 2000
                assert data[-1]["account_currency"] == ""
                assert data[-1]["debit_in_account_currency"] is None
