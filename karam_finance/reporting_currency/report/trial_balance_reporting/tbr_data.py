# ruff: noqa: D100, D103

import frappe
from erpnext.accounts.report.financial_statements import (
    filter_accounts,
    filter_out_zero_value_rows,
)
from frappe import _

from .tbr_calc import accumulate_values_into_parents, calculate_values
from .tbr_constants import DOCTYPE_RC_SETTINGS
from .tbr_query import get_gl_entries_by_account, get_opening_balances
from .tbr_rows import prepare_data


def get_data(filters: dict) -> list[dict]:
    accounts = frappe.db.sql(
        """
        SELECT name, account_number, parent_account, account_name,
               root_type, report_type, lft, rgt, is_group
        FROM `tabAccount`
        WHERE company = %s
        ORDER BY lft
        """,
        filters.company,
        as_dict=True,
    )

    if not accounts:
        return []

    reporting_currency = frappe.db.get_single_value(
        DOCTYPE_RC_SETTINGS, "reporting_currency"
    )

    if not reporting_currency:
        frappe.throw(
            _("Reporting Currency is not configured in Reporting Currency Settings")
        )

    accounts, accounts_by_name, parent_children_map = filter_accounts(accounts)

    opening_balances = get_opening_balances(filters)
    gl_entries_by_account = get_gl_entries_by_account(filters)
    calculate_values(accounts, gl_entries_by_account, opening_balances)
    accumulate_values_into_parents(accounts, accounts_by_name)

    data = prepare_data(accounts, filters, reporting_currency)

    return filter_out_zero_value_rows(data, parent_children_map, show_zero_values=True)
