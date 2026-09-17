"""Authoritative, transaction-locked reconciliation selections."""

from __future__ import annotations

from decimal import Decimal, DecimalException
from typing import Any, cast

import frappe
from frappe import _
from frappe.utils import flt


def selected_ids(items: list[dict[str, Any]]) -> list[str]:
    names = [item.get("jv_row_name") for item in items]
    if any(not isinstance(name, str) or not name.strip() for name in names):
        frappe.throw(_("Every selected entry must have a valid journal row ID."))
    if len(set(names)) != len(names):
        frappe.throw(_("Duplicate journal row IDs are not allowed."))
    return cast("list[str]", names)


def load_selection(
    credit_items: list[dict[str, Any]],
    debit_items: list[dict[str, Any]],
    *,
    account: str | None,
    permission: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Lock parents before children, then validate only the current stored state."""
    names = selected_ids(credit_items + debit_items)
    parents = frappe.get_all(
        "Journal Entry Account",
        filters={"name": ["in", names], "parenttype": "Journal Entry"},
        pluck="parent",
        limit=0,
    )
    journals = _lock_journals(sorted(set(parents)), permission)
    rows = _lock_rows(names)
    if set(rows) != set(names):
        frappe.throw(
            _("A selected journal row no longer exists. Reload and try again.")
        )
    _validate_scope(list(rows.values()), journals, account)
    credit_rows = _side_rows(selected_ids(credit_items), rows, "credit")
    debits = _side_rows(selected_ids(debit_items), rows, "debit")
    return credit_rows, debits


def _lock_journals(names: list[str], permission: str) -> dict[str, Any]:
    journal = frappe.qb.DocType("Journal Entry")
    rows = (
        frappe.qb.from_(journal)
        .select(
            journal.name,
            journal.company,
            journal.posting_date,
            journal.docstatus,
            journal.voucher_type,
        )
        .where(journal.name.isin(names))
        .orderby(journal.name)
        .for_update()
    ).run(as_dict=True)
    for row in rows:
        if (
            row.docstatus != 1
            or row.voucher_type != "Journal Entry"
            or not row.posting_date
        ):
            frappe.throw(_("Only submitted Journal Entries can be reconciled."))
        frappe.has_permission("Journal Entry", permission, doc=row.name, throw=True)
    return {row.name: row for row in rows}


def _lock_rows(names: list[str]) -> dict[str, Any]:
    child = frappe.qb.DocType("Journal Entry Account")
    rows = (
        frappe.qb.from_(child)
        .select(child.star)
        .where(child.name.isin(names))
        .orderby(child.name)
        .for_update()
    ).run(as_dict=True)
    return {row.name: row for row in rows}


def _validate_scope(
    rows: list[dict[str, Any]], journals: dict[str, Any], account: str | None
) -> None:
    _validate_parent_rows(rows, journals)
    accounts = {row["account"] for row in rows}
    companies = {journals[row["parent"]].company for row in rows}
    if (
        len(accounts) != 1
        or len(companies) != 1
        or not all(accounts | companies)
        or (account and account not in accounts)
    ):
        frappe.throw(_("Select entries from one account and one company."))
    account_name = next(iter(accounts))
    company = next(iter(companies))
    frappe.has_permission("Account", doc=account_name, throw=True)
    frappe.has_permission("Company", doc=company, throw=True)
    if frappe.db.get_value("Account", account_name, "company") != company:
        frappe.throw(_("The selected account does not belong to the journal company."))
    for row in rows:
        row["posting_date"] = journals[row["parent"]].posting_date
        row["jv_row_name"] = row["name"]
        row["credit"] = row["credit_in_account_currency"]
        row["debit"] = row["debit_in_account_currency"]


def _validate_parent_rows(rows: list[dict[str, Any]], journals: dict[str, Any]) -> None:
    if any(
        row.get("parenttype") != "Journal Entry"
        or row.get("parentfield") != "accounts"
        or row.get("docstatus") != 1
        for row in rows
    ):
        frappe.throw(_("Only submitted Journal Entry account rows can be reconciled."))
    if any(row["parent"] not in journals for row in rows):
        frappe.throw(_("A selected journal changed. Reload and try again."))


def _side_rows(
    names: list[str], rows: dict[str, Any], side: str
) -> list[dict[str, Any]]:
    selected = [rows[name] for name in names]
    other = "debit" if side == "credit" else "credit"
    if any(flt(row[side]) <= 0 or flt(row[other]) != 0 for row in selected):
        frappe.throw(_("Selected entries must have the correct debit or credit side."))
    return selected


def validate_client_snapshot(
    items: list[dict[str, Any]], stored: list[dict[str, Any]], *, precision: int
) -> None:
    """Reject stale letters and forged amounts while allowing currency display rounding."""
    quantum = Decimal(1).scaleb(-precision)
    for item, row in zip(items, stored, strict=True):
        if (
            "letter" in item
            and str(item["letter"] or "").strip()
            != str(row.get("letter") or "").strip()
        ):
            frappe.throw(
                _("Selected letters changed. Reload the entries and try again.")
            )
        for field in ("credit", "debit"):
            if field in item and not _amount_matches(item[field], row[field], quantum):
                frappe.throw(
                    _(
                        "Selected amounts differ from the stored journal rows. Reload and try again."
                    )
                )


def _amount_matches(supplied: Any, stored: Any, quantum: Decimal) -> bool:
    try:
        value = Decimal(str(supplied or 0))
        return value.is_finite() and abs(value - Decimal(str(stored or 0))) < quantum
    except DecimalException:
        return False
