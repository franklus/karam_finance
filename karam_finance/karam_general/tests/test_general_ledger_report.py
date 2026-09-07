"""Tests for the Karam General GL report helpers."""

from __future__ import annotations

import importlib
from datetime import date
from typing import TYPE_CHECKING
from unittest.mock import patch

import frappe
from frappe import _dict
from frappe.query_builder import Criterion
from frappe.tests.utils import FrappeTestCase

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

MODULE_NAME = (
    "karam_finance.karam_general.report.general_ledger_(karam).general_ledger_(karam)"
)


def _load_module() -> ModuleType:
    return importlib.import_module(MODULE_NAME)


class TestGeneralLedgerReport(FrappeTestCase):
    """Unit tests for General Ledger report helpers."""

    def test_repeated_display_values_are_translated_once_per_report(self) -> None:
        """Avoid repeating Frappe translation cache reads for identical values."""
        module = _load_module()
        translation_cache = {}
        entries = [
            _dict(
                voucher_subtype="Journal Entry",
                against_voucher_type="Purchase Invoice",
                remarks="Repeated remark",
                party_type="Supplier",
            )
            for _ in range(2)
        ]

        with patch.object(
            module._gl_aggregation,
            "_",
            side_effect=lambda value: f"translated:{value}",
        ) as translate:
            for entry in entries:
                module._gl_aggregation._prepare_gle_for_output(entry, translation_cache)

        assert translate.call_count == 4
        assert entries[0].voucher_subtype == "translated:Journal Entry"
        assert entries[1].remarks == "translated:Repeated remark"

    def test_attach_series_translation_populates_fields(self) -> None:
        """Ensure translation and series are hydrated for curated doctypes."""
        module = _load_module()
        hydrate: Callable[[list[_dict]], None] = module._attach_series_translation
        entries: list[_dict] = [
            _dict(
                voucher_type="Sales Invoice",
                voucher_no="SINV-0001",
                karam_series=None,
                translation=None,
            )
        ]

        with patch.object(
            module._gl_enrichment,
            "_fetch_voucher_data",
            return_value={
                ("Sales Invoice", "SINV-0001"): {
                    "name": "SINV-0001",
                    "karam_series": "SER-1",
                    "translation": "Arabic",
                }
            },
        ) as mock_fetch:
            hydrate(entries)

        assert entries[0].karam_series == "SER-1"
        assert entries[0].translation == "Arabic"
        mock_fetch.assert_called_once_with({"Sales Invoice": {"SINV-0001"}})

    def test_attach_series_translation_skips_non_curated_doctype(self) -> None:
        """Ensure non-curated doctypes are ignored."""
        module = _load_module()
        hydrate: Callable[[list[_dict]], None] = module._attach_series_translation
        entries: list[_dict] = [
            _dict(
                voucher_type="Quotation",
                voucher_no="QTN-0001",
                karam_series=None,
                translation=None,
            )
        ]

        with (
            patch.object(module.frappe, "get_meta") as mock_get_meta,
            patch.object(module.frappe.db, "get_all") as mock_get_all,
        ):
            hydrate(entries)

        mock_get_meta.assert_not_called()
        mock_get_all.assert_not_called()

    def test_attach_series_translation_includes_journal_entry_children(self) -> None:
        """Hydrate every GL row sharing a supported parent voucher."""
        module = _load_module()
        entries: list[_dict] = [
            _dict(
                voucher_type="Journal Entry",
                voucher_no="JV-0001",
                karam_series=None,
                translation=None,
            ),
            _dict(
                voucher_type="Journal Entry",
                voucher_no="JV-0001",
                karam_series=None,
                translation=None,
            ),
        ]

        with patch.object(
            module._gl_enrichment,
            "_fetch_voucher_data",
            return_value={
                ("Journal Entry", "JV-0001"): {
                    "name": "JV-0001",
                    "karam_series": "JV",
                    "translation": "Journal",
                }
            },
        ) as mock_fetch:
            module._attach_series_translation(entries)

        assert [entry.karam_series for entry in entries] == ["JV", "JV"]
        assert [entry.translation for entry in entries] == ["Journal", "Journal"]
        mock_fetch.assert_called_once_with({"Journal Entry": {"JV-0001"}})

    def test_consolidated_grouping_deduplicates_against_vouchers(self) -> None:
        """Ensure consolidated rows keep unique against-voucher values in order."""
        module = _load_module()
        filters = _dict(
            categorize_by="Categorise by Voucher (Consolidated)",
            show_net_values_in_party_account=0,
            include_dimensions=0,
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            show_opening_entries=0,
        )

        gl_entries = [
            _dict(
                posting_date=date(2024, 6, 1),
                voucher_type="Journal Entry",
                voucher_no="JV-0001",
                account="Debtors - K",
                party_type="Customer",
                party="CUST-0001",
                against_voucher="PINV-0001",
                is_opening="No",
                creation="2024-06-01 10:00:00",
                debit=60.0,
                credit=0.0,
                debit_in_account_currency=60.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=60.0,
                credit_in_company_currency=0.0,
                cost_center=None,
                project=None,
            ),
            _dict(
                posting_date=date(2024, 6, 1),
                voucher_type="Journal Entry",
                voucher_no="JV-0001",
                account="Debtors - K",
                party_type="Customer",
                party="CUST-0001",
                against_voucher="PINV-0001",
                is_opening="No",
                creation="2024-06-01 10:05:00",
                debit=40.0,
                credit=0.0,
                debit_in_account_currency=40.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=40.0,
                credit_in_company_currency=0.0,
                cost_center=None,
                project=None,
            ),
            _dict(
                posting_date=date(2024, 6, 1),
                voucher_type="Journal Entry",
                voucher_no="JV-0001",
                account="Debtors - K",
                party_type="Customer",
                party="CUST-0001",
                against_voucher="PINV-0002",
                is_opening="No",
                creation="2024-06-01 10:10:00",
                debit=10.0,
                credit=0.0,
                debit_in_account_currency=10.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=10.0,
                credit_in_company_currency=0.0,
                cost_center=None,
                project=None,
            ),
        ]

        totals = module._get_totals_dict()
        with patch.object(module.frappe.db, "get_single_value", return_value=0):
            updated_totals, entries = module._get_account_wise_gle(
                filters,
                [],
                gl_entries,
                gle_map={},
                totals=totals,
            )

        assert len(entries) == 1
        assert entries[0].against_voucher == "PINV-0001, PINV-0002"
        assert updated_totals.total.debit == 110.0
        assert updated_totals.closing.debit == 110.0

    def test_group_separator_rows_render_blank_values(self) -> None:
        """Ensure logical separator rows render as blanks, not currency zeroes."""
        module = _load_module()
        row = _dict(
            is_separator=1,
            debit=0.0,
            credit=0.0,
            debit_in_account_currency=0.0,
            credit_in_account_currency=0.0,
            debit_in_company_currency=0.0,
            credit_in_company_currency=0.0,
            debit_in_transaction_currency=0.0,
            credit_in_transaction_currency=0.0,
            account_currency="LBP",
            transaction_currency="USD",
            presentation_currency="LBP",
        )

        result = module.get_result_as_list([row], _dict(presentation_currency="LBP"))
        separator = result[0]

        assert separator.debit == ""
        assert separator.credit == ""
        assert separator.debit_in_company_currency == ""
        assert separator.credit_in_company_currency == ""
        assert separator.balance == ""
        assert separator.account_currency == ""
        assert separator.presentation_currency == ""

    def test_currency_columns_omit_duplicate_company_presentation_set(self) -> None:
        """Default presentation currency should not render duplicate amount columns."""
        columns_module = importlib.import_module(
            "karam_finance.karam_general.report.general_ledger_(karam).gl_columns"
        )

        with (
            patch.object(columns_module, "get_company_currency", return_value="LBP"),
            patch.object(columns_module, "get_default_company", return_value="Karam"),
            patch.object(
                columns_module.frappe.db,
                "get_single_value",
                return_value="Supplier Name",
            ),
        ):
            columns = columns_module.get_columns(_dict(company="Karam"))

        fields = {column["fieldname"] for column in columns}
        assert {
            "account_currency",
            "debit_in_account_currency",
            "credit_in_account_currency",
            "debit_in_company_currency",
            "credit_in_company_currency",
            "balance_in_company_currency",
        } <= fields
        assert not {"debit", "credit", "balance"} & fields

    def test_currency_columns_include_presentation_set_when_different(self) -> None:
        """A different presentation currency should render all three amount sets."""
        columns_module = importlib.import_module(
            "karam_finance.karam_general.report.general_ledger_(karam).gl_columns"
        )

        with (
            patch.object(columns_module, "get_company_currency", return_value="LBP"),
            patch.object(columns_module, "get_default_company", return_value="Karam"),
            patch.object(
                columns_module.frappe.db,
                "get_single_value",
                return_value="Supplier Name",
            ),
        ):
            columns = columns_module.get_columns(
                _dict(company="Karam", presentation_currency="GBP")
            )

        fields = {column["fieldname"] for column in columns}
        assert {
            "debit_in_account_currency",
            "credit_in_account_currency",
            "debit_in_company_currency",
            "credit_in_company_currency",
            "balance_in_company_currency",
            "debit",
            "credit",
            "balance",
        } <= fields

    def test_company_running_balance_is_independent_and_resets_with_separators(
        self,
    ) -> None:
        """Company balance follows company amounts, not converted presentation values."""
        module = _load_module()
        rows = [
            _dict(
                row_type="opening",
                debit=100.0,
                credit=0.0,
                debit_in_company_currency=1_000.0,
                credit_in_company_currency=0.0,
            ),
            _dict(
                row_type="entry",
                posting_date=date(2024, 1, 1),
                debit=25.0,
                credit=0.0,
                debit_in_company_currency=250.0,
                credit_in_company_currency=0.0,
            ),
            _dict(is_separator=1),
            _dict(
                row_type="entry",
                posting_date=date(2024, 1, 2),
                debit=7.0,
                credit=2.0,
                debit_in_company_currency=70.0,
                credit_in_company_currency=20.0,
            ),
            _dict(
                row_type="report_total",
                debit=7.0,
                credit=2.0,
                debit_in_company_currency=70.0,
                credit_in_company_currency=20.0,
            ),
        ]

        result = module.get_result_as_list(
            rows,
            _dict(presentation_currency="GBP"),
        )

        assert result[0].balance == 100.0
        assert result[0].balance_in_company_currency == 1_000.0
        assert result[1].balance == 125.0
        assert result[1].balance_in_company_currency == 1_250.0
        assert result[2].balance == ""
        assert result[2].balance_in_company_currency == ""
        assert result[3].balance == 5.0
        assert result[3].balance_in_company_currency == 50.0
        report_total = next(
            row for row in result if row.get("row_type") == "report_total"
        )
        assert report_total.balance == 5.0
        assert report_total.balance_in_company_currency == 50.0

    def test_legacy_company_currency_filter_is_ignored(self) -> None:
        """Old saved checkbox values must not reach presentation conversion."""
        module = _load_module()
        filters = _dict(
            company="Karam",
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            show_amount_in_company_currency=1,
        )

        module.validate_filters(filters, {})

        assert "show_amount_in_company_currency" not in filters

    def test_footer_separator_targets_trailing_footer_only(self) -> None:
        """Insert separator before final footer, not first grouped total row."""
        module = _load_module()
        rows = [
            _dict(account="Party A", debit=10.0, credit=0.0),
            _dict(account="'Total'", debit=10.0, credit=0.0),
            _dict(account="'Closing (Opening + Total)'", debit=10.0, credit=0.0),
            _dict(account="Party B", debit=5.0, credit=0.0),
            _dict(account="'Total'", debit=15.0, credit=0.0),
            _dict(account="'Closing (Opening + Total)'", debit=15.0, credit=0.0),
        ]

        result = module._insert_footer_separator(rows)

        assert len(result) == 7
        assert result[1].account == "'Total'"
        assert result[4].get("is_separator") == 1
        assert result[5].account == "'Total'"

    def test_row_types_are_set_for_totals_and_separators(self) -> None:
        """Totals and separators should carry explicit row-type markers."""
        module = _load_module()

        group_totals = module._get_totals_dict()
        report_totals = module._get_totals_dict(total_row_type="report_total")
        separator = module._make_group_separator_row()

        assert group_totals.opening.row_type == "opening"
        assert group_totals.total.row_type == "group_total"
        assert group_totals.closing.row_type == "closing"
        assert report_totals.total.row_type == "report_total"
        assert separator["row_type"] == "separator"
        assert separator["is_separator"] == 1

    def test_footer_separator_uses_row_type_when_present(self) -> None:
        """Footer insertion should honour explicit row_type markers."""
        module = _load_module()
        rows = [
            _dict(account="Party A", row_type="entry", debit=10.0, credit=0.0),
            _dict(
                account="Not a footer label",
                row_type="report_total",
                debit=10.0,
                credit=0.0,
            ),
            _dict(account="Any label", row_type="closing", debit=10.0, credit=0.0),
        ]

        result = module._insert_footer_separator(rows)

        assert len(result) == 4
        assert result[1]["is_separator"] == 1
        assert result[1]["row_type"] == "separator"
        assert result[2]["row_type"] == "report_total"
        assert result[3]["row_type"] == "closing"

    def test_group_key_normalisation_merges_whitespace_party_keys(self) -> None:
        """Whitespace and empty variants should map to deterministic group keys."""
        module = _load_module()
        entries = [
            _dict(party="  CUST-0001  "),
            _dict(party="CUST-0001"),
            _dict(party=""),
            _dict(party=None),
            _dict(party="   "),
        ]
        filters = _dict(categorize_by="Categorise by Party")
        totals = module._get_totals_dict()

        gle_map = module._init_gle_map(entries, filters, totals)

        assert "CUST-0001" in gle_map
        assert None in gle_map
        assert len(gle_map) == 2

    def test_empty_party_group_uses_the_normalised_map_key(self) -> None:
        """Party grouping must not fail when an allowed party type has no party."""
        module = _load_module()
        filters = _dict(
            categorize_by="Categorise by Party",
            show_net_values_in_party_account=0,
            include_dimensions=0,
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            show_opening_entries=0,
        )
        entries = [
            _dict(
                posting_date=date(2024, 6, 1),
                voucher_type="Journal Entry",
                voucher_subtype="Journal Entry",
                voucher_no="JV-EMPTY-PARTY",
                account="Debtors - K",
                party_type="Customer",
                party="",
                against_voucher_type=None,
                against_voucher=None,
                remarks=None,
                is_opening="No",
                creation="2024-06-01 10:00:00",
                debit=10.0,
                credit=0.0,
                debit_in_account_currency=10.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=10.0,
                credit_in_company_currency=0.0,
                cost_center=None,
                project=None,
            )
        ]
        totals = module._get_totals_dict()
        gle_map = module._init_gle_map(entries, filters, totals)

        with patch.object(module.frappe.db, "get_single_value", return_value=0):
            updated_totals, grouped_entries = module._get_account_wise_gle(
                filters, [], entries, gle_map=gle_map, totals=totals
            )

        assert grouped_entries == []
        assert updated_totals.total.debit == 10.0
        assert gle_map[None].entries == entries

    def test_consolidated_key_normalises_string_parts(self) -> None:
        """Consolidated keys should collapse whitespace-only differences."""
        module = _load_module()
        base = _dict(
            posting_date=date(2024, 1, 1),
            voucher_type="Journal Entry",
            voucher_no="JV-1",
            account="Debtors - K",
            party_type="Customer",
            party="CUST-0001",
            creation="2024-01-01 12:00:00",
            cost_center="Main - K",
            project="PRJ-1",
            dimension_a="A",
        )
        variant = _dict(
            posting_date=date(2024, 1, 1),
            voucher_type="  Journal Entry ",
            voucher_no="JV-1  ",
            account=" Debtors - K",
            party_type="Customer ",
            party=" CUST-0001 ",
            creation="2024-01-01 12:00:00 ",
            cost_center=" Main - K ",
            project=" PRJ-1",
            dimension_a=" A ",
        )

        k1 = module._consolidated_key(
            base, True, True, accounting_dimensions=["dimension_a"]
        )
        k2 = module._consolidated_key(
            variant, True, True, accounting_dimensions=["dimension_a"]
        )
        assert k1 == k2

    def test_opening_closing_matrix_for_show_opening_entries_and_group_modes(
        self,
    ) -> None:
        """Opening/total/closing must stay stable across grouped modes."""
        module = _load_module()
        base_filters = {
            "show_net_values_in_party_account": 0,
            "include_dimensions": 0,
            "from_date": date(2024, 1, 1),
            "to_date": date(2024, 12, 31),
            "company": "Karam",
        }
        gl_entries = [
            _dict(
                posting_date=date(2023, 12, 31),
                voucher_type="Journal Entry",
                voucher_no="JV-OPEN",
                account="Debtors - K",
                party_type="Customer",
                party="CUST-0001",
                against_voucher=None,
                is_opening="Yes",
                creation="2023-12-31 09:00:00",
                debit=10.0,
                credit=0.0,
                debit_in_account_currency=10.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=10.0,
                credit_in_company_currency=0.0,
                cost_center=None,
                project=None,
            ),
            _dict(
                posting_date=date(2024, 2, 1),
                voucher_type="Journal Entry",
                voucher_no="JV-001",
                account="Debtors - K",
                party_type="Customer",
                party="CUST-0001",
                against_voucher=None,
                is_opening="No",
                creation="2024-02-01 09:00:00",
                debit=15.0,
                credit=0.0,
                debit_in_account_currency=15.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=15.0,
                credit_in_company_currency=0.0,
                cost_center=None,
                project=None,
            ),
            _dict(
                posting_date=date(2025, 1, 2),
                voucher_type="Journal Entry",
                voucher_no="JV-POST",
                account="Debtors - K",
                party_type="Customer",
                party="CUST-0001",
                against_voucher=None,
                is_opening="No",
                creation="2025-01-02 09:00:00",
                debit=20.0,
                credit=0.0,
                debit_in_account_currency=20.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=20.0,
                credit_in_company_currency=0.0,
                cost_center=None,
                project=None,
            ),
        ]

        cases = [
            ("", 0, 10.0, 15.0, 25.0),
            ("Categorise by Party", 0, 10.0, 15.0, 25.0),
            ("Categorise by Account", 0, 10.0, 15.0, 25.0),
            ("Categorise by Voucher (Consolidated)", 0, 10.0, 15.0, 25.0),
            ("Categorise by Party", 1, 10.0, 15.0, 25.0),
        ]

        with patch.object(module.frappe.db, "get_single_value", return_value=0):
            for categorize_by, show_opening_entries, opening, total, closing in cases:
                filters = _dict(
                    **base_filters,
                    categorize_by=categorize_by,
                    show_opening_entries=show_opening_entries,
                )
                totals_template = module._get_totals_dict(total_row_type="report_total")
                gle_map = module._init_gle_map(
                    gl_entries,
                    filters,
                    module._get_totals_dict(),
                )
                totals, _entries = module._get_account_wise_gle(
                    filters,
                    [],
                    gl_entries,
                    gle_map=gle_map,
                    totals=totals_template,
                )
                assert totals.opening.debit == opening
                assert totals.total.debit == total
                assert totals.closing.debit == closing

    def test_group_by_account_with_opening_identifies_opening_only_account(
        self,
    ) -> None:
        """Show the account between its opening and closing when it has no movement."""
        module = _load_module()
        filters = _dict(
            categorize_by="Group by Account w/ Opening",
            show_net_values_in_party_account=0,
            include_dimensions=0,
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            show_opening_entries=0,
            company="Karam",
        )
        gl_entries = [
            _dict(
                posting_date=date(2023, 12, 31),
                voucher_type="Journal Entry",
                voucher_no="JV-OPEN",
                account="Opening Only - K",
                party_type=None,
                party=None,
                against_voucher=None,
                is_opening="No",
                creation="2023-12-31 09:00:00",
                debit=10.0,
                credit=0.0,
                debit_in_account_currency=10.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=10.0,
                credit_in_company_currency=0.0,
                cost_center=None,
                project=None,
            ),
            _dict(
                posting_date=date(2024, 6, 1),
                voucher_type="Journal Entry",
                voucher_no="JV-MOVING",
                account="Moving - K",
                party_type=None,
                party=None,
                against_voucher=None,
                is_opening="No",
                creation="2024-06-01 09:00:00",
                debit=15.0,
                credit=0.0,
                debit_in_account_currency=15.0,
                credit_in_account_currency=0.0,
                debit_in_company_currency=15.0,
                credit_in_company_currency=0.0,
                cost_center=None,
                project=None,
            ),
            _dict(
                posting_date=date(2023, 12, 31),
                voucher_type="Journal Entry",
                voucher_no="JV-RESIDUAL",
                account="Rounded Zero - K",
                party_type=None,
                party=None,
                against_voucher=None,
                is_opening="No",
                creation="2023-12-31 10:00:00",
                debit=0.004,
                credit=0.0,
                debit_in_account_currency=0.004,
                credit_in_account_currency=0.0,
                debit_in_company_currency=0.004,
                credit_in_company_currency=0.0,
                cost_center=None,
                project=None,
            ),
        ]

        with (
            patch.object(module.frappe.db, "get_single_value", return_value=0),
            patch.object(
                module._gl_aggregation,
                "get_currency_precision",
                return_value=2,
            ),
        ):
            data = module.get_data_with_opening_closing(filters, [], gl_entries)

        account_row_index = next(
            index
            for index, row in enumerate(data)
            if row.get("account") == "Opening Only - K"
        )
        assert data[account_row_index]["row_type"] == "account_header"
        assert data[account_row_index - 1]["row_type"] == "opening"
        assert data[account_row_index - 1].credit == 0.0
        assert data[account_row_index - 1].debit == 10.0
        assert data[account_row_index + 1]["row_type"] == "closing"
        assert data[account_row_index + 1].debit == 10.0
        assert all(
            row.get("row_type") != "group_total"
            for row in data[account_row_index - 1 : account_row_index + 2]
        )
        assert any(row.get("account") == "Moving - K" for row in data)
        assert not any(row.get("account") == "Rounded Zero - K" for row in data)

    def test_build_date_conditions_matrix(self) -> None:
        """Date conditions should respect opening handling and grouped modes."""
        module = _load_module()
        base = _dict(
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            account=None,
            party=None,
        )

        ungrouped = module._build_date_conditions(_dict(base), ignore_is_opening=False)
        assert "(gl.posting_date >=%(from_date)s or gl.is_opening = 'Yes')" in ungrouped
        assert "(gl.posting_date <=%(to_date)s or gl.is_opening = 'Yes')" in ungrouped

        ignore_opening = module._build_date_conditions(
            _dict(base),
            ignore_is_opening=True,
        )
        assert "gl.posting_date >=%(from_date)s" in ignore_opening
        assert "gl.posting_date <=%(to_date)s" in ignore_opening

        grouped_party = module._build_date_conditions(
            _dict(base, categorize_by="Categorise by Party"),
            ignore_is_opening=False,
        )
        assert (
            "(gl.posting_date >=%(from_date)s or gl.is_opening = 'Yes')"
            not in grouped_party
        )
        assert (
            "(gl.posting_date <=%(to_date)s or gl.is_opening = 'Yes')" in grouped_party
        )

    def test_account_with_opening_keeps_the_same_history_as_account_grouping(
        self,
    ) -> None:
        """Unfiltered account openings must include ordinary historical postings."""
        query_module = _load_module()._gl_query
        gl = frappe.qb.DocType("GL Entry")
        for ignore_opening in (False, True):
            for disable_opening in (False, True):
                filters = _dict(
                    from_date=date(2024, 1, 1),
                    to_date=date(2024, 12, 31),
                    _ignore_is_opening=ignore_opening,
                    disable_opening_balance_calculation=disable_opening,
                )
                queries = []
                legacy = []
                for mode in ("Categorise by Account", "Group by Account w/ Opening"):
                    filters.categorize_by = mode
                    conditions = query_module._build_qb_date_conditions(filters, gl)
                    queries.append(
                        frappe.qb.from_(gl)
                        .select(gl.name)
                        .where(Criterion.all(conditions))
                        .walk()
                    )
                    legacy.append(
                        query_module._build_date_conditions(filters, ignore_opening)
                    )
                assert queries[0] == queries[1]
                assert legacy[0] == legacy[1]
                assert (
                    any("from_date" in condition for condition in legacy[1])
                    == disable_opening
                )

    def test_party_name_enrichment_gate(self) -> None:
        """Party-name enrichment should trigger only when needed."""
        module = _load_module()
        query_module = module._gl_query

        assert query_module._should_attach_party_names(_dict(_needs_party_name=True))
        assert query_module._should_attach_party_names(
            _dict(categorize_by="Categorise by Party")
        )
        assert not query_module._should_attach_party_names(_dict(categorize_by=""))

    def test_legacy_group_by_normalises_upstream_and_historical_spelling(
        self,
    ) -> None:
        """Saved v15 and ERPNext v16 grouping values share one canonical form."""
        module = _load_module()
        filters = _dict(
            company="Example Company",
            from_date=date(2024, 1, 1),
            to_date=date(2024, 12, 31),
            group_by="Categorize by Voucher",
        )

        module.validate_filters(filters, {})

        assert filters.categorize_by == "Categorise by Voucher"

    def test_series_translation_queries_cover_supported_voucher_sources(self) -> None:
        """Every required voucher source uses one parameterised QB projection."""
        module = _load_module()
        enrichment = module._gl_enrichment
        required_sources = (
            "Journal Entry",
            "Sales Invoice",
            "Purchase Invoice",
            "Payment Entry",
            "Stock Entry",
            "Exchange Rate Revaluation",
        )

        with patch.object(
            enrichment,
            "_get_karam_fields_for_doctype",
            return_value=["name", "karam_series", "translation"],
        ):
            for doctype in required_sources:
                query = enrichment._build_voucher_query(
                    doctype,
                    None,
                    series="EX",
                    translation="Exchange Rate",
                )
                sql, parameters = query.walk()

                assert f"`tab{doctype}`" in sql
                assert "%(param" in sql
                assert doctype in parameters.values()
                assert "EX" in parameters.values()
                assert "%Exchange Rate%" in parameters.values()

    def test_bulk_voucher_names_remain_bound_and_doctype_scoped(self) -> None:
        """Keep quoted names out of SQL and preserve identical names across sources."""
        enrichment = _load_module()._gl_enrichment
        name = "shared'voucher%"
        rows = [
            _dict(
                _doctype="Journal Entry", name=name, karam_series="JV", translation=""
            ),
            _dict(
                _doctype="Payment Entry",
                name=name,
                karam_series=None,
                translation="Paid",
            ),
        ]
        with (
            patch.object(
                enrichment,
                "_get_karam_fields_for_doctype",
                return_value=["name", "karam_series", "translation"],
            ),
            patch.object(frappe.db, "sql", return_value=rows) as execute,
        ):
            result = enrichment._fetch_voucher_data(
                {"Journal Entry": {name}, "Payment Entry": {name}, "Quotation": {name}}
            )

        execute.assert_called_once()
        sql, parameters = execute.call_args.args
        assert name not in sql
        assert "tabQuotation" not in sql
        assert "UNION ALL" in sql
        assert list(parameters.values()).count((name,)) == 2
        assert result[("Journal Entry", name)]["karam_series"] == "JV"
        assert result[("Payment Entry", name)]["translation"] == "Paid"
        assert result[("Payment Entry", name)]["karam_series"] is None

    def test_empty_voucher_targets_do_not_query(self) -> None:
        """An empty source set must never turn into an unrestricted lookup."""
        enrichment = _load_module()._gl_enrichment
        with (
            patch.object(
                enrichment,
                "_get_karam_fields_for_doctype",
                return_value=["name", "karam_series"],
            ),
            patch.object(frappe.db, "sql") as execute,
        ):
            assert (
                enrichment._fetch_voucher_data(
                    {"Journal Entry": set(), "Payment Entry": set()}
                )
                == {}
            )
        execute.assert_not_called()

    def test_dynamic_dimension_filters_are_validated_and_tree_expanded(self) -> None:
        """Only active GL fields become conditions and tree values include children."""
        module = _load_module()
        query_module = module._gl_query
        gl_entry = frappe.qb.DocType("GL Entry")
        dimensions = [
            _dict(
                fieldname="equipment_center",
                document_type="Equipment Center",
                disabled=0,
            ),
            _dict(
                fieldname="disabled_dimension",
                document_type="Disabled Dimension",
                disabled=1,
            ),
            _dict(
                fieldname="finance_book",
                document_type="Finance Book",
                disabled=0,
            ),
        ]
        filters = _dict(
            equipment_center=["Main"],
            disabled_dimension=["Hidden"],
            finance_book="Default",
        )

        with (
            patch.object(
                query_module,
                "get_accounting_dimensions",
                return_value=dimensions,
            ),
            patch.object(query_module.frappe, "get_meta") as get_meta,
            patch.object(
                query_module.frappe,
                "get_cached_value",
                return_value=1,
            ),
            patch.object(
                query_module,
                "get_dimension_with_children",
                return_value=["Main", "Child"],
            ) as get_children,
        ):
            get_meta.return_value.has_field.side_effect = lambda fieldname: (
                fieldname == "equipment_center"
            )
            conditions = query_module._build_qb_dimension_conditions(filters, gl_entry)

        assert len(conditions) == 1
        assert filters.equipment_center == ["Main", "Child"]
        get_children.assert_called_once_with("Equipment Center", ["Main"])

    def test_letter_filter_is_independent_of_accounting_dimensions(self) -> None:
        """Letter remains a GL field and never aliases LoC or Auxiliary dimensions."""
        module = _load_module()
        query_module = module._gl_query
        gl_entry = frappe.qb.DocType("GL Entry")
        filters = _dict(
            letter="LETTER-1",
            letter_of_credit="LC-1",
            auxiliary="AUX-1",
        )

        conditions = query_module._build_qb_karam_conditions(
            filters, gl_entry, voucher_data=None
        )
        query = (
            frappe.qb.from_(gl_entry)
            .select(gl_entry.name)
            .where(Criterion.all(conditions))
        )
        sql, parameters = query.walk()

        assert len(conditions) == 1
        assert "`letter`" in sql
        assert "letter_of_credit" not in sql
        assert "auxiliary" not in sql
        assert "LETTER-1" in parameters.values()

    def test_voucher_filter_pair_expansion_fails_loudly_when_unbounded(self) -> None:
        """A broad source filter cannot create an unbounded OR expression."""
        module = _load_module()
        query_module = module._gl_query
        gl_entry = frappe.qb.DocType("GL Entry")
        voucher_data = {("Journal Entry", f"JV-{index}"): {} for index in range(3)}

        with (
            patch.object(query_module, "_MAX_VOUCHER_FILTER_PAIRS", 2),
            self.assertRaises(frappe.ValidationError),
        ):
            query_module._build_qb_karam_conditions(
                _dict(karam_series="JV"), gl_entry, voucher_data
            )

    def test_summary_rows_use_selected_account_currency_without_overwriting_details(
        self,
    ) -> None:
        """Totals get the selected currency while detail rows retain their own."""
        module = _load_module()
        rows = [
            _dict(row_type="opening", debit=10.0, credit=0.0),
            _dict(
                row_type="entry",
                posting_date=date(2024, 1, 1),
                debit=5.0,
                credit=0.0,
                account_currency="USD",
            ),
        ]

        result = module.get_result_as_list(
            rows,
            _dict(account_currency="LBP", presentation_currency="LBP"),
        )

        assert result[0].account_currency == "LBP"
        assert result[1].account_currency == "USD"
