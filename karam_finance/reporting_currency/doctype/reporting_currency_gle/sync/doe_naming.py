"""Final DOE Record IDs and shared pair references."""

from collections import Counter
from typing import Any

import frappe
from frappe.utils import getdate


def assign_doe_record_names(records: list[dict[str, Any]]) -> None:
    """Reserve literal bases and surviving IDs before numbering collisions."""
    if not records:
        return
    account_names = sorted({row["account"] for row in records})
    accounts = {
        row.name: row
        for row in frappe.get_all(
            "Account",
            filters={"name": ["in", account_names]},
            fields=["name", "account_number", "account_type"],
            limit_page_length=len(account_names),
        )
    }
    bases = [_record_base(record, accounts[record["account"]]) for record in records]
    occupied = _occupied_names()
    names = _allocate_names(bases, occupied)
    for record, name in zip(records, names, strict=True):
        record["name"] = name


def _record_base(record: dict[str, Any], account: Any) -> str:
    date = getdate(record["posting_date"])
    if date is None:
        message = "Invalid DOE posting date"
        raise ValueError(message)
    base = f"DOE-{date:%d%m%Y}-{account.account_number or account.name}"
    if account.account_type in ("Payable", "Receivable") and record.get("party"):
        base += f"-{record['party']}"
    return base


def _allocate_names(bases: list[str], occupied: set[str]) -> list[str]:
    counts = Counter(base.casefold() for base in bases)
    reserved = occupied | set(counts)
    sequences: Counter[str] = Counter()
    names = []
    for base in bases:
        key = base.casefold()
        name = base
        if counts[key] > 1 or key in occupied:
            name = _next_name(base, sequences, reserved)
        if len(name) > 140:
            message = f"DOE Record ID exceeds 140 characters: {name}"
            raise ValueError(message)
        reserved.add(name.casefold())
        names.append(name)
    return names


def _next_name(base: str, sequences: Counter[str], reserved: set[str]) -> str:
    key = base.casefold()
    while True:
        sequences[key] += 1
        name = f"{base}-{sequences[key]:03d}"
        if name.casefold() not in reserved:
            return name


def _occupied_names() -> set[str]:
    occupied: set[str] = set()
    filters: list[list[Any]] = [["name", "like", "DOE-%"]]
    while True:
        # Permission-independent collision inventory, read in bounded keyset pages.
        # nosemgrep: frappe-n-plus-one-read-in-loop
        page = frappe.get_all(
            "Reporting Currency GLE",
            filters=filters,
            pluck="name",
            order_by="name asc",
            limit_page_length=500,
        )
        occupied.update(name.casefold() for name in page)
        if len(page) < 500:
            return occupied
        filters = [["name", "like", "DOE-%"], ["name", ">", page[-1]]]
