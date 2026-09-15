"""Validate DOE offsets before settings or generated entries can change."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _


def validate_offset_accounts(rows: list[Any], company: str | None = None) -> None:
    """Require eligible posting accounts in the reporting company's P&L tree."""
    if not rows:
        return
    names = {
        name for row in rows for name in (row.profit_account, row.loss_account) if name
    }
    accounts = {
        row.name: row
        for row in frappe.get_all(
            "Account",
            filters={"name": ["in", sorted(names)]},
            fields=["name", "company", "is_group", "disabled", "root_type"],
            limit=0,
        )
    }
    company = company or _configuration_company(accounts)
    for row in rows:
        for name in (row.profit_account, row.loss_account):
            _validate_offset(accounts.get(name), company, row.idx)


def _configuration_company(accounts: dict[str, Any]) -> str | None:
    companies = {row.company for row in accounts.values()}
    for doctype in ("GL Entry", "Reporting Currency GLE"):
        companies.update(
            # Exactly two metadata queries, each returning at most two companies.
            # nosemgrep: frappe-n-plus-one-read-in-loop
            frappe.db.get_all(
                doctype,
                pluck="company",
                distinct=True,
                limit=2,
            )
        )
    companies.discard(None)
    if len(companies) > 1:
        frappe.throw(
            _("DOE offset accounts must belong to the single reporting company.")
        )
    return next(iter(companies), None)


def _validate_offset(account: Any, company: str | None, index: int) -> None:
    eligible = (
        account
        and account.company == company
        and not account.is_group
        and not account.disabled
        and account.root_type in ("Income", "Expense")
    )
    if not eligible:
        frappe.throw(
            _(
                "Row {0}: DOE offset account must be an existing, enabled, non-group "
                "Income or Expense account belonging to company {1}."
            ).format(index, company or "(not configured)")
        )
