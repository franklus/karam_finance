"""Real row locks, stored selections and linked GL updates through the RPCs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any, override
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from karam_finance.letter_reconciliation.doctype.letter_reconciliation import (
    letter_reconciliation as reconciliation,
)
from karam_finance.patches import backfill_gl_letter_from_je_account


class TestFinanceReviewReconciliation(IntegrationTestCase):
    SHOW_TRANSACTION_COMMIT_WARNINGS = False  # noqa: V107 - Frappe IntegrationTestCase configuration.

    @override
    def setUp(self) -> None:
        super().setUp()
        frappe.set_user("Administrator")
        self.token = "letter_review_" + frappe.generate_hash(length=8)
        self.saved_year = frappe.get_all(
            "Letter Settings", filters={"name": "2097"}, fields=["*"]
        )
        self.parents = [self.token + suffix for suffix in ("C0", "D0", "C1", "D1")]
        self.addCleanup(self._restore_fixture)
        self._insert("Company", self.token, default_currency="USD")
        for suffix in ("Cash", "Offset"):
            self._insert(
                "Account",
                self.token + suffix,
                company=self.token,
                account_name=suffix,
                account_currency="USD",
                enable_lettering=1,
                root_type="Asset",
                report_type="Balance Sheet",
                is_group=0,
            )
        for parent in self.parents:
            self._create_journal(parent)
        frappe.db.delete("Letter Settings", {"name": "2097"})
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()

    def _create_journal(self, parent: str) -> None:
        is_credit = parent[-2] == "C"
        self._insert(
            "Journal Entry",
            parent,
            company=self.token,
            docstatus=1,
            voucher_type="Journal Entry",
            posting_date="2097-01-15",
        )
        for index, suffix in enumerate(("Cash", "Offset")):
            credit = 100 if is_credit == (index == 0) else 0
            debit = 100 - credit
            name = parent + suffix
            self._insert(
                "Journal Entry Account",
                name,
                parent=parent,
                parenttype="Journal Entry",
                parentfield="accounts",
                idx=index + 1,
                account=self.token + suffix,
                account_currency="USD",
                docstatus=1,
                credit=credit,
                debit=debit,
                credit_in_account_currency=credit,
                debit_in_account_currency=debit,
                exchange_rate=1,
                letter="",
            )
            self._insert(
                "GL Entry",
                name,
                voucher_type="Journal Entry",
                voucher_no=parent,
                voucher_detail_no=name,
                company=self.token,
                account=self.token + suffix,
                account_currency="USD",
                posting_date="2097-01-15",
                docstatus=1,
                credit=credit,
                debit=debit,
                credit_in_account_currency=credit,
                debit_in_account_currency=debit,
                letter="",
                is_cancelled=0,
            )

    def _restore_fixture(self) -> None:
        frappe.db.rollback()
        for doctype in ("GL Entry", "Account"):
            frappe.db.delete(doctype, {"company": self.token})
        frappe.db.delete("Journal Entry Account", {"parent": ["in", self.parents]})
        frappe.db.delete("Journal Entry", {"name": ["in", self.parents]})
        frappe.db.delete("Company", {"name": self.token})
        frappe.db.delete("Letter Settings", {"name": "2097"})
        for row in self.saved_year:
            self._insert("Letter Settings", row.pop("name"), **row)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.

    def _selection(self, pair: int = 0) -> tuple[list[Any], list[Any]]:
        return (
            [{"jv_row_name": self.token + f"C{pair}Cash"}],
            [{"jv_row_name": self.token + f"D{pair}Cash"}],
        )

    def test_assignment_and_removal_update_only_selected_journal_and_gl_rows(
        self,
    ) -> None:
        result = reconciliation.set_letter(*self._selection(), _latest_year=1900)
        assert result == {"last_letter": "A", "next_letter": "B"}
        assert frappe.db.get_value("Letter Settings", "2097", "letter") == "B"
        self._assert_letters(pair=0, expected="A")
        self._assert_letters(pair=1, expected="")
        assert reconciliation.remove_letter(*self._selection()) == {"success": True}
        self._assert_letters(pair=0, expected="")

    def _assert_letters(self, *, pair: int, expected: str) -> None:
        names = [item["jv_row_name"] for side in self._selection(pair) for item in side]
        for doctype in ("Journal Entry Account", "GL Entry"):
            assert frappe.get_all(
                doctype, filters={"name": ["in", names]}, pluck="letter"
            ) == [expected, expected]

    def test_forged_amounts_and_wrong_account_fail_without_mutation(self) -> None:
        credit, debit = self._selection()
        credit[0]["credit"] = 0
        debit[0]["debit"] = 0
        with self.assertRaisesRegex(frappe.ValidationError, "stored journal rows"):
            reconciliation.set_letter(credit, debit)
        with self.assertRaisesRegex(frappe.ValidationError, "one account"):
            reconciliation.set_letter(*self._selection(), account=self.token + "Offset")
        self._assert_letters(pair=0, expected="")

    def test_cancelled_and_stale_rows_cannot_be_reconciled(self) -> None:
        frappe.db.set_value("Journal Entry", self.parents[0], "docstatus", 2)
        with self.assertRaisesRegex(frappe.ValidationError, "submitted"):
            reconciliation.set_letter(*self._selection())
        frappe.db.set_value("Journal Entry", self.parents[0], "docstatus", 1)
        reconciliation.set_letter(*self._selection())
        with self.assertRaisesRegex(frappe.ValidationError, "already have a letter"):
            reconciliation.set_letter(*self._selection())
        credit, debit = self._selection()
        credit[0]["letter"] = debit[0]["letter"] = "B"
        with self.assertRaisesRegex(frappe.ValidationError, "letters changed"):
            reconciliation.remove_letter(credit, debit)
        self._assert_letters(pair=0, expected="A")

    def test_gl_write_failure_rolls_back_rows_and_year_allocation(self) -> None:
        sql = frappe.local.db.sql

        def fail_gl_update(query: str, *args: Any, **kwargs: Any) -> Any:
            result = sql(query, *args, **kwargs)
            if query.startswith("UPDATE `tabGL Entry` SET `letter`"):
                message = "Injected GL failure"
                raise RuntimeError(message)
            return result

        with (
            patch.object(frappe.local.db, "sql", side_effect=fail_gl_update),
            self.assertRaisesRegex(frappe.ValidationError, "Injected GL failure"),
        ):
            reconciliation.set_letter(*self._selection())
        self._assert_letters(pair=0, expected="")
        assert not frappe.db.exists("Letter Settings", "2097")

    def test_backfill_respects_party_dimensions_blank_sources_and_existing_letters(
        self,
    ) -> None:
        name = self.token + "C0Cash"
        values = {"party_type": "Customer", "party": "A", "branch": "Branch A"}
        frappe.db.set_value("GL Entry", name, {**values, "voucher_detail_no": None})
        frappe.db.set_value("Journal Entry Account", name, {**values, "letter": "X"})
        self._insert(
            "Journal Entry Account",
            self.token + "another",
            parent=self.parents[0],
            parenttype="Journal Entry",
            parentfield="accounts",
            docstatus=1,
            account=self.token + "Cash",
            account_currency="USD",
            letter="Y",
            party_type="Customer",
            party="B",
            branch="Branch A",
        )
        backfill_gl_letter_from_je_account.execute()
        assert frappe.db.get_value("GL Entry", name, "letter") == "X"
        backfill_gl_letter_from_je_account.execute()
        assert frappe.db.get_value("GL Entry", name, "letter") == "X"
        frappe.db.set_value("GL Entry", name, "letter", "")
        frappe.db.set_value(
            "Journal Entry Account",
            self.token + "another",
            {"party": "A", "branch": "Branch B", "letter": ""},
        )
        backfill_gl_letter_from_je_account.execute()
        assert frappe.db.get_value("GL Entry", name, "letter") == "X"
        frappe.db.set_value("GL Entry", name, "letter", "")
        frappe.db.set_value(
            "Journal Entry Account", self.token + "another", "branch", "Branch A"
        )
        backfill_gl_letter_from_je_account.execute()
        assert frappe.db.get_value("GL Entry", name, "letter") == ""
        frappe.db.set_value("GL Entry", name, "letter", "HISTORICAL")
        backfill_gl_letter_from_je_account.execute()
        assert frappe.db.get_value("GL Entry", name, "letter") == "HISTORICAL"

    def test_competing_assignments_cannot_overwrite_the_same_rows(self) -> None:
        outcomes = self._compete((0, 0))
        assert sorted(outcome["status"] for outcome in outcomes) == [
            "rejected",
            "success",
        ]
        frappe.db.rollback()
        self._assert_letters(pair=0, expected="A")
        assert frappe.db.get_value("Letter Settings", "2097", "letter") == "B"

    def test_competing_first_year_allocations_are_unique(self) -> None:
        outcomes = self._compete((0, 1))
        # MariaDB snapshot isolation may reject a waiter. A fresh request must
        # then allocate the next letter without any changes from the failed one.
        for pair, outcome in enumerate(outcomes):
            if outcome["status"] == "rejected":
                assert "Reload and try again" in outcome["message"]
                frappe.db.rollback()
                self._assert_letters(pair=pair, expected="")
                outcomes[pair] = {
                    "status": "success",
                    **reconciliation.set_letter(*self._selection(pair)),
                }
                frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
        assert {outcome.get("last_letter") for outcome in outcomes} == {"A", "B"}
        assert all(outcome["status"] == "success" for outcome in outcomes)
        frappe.db.rollback()
        assert frappe.db.get_value("Letter Settings", "2097", "letter") == "C"

    def _compete(self, pairs: tuple[int, int]) -> list[dict[str, str]]:
        barrier = Barrier(2)
        site = frappe.local.site
        sites_path = frappe.local.sites_path

        def assign(pair: int) -> dict[str, str]:
            frappe.init(site=site, sites_path=sites_path)
            frappe.connect()
            try:
                barrier.wait(timeout=10)
                result = reconciliation.set_letter(*self._selection(pair))
                frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
            except frappe.ValidationError as exc:
                frappe.db.rollback()
                return {"status": "rejected", "message": str(exc)}
            else:
                return {"status": "success", **result}
            finally:
                frappe.destroy()

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(assign, pairs))
