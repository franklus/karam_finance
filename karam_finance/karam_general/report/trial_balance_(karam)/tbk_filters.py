from datetime import date
from typing import Any, cast

import frappe
from erpnext.accounts.utils import get_fiscal_year
from frappe import _
from frappe.utils import formatdate, getdate


def validate_filters(filters: Any) -> None:
    if filters.get("ignore_fiscal_year"):
        validate_date_range_filters(filters)
        return

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

    filters.year_start_date = _require_date(
        fiscal_year.year_start_date, "Fiscal Year start date"
    )
    filters.year_end_date = _require_date(
        fiscal_year.year_end_date, "Fiscal Year end date"
    )

    if not filters.from_date:
        filters.from_date = filters.year_start_date

    if not filters.to_date:
        filters.to_date = filters.year_end_date

    filters.from_date = _require_date(filters.from_date, "From Date")
    filters.to_date = _require_date(filters.to_date, "To Date")

    if filters.from_date > filters.to_date:
        frappe.throw(_("From Date cannot be greater than To Date"))

    if (
        filters.from_date < filters.year_start_date
        or filters.from_date > filters.year_end_date
    ):
        frappe.msgprint(
            _(
                "From Date should be within the Fiscal Year. Assuming From Date = {0}"
            ).format(formatdate(filters.year_start_date))
        )
        filters.from_date = filters.year_start_date

    if (
        filters.to_date < filters.year_start_date
        or filters.to_date > filters.year_end_date
    ):
        frappe.msgprint(
            _(
                "To Date should be within the Fiscal Year. Assuming To Date = {0}"
            ).format(formatdate(filters.year_end_date))
        )
        filters.to_date = filters.year_end_date


def validate_date_range_filters(filters: Any) -> None:
    if not filters.from_date:
        frappe.throw(_("From Date is required when Fiscal Year is ignored"))

    if not filters.to_date:
        frappe.throw(_("To Date is required when Fiscal Year is ignored"))

    filters.from_date = _require_date(filters.from_date, "From Date")
    filters.to_date = _require_date(filters.to_date, "To Date")

    if filters.from_date > filters.to_date:
        frappe.throw(_("From Date cannot be greater than To Date"))

    fiscal_year = get_fiscal_year(filters.from_date, company=filters.company, verbose=0)
    filters.year_start_date = _require_date(fiscal_year[1], "Fiscal Year start date")
    filters.year_end_date = _require_date(fiscal_year[2], "Fiscal Year end date")


def _require_date(value: Any, label: str) -> date:
    parsed = getdate(value)
    if not parsed:
        frappe.throw(_("{0} must be a valid date").format(label))
    return cast(date, parsed)
