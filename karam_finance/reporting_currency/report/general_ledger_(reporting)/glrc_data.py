# ruff: noqa: D100, D103, ANN001, ANN201, PLC0415

import frappe


def get_result(filters: dict, account_details: dict[str, frappe._dict]) -> list[dict]:
    accounts_settings = frappe.get_cached_doc("Accounts Settings")
    filters["_remarks_length"] = accounts_settings.general_ledger_remarks_length or 0
    filters["_ignore_is_opening"] = (
        accounts_settings.ignore_is_opening_check_for_reporting
    )

    from .glrc_queries import get_gl_entries
    from .glrc_render import get_result_as_list

    gl_entries = get_gl_entries(filters)
    data = get_data_with_opening_closing(filters, account_details, gl_entries)

    return get_result_as_list(data, filters)


def get_data_with_opening_closing(filters, account_details, gl_entries):
    categorize_by = filters.get("categorize_by", "")
    if categorize_by:
        filters["categorize_by"] = categorize_by.replace("Categorise", "Categorize")

    from erpnext.accounts.report.general_ledger.general_ledger import (
        get_data_with_opening_closing as base_get_data_with_opening_closing,
    )

    return base_get_data_with_opening_closing(filters, account_details, [], gl_entries)
