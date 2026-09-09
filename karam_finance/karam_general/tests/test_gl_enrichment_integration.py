"""MariaDB voucher enrichment contracts; fixtures bypass document lifecycle."""

import importlib
from typing import override

import frappe
from frappe.tests import IntegrationTestCase


class TestGLEnrichmentIntegration(IntegrationTestCase):
    @override
    def setUp(self) -> None:
        super().setUp()
        self.token = "enrichment_" + frappe.generate_hash(length=10)
        frappe.db.savepoint(self.token)
        self.addCleanup(frappe.db.rollback, save_point=self.token)
        self.modules = [
            importlib.import_module("karam_finance." + path + ".gl_enrichment")
            for path in (
                "karam_general.report.general_ledger_(karam)",
                "reporting_currency.report.general_ledger_(reporting_currency)",
            )
        ]
        for module in self.modules:
            module._KARAM_FIELDS_CACHE.clear()
            self.addCleanup(module._KARAM_FIELDS_CACHE.clear)
        for doctype, series in (
            ("Sales Invoice", "Invoice"),
            ("Journal Entry", "Journal"),
        ):
            frappe.get_doc(
                {
                    "doctype": doctype,
                    "name": self.token,
                    "karam_series": series,
                    "translation": "Literal 50%_off\\sale",
                }
            ).db_insert()

    def test_named_union_keeps_same_number_in_different_doctypes(self) -> None:
        names = {dt: {self.token} for dt in ("Sales Invoice", "Journal Entry")}
        for module in self.modules:
            with self.subTest(module=module.__name__):
                result = module._fetch_voucher_data(names)
                self.assertEqual(set(result), {(dt, self.token) for dt in names})
                self.assertEqual(
                    result[("Sales Invoice", self.token)]["karam_series"], "Invoice"
                )
                self.assertEqual(
                    result[("Journal Entry", self.token)]["karam_series"], "Journal"
                )

    def test_union_filters_match_literal_wildcards_and_series(self) -> None:
        names = {dt: {self.token} for dt in ("Sales Invoice", "Journal Entry")}
        for module in self.modules:
            with self.subTest(module=module.__name__):
                result = module._fetch_voucher_data(
                    names, series="Invoice", translation="50%_off\\sale"
                )
                self.assertEqual(set(result), {("Sales Invoice", self.token)})
                self.assertEqual(
                    module._fetch_voucher_data(names, translation="50X_off"), {}
                )
