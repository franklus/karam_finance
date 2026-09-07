"""Trial Balance for Party (Karam) report entry point."""

from __future__ import annotations

from typing import Any

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


def execute(filters: Any = None) -> Any:
    validate_filters(filters)
    show_party_name = is_party_name_visible(filters)
    columns = get_columns(filters, show_party_name)
    data = get_data(filters, show_party_name)
    return columns, data


def validate_filters(filters: Any) -> Any:
    return _validate_filters(filters)


def get_data(filters: Any, show_party_name: Any) -> Any:
    return _get_data(filters, show_party_name)


def get_blank_row() -> Any:
    return _get_blank_row()


def get_party_name_field(filters: Any) -> Any:
    return _get_party_name_field(filters)


build_party_row = _build_party_row


def toggle_debit_credit(debit: Any, credit: Any) -> Any:
    return _toggle_debit_credit(debit, credit)


def get_columns(filters: Any, show_party_name: Any) -> Any:
    return _get_columns(filters, show_party_name)


def is_party_name_visible(filters: Any) -> Any:
    return _is_party_name_visible(filters)
