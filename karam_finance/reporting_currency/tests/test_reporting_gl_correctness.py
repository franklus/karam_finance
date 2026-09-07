"""Regression checks for RC-specific semantics, without inserting accounting rows."""

import importlib
from decimal import Decimal
from typing import Any
from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe.query_builder import Criterion
from frappe.utils import getdate

ROOT = "karam_finance.reporting_currency.report.general_ledger_(reporting_currency)."
report = importlib.import_module(ROOT + "general_ledger_(reporting_currency)")
query = importlib.import_module(ROOT + "gl_query")
columns = importlib.import_module(ROOT + "gl_columns")
filters_module = importlib.import_module(ROOT + "gl_filters")


def entry(**values: Any) -> Any:
    return frappe._dict(
        {
            "gl_entry": "RC-1",
            "posting_date": getdate("2026-02-01"),
            "creation": "2026-02-01",
            "account": "A",
            "account_currency": "USD",
            "party_type": "Customer",
            "party": "Same",
            "voucher_type": "Journal Entry",
            "voucher_no": "JE-1",
            "is_opening": "No",
            "debit": 10,
            "credit": 0,
            "debit_in_company_currency": 100,
            "credit_in_company_currency": 0,
            "debit_in_account_currency": 10,
            "credit_in_account_currency": 0,
            "transaction_currency": "USD",
            "debit_in_transaction_currency": 10,
            "credit_in_transaction_currency": 0,
        }
        | values
    )


def render(
    rows: list[Any], mode: str = "Flat Chronological", **options: Any
) -> list[Any]:
    filters = frappe._dict(
        company="Company",
        from_date="2026-01-01",
        to_date="2026-12-31",
        categorize_by=mode,
        presentation_currency="USD",
        **options,
    )
    with (
        patch.object(report._gl_aggregation, "_set_bill_no"),
        patch.object(frappe.db, "get_single_value", return_value=0),
    ):
        return report.get_result_as_list(
            report.get_data_with_opening_closing(filters, [], rows), filters
        )


class _OpeningQuery:
    def __init__(self, result: list[Any]) -> None:
        self.result = result

    def left_join(self, *_args: Any) -> _OpeningQuery:
        return self

    def on(self, *_args: Any) -> _OpeningQuery:
        return self

    def select(self, *_args: Any) -> _OpeningQuery:
        return self

    def where(self, *_args: Any) -> _OpeningQuery:
        return self

    def groupby(self, *_args: Any) -> _OpeningQuery:
        return self

    def run(self, **_kwargs: Any) -> list[Any]:
        return self.result


class TestReportingGLCorrectness(TestCase):
    def test_manual_entries_keep_their_identity(self) -> None:
        rows = render(
            [
                entry(manual_entry=1, voucher_no=None),
                entry(manual_entry=1, voucher_no=None, gl_entry="RC-2"),
            ],
            "Categorise by Voucher (Consolidated)",
        )
        self.assertEqual(len([r for r in rows if r.get("posting_date")]), 2)

    def test_party_type_is_part_of_the_group(self) -> None:
        rows = render([entry(), entry(party_type="Supplier")], "Categorise by Party")
        self.assertEqual(
            len([r for r in rows if r.get("row_type") == "group_total"]), 2
        )

    def test_transaction_currencies_never_merge(self) -> None:
        rows = render(
            [entry(), entry(transaction_currency="EUR")],
            "Categorise by Voucher (Consolidated)",
            add_values_in_transaction_currency=1,
        )
        details = [r for r in rows if r.get("posting_date")]
        self.assertEqual({r["transaction_currency"] for r in details}, {"USD", "EUR"})
        self.assertEqual(
            [r["debit_in_transaction_currency"] for r in details], [10, 10]
        )

    def test_future_openings_are_excluded(self) -> None:
        rows = render([entry(posting_date=getdate("2027-01-01"), is_opening="Yes")])
        self.assertEqual(rows[0]["debit"], 0)
        self.assertFalse(any(r.get("posting_date") for r in rows))

    def test_reporting_only_cells_are_not_currency_decorated_zeroes(self) -> None:
        rows = render(
            [
                entry(
                    reporting_doe=1,
                    debit_in_account_currency=0,
                    debit_in_company_currency=0,
                )
            ]
        )
        for row in rows:
            if row.get("row_type") != "separator":
                self.assertEqual(row["debit_in_account_currency"], "")
                self.assertEqual(row["debit_in_company_currency"], "")
        self.assertEqual(rows[-1]["balance"], 10)

    def test_decimal_sum_survives_the_display_boundary(self) -> None:
        rows = render([entry(debit="51309440814079.5400"), entry(debit="0.0044")])
        total = next(r for r in rows if r.get("row_type") == "report_total")
        self.assertEqual(total["_display_amounts"]["debit"], "51309440814079.54")

    def test_closing_is_independent_of_layout_order(self) -> None:
        balances = []
        for mode in (
            "Flat Chronological",
            "Categorise by Account",
            "Categorise by Voucher (Consolidated)",
        ):
            rows = render(
                [
                    entry(posting_date=getdate("2025-12-31"), debit=100),
                    entry(credit=20),
                ],
                mode,
            )
            balances.append(
                (rows[-1]["debit"], rows[-1]["credit"], rows[-1]["balance"])
            )
        self.assertEqual(balances, [(90, 0, 90)] * 3)

    def test_single_missing_date_is_a_validation_error(self) -> None:
        for field in ("from_date", "to_date"):
            values: Any = frappe._dict(
                company="Company", from_date="2026-01-01", to_date="2026-12-31"
            )
            values[field] = None
            with self.assertRaises(frappe.ValidationError):
                filters_module._validate_required_dates(values)

    def test_invalid_grouping_and_conflicting_entry_types_are_rejected(self) -> None:
        with self.assertRaises(frappe.ValidationError):
            filters_module._validate_options(frappe._dict(categorize_by="invalid"))
        with self.assertRaises(frappe.ValidationError):
            filters_module._validate_entry_type(
                frappe._dict(reporting_doe=1, manual_entry=1)
            )

    def test_every_layout_uses_the_same_date_predicate(self) -> None:
        table = frappe.qb.DocType("Reporting Currency GLE")
        conditions = [
            str(
                Criterion.all(
                    query._build_qb_date_conditions(
                        {
                            "from_date": "2026-01-01",
                            "to_date": "2026-12-31",
                            "categorize_by": mode,
                        },
                        table,
                    )
                )
            )
            for mode in (
                "Flat Chronological",
                "Categorise by Account",
                "Categorise by Voucher",
            )
        ]
        self.assertEqual(len(set(conditions)), 1)
        self.assertNotIn("is_opening", conditions[0])

    def test_voucher_exclusion_preserves_manual_and_other_voucher_types(self) -> None:
        table = frappe.qb.DocType("Reporting Currency GLE")
        with patch.object(query, "_get_voucher_no_not_in_query", return_value=None):
            condition = str(
                query._build_qb_voucher_conditions(
                    {"voucher_no_not_in": ["JE-1"]}, table
                )[0]
            )
        self.assertIn("IS NULL", condition)
        self.assertIn("voucher_type", condition)
        self.assertIn("Journal Entry", condition)

    def test_unsupported_dimension_is_rejected(self) -> None:
        dimension = frappe._dict(
            fieldname="auxiliary",
            label="Auxiliary",
            document_type="Auxiliary",
            disabled=0,
        )
        with (
            patch.object(query, "get_accounting_dimensions", return_value=[dimension]),
            patch.object(frappe, "get_meta") as meta,
        ):
            meta.return_value.has_field.return_value = False
            with self.assertRaises(frappe.ValidationError):
                query._build_qb_dimension_conditions(
                    {"auxiliary": ["X"]}, frappe.qb.DocType("Reporting Currency GLE")
                )

    def test_reporting_columns_always_present_even_in_same_currency(self) -> None:
        with (
            patch.object(columns, "get_company_currency", return_value="USD"),
            patch.object(frappe.db, "get_single_value", return_value="Supplier Name"),
        ):
            result = columns.get_columns(
                {"company": "Company", "presentation_currency": "USD"}
            )
        currency_fields = {
            c["fieldname"] for c in result if c.get("fieldtype") == "Currency"
        }
        self.assertEqual(currency_fields, {"debit", "credit", "balance"})

    def test_period_only_has_no_opening_query(self) -> None:
        conditions = query._build_qb_flat_opening_conditions(
            {"disable_opening_balance_calculation": 1},
            frappe.qb.DocType("Reporting Currency GLE"),
        )
        self.assertIn('"name"=', str(conditions[0]))

    def test_flat_openings_accumulate_synced_and_reporting_only_groups(self) -> None:
        filters = frappe._dict(
            categorize_by="Flat Chronological",
            company="Company",
            from_date="2026-01-01",
            to_date="2026-12-31",
        )
        groups = [
            frappe._dict(
                account="A",
                account_currency="USD",
                manual_entry=0,
                reporting_doe=0,
                opening_balance="100.0000",
            ),
            frappe._dict(
                account="A",
                account_currency="USD",
                manual_entry=1,
                reporting_doe=0,
                opening_balance="0.0000",
            ),
            frappe._dict(
                account="A",
                account_currency="USD",
                manual_entry=0,
                reporting_doe=1,
                opening_balance="0.0000",
            ),
        ]

        for ordered_groups in (groups, list(reversed(groups))):
            with (
                patch.object(
                    query.frappe.qb,
                    "from_",
                    return_value=_OpeningQuery(ordered_groups),
                ),
                patch.object(query, "_build_qb_conditions", return_value=[]),
                patch.object(query, "build_match_conditions", return_value=None),
            ):
                openings = query.get_flat_account_currency_openings(filters)

            self.assertEqual(openings, {("A", "USD"): Decimal("100.0000")})

    def test_decimal_adapter_preserves_exact_strings(self) -> None:
        money = importlib.import_module(ROOT + "gl_money")
        self.assertEqual(
            money.decimal_amount("51309440814079.5444"), Decimal("51309440814079.5444")
        )


class TestReportingMaintenancePermissions(TestCase):
    def test_maintenance_entrypoints_check_roles_before_work(self) -> None:
        prefix = "karam_finance.reporting_currency.doctype."
        cases: list[tuple[str, str, dict[str, Any]]] = [
            ("reporting_currency_gle.sync.orchestrator", "delete_all_entries", {}),
            (
                "reporting_currency_gle.sync.orchestrator",
                "enqueue_reporting_currency_sync",
                {},
            ),
            ("reporting_currency_gle.sync.doe", "compute_doe", {"background": False}),
            (
                "reporting_currency_gle.sync.utils",
                "export_missing_currency_gl_entries_csv",
                {"account_currency": "LBP", "reporting_currency": "USD"},
            ),
            (
                "reporting_currency_gle.sync.utils",
                "export_temporal_validation_entries_csv",
                {
                    "cache_key": "test",
                    "default_currency": "LBP",
                    "reporting_currency": "USD",
                },
            ),
            (
                "reporting_currency_settings.reporting_currency_settings",
                "get_accounts_under_parent",
                {"parent_account": "A"},
            ),
        ]
        for module_name, function_name, kwargs in cases:
            function = getattr(
                importlib.import_module(prefix + module_name), function_name
            )
            with (
                self.subTest(function=function_name),
                patch.object(
                    frappe, "only_for", side_effect=frappe.PermissionError
                ) as permission,
            ):
                with self.assertRaises(frappe.PermissionError):
                    function(**kwargs)
                permission.assert_called_once_with("System Manager")

    def test_currency_is_defaulted_for_manual_and_linked_entries(self) -> None:
        module = importlib.import_module(
            "karam_finance.reporting_currency.doctype.reporting_currency_gle.reporting_currency_gle"
        )
        for manual_entry in (0, 1):
            record: Any = frappe._dict(
                manual_entry=manual_entry,
                reporting_doe=0,
                reporting_currency=None,
                is_new=lambda: False,
            )
            with (
                self.subTest(manual_entry=manual_entry),
                patch.object(frappe.db, "get_single_value", return_value="USD"),
            ):
                module.ReportingCurrencyGLE.validate(record)
                self.assertEqual(record.reporting_currency, "USD")
                record.reporting_currency = "AED"
                with self.assertRaises(frappe.ValidationError):
                    module.ReportingCurrencyGLE.validate(record)
                self.assertEqual(record.reporting_currency, "AED")

    def test_new_manual_entry_overrides_company_currency_default(self) -> None:
        module = importlib.import_module(
            "karam_finance.reporting_currency.doctype.reporting_currency_gle.reporting_currency_gle"
        )
        record: Any = frappe._dict(
            is_new=lambda: True,
            gl_entry=None,
            reporting_doe=0,
            reporting_currency="LBP",
        )
        with patch.object(frappe.db, "get_single_value", return_value="USD"):
            module.ReportingCurrencyGLE.validate(record)
        self.assertEqual(record.reporting_currency, "USD")
