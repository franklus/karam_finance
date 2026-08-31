# ruff: noqa: D100, D103, ANN001, ANN201, ANN202, N999
# cspell:ignore Categorize

from __future__ import annotations

import frappe

from . import glrc_conditions, glrc_data, glrc_filters, glrc_queries, glrc_render

PARTY_LOOKUP_BATCH_SIZE = glrc_queries.PARTY_LOOKUP_BATCH_SIZE


def execute(filters: dict | None = None) -> tuple[list[dict], list[dict]]:
    if not filters:
        return [], []

    account_details: dict[str, frappe._dict] = {}
    for acc in frappe.db.sql(
        """select name, is_group from tabAccount where company=%(company)s""",
        filters,
        as_dict=1,
    ):
        account_details.setdefault(acc.name, acc)

    if filters.get("party"):
        filters.party = frappe.parse_json(filters.get("party"))

    validate_filters(filters, account_details)
    validate_party(filters)

    columns = get_columns(filters)
    result = get_result(filters, account_details)

    return columns, result


def validate_filters(filters: dict, account_details: dict[str, frappe._dict]) -> None:
    glrc_filters.validate_filters(filters, account_details)


def validate_party(filters: dict) -> None:
    glrc_filters.validate_party(filters)


def get_result(filters: dict, account_details: dict[str, frappe._dict]) -> list[dict]:
    return glrc_data.get_result(filters, account_details)


def get_gl_entries(filters: dict) -> list[frappe._dict]:
    return glrc_queries.get_gl_entries(filters)


def _get_order_by_clause(filters: dict) -> str:
    return glrc_conditions.get_order_by_clause(filters)


def get_conditions(filters: dict) -> str:
    return glrc_conditions.get_conditions(filters)


def _build_account_conditions(filters):
    return glrc_conditions.build_account_conditions(filters)


def _build_voucher_conditions(filters):
    return glrc_conditions.build_voucher_conditions(filters)


def _build_party_conditions(filters):
    return glrc_conditions.build_party_conditions(filters)


def _build_date_conditions(filters, ignore_is_opening):
    return glrc_conditions.build_date_conditions(filters, ignore_is_opening)


def _build_system_conditions(filters):
    return glrc_conditions.build_system_conditions(filters)


def _build_reporting_conditions(filters):
    return glrc_conditions.build_reporting_conditions(filters)


def get_party_name_map(gl_entries: list[frappe._dict]) -> dict[str, dict[str, str]]:
    return glrc_queries.get_party_name_map(gl_entries)


def get_accounts_with_children(accounts):
    return glrc_conditions.get_accounts_with_children(accounts)


def _chunked(values, size):
    return glrc_conditions.chunked(values, size, PARTY_LOOKUP_BATCH_SIZE)


def get_data_with_opening_closing(filters, account_details, gl_entries):
    return glrc_data.get_data_with_opening_closing(filters, account_details, gl_entries)


def get_result_as_list(data, filters):
    return glrc_render.get_result_as_list(data, filters)


def _inject_reporting_currency(result: list[dict]) -> list[dict]:
    return glrc_render.inject_reporting_currency(result)


def _insert_footer_separator(result: list[dict]) -> list[dict]:
    return glrc_render.insert_footer_separator(result)


def get_columns(filters: dict) -> list[dict]:
    return glrc_render.get_columns(filters)
