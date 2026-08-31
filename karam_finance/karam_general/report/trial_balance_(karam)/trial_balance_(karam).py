from __future__ import annotations

from .tbk_aggregation import (
    accumulate_values_into_parents as _accumulate_values_into_parents,
)
from .tbk_aggregation import (
    apply_gl_data_to_accounts as _apply_gl_data_to_accounts,
)
from .tbk_aggregation import (
    prepare_opening_closing as _prepare_opening_closing,
)
from .tbk_columns import get_columns as _get_columns
from .tbk_constants import (
    ACCOUNT_CCY_VALUE_FIELDS as _ACCOUNT_CCY_VALUE_FIELDS,
)
from .tbk_constants import (
    VALUE_FIELDS as _VALUE_FIELDS,
)
from .tbk_data import get_data as _get_data
from .tbk_filters import validate_filters as _validate_filters
from .tbk_query import get_gl_data_optimised as _get_gl_data_optimised
from .tbk_rows import (
    calculate_total_row as _calculate_total_row,
)
from .tbk_rows import (
    get_blank_row as _get_blank_row,
)
from .tbk_rows import (
    prepare_data as _prepare_data,
)

ACCOUNT_CCY_VALUE_FIELDS = _ACCOUNT_CCY_VALUE_FIELDS
VALUE_FIELDS = _VALUE_FIELDS


def execute(filters=None):
    validate_filters(filters)
    data = get_data(filters)
    columns = get_columns()
    return columns, data


def validate_filters(filters):
    return _validate_filters(filters)


def get_data(filters):
    return _get_data(filters)


def get_gl_data_optimised(filters) -> dict:
    return _get_gl_data_optimised(filters)


def apply_gl_data_to_accounts(accounts, gl_data, show_net_values):
    return _apply_gl_data_to_accounts(accounts, gl_data, show_net_values)


def accumulate_values_into_parents(accounts, accounts_by_name):
    return _accumulate_values_into_parents(accounts, accounts_by_name)


def get_blank_row():
    return _get_blank_row()


def calculate_total_row(accounts, company_currency, show_group_accounts=True):
    return _calculate_total_row(
        accounts, company_currency, show_group_accounts=show_group_accounts
    )


def prepare_data(accounts, filters, parent_children_map, company_currency):
    return _prepare_data(accounts, filters, parent_children_map, company_currency)


def get_columns():
    return _get_columns()


def prepare_opening_closing(row):
    return _prepare_opening_closing(row)
