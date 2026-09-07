"""Filter and account validation helpers for GL report."""

from __future__ import annotations

from typing import Any

import frappe
from erpnext.accounts.utils import get_account_currency
from frappe import _
from frappe.query_builder import Criterion


def validate_filters(
    filters: Any, account_details: dict[str, frappe._dict[str, Any]]
) -> None:
    """Validate report filters and parse JSON fields."""
    filters.pop("show_amount_in_company_currency", None)
    _validate_required_dates(filters)

    _normalise_account_filters(filters, account_details)
    _normalise_list_filters(filters)
    _validate_grouping_filters(filters, account_details)


def _validate_required_dates(filters: Any) -> None:
    """Validate the mandatory date range and its ordering."""
    if not filters.get("company"):
        frappe.throw(_("{0} is mandatory").format(_("Company")))

    if not filters.get("from_date") and not filters.get("to_date"):
        frappe.throw(
            _("{0} and {1} are mandatory").format(
                frappe.bold(_("From Date")), frappe.bold(_("To Date"))
            )
        )

    if filters.from_date > filters.to_date:
        frappe.throw(_("From Date must be before To Date"))


def _normalise_account_filters(
    filters: Any, account_details: dict[str, frappe._dict[str, Any]]
) -> None:
    """Parse account filters and reject unknown accounts."""
    if not filters.get("account"):
        return

    filters.account = _as_list(filters.get("account"))
    for account in filters.account:
        if not account_details.get(account):
            frappe.throw(_("Account {0} does not exists").format(account))


def _normalise_list_filters(filters: Any) -> None:
    """Parse multi-select filters and categorisation aliases."""

    if not filters.get("categorize_by") and filters.get("group_by"):
        filters["categorize_by"] = _normalise_categorize_by(filters["group_by"])

    filters["categorize_by"] = _normalise_categorize_by(filters.get("categorize_by"))

    if filters.get("project"):
        filters.project = _as_list(filters.get("project"))

    if filters.get("cost_center"):
        filters.cost_center = _as_list(filters.get("cost_center"))


def _validate_grouping_filters(
    filters: Any, account_details: dict[str, frappe._dict[str, Any]]
) -> None:
    """Validate constraints imposed by the selected grouping."""
    if filters.get("account") and filters.get("categorize_by") in (
        "Categorise by Account",
        "Group by Account w/ Opening",
    ):
        filters.account = _as_list(filters.get("account"))
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


def _as_list(value: str | list[str] | tuple[str, ...] | set[str] | None) -> list[str]:
    """Normalise MultiSelectList values without re-parsing existing lists."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    parsed = frappe.parse_json(value)
    if isinstance(parsed, (list, tuple, set)):
        return list(parsed)
    return [parsed]


def _normalise_categorize_by(value: str | None) -> str | None:
    """Accept both the historical British and upstream American spellings."""
    if not value:
        return value
    return value.replace("Categorize", "Categorise")


def validate_party(filters: Any) -> None:
    """Validate that specified parties exist."""
    party_type, party = filters.get("party_type"), filters.get("party")

    if party and party_type:
        existing = set(
            frappe.get_all(
                party_type,
                filters={"name": ["in", party]},
                pluck="name",
                limit_page_length=len(party),
            )
        )
        missing = set(party) - existing
        if missing:
            frappe.throw(_("Invalid {0}: {1}").format(party_type, ", ".join(missing)))


def set_account_currency(filters: Any) -> Any:
    """Determine and set account currency based on filters."""
    if filters.get("account") or (filters.get("party") and len(filters.party) == 1):
        filters["company_currency"] = frappe.get_cached_value(
            "Company", filters.company, "default_currency"
        )
        account_currency = None

        if filters.get("account"):
            account_currency = _selected_accounts_currency(filters["account"])

        elif filters.get("party") and filters.get("party_type"):
            gle_currency = frappe.db.get_value(
                "GL Entry",
                {
                    "party_type": filters.party_type,
                    "party": filters.party[0],
                    "company": filters.company,
                },
                "account_currency",
            )

            account_currency = gle_currency or (
                None
                if filters.party_type in ("Employee", "Shareholder", "Member")
                else frappe.get_cached_value(
                    filters.party_type, filters.party[0], "default_currency"
                )
            )

        filters["account_currency"] = account_currency or filters.company_currency
        if (
            filters.account_currency != filters.company_currency
            and not filters.presentation_currency
        ):
            filters.presentation_currency = filters.account_currency

    return filters


def get_accounts_with_children(accounts: list[str] | str) -> list[str] | None:
    """Expand group accounts to include children; return leaf accounts as-is.

    Only expands accounts that are marked as is_group=1. Leaf accounts
    are returned without expansion to ensure precise filtering.
    """
    if not isinstance(accounts, list):
        accounts = [d.strip() for d in accounts.strip().split(",") if d]
    if not accounts:
        return None

    doctype = frappe.qb.DocType("Account")

    accounts_data = (
        frappe.qb.from_(doctype)
        .select(doctype.name, doctype.lft, doctype.rgt, doctype.is_group)
        .where(doctype.name.isin(accounts))
        .run(as_dict=True)
    )

    if not accounts_data:
        return None

    group_accounts = [acc for acc in accounts_data if acc.is_group]
    leaf_accounts = [acc.name for acc in accounts_data if not acc.is_group]

    if not group_accounts:
        return leaf_accounts

    conditions = [
        (doctype.lft >= acc.lft) & (doctype.rgt <= acc.rgt) for acc in group_accounts
    ]

    expanded_accounts = (
        frappe.qb.from_(doctype)
        .select(doctype.name)
        .where(Criterion.any(conditions))
        .run(pluck=True)
    )

    return list(set(expanded_accounts) | set(leaf_accounts))


def _selected_accounts_currency(accounts: list[str]) -> str | None:
    """Return the common currency only when all selected accounts agree."""
    currency = get_account_currency(accounts[0])
    if all(get_account_currency(account) == currency for account in accounts[1:]):
        return currency
    return None
