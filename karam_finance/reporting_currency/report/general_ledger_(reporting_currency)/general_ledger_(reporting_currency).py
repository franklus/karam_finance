# General Ledger (Reporting Currency): ERPNext General Ledger with Karam Series/Translation/Letter
# N999: Module name contains parentheses (Frappe report naming convention)

from __future__ import annotations

import importlib
from typing import Any

import frappe
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
    get_accounting_dimensions,
)
from frappe import _

from karam_finance.reporting_currency.report.reporting_source import prepare_filters

from .gl_aggregation import (
    _consolidated_key,
    _get_account_type_map,
    _get_account_wise_gle,
    _get_totals_dict,
    _group_by_field,
    _init_gle_map,
    _insert_footer_separator,
    _make_group_separator_row,
    _set_bill_no,
    get_data_with_opening_closing,
    get_result_as_list,
)
from .gl_columns import get_columns
from .gl_context import attach_report_context
from .gl_currency import (
    _apply_flat_account_currency_summaries,
    _attach_flat_account_currency_openings,
)
from .gl_enrichment import (
    _KARAM_FIELDS_CACHE,
    PARTY_LOOKUP_BATCH_SIZE,
    VOUCHER_LOOKUP_BATCH_SIZE,
    _apply_voucher_data_to_entries,
    _attach_series_translation,
    _chunked,
    _collect_voucher_targets,
    _fetch_voucher_data,
    _get_karam_fields_for_doctype,
    _hydrate_entries_from_doctype,
    get_party_name_map,
)
from .gl_filters import (
    get_accounts_with_children,
    set_account_currency,
    validate_filters,
    validate_party,
)
from .gl_query import (
    _build_account_conditions,
    _build_date_conditions,
    _build_dimension_conditions,
    _build_finance_book_conditions,
    _build_karam_conditions,
    _build_party_conditions,
    _build_system_conditions,
    _build_voucher_conditions,
    _get_order_by_clause,
    get_conditions,
    get_flat_account_currency_openings,
    get_gl_entries,
)

# Private module aliases remain for callers and focused tests that patch helper
# seams; the public imports above keep static analysis on direct submodules.
_gl_aggregation = importlib.import_module(f"{__package__}.gl_aggregation")
_gl_enrichment = importlib.import_module(f"{__package__}.gl_enrichment")
_gl_filters = importlib.import_module(f"{__package__}.gl_filters")
_gl_query = importlib.import_module(f"{__package__}.gl_query")

__all__ = [
    "PARTY_LOOKUP_BATCH_SIZE",
    "VOUCHER_LOOKUP_BATCH_SIZE",
    "_KARAM_FIELDS_CACHE",
    "_apply_voucher_data_to_entries",
    "_attach_series_translation",
    "_build_account_conditions",
    "_build_date_conditions",
    "_build_dimension_conditions",
    "_build_finance_book_conditions",
    "_build_karam_conditions",
    "_build_party_conditions",
    "_build_system_conditions",
    "_build_voucher_conditions",
    "_chunked",
    "_collect_voucher_targets",
    "_consolidated_key",
    "_fetch_voucher_data",
    "_get_account_type_map",
    "_get_account_wise_gle",
    "_get_karam_fields_for_doctype",
    "_get_order_by_clause",
    "_get_totals_dict",
    "_gl_aggregation",
    "_gl_enrichment",
    "_gl_filters",
    "_gl_query",
    "_group_by_field",
    "_hydrate_entries_from_doctype",
    "_init_gle_map",
    "_insert_footer_separator",
    "_make_group_separator_row",
    "_set_bill_no",
    "execute",
    "get_accounts_with_children",
    "get_columns",
    "get_conditions",
    "get_data_with_opening_closing",
    "get_flat_account_currency_openings",
    "get_gl_entries",
    "get_party_name_map",
    "get_result",
    "get_result_as_list",
    "set_account_currency",
    "validate_filters",
    "validate_party",
]


def execute(
    filters: Any | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Execute the General Ledger (Reporting Currency) report."""
    if not filters:
        return [], []
    filters = prepare_filters(filters)

    if (
        filters
        and filters.get("print_in_account_currency")
        and not filters.get("account")
    ):
        frappe.throw(_("Select an account to print in account currency"))

    if filters.get("party") and not isinstance(
        filters.get("party"), (list, tuple, set)
    ):
        filters.party = frappe.parse_json(filters.get("party"))

    account_details = _get_account_details(filters)

    validate_filters(filters, account_details)
    validate_party(filters)
    filters = set_account_currency(filters)

    columns = get_columns(filters)
    filters["_needs_party_name"] = any(
        col.get("fieldname") == "party_name" for col in columns
    )
    data = get_result(filters)
    attach_report_context(data, filters)
    return columns, data


def _get_account_details(filters: Any) -> dict[str, frappe._dict[str, Any]]:
    """Fetch only accounts needed for validation, with an explicit bound."""
    accounts = filters.get("account")
    if not accounts:
        return {}
    accounts = _gl_filters._as_list(accounts)
    if not accounts:
        return {}

    rows = frappe.get_all(
        "Account",
        filters={"company": filters.get("company"), "name": ["in", accounts]},
        fields=["name", "is_group"],
        limit_page_length=len(accounts),
    )
    return {row.name: row for row in rows}


def get_result(filters: Any) -> list[dict[str, Any]]:
    """Orchestrate GL data retrieval and processing."""
    accounts_settings = frappe.get_cached_doc("Accounts Settings")
    filters["_remarks_length"] = (
        accounts_settings.get("general_ledger_remarks_length") or 0
    )
    filters["_ignore_is_opening"] = accounts_settings.get(
        "ignore_is_opening_check_for_reporting"
    )

    accounting_dimensions: list[str] = []
    if filters.get("include_dimensions"):
        accounting_dimensions = get_accounting_dimensions()
        filters["_dimensions_meta"] = get_accounting_dimensions(as_list=False)

    gl_entries = get_gl_entries(
        filters, accounting_dimensions, enrich_opening_entries=False
    )
    data = get_data_with_opening_closing(filters, accounting_dimensions, gl_entries)
    if filters.get("categorize_by") == "Flat Chronological":
        openings = get_flat_account_currency_openings(filters)
        _apply_flat_account_currency_summaries(data, openings)
        _attach_flat_account_currency_openings(data, openings)
    return get_result_as_list(data, filters)
