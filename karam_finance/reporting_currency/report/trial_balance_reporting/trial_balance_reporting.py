# ruff: noqa: D103, ANN202
"""Trial Balance (Reporting) - Trial Balance using Reporting Currency GLE."""

from __future__ import annotations

from . import tbr_calc, tbr_constants, tbr_data, tbr_filters, tbr_query, tbr_rows

ZERO_THRESHOLD = tbr_constants.ZERO_THRESHOLD
VALUE_FIELDS = tbr_constants.VALUE_FIELDS
DOCTYPE_RC_GLE = tbr_constants.DOCTYPE_RC_GLE
DOCTYPE_RC_SETTINGS = tbr_constants.DOCTYPE_RC_SETTINGS


def execute(filters: dict | None = None) -> tuple[list, list]:
    if filters is None:
        filters = {}
    validate_filters(filters)
    data = get_data(filters)
    columns = get_columns()
    return columns, data


def validate_filters(filters: dict) -> None:
    tbr_filters.validate_filters(filters)


def get_data(filters: dict) -> list[dict]:
    return tbr_data.get_data(filters)


def _build_base_query(filters: dict):
    return tbr_query.build_base_query(filters)


def get_opening_balances(filters: dict) -> dict:
    return tbr_query.get_opening_balances(filters)


def get_gl_entries_by_account(filters: dict) -> dict:
    return tbr_query.get_gl_entries_by_account(filters)


def calculate_values(
    accounts: list[dict],
    gl_entries_by_account: dict,
    opening_balances: dict,
) -> None:
    tbr_calc.calculate_values(accounts, gl_entries_by_account, opening_balances)


def accumulate_values_into_parents(
    accounts: list[dict], accounts_by_name: dict
) -> None:
    tbr_calc.accumulate_values_into_parents(accounts, accounts_by_name)


def _prepare_opening_closing(row: dict) -> None:
    tbr_calc.prepare_opening_closing(row)


def prepare_data(
    accounts: list[dict],
    filters: dict,
    reporting_currency: str,
) -> list[dict]:
    return tbr_rows.prepare_data(accounts, filters, reporting_currency)


def calculate_total_row(accounts: list[dict], reporting_currency: str) -> dict:
    return tbr_rows.calculate_total_row(accounts, reporting_currency)


def get_columns() -> list[dict]:
    return tbr_rows.get_columns()
