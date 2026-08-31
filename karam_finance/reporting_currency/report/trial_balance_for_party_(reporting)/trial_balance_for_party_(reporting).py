"""Trial Balance for Party (Reporting) report.

Mirrors ERPNext vanilla Trial Balance for Party row semantics, but values are
taken from Reporting Currency GLE and the hidden currency is the configured
reporting currency.
"""

# ruff: noqa: D103, ANN001, ANN201, N999
# N999: Module name contains parentheses (Frappe report naming convention)

from __future__ import annotations

from . import tbfpr_columns, tbfpr_constants, tbfpr_data, tbfpr_filters, tbfpr_rows

VALUE_FIELDS = tbfpr_constants.VALUE_FIELDS


def execute(filters=None):
    validate_filters(filters)
    show_party_name = is_party_name_visible(filters)
    columns = get_columns(filters, show_party_name)
    data = get_data(filters, show_party_name)
    return columns, data


def validate_filters(filters):
    return tbfpr_filters.validate_filters(filters)


def get_data(filters, show_party_name):
    return tbfpr_data.get_data(filters, show_party_name)


def get_columns(filters, show_party_name):
    return tbfpr_columns.get_columns(filters, show_party_name)


def is_party_name_visible(filters):
    return tbfpr_filters.is_party_name_visible(filters)


def get_blank_row():
    return tbfpr_rows.get_blank_row()


def get_party_name_field(filters):
    return tbfpr_filters.get_party_name_field(filters)


def toggle_debit_credit(debit, credit):
    return tbfpr_rows.toggle_debit_credit(debit, credit)
