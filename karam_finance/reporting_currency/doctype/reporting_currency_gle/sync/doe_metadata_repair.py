"""Preview and repair generated DOE metadata without recomputing ledger amounts.

These maintenance functions are deliberately not whitelisted. Apply requires the
fingerprint from an inspected preview, saves a private recovery file before any
update, and leaves commit/rollback ownership with the caller. Run during a pause
in sync/DOE jobs. Ambiguous pairs block apply rather than guessing their owner.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from operator import itemgetter
from pathlib import Path
from typing import Any
from uuid import uuid4

import frappe
from frappe import _

from .doe_legacy_reference import build_doe_voucher_no
from .doe_storage import DOCTYPE_RC_GLE

METADATA_FIELDS = ("party_type", "party", "against", "voucher_no", "is_opening")
SUMMARY_FIELDS = (
    "total_debit_default_currency",
    "total_credit_default_currency",
    "difference_default_currency",
    "reporting_debit_total",
    "reporting_credit_total",
    "difference_reporting_currency",
    "reporting_doe_difference",
)
PAIR_FIELDS = (
    "company",
    "posting_date",
    "fiscal_year",
    "reporting_currency",
    "voucher_type",
    "voucher_no",
)
NAME_PATTERN = re.compile(r"KE-RCDOE-GLE-(\d{4})-(\d+)")
AMOUNT_TOLERANCE = Decimal("0.0002")  # Summary fields are independently stored to 4dp.


def _amount(row: dict[str, Any], field: str) -> Decimal:
    return Decimal(str(row.get(field) or 0))


def preview_doe_metadata_repair(company: str) -> dict[str, Any]:
    """Return exact before/after metadata plus skipped rows; perform no writes."""
    return build_repair_preview(company, _read_rows(company))


def _read_rows(company: str, *, for_update: bool = False) -> list[dict[str, Any]]:
    # Fixed field selection; Query Builder binds the company value.
    fields = (
        "name",
        "company",
        "posting_date",
        "fiscal_year",
        "reporting_currency",
        "account",
        "account_currency",
        "reporting_doe",
        "manual_entry",
        "is_cancelled",
        "docstatus",
        "modified",
        "voucher_type",
        "debit",
        "credit",
        "debit_amount_in_account_currency",
        "credit_amount_in_account_currency",
        "reporting_debit",
        "reporting_credit",
        *METADATA_FIELDS,
        *SUMMARY_FIELDS,
    )
    ledger = frappe.qb.DocType(DOCTYPE_RC_GLE)
    account = frappe.qb.DocType("Account")
    query = (
        frappe.qb.from_(ledger)
        .left_join(account)
        .on(account.name == ledger.account)
        .select(
            *(ledger[field] for field in fields),
            account.account_type.as_("_account_type"),
            account.report_type.as_("_report_type"),
            account.account_number.as_("_account_number"),
        )
        .where((ledger.company == company) & (ledger.reporting_doe == 1))
        .orderby(ledger.name)
    )
    if for_update:
        query = query.for_update()
    rows = query.run(as_dict=True)
    # Canonicalise dates/Decimals once for portable previews and recovery files.
    return json.loads(json.dumps(rows, default=str))


def _fingerprint(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(sorted(rows, key=itemgetter("name")), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _row_error(row: dict[str, Any]) -> str | None:
    if row.get("manual_entry") or row.get("is_cancelled") or row.get("docstatus") != 1:
        return "Not an active submitted generated DOE record"
    if row.get("_report_type") is None:
        return "Account metadata missing"
    if row.get("is_opening") not in (None, "", "No"):
        return "Explicit opening flag needs separate review"
    if any(
        _amount(row, field)
        for field in (
            "debit",
            "credit",
            "debit_amount_in_account_currency",
            "credit_amount_in_account_currency",
        )
    ):
        return "DOE contains non-zero source-currency amounts"
    debit, credit = _amount(row, "reporting_debit"), _amount(row, "reporting_credit")
    if debit < 0 or credit < 0 or (debit and credit):
        return "DOE must have one non-negative debit or credit"
    return None


def _context_error(primary: dict[str, Any], offset: dict[str, Any]) -> str | None:
    if any(
        primary.get(field) != offset.get(field)
        for field in (*PAIR_FIELDS, *SUMMARY_FIELDS)
    ):
        return "Adjacent records do not share adjustment context"
    if (
        offset["_report_type"] != "Profit and Loss"
        or primary["account"] == offset["account"]
    ):
        return "Counterentry is not a distinct income/expense account"
    if primary.get("against") != offset["account"]:
        return "Primary Against does not identify the counterentry account"
    if offset.get("against") not in (primary["account"], primary.get("party")):
        return "Counterentry Against does not identify the primary account or party"
    return None


def _pair_error(primary: dict[str, Any], offset: dict[str, Any]) -> str | None:
    error = _row_error(primary) or _row_error(offset) or _context_error(primary, offset)
    if error:
        return error

    amount = _amount(primary, "reporting_debit") - _amount(primary, "reporting_credit")
    reverse = _amount(offset, "reporting_credit") - _amount(offset, "reporting_debit")
    expected = _amount(primary, "reporting_doe_difference") - _amount(
        primary, "difference_reporting_currency"
    )
    if not amount or amount != reverse or abs(amount - expected) > AMOUNT_TOLERANCE:
        return "Amounts do not identify a balanced primary/counterentry pair"

    if bool(primary.get("party")) != bool(primary.get("party_type")):
        return "Incomplete primary party context"
    for field in ("party", "party_type"):
        if offset.get(field) and offset[field] != primary.get(field):
            return "Counterentry party differs from the primary party"
    return None


def build_repair_preview(company: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Identify consecutive generated pairs, validating their accounting context."""
    changes = []
    skipped = []
    by_name = {row["name"]: row for row in rows}
    visited = set()
    pair_count = 0
    if any(row["company"] != company for row in rows):
        frappe.throw(_("DOE preview contains records from a different company."))
    for primary in sorted(rows, key=_sequence_key):
        name = primary["name"]
        if name in visited:
            continue
        offset = _find_offset(name, by_name)
        error = (
            _pair_error(primary, offset)
            if offset
            else "Consecutive generated counterentry missing"
        )
        if error:
            skipped.append({"name": name, "reason": error})
            visited.add(name)
            continue
        visited.update((name, offset["name"]))
        pair_count += 1
        changes.extend(_pair_changes(primary, offset))
    projected = _project_rows(rows, changes)
    return {
        "company": company,
        "row_count": len(rows),
        "pair_count": pair_count,
        "fingerprint": _fingerprint(rows),
        "projected_fingerprint": _fingerprint(projected),
        "changes": changes,
        "skipped": skipped,
    }


def _pair_changes(
    primary: dict[str, Any], offset: dict[str, Any]
) -> list[dict[str, Any]]:
    changes = []
    is_party_account = primary["_account_type"] in ("Receivable", "Payable")
    party = primary.get("party") if is_party_account else None
    party_type = primary.get("party_type") if is_party_account else None
    voucher = build_doe_voucher_no(
        primary.get("_account_number") or primary["account"],
        party_type,
        party,
        posting_date=primary["posting_date"],
        pair_name=primary["name"],
    )
    for row, desired in (
        (
            primary,
            {
                "party": party,
                "party_type": party_type,
                "against": offset["account"],
            },
        ),
        (
            offset,
            {
                "party": None,
                "party_type": None,
                "against": party or primary["account"],
            },
        ),
    ):
        desired.update(voucher_no=voucher, is_opening="No")
        changes.extend(_metadata_change(row, desired))
    return changes


def _metadata_change(
    row: dict[str, Any], desired: dict[str, Any]
) -> list[dict[str, Any]]:
    after = {key: value for key, value in desired.items() if row.get(key) != value}
    if not after:
        return []
    return [
        {
            "name": row["name"],
            "before": {key: row.get(key) for key in after},
            "after": after,
        }
    ]


def _find_offset(name: str, by_name: dict[str, Any]) -> Any:
    match = NAME_PATTERN.fullmatch(name)
    if not match:
        return None
    year, sequence = match.groups()
    return by_name.get(f"KE-RCDOE-GLE-{year}-{int(sequence) + 1:05d}")


def _sequence_key(row: dict[str, Any]) -> tuple[str, int]:
    match = NAME_PATTERN.fullmatch(row["name"])
    return (match[1], int(match[2])) if match else (row["name"], 0)


def _project_rows(
    rows: list[dict[str, Any]], changes: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    updates = {change["name"]: change["after"] for change in changes}
    return [{**row, **updates.get(row["name"], {})} for row in rows]


def apply_doe_metadata_repair(
    company: str, expected_fingerprint: str
) -> dict[str, Any]:
    """Apply a reviewed snapshot and save recovery data; caller owns the commit."""
    preview = build_repair_preview(company, _read_rows(company, for_update=True))
    if preview["fingerprint"] != expected_fingerprint:
        frappe.throw(
            _("DOE records changed since preview. Generate and review a fresh preview.")
        )
    if preview["skipped"]:
        frappe.throw(
            _("Unmatched DOE records need review before applying metadata repair.")
        )
    if not preview["changes"]:
        return {"updated": 0, "recovery_file": None}

    directory = Path(frappe.get_site_path("private", "backups"))
    directory.mkdir(parents=True, exist_ok=True)
    recovery_file = directory / f"doe-metadata-recovery-{uuid4().hex}.json"
    # Create privately before writing any recovery contents or changing any row.
    recovery_file.touch(mode=0o600, exist_ok=False)
    recovery_file.write_text(json.dumps(preview, indent=2), encoding="utf-8")
    _write_metadata(preview["changes"], company, preview["projected_fingerprint"])
    return {"updated": len(preview["changes"]), "recovery_file": str(recovery_file)}


def restore_doe_metadata(recovery_file: str) -> dict[str, int]:
    """Restore only an unchanged repaired snapshot; caller owns the commit."""
    preview = json.loads(Path(recovery_file).read_text(encoding="utf-8"))
    rows = _read_rows(preview["company"], for_update=True)
    if _fingerprint(rows) != preview["projected_fingerprint"]:
        frappe.throw(
            _("DOE records changed after repair; automatic restoration stopped.")
        )
    changes = [{"name": c["name"], "after": c["before"]} for c in preview["changes"]]
    _write_metadata(changes, preview["company"], preview["fingerprint"])
    return {"restored": len(changes)}


def _write_metadata(
    changes: list[dict[str, Any]], company: str, expected_fingerprint: str
) -> None:
    for change in changes:
        if set(change["after"]) - set(METADATA_FIELDS):
            frappe.throw(
                _("DOE metadata repair cannot alter monetary or source fields.")
            )
    frappe.db.savepoint("doe_metadata_repair")
    try:
        frappe.db.bulk_update(
            DOCTYPE_RC_GLE,
            {change["name"]: change["after"] for change in changes},
            chunk_size=100,
            update_modified=False,
        )
        if _fingerprint(_read_rows(company)) != expected_fingerprint:
            frappe.throw(_("DOE metadata verification failed; repair rolled back."))
    except Exception:
        frappe.db.rollback(save_point="doe_metadata_repair")
        raise
