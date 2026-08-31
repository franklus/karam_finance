# ruff: noqa: D100, D103

import frappe
from frappe import _


def validate_filters(filters: dict, account_details: dict[str, frappe._dict]) -> None:
    if not filters.get("company"):
        frappe.throw(_("{0} is mandatory").format(_("Company")))

    if not filters.get("from_date") and not filters.get("to_date"):
        frappe.throw(
            _("{0} and {1} are mandatory").format(
                frappe.bold(_("From Date")), frappe.bold(_("To Date"))
            )
        )

    if filters.get("account"):
        filters.account = frappe.parse_json(filters.get("account"))
        for account in filters.account:
            if not account_details.get(account):
                frappe.throw(_("Account {0} does not exists").format(account))

    if not filters.get("categorize_by") and filters.get("group_by"):
        filters["categorize_by"] = filters["group_by"].replace(
            "Group by", "Categorise by"
        )

    if (
        filters.get("account")
        and filters.get("categorize_by") == "Categorise by Account"
    ):
        filters.account = frappe.parse_json(filters.get("account"))
        for account in filters.account:
            if account_details[account].is_group == 0:
                frappe.throw(
                    _("Can not filter based on Child Account, if grouped by Account")
                )

    if (
        filters.get("voucher_no")
        and filters.get("categorize_by") == "Categorise by Voucher"
    ):
        frappe.throw(_("Can not filter based on Voucher No, if grouped by Voucher"))

    if filters.from_date > filters.to_date:
        frappe.throw(_("From Date must be before To Date"))

    if filters.get("project"):
        filters.project = frappe.parse_json(filters.get("project"))

    if filters.get("cost_center"):
        filters.cost_center = frappe.parse_json(filters.get("cost_center"))


def validate_party(filters: dict) -> None:
    party_type, party = filters.get("party_type"), filters.get("party")
    if party and party_type:
        existing = set(
            frappe.get_all(
                party_type, filters={"name": ["in", party]}, pluck="name", limit=0
            )
        )
        missing = set(party) - existing
        if missing:
            frappe.throw(_("Invalid {0}: {1}").format(party_type, ", ".join(missing)))
