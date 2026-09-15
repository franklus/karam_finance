"""Native Bank Reconciliation results must not total their own summaries."""

import csv
from decimal import Decimal
from io import StringIO
from typing import Any, override

import frappe
from frappe.desk.query_report import export_query, run
from frappe.tests import IntegrationTestCase
from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

REPORT = "Bank Reconciliation Statement (Karam)"


class TestBRSTotals(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "brs_total_" + frappe.generate_hash(length=8)
        previous_user = frappe.session.user
        frappe.set_user("Administrator")
        self.addCleanup(frappe.set_user, previous_user)
        self.addCleanup(frappe.clear_cache, doctype="Report")
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        self._insert("Company", self.token, default_currency="USD")
        self._insert(
            "Account",
            self.token,
            company=self.token,
            account_name="Bank",
            account_currency="USD",
            account_type="Bank",
            root_type="Asset",
            report_type="Balance Sheet",
            is_group=0,
            lft=1,
            rgt=2,
        )
        self._insert(
            "GL Entry",
            self.token,
            company=self.token,
            account=self.token,
            account_currency="USD",
            posting_date="2026-01-01",
            docstatus=1,
            debit=100,
            debit_in_account_currency=100,
            credit=0,
            credit_in_account_currency=0,
            is_cancelled=0,
        )
        # Reproduce a site whose stored Report definition has not been reloaded.
        frappe.db.set_value(
            "Report", REPORT, {"add_total_row": 1, "prepared_report": 0}
        )
        frappe.clear_cache(doctype="Report")
        self.filters = {
            "company": self.token,
            "account": self.token,
            "report_date": "2026-01-31",
        }

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()

    def test_report_keeps_100_balance_without_an_automatic_200_total(self) -> None:
        result = run(REPORT, filters=self.filters, ignore_prepared_report=True)
        rows = result["result"]
        assert len(rows) == 6, rows
        assert (
            rows[0]["payment_entry"] == "Bank Statement balance as per General Ledger"
        )
        assert (rows[0]["debit"], rows[0]["credit"]) == (100, 0)
        assert rows[-1]["payment_entry"] == "Calculated Bank Statement balance"
        assert (rows[-1]["debit"], rows[-1]["credit"]) == (100, 0)
        assert result["skip_total_row"] and not result["add_total_row"]

    def test_outstanding_and_incorrect_clearance_summaries_keep_signed_balances(
        self,
    ) -> None:
        for suffix, received, paid, posting, clearance in (
            ("receipt", 100, 0, "2026-01-01", None),
            ("payment", 0, 40, "2026-01-01", None),
            ("incorrect", 0, 25, "2026-02-01", "2026-01-15"),
        ):
            self._insert(
                "Payment Entry",
                self.token + suffix,
                company=self.token,
                docstatus=1,
                paid_to=self.token if received else None,
                paid_from=self.token if paid else None,
                received_amount_after_tax=received,
                paid_amount_after_tax=paid,
                paid_to_account_currency="USD",
                paid_from_account_currency="USD",
                posting_date=posting,
                clearance_date=clearance,
            )
        for debit, credit, expected_debit, expected_credit in (
            (1000, 0, 915, 0),
            (0, 100, 0, 185),
        ):
            with self.subTest(system_debit=debit, system_credit=credit):
                frappe.db.set_value(
                    "GL Entry",
                    self.token,
                    {
                        "debit": debit,
                        "credit": credit,
                        "debit_in_account_currency": debit,
                        "credit_in_account_currency": credit,
                    },
                )
                result = run(REPORT, filters=self.filters, ignore_prepared_report=True)
                rows = result["result"]
                assert len(rows) == 8
                assert {
                    row["payment_entry"] for row in rows if row.get("payment_document")
                } == {self.token + "receipt", self.token + "payment"}
                summary = rows[-6:]
                assert (summary[0]["debit"], summary[0]["credit"]) == (debit, credit)
                assert (summary[2]["debit"], summary[2]["credit"]) == (100, 40)
                assert (summary[3]["debit"], summary[3]["credit"]) == (0, 25)
                assert (summary[-1]["debit"], summary[-1]["credit"]) == (
                    expected_debit,
                    expected_credit,
                )
                assert result["skip_total_row"] and not result["add_total_row"]

    def test_empty_report_also_disables_automatic_totals(self) -> None:
        result = run(REPORT, filters={}, ignore_prepared_report=True)
        assert not result["result"]
        assert result["skip_total_row"] and not result["add_total_row"]

    def test_foreign_invoice_payments_are_reported_in_bank_currency(self) -> None:
        self._insert(
            "Purchase Invoice",
            self.token + "PI",
            company=self.token,
            docstatus=1,
            is_paid=1,
            cash_bank_account=self.token,
            currency="EUR",
            conversion_rate=1.2,
            paid_amount=100,
            base_paid_amount=120,
            posting_date="2026-01-15",
        )
        self._insert(
            "Sales Invoice",
            self.token + "SI",
            company=self.token,
            docstatus=1,
            is_pos=1,
            currency="EUR",
            conversion_rate=1.2,
            posting_date="2026-01-15",
        )
        self._insert(
            "Sales Invoice Payment",
            self.token + "payment",
            parent=self.token + "SI",
            parenttype="Sales Invoice",
            parentfield="payments",
            account=self.token,
            amount=200,
            base_amount=240,
        )
        result = run(
            REPORT,
            filters={**self.filters, "include_pos_transactions": 1},
            ignore_prepared_report=True,
        )
        rows = {
            row["payment_entry"]: row
            for row in result["result"]
            if row.get("payment_document")
        }
        assert rows[self.token + "PI"]["credit"] == 120
        assert rows[self.token + "SI"]["debit"] == 240
        summary = result["result"][-1]
        assert (summary["debit"], summary["credit"]) == (0, 20)

    def test_existing_report_reload_keeps_the_runtime_total_disabled(self) -> None:
        frappe.reload_doc(
            "karam_general",
            "report",
            "bank_reconciliation_statement_(karam)",
            force=True,
        )
        # Frappe deliberately preserves add_total_row during an existing import.
        assert frappe.get_doc("Report", REPORT).get("add_total_row") == 1
        result = run(REPORT, filters=self.filters, ignore_prepared_report=True)
        assert len(result["result"]) == 6 and not result["add_total_row"]

    def test_fresh_standard_report_import_disables_the_default_total(self) -> None:
        # Fixture-only removal avoids Report deletion hooks and is rolled back.
        for field in frappe.get_meta("Report").get_table_fields():
            frappe.db.delete(field.options, {"parent": REPORT, "parenttype": "Report"})
        frappe.db.delete("Report", {"name": REPORT})
        frappe.clear_cache(doctype="Report")
        frappe.reload_doc(
            "karam_general",
            "report",
            "bank_reconciliation_statement_(karam)",
            force=True,
        )
        assert frappe.get_doc("Report", REPORT).get("add_total_row") == 0
        result = run(REPORT, filters=self.filters, ignore_prepared_report=True)
        assert len(result["result"]) == 6 and not result["add_total_row"]

    def _export(self, file_format: str, *, visible_idx: list[int] | None) -> bytes:
        old_form, old_response = frappe.local.form_dict, frappe.local.response
        try:
            frappe.local.form_dict = frappe._dict(
                report_name=REPORT,
                filters=frappe.as_json(self.filters),
                file_format_type=file_format,
                visible_idx=frappe.as_json(visible_idx or []),
                export_in_background=0,
            )
            frappe.local.response = frappe._dict()
            export_query()
            return frappe.local.response.filecontent
        finally:
            frappe.local.form_dict, frappe.local.response = old_form, old_response

    @staticmethod
    def _read_export(content: bytes, file_format: str) -> list[Any]:
        if file_format == "CSV":
            return list(csv.reader(StringIO(content.decode("utf-8-sig"))))
        rows = read_xlsx_file_from_attached_file(fcontent=content, read_only=True)
        assert rows is not None
        return rows

    def test_csv_and_excel_exports_do_not_reintroduce_a_total(self) -> None:
        for file_format, selected, expected_count in (
            ("CSV", None, 7),
            ("CSV", [0, 5], 3),
            ("Excel", None, 7),
            ("Excel", [0, 5], 3),
        ):
            with self.subTest(file_format=file_format, selected=selected):
                rows = self._read_export(
                    self._export(file_format, visible_idx=selected), file_format
                )
                assert len(rows) == expected_count
                label_index, debit_index = (
                    rows[0].index("Payment Document"),
                    rows[0].index("Debit"),
                )
                assert (
                    rows[1][label_index]
                    == "Bank Statement balance as per General Ledger"
                )
                assert rows[-1][label_index] == "Calculated Bank Statement balance"
                assert Decimal(str(rows[1][debit_index])) == Decimal(100)
                assert Decimal(str(rows[-1][debit_index])) == Decimal(100)
