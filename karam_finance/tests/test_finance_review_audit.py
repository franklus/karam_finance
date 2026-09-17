"""Audit permissions, bounded evidence and absence of business-data writes."""

from typing import Any
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from karam_finance.reporting_currency.audit import audit_finance_data


class TestFinanceReviewAudit(IntegrationTestCase):
    def test_guest_cannot_read_audit(self) -> None:
        frappe.set_user("Guest")
        try:
            with self.assertRaises(frappe.PermissionError):
                audit_finance_data()
        finally:
            frappe.set_user("Administrator")

    def test_counts_samples_and_read_only_queries(self) -> None:
        frappe.set_user("Administrator")
        token = "audit_review_" + frappe.generate_hash(length=8)
        for index in range(3):
            self._insert(
                "GL Entry",
                f"{token}-gl-{index}",
                docstatus=1,
                company=token,
                is_cancelled=0,
            )
        self._insert(
            "Reporting Currency GLE",
            token + "orphan",
            gl_entry=token,
            manual_entry=0,
            reporting_doe=0,
            reporting_currency="USD",
        )
        self._insert(
            "Reporting Currency GLE",
            token + "manual",
            gl_entry=token + "manual-source",
            manual_entry=1,
            reporting_doe=0,
            reporting_currency="EUR",
        )
        before = frappe.db.count("Reporting Currency GLE")
        sql = frappe.local.db.sql

        def read_only(query: Any, *args: Any, **kwargs: Any) -> Any:
            assert (
                str(query)
                .lstrip()
                .lower()
                .startswith(("select", "with", "desc", "show"))
            ), query
            return sql(query, *args, **kwargs)

        with patch.object(frappe.local.db, "sql", side_effect=read_only):
            result = audit_finance_data(sample_limit=1)
        assert result["findings"]["missing_generated"]["count"] >= 3
        assert result["findings"]["orphaned_generated"]["count"] >= 1
        assert result["findings"]["mixed_currencies"]["count"] >= 1
        assert all(len(item["samples"]) <= 1 for item in result["findings"].values())
        assert frappe.db.count("Reporting Currency GLE") == before
        assert frappe.db.exists("Reporting Currency GLE", token + "manual")

    @staticmethod
    def _insert(doctype: str, name: str, **values: Any) -> None:
        frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()

    def test_current_cancelled_copies_are_not_stale_but_status_mismatches_are(
        self,
    ) -> None:
        token = "audit_review_" + frappe.generate_hash(length=8)
        self._insert("GL Entry", token, docstatus=1, is_cancelled=1)
        modified = frappe.db.get_value("GL Entry", token, "modified")
        self._insert(
            "Reporting Currency GLE",
            token,
            gl_entry=token,
            manual_entry=0,
            reporting_doe=0,
            is_cancelled=1,
            docstatus=1,
            gl_entry_modified=modified,
        )
        result = audit_finance_data(sample_limit=100)["findings"]["stale_generated"]
        assert token not in {row["name"] for row in result["samples"]}
        frappe.db.set_value("Reporting Currency GLE", token, "is_cancelled", 0)
        result = audit_finance_data(sample_limit=100)["findings"]["stale_generated"]
        assert token in {row["name"] for row in result["samples"]}

    def test_conflicting_selected_rates_and_invalid_offsets_are_counted(self) -> None:
        token = "audit_review_" + frappe.generate_hash(length=8)
        self._insert("Company", token, default_currency="AUD")
        frappe.db.set_single_value(
            "Reporting Currency Settings", "reporting_currency", "USD"
        )
        field = frappe.get_meta("Reporting Currency Settings").get_field(
            "rc_parameters"
        )
        assert field is not None
        childtype = str(field.options)
        self._insert(
            childtype,
            token,
            parent="Reporting Currency Settings",
            parentfield="rc_parameters",
            parenttype="Reporting Currency Settings",
            idx=1,
            exchange_rate=2,
            doe_posting_date="2095-12-31",
            profit_account=token,
            loss_account=token,
        )
        for index, (source, target, day, rate) in enumerate(
            (
                ("AUD", "USD", "2095-01-01", 2),
                ("AUD", "USD", "2095-01-01", 3),
                ("USD", "AUD", "2095-01-01", 4),
                ("USD", "AUD", "2095-01-01", 5),
                ("USD", "AUD", "2095-02-01", 6),
                ("USD", "AUD", "2095-02-01", 7),
            )
        ):
            self._insert(
                "Currency Exchange",
                token + str(index),
                from_currency=source,
                to_currency=target,
                date=day,
                exchange_rate=rate,
            )
        result = audit_finance_data(sample_limit=1)["findings"]
        assert result["invalid_doe_offsets"]["count"] >= 1
        assert result["conflicting_exchange_rates"]["count"] == 2
        assert len(result["conflicting_exchange_rates"]["samples"]) == 1

    def test_legacy_ambiguity_and_provable_mismatches_have_distinct_categories(
        self,
    ) -> None:
        token = "audit_review_" + frappe.generate_hash(length=8)
        cases: tuple[tuple[str, str, list[str]], ...] = (
            ("A", "Y", ["X"]),
            ("B", "", ["X"]),
            ("C", "", ["X", ""]),
            ("D", "", [""]),
            ("E", "", []),
        )
        for suffix, letter, source_letters in cases:
            parent = token + suffix
            self._insert(
                "Journal Entry",
                parent,
                company=token,
                docstatus=1,
                posting_date="2095-01-01",
            )
            self._insert(
                "GL Entry",
                parent,
                company=token,
                account=token,
                letter=letter,
                voucher_type="Journal Entry",
                voucher_no=parent,
                voucher_detail_no=None,
                posting_date="2095-01-01",
            )
            for index, source in enumerate(source_letters):
                self._insert(
                    "Journal Entry Account",
                    parent + str(index),
                    parent=parent,
                    parenttype="Journal Entry",
                    parentfield="accounts",
                    docstatus=1,
                    account=token,
                    letter=source,
                )
        result = audit_finance_data(sample_limit=1)["findings"]
        assert result["ambiguous_legacy_letters"]["count"] >= 2
        assert len(result["ambiguous_legacy_letters"]["samples"]) == 1
        assert result["ambiguous_legacy_letters"]["confidence"] == "review_candidate"
        assert result["inconsistent_legacy_letters"]["count"] >= 1
        assert result["inconsistent_legacy_letters"]["confidence"] == "definite"
        assert result["missing_legacy_letters"]["count"] >= 1
