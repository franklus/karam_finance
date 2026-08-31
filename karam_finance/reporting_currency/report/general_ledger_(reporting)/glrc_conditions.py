# ruff: noqa: D100, D103, ANN001, ANN201, PLC0415

from collections.abc import Iterable

import frappe
from erpnext.accounts.report.financial_statements import get_cost_centers_with_children
from frappe.query_builder import Criterion


def get_order_by_clause(filters: dict) -> str:
    categorize_by = filters.get("categorize_by", "")

    if categorize_by == "Categorise by Voucher":
        return "order by rc.posting_date, rc.voucher_type, rc.voucher_no"
    if categorize_by == "Categorise by Account":
        return "order by rc.account, rc.posting_date, rc.creation"
    if filters.get("include_dimensions"):
        return "order by rc.posting_date, rc.creation"

    return "order by rc.posting_date, rc.account, rc.creation"


def get_conditions(filters: dict) -> str:
    conditions = []
    ignore_is_opening = filters.get("_ignore_is_opening", False)

    conditions.extend(build_account_conditions(filters))
    conditions.extend(build_voucher_conditions(filters))
    conditions.extend(build_party_conditions(filters))
    conditions.extend(build_date_conditions(filters, ignore_is_opening))
    conditions.extend(build_system_conditions(filters))
    conditions.extend(build_reporting_conditions(filters))

    return "and {}".format(" and ".join(conditions)) if conditions else ""


def build_account_conditions(filters):
    conditions = []

    if filters.get("account"):
        filters.account = get_accounts_with_children(filters.account)
        if filters.account:
            conditions.append("rc.account in %(account)s")

    if filters.get("cost_center"):
        filters.cost_center = get_cost_centers_with_children(filters.cost_center)
        conditions.append("rc.cost_center in %(cost_center)s")

    if filters.get("project"):
        conditions.append("rc.project in %(project)s")

    return conditions


def build_voucher_conditions(filters):
    conditions = []

    if filters.get("voucher_no"):
        conditions.append("rc.voucher_no=%(voucher_no)s")

    if filters.get("against_voucher_no"):
        conditions.append("rc.against_voucher=%(against_voucher_no)s")

    if filters.get("ignore_err"):
        err_journals = frappe.db.get_all(
            "Journal Entry",
            filters={
                "company": filters.get("company"),
                "docstatus": 1,
                "voucher_type": (
                    "in",
                    ["Exchange Rate Revaluation", "Exchange Gain Or Loss"],
                ),
            },
            as_list=True,
        )
        if err_journals:
            filters.update({"voucher_no_not_in": [x[0] for x in err_journals]})

    if filters.get("voucher_no_not_in"):
        conditions.append("rc.voucher_no not in %(voucher_no_not_in)s")

    return conditions


def build_party_conditions(filters):
    conditions = []

    if filters.get("categorize_by") == "Categorise by Party" and not filters.get(
        "party_type"
    ):
        conditions.append("rc.party_type in ('Customer', 'Supplier')")

    if filters.get("party_type"):
        conditions.append("rc.party_type=%(party_type)s")

    if filters.get("party"):
        conditions.append("rc.party in %(party)s")

    return conditions


def build_date_conditions(filters, ignore_is_opening):
    conditions = []

    if not (
        filters.get("account")
        or filters.get("party")
        or filters.get("categorize_by")
        in ["Categorise by Account", "Categorise by Party"]
    ):
        if not ignore_is_opening:
            conditions.append(
                "(rc.posting_date >=%(from_date)s or rc.is_opening = 'Yes')"
            )
        else:
            conditions.append("rc.posting_date >=%(from_date)s")

    if not ignore_is_opening:
        conditions.append("(rc.posting_date <=%(to_date)s or rc.is_opening = 'Yes')")
    else:
        conditions.append("rc.posting_date <=%(to_date)s")

    return conditions


def build_system_conditions(filters):
    conditions = []

    if not filters.get("show_cancelled_entries"):
        conditions.append("rc.is_cancelled = 0")

    from frappe.desk.reportview import build_match_conditions

    match_conditions = build_match_conditions("Reporting Currency GLE")
    if match_conditions:
        conditions.append(match_conditions)

    return conditions


def build_reporting_conditions(filters):
    conditions = []

    if filters.get("reporting_doe"):
        conditions.append("rc.reporting_doe = 1")

    if filters.get("manual_entry"):
        conditions.append("rc.manual_entry = 1")

    return conditions


def get_accounts_with_children(accounts):
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


def chunked(values: list[str], size: int, default_size: int) -> Iterable[list[str]]:
    if size <= 0:
        size = default_size
    for idx in range(0, len(values), size):
        yield values[idx : idx + size]
