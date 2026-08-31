"""Filter validation and helpers for Trial Balance for Party (Reporting)."""

# ruff: noqa: D103, ANN001, ANN201

from typing import TYPE_CHECKING, cast

import frappe

if TYPE_CHECKING:
    from datetime import date
from frappe import _
from frappe.utils import formatdate, getdate


def validate_filters(filters):
    if not filters.fiscal_year:
        frappe.throw(_("Fiscal Year {0} is required").format(filters.fiscal_year))

    fiscal_year = frappe.get_cached_value(
        "Fiscal Year",
        filters.fiscal_year,
        ["year_start_date", "year_end_date"],  # pyright: ignore[reportArgumentType]
        as_dict=True,
    )
    if not fiscal_year:
        frappe.throw(_("Fiscal Year {0} does not exist").format(filters.fiscal_year))

    year_start_date = cast("date", getdate(fiscal_year.year_start_date))
    year_end_date = cast("date", getdate(fiscal_year.year_end_date))
    filters.year_start_date = year_start_date
    filters.year_end_date = year_end_date

    from_date = cast("date", getdate(filters.from_date or year_start_date))
    to_date = cast("date", getdate(filters.to_date or year_end_date))

    if from_date > to_date:
        frappe.throw(_("From Date cannot be greater than To Date"))

    if from_date < year_start_date or from_date > year_end_date:
        frappe.msgprint(
            _(
                "From Date should be within the Fiscal Year. Assuming From Date = {0}"
            ).format(formatdate(year_start_date))
        )
        from_date = year_start_date

    if to_date < year_start_date or to_date > year_end_date:
        frappe.msgprint(
            _(
                "To Date should be within the Fiscal Year. Assuming To Date = {0}"
            ).format(formatdate(year_end_date))
        )
        to_date = year_end_date

    filters.from_date = from_date
    filters.to_date = to_date


def get_party_name_field(filters):
    if filters.get("party_type") in ("Customer", "Supplier", "Employee", "Member"):
        return "{}_name".format(frappe.scrub(filters.get("party_type")))
    if filters.get("party_type") == "Shareholder":
        return "title"
    return "name"


def is_party_name_visible(filters):
    show_party_name = False

    if filters.get("party_type") in ["Customer", "Supplier"]:
        if filters.get("party_type") == "Customer":
            party_naming_by = frappe.db.get_single_value(
                "Selling Settings", "cust_master_name"
            )
        else:
            party_naming_by = frappe.db.get_single_value(
                "Buying Settings", "supp_master_name"
            )

        if party_naming_by == "Naming Series":
            show_party_name = True
    else:
        show_party_name = True

    return show_party_name
