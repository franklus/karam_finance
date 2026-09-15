"""Native Sync and source renaming agree on synced reporting-entry identity."""

from typing import Any, override

import frappe
from frappe.tests import IntegrationTestCase
from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.orchestrator import (
    sync_reporting_currency_entries,
)


class TestSyncedGLNaming(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "naming_review_" + frappe.generate_hash(length=8)
        previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, previous_user)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        # Normal Sync is site-wide; isolate its inputs in the test transaction.
        frappe.db.delete("Reporting Currency GLE")
        frappe.db.delete("GL Entry")
        self._insert("Company", self.token, default_currency="EUR")
        self._insert(
            "Account",
            self.token,
            company=self.token,
            account_currency="EUR",
            account_type="Income Account",
            root_type="Income",
            report_type="Profit and Loss",
        )
        settings = frappe.get_single("Reporting Currency Settings")
        settings.update(
            {
                "reporting_currency": "EUR",
                "last_sync_timestamp": None,
                "last_ce_sync_timestamp": None,
            }
        )
        settings.set("rc_parameters", [])
        settings.append(
            "rc_parameters",
            {
                "doe_posting_date": "2040-12-31",
                "exchange_rate": 1,
                "profit_account": self.token,
                "loss_account": self.token,
            },
        )
        settings.save()

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()

    def _source(self, name: str) -> None:
        self._insert(
            "GL Entry",
            name,
            company=self.token,
            account=self.token,
            account_currency="EUR",
            debit=125,
            credit=0,
            debit_in_account_currency=125,
            credit_in_account_currency=0,
            posting_date="2040-06-30",
            fiscal_year="2040",
            docstatus=1,
            is_cancelled=0,
            voucher_type="Journal Entry",
            voucher_no=self.token + "_voucher",
            is_opening="No",
        )

    def _reporting_for(self, source: str) -> Any:
        names = frappe.get_list(
            "Reporting Currency GLE",
            filters={"gl_entry": source},
            pluck="name",
            limit=0,
        )
        assert len(names) == 1
        return frappe.get_doc("Reporting Currency GLE", names[0])

    def test_native_source_rename_matches_initial_sync_naming(self) -> None:
        self._source(self.token)
        sync_reporting_currency_entries(self.token, "Administrator")
        assert self._reporting_for(self.token).name == "RC-" + self.token
        new_source = "ACC-GLE-2040-00001"
        frappe.rename_doc("GL Entry", self.token, new_source, force=True)
        row = self._reporting_for(new_source)
        assert row.name == "KE-RCGLE-2040-00001"
        assert row.get("voucher_no") == self.token + "_voucher"
        assert row.get("reporting_debit") == 125

    def test_sync_and_native_rename_preserve_all_existing_name_formats(self) -> None:
        self._source(self.token)
        sync_reporting_currency_entries(self.token, "Administrator")
        old = self.token
        for source, expected in (
            ("ACC-GLE-2040-00007", "KE-RCGLE-2040-00007"),
            ("ACC-GLE-2041-00008", "KE-RCGLE-2041-00008"),
            ("newhash", "RC-newhash"),
            ("short-name", "RC-short-name"),
            ("three-part-name", "RC-three-part-name"),
            ("CUSTOM-PREFIX-2042-00009-suffix", "KE-RCGLE-2042-00009"),
        ):
            with self.subTest(source=source):
                frappe.rename_doc("GL Entry", old, source, force=True)
                row = self._reporting_for(source)
                assert row.name == expected
                assert row.get("voucher_no") == self.token + "_voucher"
                frappe.db.set_single_value(
                    "Reporting Currency Settings", "last_sync_timestamp", None
                )
                sync_reporting_currency_entries(self.token, "Administrator")
                rebuilt = self._reporting_for(source)
                assert rebuilt.name == expected
                assert rebuilt.get("voucher_no") == self.token + "_voucher"
                assert rebuilt.get("reporting_debit") == 125
                old = source

    def test_scheduler_callback_updates_the_old_source_link(self) -> None:
        self._source(self.token)
        sync_reporting_currency_entries(self.token, "Administrator")
        target = "ACC-GLE-2040-00010"
        # Reproduce the persisted state handed to callbacks by ERPNext's scheduler:
        # its SQL has renamed the GL row, but has not updated its Link references.
        frappe.db.set_value("GL Entry", self.token, "name", target)
        for hook in frappe.get_hooks("on_gle_rename"):
            frappe.call(hook, newname=target, oldname=self.token)
        row = self._reporting_for(target)
        assert row.name == "KE-RCGLE-2040-00010"
        assert row.get("voucher_no") == self.token + "_voucher"
        assert row.get("reporting_debit") == 125

    def test_native_rename_preserves_independent_doe_pair_names_and_voucher(
        self,
    ) -> None:
        self._source(self.token)
        primary = "DOE-30062040-primary"
        offset = "DOE-30062040-offset"
        voucher = "DOE-pair-reference"
        self._insert(
            "Reporting Currency GLE",
            primary,
            gl_entry=self.token,
            reporting_doe=1,
            manual_entry=0,
            voucher_no=voucher,
            reporting_debit=125,
        )
        self._insert(
            "Reporting Currency GLE",
            offset,
            reporting_doe=1,
            manual_entry=0,
            voucher_no=voucher,
            reporting_credit=125,
        )
        target = "ACC-GLE-2040-00011"
        frappe.rename_doc("GL Entry", self.token, target, force=True)
        row = self._reporting_for(target)
        companion = frappe.get_doc("Reporting Currency GLE", offset)
        assert row.name == primary and companion.name == offset
        assert row.get("voucher_no") == companion.get("voucher_no") == voucher
        assert row.get("reporting_debit") == companion.get("reporting_credit") == 125

    def test_native_rename_preserves_a_manual_reporting_record_id(self) -> None:
        self._source(self.token)
        self._insert(
            "Reporting Currency GLE",
            "MANUAL-independent",
            gl_entry=self.token,
            manual_entry=1,
            voucher_no="manual-voucher",
            reporting_debit=125,
        )
        target = "ACC-GLE-2040-00012"
        frappe.rename_doc("GL Entry", self.token, target, force=True)
        row = self._reporting_for(target)
        assert row.name == "MANUAL-independent"
        assert row.get("voucher_no") == "manual-voucher"
        assert row.get("reporting_debit") == 125
