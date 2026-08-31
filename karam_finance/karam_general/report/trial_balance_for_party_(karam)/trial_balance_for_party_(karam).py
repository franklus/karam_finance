"""Trial Balance for Party (Karam) report entry point."""

from __future__ import annotations

from .tbfp_columns import get_columns as _get_columns
from .tbfp_constants import ACCOUNT_CCY_VALUE_FIELDS as _ACCOUNT_CCY_VALUE_FIELDS
from .tbfp_constants import VALUE_FIELDS as _VALUE_FIELDS
from .tbfp_data import get_data as _get_data
from .tbfp_filters import get_party_name_field as _get_party_name_field
from .tbfp_filters import is_party_name_visible as _is_party_name_visible
from .tbfp_filters import validate_filters as _validate_filters
from .tbfp_rows import build_party_row as _build_party_row
from .tbfp_rows import get_blank_row as _get_blank_row
from .tbfp_rows import toggle_debit_credit as _toggle_debit_credit

VALUE_FIELDS = _VALUE_FIELDS
ACCOUNT_CCY_VALUE_FIELDS = _ACCOUNT_CCY_VALUE_FIELDS


def execute(filters=None):
    validate_filters(filters)
    show_party_name = is_party_name_visible(filters)
    columns = get_columns(filters, show_party_name)
    data = get_data(filters, show_party_name)
    return columns, data


def validate_filters(filters):
    return _validate_filters(filters)


def get_data(filters, show_party_name):
    return _get_data(filters, show_party_name)


def get_blank_row():
    return _get_blank_row()


def get_party_name_field(filters):
    return _get_party_name_field(filters)


def build_party_row(
    party, party_name, show_party_name, account_currency, company_currency, values
):
    return _build_party_row(
        party, party_name, show_party_name, account_currency, company_currency, values
    )


def toggle_debit_credit(debit, credit):
    return _toggle_debit_credit(debit, credit)


def get_columns(filters, show_party_name):
    return _get_columns(filters, show_party_name)


def is_party_name_visible(filters):
    return _is_party_name_visible(filters)
