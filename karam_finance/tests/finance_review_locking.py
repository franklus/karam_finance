"""Real database connections exercise reporting-ledger writer exclusion."""

from __future__ import annotations

from typing import Any, override

import frappe
from erpnext.accounts.doctype.gl_entry.gl_entry import rename_temporarily_named_docs
from frappe.tests import IntegrationTestCase
from karam_finance.reporting_currency import currency_change
from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync import (
    doe,
    orchestrator,
)
from karam_finance.reporting_currency.ledger_lock import (
    hold_ledger_lock,
    ledger_operation,
)
from karam_finance.tests.site_safety import require_disposable_test_site


class TestFinanceReviewLocking(IntegrationTestCase):
    @classmethod
    @override
    def setUpClass(_cls) -> None:
        require_disposable_test_site()
        super().setUpClass()

    @override
    def setUp(self) -> None:
        require_disposable_test_site()
        super().setUp()
        self.token = "locking_review_" + frappe.generate_hash(length=8)
        self.currency = self.token + "_CUR"
        self.company = self.token
        self.account = self.token + "_CASH"
        self.gl_entry = self.token + "_GL"
        self.rc_gle = "RC-" + self.gl_entry
        self.addCleanup(self._restore_fixture)
        self._create_fixture()
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — committed lock fixture.
        # Initialising Frappe's secondary connection changes frappe.local.db.
        # Restore the primary explicitly before the transaction under test.
        frappe.local.db = self._primary_connection

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()

    def _create_fixture(self) -> None:
        self._insert(
            "Currency",
            self.currency,
            currency_name=self.currency,
            enabled=1,
            fraction_units=100,
            smallest_currency_fraction_value=0.01,
        )
        self._insert(
            "Company",
            self.company,
            company_name=self.company,
            abbr="LR" + self.token[-6:].upper(),
            default_currency=self.currency,
        )
        self._insert(
            "Fiscal Year",
            self.token,
            year_start_date="2096-01-01",
            year_end_date="2096-12-31",
            disabled=0,
        )
        self._insert(
            "Account",
            self.account,
            company=self.company,
            account_name="Cash",
            account_currency=self.currency,
            root_type="Asset",
            report_type="Balance Sheet",
            account_type="Cash",
            is_group=0,
            disabled=0,
        )
        self._insert(
            "GL Entry",
            self.gl_entry,
            company=self.company,
            account=self.account,
            account_currency=self.currency,
            debit=100,
            credit=0,
            debit_in_account_currency=100,
            credit_in_account_currency=0,
            posting_date="2096-01-15",
            fiscal_year=self.token,
            is_cancelled=0,
            docstatus=1,
            is_opening="No",
            voucher_type="Journal Entry",
            voucher_no=self.token,
            to_rename=0,
        )
        values = frappe.get_doc("GL Entry", self.gl_entry).as_dict()
        values.pop("doctype")
        values.pop("name")
        values.update(
            gl_entry=self.gl_entry,
            reporting_currency=self.currency,
            reporting_doe=0,
            manual_entry=0,
            reporting_debit=100,
            reporting_credit=0,
        )
        self._insert("Reporting Currency GLE", self.rc_gle, **values)

    def _restore_fixture(self) -> None:
        frappe.local.db = self._primary_connection
        frappe.db.rollback()
        frappe.db.delete("Reporting Currency GLE", {"company": self.company})
        frappe.db.delete("GL Entry", {"company": self.company})
        frappe.db.delete("Account", {"company": self.company})
        frappe.db.delete("Fiscal Year", self.token)
        frappe.db.delete("Company", self.company)
        frappe.db.delete("Currency", self.currency)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — fixture cleanup.
        require_disposable_test_site()

    def _assert_renamed_state(self, old: str, new: str) -> None:
        with self.secondary_connection():
            frappe.db.rollback()
            assert not frappe.db.exists("GL Entry", old)
            assert frappe.db.exists("GL Entry", new)
            rows = frappe.get_all(
                "Reporting Currency GLE",
                filters={"gl_entry": new},
                fields=["name", "gl_entry"],
            )
            assert len(rows) == 1
            assert rows[0].gl_entry == new
        frappe.local.db = self._primary_connection

    def test_settings_transaction_prevents_concurrent_ledger_clear(self) -> None:
        settings = frappe.get_single("Reporting Currency Settings")
        # The disposable fixture has reporting rows but intentionally leaves the
        # site-wide setting at its baseline value.  Match the in-memory document
        # to that persisted value so validation exercises the lock without
        # requesting a currency transition.
        settings.set(
            "reporting_currency",
            frappe.db.get_single_value(
                "Reporting Currency Settings", "reporting_currency", cache=False
            ),
        )
        settings.run_method("validate")
        with (
            self.secondary_connection(),
            self.assertRaisesRegex(frappe.ValidationError, "already running"),
        ):
            orchestrator.delete_all_entries()

    def test_manual_insert_and_all_workers_reject_overlapping_settings_transaction(
        self,
    ) -> None:
        hold_ledger_lock()
        operations = (
            lambda: frappe.get_doc({"doctype": "Reporting Currency GLE"}).insert(),
            lambda: orchestrator.run_reporting_currency_sync_job(
                "review", "review_done"
            ),
            doe._compute_doe_background,
            lambda: currency_change.run_currency_change_job(
                "USD",
                None,
                progress_event="review",
                done_event="review_done",
                user="Administrator",
            ),
            lambda: orchestrator.on_gl_entry_before_rename(
                object(), "before_rename", "old-gl", "new-gl"
            ),
            lambda: orchestrator.on_gl_entry_rename(
                object(), "after_rename", "old-gl", "new-gl"
            ),
            lambda: orchestrator.on_gle_rename_hook(newname="new-gl", oldname="old-gl"),
        )
        for operation in operations:
            with self.subTest(operation=operation), self.secondary_connection():
                with self.assertRaisesRegex(frappe.ValidationError, "already running"):
                    operation()
                frappe.db.rollback()

    def test_commit_releases_document_lock_for_another_connection(self) -> None:
        hold_ledger_lock()
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
        with self.secondary_connection():
            lock = hold_ledger_lock()
            assert not lock.released
            frappe.db.rollback()
            assert lock.released

    def test_operation_keeps_lock_across_commit_and_releases_on_failure(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "test rollback"), ledger_operation():
            frappe.db.commit()  # nosemgrep: frappe-manual-commit — separate-connection integration evidence.
            with (
                self.secondary_connection(),
                self.assertRaisesRegex(frappe.ValidationError, "already running"),
            ):
                hold_ledger_lock()
            message = "test rollback"
            raise RuntimeError(message)
        with self.secondary_connection():
            assert not hold_ledger_lock().released
            frappe.db.rollback()

    def test_native_rename_doc_blocks_concurrent_connection_and_commits_link(
        self,
    ) -> None:
        renamed = self.gl_entry + "_RENAMED"
        frappe.rename_doc(
            "GL Entry",
            self.gl_entry,
            renamed,
            force=True,
            rebuild_search=False,
            show_alert=False,
        )
        with self.secondary_connection():
            with self.assertRaisesRegex(frappe.ValidationError, "already running"):
                frappe.rename_doc(
                    "GL Entry",
                    self.gl_entry,
                    self.gl_entry + "_SECOND",
                    force=True,
                    rebuild_search=False,
                    show_alert=False,
                )
            frappe.db.rollback()
        frappe.local.db = self._primary_connection
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — rename transaction evidence.
        self._assert_renamed_state(self.gl_entry, renamed)

    def test_scheduler_rename_rolls_back_source_and_reporting_link_together(
        self,
    ) -> None:
        frappe.db.set_value("GL Entry", self.gl_entry, "to_rename", 1)
        rename_temporarily_named_docs("GL Entry")
        renamed = frappe.db.get_value(
            "GL Entry", {"company": self.company, "name": ["!=", self.gl_entry]}, "name"
        )
        assert renamed and renamed != self.gl_entry
        with self.secondary_connection():
            frappe.db.rollback()
            assert frappe.db.exists("GL Entry", self.gl_entry)
            assert frappe.db.exists("Reporting Currency GLE", self.rc_gle)
        frappe.local.db = self._primary_connection
        frappe.db.rollback()  # nosemgrep: frappe-manual-rollback — rollback evidence.
        with self.secondary_connection():
            frappe.db.rollback()
            assert frappe.db.exists("GL Entry", self.gl_entry)
            assert frappe.db.exists("Reporting Currency GLE", self.rc_gle)

    def test_scheduler_rename_is_blocked_by_active_sync_lock(self) -> None:
        frappe.db.set_value("GL Entry", self.gl_entry, "to_rename", 1)
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — scheduler contention setup.
        hold_ledger_lock()

        with self.secondary_connection():
            with self.assertRaisesRegex(frappe.ValidationError, "already running"):
                rename_temporarily_named_docs("GL Entry")
            frappe.db.rollback()  # nosemgrep: frappe-manual-rollback — contention recovery.
            assert frappe.db.exists("GL Entry", self.gl_entry)
            assert (
                frappe.db.get_value("Reporting Currency GLE", self.rc_gle, "gl_entry")
                == self.gl_entry
            )

        frappe.local.db = self._primary_connection
        frappe.db.rollback()  # nosemgrep: frappe-manual-rollback — release sync lock.

    def test_scheduler_rename_commits_source_and_reporting_link_together(self) -> None:
        frappe.db.set_value("GL Entry", self.gl_entry, "to_rename", 1)
        rename_temporarily_named_docs("GL Entry")
        renamed = frappe.db.get_value(
            "GL Entry", {"company": self.company, "name": ["!=", self.gl_entry]}, "name"
        )
        assert renamed and renamed != self.gl_entry
        frappe.db.commit()  # nosemgrep: frappe-manual-commit — scheduler transaction evidence.
        self._assert_renamed_state(self.gl_entry, renamed)
