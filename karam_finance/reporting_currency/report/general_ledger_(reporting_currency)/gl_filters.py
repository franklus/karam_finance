"""Filter and account validation helpers for GL report."""

from __future__ import annotations

from typing import Any

import frappe
from erpnext.accounts.utils import get_account_currency
from frappe import _
from frappe.query_builder import Criterion
from frappe.utils import cint, getdate


def validate_filters(
    filters: Any, account_details: dict[str, frappe._dict[str, Any]]
) -> None:
    """Validate report filters and parse JSON fields."""
    filters.pop("show_amount_in_company_currency", None)
    _validate_required_dates(filters)

    _normalise_account_filters(filters, account_details)
    _normalise_list_filters(filters)
    _validate_options(filters)
    _validate_entry_type(filters)
    _validate_party_filter(filters)


def _validate_required_dates(filters: Any) -> None:
    """Validate the mandatory date range and its ordering."""
    if not filters.get("company"):
        frappe.throw(_("{0} is mandatory").format(_("Company")))

    for field in ("from_date", "to_date"):
        value = filters.get(field)
        if not value:
            frappe.throw(_("{0} is mandatory").format(field.replace("_", " ").title()))
        try:
            filters[field] = getdate(value)
        except TypeError, ValueError:
            frappe.throw(_("Invalid date for {0}").format(field))
    if filters.from_date > filters.to_date:
        frappe.throw(_("From Date must be before To Date"))


def _validate_options(filters: Any) -> None:
    modes = {
        "Flat Chronological",
        "Categorise by Account",
        "Group by Account w/ Opening",
        "Categorise by Party",
        "Categorise by Voucher",
        "Categorise by Voucher (Consolidated)",
    }
    mode = (
        _normalise_categorize_by(
            filters.get("categorize_by") or filters.get("group_by")
        )
        or "Flat Chronological"
    )
    if mode not in modes:
        frappe.throw(_("Invalid Categorise By option"))
    filters["categorize_by"] = mode
    for key in (
        "show_opening_entries",
        "disable_opening_balance_calculation",
        "include_default_book_entries",
        "show_cancelled_entries",
        "show_net_values_in_party_account",
        "include_dimensions",
        "add_values_in_transaction_currency",
        "show_source_currency_columns",
        "show_remarks",
        "ignore_err",
        "ignore_cr_dr_notes",
        "reporting_doe",
        "manual_entry",
        "exclude_reporting_doe",
        "exclude_manual_entries",
    ):
        filters[key] = cint(filters.get(key))


def _validate_party_filter(filters: Any) -> None:
    if filters.get("party") and not filters.get("party_type"):
        frappe.throw(_("Select Party Type when filtering by Party"))
    if filters.get("party_type") and filters.party_type not in frappe.get_all(
        "Party Type", pluck="name"
    ):
        frappe.throw(_("Invalid Party Type"))


def _validate_entry_type(filters: Any) -> None:
    entry_type = filters.get("entry_type") or "All"
    if filters.reporting_doe and filters.manual_entry:
        frappe.throw(_("Select one Entry Type: Reporting DOE or Manual"))
    legacy_type = (
        "Reporting DOE"
        if filters.reporting_doe
        else "Manual"
        if filters.manual_entry
        else None
    )
    if legacy_type and entry_type not in ("All", legacy_type):
        frappe.throw(_("Conflicting Entry Type filters"))
    filters["entry_type"] = legacy_type or entry_type
    if filters.entry_type not in ("All", "Synced GL", "Reporting DOE", "Manual"):
        frappe.throw(_("Invalid Entry Type"))


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
                "Reporting Currency GLE",
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
