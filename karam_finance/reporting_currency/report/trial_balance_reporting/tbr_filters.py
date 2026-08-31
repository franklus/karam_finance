# ruff: noqa: D100, D103

import frappe
from frappe import _
from frappe.utils import formatdate, getdate


def validate_filters(filters: dict) -> None:
    if not filters.fiscal_year:
        frappe.throw(_("Fiscal Year {0} is required").format(filters.fiscal_year))

    fiscal_year = frappe.get_cached_value(
        "Fiscal Year",
        filters.fiscal_year,
        ["year_start_date", "year_end_date"],
        as_dict=True,
    )

    if not fiscal_year:
        frappe.throw(_("Fiscal Year {0} does not exist").format(filters.fiscal_year))

    filters.year_start_date = getdate(fiscal_year.year_start_date)
    filters.year_end_date = getdate(fiscal_year.year_end_date)

    if not filters.from_date:
        filters.from_date = filters.year_start_date

    if not filters.to_date:
        filters.to_date = filters.year_end_date

    filters.from_date = getdate(filters.from_date)
    filters.to_date = getdate(filters.to_date)

    if filters.from_date > filters.to_date:
        frappe.throw(_("From Date cannot be greater than To Date"))

    if (filters.from_date < filters.year_start_date) or (
        filters.from_date > filters.year_end_date
    ):
        msg = _("From Date should be within the Fiscal Year. Assuming From Date = {0}")
        frappe.msgprint(msg.format(formatdate(filters.year_start_date)))
        filters.from_date = filters.year_start_date

    if (filters.to_date < filters.year_start_date) or (
        filters.to_date > filters.year_end_date
    ):
        msg = _("To Date should be within the Fiscal Year. Assuming To Date = {0}")
        frappe.msgprint(msg.format(formatdate(filters.year_end_date)))
        filters.to_date = filters.year_end_date
