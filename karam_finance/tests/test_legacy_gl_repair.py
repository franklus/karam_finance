"""Public repair-entry regressions on native submitted Journal Entry fixtures."""

from operator import itemgetter
from typing import Any, override
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from karam_finance.patches.fix_merged_gl_entries import execute

EXTRA_TEST_RECORD_DEPENDENCIES = ["Journal Entry"]  # noqa: V107 - Frappe fixture loader.


class TestLegacyGLRepair(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "split_review_" + frappe.generate_hash(length=8)
        self.previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, self.previous_user)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        for suffix in ("A", "B"):
            frappe.get_doc(
                {
                    "doctype": "Project",
                    "name": self.token + suffix,
                    "project_name": self.token + suffix,
                }
            ).db_insert()
        self._create_voucher()

    def _create_voucher(
        self, *, bank_account: str | None = None, exchange_rate: float = 1
    ) -> None:
        self.voucher = frappe.copy_doc(self.globalTestRecords["Journal Entry"][0])
        bank = self.voucher.get("accounts")[1].as_dict()
        bank["account"] = bank_account or bank["account"]
        bank["account_currency"] = None
        self.voucher.set("multi_currency", int(exchange_rate != 1))
        self.voucher.set("accounts", [self.voucher.get("accounts")[0]])
        for project, amount in (("A", 60), ("A", 90), ("B", 100), ("B", 150)):
            row = dict(bank)
            for key in ("name", "parent", "idx"):
                row.pop(key, None)
            row.update(
                project=self.token + project,
                debit_in_account_currency=amount / exchange_rate,
                exchange_rate=exchange_rate,
                reference_detail_no=None,
            )
            self.voucher.append("accounts", row)
        self.voucher.insert()
        self.voucher.submit()
        assert self.voucher.name
        self.name = self.voucher.name
        self.bank = bank["account"]
        self._merge_fixture_rows()

    def _rows(self) -> list[Any]:
        names = frappe.get_list(
            "GL Entry",
            filters={"voucher_type": "Journal Entry", "voucher_no": self.name},
            pluck="name",
            limit=0,
        )
        return [frappe.get_doc("GL Entry", name) for name in names]

    def _active_bank_rows(self) -> list[Any]:
        return [
            row
            for row in self._rows()
            if row.get("account") == self.bank and not row.get("is_cancelled")
        ]

    def _merge_fixture_rows(self) -> None:
        for project in (self.token + "A", self.token + "B"):
            self._merge_project_rows(project)
        for child in self.voucher.get("accounts"):
            frappe.db.set_value(
                "Journal Entry Account", child.name, "reference_detail_no", ""
            )

    def _merge_project_rows(self, project: str) -> None:
        rows = [row for row in self._rows() if row.get("project") == project]
        amounts = {
            field: sum(row.get(field) or 0 for row in rows)
            for field in (
                "debit",
                "credit",
                "debit_in_account_currency",
                "credit_in_account_currency",
                "debit_in_transaction_currency",
                "credit_in_transaction_currency",
            )
        }
        amounts["voucher_detail_no"] = ""
        frappe.db.set_value("GL Entry", rows[0].name, amounts)
        for row in rows[1:]:
            frappe.db.delete("GL Entry", {"name": row.name})

    def test_targeted_repair_preserves_both_projects_and_each_child_amount(
        self,
    ) -> None:
        execute(voucher_nos=[self.name])
        rows = self._active_bank_rows()
        assert sorted((row.get("project"), row.get("debit")) for row in rows) == [
            (self.token + "A", 60),
            (self.token + "A", 90),
            (self.token + "B", 100),
            (self.token + "B", 150),
        ]
        assert {row.get("voucher_detail_no") for row in rows} == {
            child.name
            for child in self.voucher.get("accounts")
            if child.account == self.bank
        }

    def _snapshot(self) -> tuple[Any, Any]:
        return (
            sorted((row.as_dict() for row in self._rows()), key=itemgetter("name")),
            frappe.get_doc("Journal Entry", self.name).as_dict(),
        )

    def test_each_currency_amount_mismatch_preserves_originals(self) -> None:
        original = next(
            row for row in self._rows() if row.get("project") == self.token + "A"
        )
        for field in (
            "debit",
            "credit",
            "debit_in_account_currency",
            "credit_in_account_currency",
            "debit_in_transaction_currency",
            "credit_in_transaction_currency",
        ):
            with self.subTest(field=field):
                frappe.db.set_value(
                    "GL Entry", original.name, field, (original.get(field) or 0) + 1
                )
                before = self._snapshot()
                with self.assertRaisesRegex(frappe.ValidationError, "do not match"):
                    execute(voucher_nos=[self.name])
                assert self._snapshot() == before
                frappe.db.set_value(
                    "GL Entry", original.name, field, original.get(field)
                )

    def test_repeated_repair_is_a_noop_and_caller_can_roll_back(self) -> None:
        execute(voucher_nos=[self.name])
        after = self._snapshot()
        execute(voucher_nos=[self.name])
        assert self._snapshot() == after
        frappe.db.rollback(save_point=self.token)
        assert not frappe.db.exists("Journal Entry", self.name)

    def test_database_insertion_failure_restores_the_entire_voucher(self) -> None:
        before = self._snapshot()
        original_sql = frappe.db.sql
        injected = []

        def sql(query: Any, *args: Any, **kwargs: Any) -> Any:
            result = original_sql(query, *args, **kwargs)
            if "INSERT INTO `tabGL Entry`" in str(query):
                injected.append(True)
                message = "Injected GL insertion failure"
                raise RuntimeError(message)
            return result

        with (
            patch.object(frappe.db, "sql", side_effect=sql),
            self.assertRaisesRegex(RuntimeError, "Injected GL"),
        ):
            execute(voucher_nos=[self.name])
        assert injected
        assert self._snapshot() == before

    def test_manual_reporting_source_reference_blocks_repair(self) -> None:
        source = self._rows()[0]
        frappe.get_doc(
            {
                "doctype": "Reporting Currency GLE",
                "name": self.token,
                "gl_entry": source.name,
                "manual_entry": 1,
            }
        ).db_insert()
        before = self._snapshot()
        with self.assertRaises(frappe.LinkExistsError):
            execute(voucher_nos=[self.name])
        assert self._snapshot() == before

    def test_active_accounting_dimension_is_preserved_and_mismatches_are_rejected(
        self,
    ) -> None:
        for suffix in ("A", "B"):
            branch = self.token + suffix
            frappe.get_doc(
                {"doctype": "Branch", "name": branch, "branch": branch}
            ).db_insert()
        for child in self.voucher.get("accounts"):
            if child.project:
                frappe.db.set_value(
                    "Journal Entry Account", child.name, "branch", child.project
                )
        before = self._snapshot()
        with self.assertRaisesRegex(frappe.ValidationError, "do not match"):
            execute(voucher_nos=[self.name])
        assert self._snapshot() == before
        for row in self._rows():
            if row.get("project"):
                frappe.db.set_value("GL Entry", row.name, "branch", row.get("project"))
        execute(voucher_nos=[self.name])
        rows = self._active_bank_rows()
        assert sorted((row.get("branch"), row.get("debit")) for row in rows) == [
            (self.token + "A", 60),
            (self.token + "A", 90),
            (self.token + "B", 100),
            (self.token + "B", 150),
        ]

    def test_partial_existing_child_does_not_authorise_deleting_originals(self) -> None:
        original = next(
            row for row in self._rows() if row.get("project") == self.token + "A"
        )
        child = next(
            child
            for child in self.voucher.get("accounts")
            if child.project == self.token + "A"
        )
        partial = frappe.copy_doc(original)
        partial.name = self.token + "partial"
        partial.set("voucher_detail_no", child.name)
        partial.db_insert()
        before = self._snapshot()
        with self.assertRaisesRegex(frappe.ValidationError, "do not match"):
            execute(voucher_nos=[self.name])
        assert self._snapshot() == before

    def test_disabled_dimension_cannot_be_silently_dropped_by_repost(self) -> None:
        frappe.get_doc(
            {"doctype": "Branch", "name": self.token, "branch": self.token}
        ).db_insert()
        for child in self.voucher.get("accounts"):
            frappe.db.set_value(
                "Journal Entry Account", child.name, "branch", self.token
            )
        for row in self._rows():
            frappe.db.set_value("GL Entry", row.name, "branch", self.token)
        frappe.db.set_value(
            "Accounting Dimension", {"fieldname": "branch"}, "disabled", 1
        )
        before = self._snapshot()
        with self.assertRaisesRegex(frappe.ValidationError, "do not match"):
            execute(voucher_nos=[self.name])
        assert self._snapshot() == before

    def test_explicit_scope_leaves_other_candidates_untouched(self) -> None:
        before = self._snapshot()
        execute(voucher_nos=[])
        execute(voucher_nos=[self.token + "missing"])
        assert self._snapshot() == before

    def test_wrong_original_project_is_rejected_without_redistributing_amounts(
        self,
    ) -> None:
        original = next(
            row for row in self._rows() if row.get("project") == self.token + "B"
        )
        frappe.db.set_value("GL Entry", original.name, "project", self.token + "A")
        before = self._snapshot()
        with self.assertRaisesRegex(frappe.ValidationError, "do not match"):
            execute(voucher_nos=[self.name])
        assert self._snapshot() == before

    def test_later_voucher_failure_rolls_back_the_whole_request(self) -> None:
        first_name, first_before = self.name, self._snapshot()
        self._create_voucher()
        second_name = self.name
        assert first_name < second_name
        original = next(
            row for row in self._rows() if row.get("project") == self.token + "A"
        )
        frappe.db.set_value(
            "GL Entry", original.name, "debit", original.get("debit") + 1
        )
        second_before = self._snapshot()
        with self.assertRaisesRegex(frappe.ValidationError, "do not match"):
            execute(voucher_nos=[first_name, second_name])
        assert self._snapshot() == second_before
        self.name = first_name
        assert self._snapshot() == first_before

    def test_foreign_account_conserves_all_three_currencies_and_scopes_the_repair(
        self,
    ) -> None:
        domestic_name = self.name
        domestic_before = self._snapshot()
        parent = frappe.get_doc("Account", self.bank).get("parent_account")
        foreign_bank = frappe.get_doc(
            {
                "doctype": "Account",
                "account_name": self.token + "USD",
                "account_type": "Bank",
                "company": self.voucher.get("company"),
                "parent_account": parent,
                "account_currency": "USD",
            }
        ).insert()
        self._create_voucher(bank_account=foreign_bank.name, exchange_rate=2)
        execute(voucher_nos=[self.name])
        assert sorted(
            (
                row.get("project"),
                row.get("debit"),
                row.get("debit_in_account_currency"),
                row.get("debit_in_transaction_currency"),
            )
            for row in self._active_bank_rows()
        ) == [
            (self.token + "A", 60, 30, 30),
            (self.token + "A", 90, 45, 45),
            (self.token + "B", 100, 50, 50),
            (self.token + "B", 150, 75, 75),
        ]
        credit = next(
            row
            for row in self._rows()
            if row.get("credit") and not row.get("is_cancelled")
        )
        assert (
            credit.get("credit"),
            credit.get("credit_in_account_currency"),
            credit.get("credit_in_transaction_currency"),
        ) == (400, 400, 200)
        self.name = domestic_name
        assert self._snapshot() == domestic_before
