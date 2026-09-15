"""Reporting snapshots must not block source deletion or revive cancelled rows."""

from typing import Any, override
from unittest.mock import patch

import frappe
from frappe.model.dynamic_links import invalidate_distinct_link_doctypes
from frappe.tests import IntegrationTestCase
from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync import (
    data_fetch,
    orchestrator,
    phases,
)

EXTRA_TEST_RECORD_DEPENDENCIES = ["Journal Entry"]  # noqa: V107 - Native test fixture loader.


class TestReportingSourceDeletion(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "rc_delete_" + frappe.generate_hash(length=8)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, previous_user)

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()
        # Raw fixture insertion bypasses Document's dynamic-link cache maintenance.
        for field in ("voucher_type", "against_voucher_type", "party_type"):
            if linked_doctype := values.get(field):
                invalidate_distinct_link_doctypes(doctype, field, linked_doctype)

    def test_manual_reporting_entry_protects_its_account_from_deletion(self) -> None:
        self._insert(
            "Account", self.token, account_name=self.token, lft=10000, rgt=10001
        )
        self._insert(
            "Reporting Currency GLE", self.token, account=self.token, manual_entry=1
        )
        before = frappe.get_doc("Reporting Currency GLE", self.token).as_dict()
        with self.assertRaises(frappe.LinkExistsError):
            frappe.delete_doc("Account", self.token)
        assert frappe.get_doc("Account", self.token).name == self.token
        assert frappe.get_doc("Reporting Currency GLE", self.token).as_dict() == before

    def test_gl_entry_delete_removes_only_its_generated_snapshot(self) -> None:
        self._insert("GL Entry", self.token)
        self._insert(
            "Reporting Currency GLE",
            self.token,
            gl_entry=self.token,
            docstatus=1,
            manual_entry=0,
            reporting_doe=0,
        )
        self._insert("Reporting Currency GLE", self.token + "manual", manual_entry=1)
        frappe.delete_doc("GL Entry", self.token)
        assert not frappe.db.exists("GL Entry", self.token)
        assert not frappe.db.exists("Reporting Currency GLE", self.token)
        assert (
            frappe.get_doc("Reporting Currency GLE", self.token + "manual").get(
                "manual_entry"
            )
            == 1
        )

    def test_empty_source_sync_removes_generated_doe_and_preserves_manual(self) -> None:
        # The test savepoint restores the isolated CI site's original ledger.
        frappe.db.delete("Reporting Currency GLE")
        self._insert(
            "Reporting Currency GLE",
            self.token + "doe",
            reporting_doe=1,
            manual_entry=0,
        )
        self._insert("Reporting Currency GLE", self.token + "manual", manual_entry=1)
        with (
            patch.object(orchestrator, "hold_ledger_lock"),
            patch.object(
                orchestrator,
                "validate_settings",
                return_value={
                    "reporting_currency": "USD",
                    "last_sync_timestamp": "2026-01-01",
                },
            ),
            patch.object(phases, "fetch_gl_entries", return_value=[]),
            patch.object(orchestrator, "publish_sync_progress"),
            patch.object(phases, "publish_sync_progress"),
        ):
            for _ in range(2):
                assert (
                    orchestrator.sync_reporting_currency_entries("event")["inserted"]
                    == 0
                )
                assert frappe.get_all("Reporting Currency GLE", pluck="name") == [
                    self.token + "manual"
                ]

    def test_referenced_exchange_rate_cannot_be_deleted(self) -> None:
        for manual in (0, 1):
            with self.subTest(manual_entry=manual):
                name = self.token + str(manual)
                self._insert(
                    "Currency Exchange",
                    name,
                    from_currency="EUR",
                    to_currency="GBP",
                    date="2096-01-01",
                    exchange_rate=3,
                )
                self._insert(
                    "Reporting Currency GLE",
                    name,
                    currency_exchange=name,
                    manual_entry=manual,
                    reporting_debit=300,
                    source_exchange_rate=3,
                )
                before = frappe.get_doc("Reporting Currency GLE", name).as_dict()
                with self.assertRaises(frappe.LinkExistsError):
                    frappe.delete_doc("Currency Exchange", name)
                assert (
                    frappe.get_doc("Currency Exchange", name).get("exchange_rate") == 3
                )
                assert (
                    frappe.get_doc("Reporting Currency GLE", name).as_dict() == before
                )

    def test_voucher_delete_is_not_blocked_by_reporting_dynamic_links(self) -> None:
        self._insert("Journal Entry", self.token)
        for suffix, field in (
            ("voucher", "voucher_no"),
            ("against", "against_voucher"),
        ):
            values = {field: self.token}
            values[
                "voucher_type" if field == "voucher_no" else "against_voucher_type"
            ] = "Journal Entry"
            self._insert(
                "Reporting Currency GLE",
                self.token + suffix,
                docstatus=1,
                reporting_debit=125,
                **values,
            )
        frappe.delete_doc("Journal Entry", self.token)
        assert not frappe.db.exists("Journal Entry", self.token)
        assert not frappe.db.exists("Reporting Currency GLE", self.token + "voucher")
        against = frappe.get_doc("Reporting Currency GLE", self.token + "against")
        assert against.get("reporting_debit") == 125
        assert not against.get("against_voucher")
        assert not against.get("against_voucher_type")

    def test_native_voucher_cancellation_allows_generated_reporting_references(
        self,
    ) -> None:
        voucher = frappe.copy_doc(self.globalTestRecords["Journal Entry"][0])
        voucher.insert()
        voucher.submit()
        assert voucher.name
        self._insert(
            "Reporting Currency GLE",
            self.token,
            voucher_type="Journal Entry",
            voucher_no=voucher.name,
            docstatus=1,
        )
        voucher.cancel()
        assert frappe.get_doc("Journal Entry", voucher.name).docstatus == 2
        assert (
            frappe.get_doc("Reporting Currency GLE", self.token).get("voucher_no")
            == voucher.name
        )

    def test_other_voucher_dependencies_still_block_deletion(self) -> None:
        self._insert("Journal Entry", self.token)
        self._insert("Journal Entry", self.token + "dependent", amended_from=self.token)
        self._insert(
            "Reporting Currency GLE",
            self.token,
            voucher_type="Journal Entry",
            voucher_no=self.token,
            docstatus=1,
        )
        with self.assertRaises(frappe.LinkExistsError):
            frappe.delete_doc("Journal Entry", self.token)
        assert frappe.db.exists("Journal Entry", self.token)

    def test_native_voucher_cancellation_preserves_manual_link_protection(self) -> None:
        voucher = frappe.copy_doc(self.globalTestRecords["Journal Entry"][0])
        voucher.insert()
        voucher.submit()
        assert voucher.name
        self._insert(
            "Reporting Currency GLE",
            self.token,
            voucher_type="Journal Entry",
            voucher_no=voucher.name,
            docstatus=1,
            manual_entry=1,
            reporting_debit=125,
        )
        before = frappe.get_doc("Reporting Currency GLE", self.token).as_dict()
        frappe.db.savepoint("before_cancel")
        with self.assertRaises(frappe.LinkExistsError):
            voucher.cancel()
        frappe.db.rollback(save_point="before_cancel")
        assert frappe.get_doc("Journal Entry", voucher.name).docstatus == 1
        assert frappe.get_doc("Reporting Currency GLE", self.token).as_dict() == before

    def test_submitted_voucher_still_requires_cancellation(self) -> None:
        self._insert("Journal Entry", self.token, docstatus=1)
        with self.assertRaises(frappe.ValidationError):
            frappe.delete_doc("Journal Entry", self.token)
        assert frappe.db.exists("Journal Entry", self.token)

    def test_manual_source_links_still_block_deletion(self) -> None:
        for doctype, link in (
            ("GL Entry", {"gl_entry": self.token}),
            (
                "Journal Entry",
                {"voucher_type": "Journal Entry", "voucher_no": self.token},
            ),
            (
                "Journal Entry",
                {
                    "against_voucher_type": "Journal Entry",
                    "against_voucher": self.token,
                },
            ),
        ):
            with self.subTest(link=link):
                frappe.db.savepoint("manual_source")
                self._insert(doctype, self.token)
                self._insert(
                    "Reporting Currency GLE", self.token, manual_entry=1, **link
                )
                before = frappe.get_doc("Reporting Currency GLE", self.token).as_dict()
                with self.assertRaises(frappe.LinkExistsError):
                    frappe.delete_doc(doctype, self.token)
                assert frappe.get_doc(doctype, self.token).name == self.token
                assert (
                    frappe.get_doc("Reporting Currency GLE", self.token).as_dict()
                    == before
                )
                frappe.db.rollback(save_point="manual_source")

    def test_delete_permission_remains_required(self) -> None:
        self._insert("Journal Entry", self.token)
        frappe.set_user("Guest")
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc("Journal Entry", self.token)
        assert frappe.db.exists("Journal Entry", self.token)

    def test_voucher_ledger_cleanup_cannot_orphan_a_manual_gl_reference(self) -> None:
        frappe.db.set_single_value(
            "Accounts Settings", "delete_linked_ledger_entries", 1
        )
        self._insert("Journal Entry", self.token)
        self._insert(
            "GL Entry", self.token, voucher_type="Journal Entry", voucher_no=self.token
        )
        self._insert(
            "Reporting Currency GLE", self.token, gl_entry=self.token, manual_entry=1
        )
        with self.assertRaises(frappe.LinkExistsError):
            frappe.delete_doc("Journal Entry", self.token)
        assert frappe.get_doc("GL Entry", self.token).name == self.token
        assert (
            frappe.get_doc("Reporting Currency GLE", self.token).get("gl_entry")
            == self.token
        )

    def test_cancelled_gl_rows_are_not_fetched_and_stale_copies_are_cleaned(
        self,
    ) -> None:
        self._insert("GL Entry", self.token, docstatus=1, is_cancelled=1)
        frappe.db.set_value(
            "GL Entry", self.token, "modified", "2020-01-01", update_modified=False
        )
        self._insert("Reporting Currency GLE", self.token, gl_entry=self.token)
        assert self.token not in {row["name"] for row in data_fetch.fetch_gl_entries()}
        assert self.token not in {
            row["name"] for row in data_fetch.fetch_cancelled_gl_entries("2021-01-01")
        }
        with patch.object(phases, "publish_sync_progress"):
            phases.run_deletion_phase("test", None, "2021-01-01", True)
        assert not frappe.db.exists("Reporting Currency GLE", self.token)

    def test_active_source_is_fetched_and_its_snapshot_is_preserved(self) -> None:
        self._insert("GL Entry", self.token, docstatus=1, is_cancelled=0)
        self._insert("Reporting Currency GLE", self.token, gl_entry=self.token)
        assert self.token in {row["name"] for row in data_fetch.fetch_gl_entries()}
        data_fetch.cleanup_orphaned_rc_gle_records()
        assert frappe.db.exists("Reporting Currency GLE", self.token)

    def test_cleanup_preserves_manual_and_doe_rows(self) -> None:
        self._insert("Reporting Currency GLE", self.token + "manual", manual_entry=1)
        self._insert("Reporting Currency GLE", self.token + "doe", reporting_doe=1)
        data_fetch.cleanup_orphaned_rc_gle_records()
        assert frappe.db.exists("Reporting Currency GLE", self.token + "manual")
        assert frappe.db.exists("Reporting Currency GLE", self.token + "doe")
