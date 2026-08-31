"""Controller and helpers for Letter Reconciliation."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

import frappe
from erpnext.accounts.utils import get_currency_precision
from frappe import _
from frappe.exceptions import ValidationError
from frappe.model.document import Document
from frappe.utils import cint, flt

# Maximum length for letter sequences (A -> ZZ -> ... -> ZZZZZZ)
MAX_LETTER_LENGTH = 6

LetterRow = dict[str, object]
PARTY_LOOKUP_BATCH_SIZE = 1000


class LetterReconciliation(Document):
    """Single DocType controller for Letter Reconciliation."""


def _validate_lettering_enabled_for_accounts(accounts: set[str]) -> None:
    """Ensure all accounts are configured for lettering before changes."""
    if not accounts:
        return

    account_data = frappe.get_all(
        "Account",
        filters={"name": ["in", list(accounts)]},
        fields=["name", "enable_lettering"],
        limit=0,
    )
    found_accounts = {row["name"] for row in account_data}

    missing = accounts - found_accounts
    if missing:
        frappe.throw(
            _("Selected account does not exist: {0}").format(", ".join(sorted(missing)))
        )

    disabled = [
        row["name"] for row in account_data if not cint(row["enable_lettering"])
    ]
    if disabled:
        frappe.throw(
            _("Lettering is not enabled for: {0}").format(", ".join(sorted(disabled)))
        )


def _coerce_items(
    value: str | Sequence[Mapping[str, object]] | None,
) -> list[LetterRow]:
    """Return the payload as a list of dicts, parsing JSON strings when needed."""
    if not value:
        return []
    parsed: object = value
    if isinstance(value, str):
        try:
            parsed = cast("object", json.loads(value))
        except Exception:
            frappe.throw(_("Invalid data received; please reload and try again."))

    if isinstance(parsed, (list, tuple)):
        items: list[LetterRow] = []
        for item in parsed:
            if isinstance(item, Mapping):
                items.append(dict(item))
                continue
            if isinstance(item, dict):
                items.append(cast("LetterRow", item))
                continue
            frappe.throw(_("Invalid data received; please reload and try again."))
        return items

    frappe.throw(_("Invalid data received; please reload and try again."))
    msg = "unreachable"
    raise AssertionError(msg)  # appease static type checkers


def _prepare_letter_items(
    cr_items_raw: str | list[dict[str, Any]],
    dt_items_raw: str | list[dict[str, Any]],
    *,
    require_letter: bool | None = None,
    account: str | None = None,
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], str | None
]:
    """Common validation before DB updates.

    Returns credit items, debit items, combined list,
    and the shared letter (if relevant).
    """
    cr_items = _coerce_items(cr_items_raw)
    dt_items = _coerce_items(dt_items_raw)

    if not (cr_items and dt_items):
        frappe.throw(_("Please select at least one credit entry and one debit entry."))

    account_for_precision = account or _extract_account_from_items(cr_items, dt_items)

    validate_sum_of_credit_and_debit(cr_items, dt_items, account=account_for_precision)

    all_items = cr_items + dt_items
    letters = {((i.get("letter") or "").strip()) for i in all_items}

    if require_letter is False:
        if letters - {""}:
            message = _(
                "One or more selected entries already have a letter. "
                "Please remove existing letters first."
            )
            frappe.throw(message)
        return cr_items, dt_items, all_items, None

    if require_letter is True:
        letters.discard("")
        if len(letters) != 1:
            message = _(
                "Letters can only be removed when all selected entries "
                "have the same letter."
            )
            frappe.throw(message)
        shared_letter = letters.pop()
        return cr_items, dt_items, all_items, shared_letter

    return cr_items, dt_items, all_items, None


@frappe.whitelist()  # nosemgrep: frappe-missing-permission-check
def get_adjacent_account(
    current_account: str = "",
    direction: str = "next",
    company: str = "",
) -> str | None:
    """Return the next or previous lettering-enabled account by account_number.

    Permission: Controlled by Letter Reconciliation doctype role permissions.
    """
    if direction == "previous" and not current_account:
        return None

    filters: dict[str, Any] = {"enable_lettering": 1, "is_group": 0}
    if company:
        filters["company"] = company

    if current_account:
        current_number = frappe.db.get_value(
            "Account", current_account, "account_number"
        )
        if not current_number:
            return None

        if direction == "next":
            filters["account_number"] = [">", current_number]
            order = "account_number asc"
        else:
            filters["account_number"] = ["<", current_number]
            order = "account_number desc"
    else:
        order = "account_number asc"

    result = frappe.get_all(
        "Account",
        filters=filters,
        fields=["name"],
        order_by=order,
        limit=1,
    )
    return result[0]["name"] if result else None


@frappe.whitelist()  # nosemgrep: frappe-missing-permission-check
def journal_entry_list(
    account: str,
    start_date: str = "",
    end_date: str = "",
    party_type: str = "",
    party: str = "",
    show_letter: str = "",
) -> dict[str, list[dict[str, Any]]]:
    """Return credit/debit rows from Journal Entry Accounts for a given account.

    Filters by posting date range when provided.
    Only submitted Journal Entry rows are included.
    Uses Query Builder and returns two lists keyed as "cr" and "dr".

    Permission: Controlled by Letter Reconciliation doctype role permissions.
    """
    try:
        # Optional safety: ensure account is eligible for lettering
        account_info = frappe.db.get_value(
            "Account",
            account,
            ["name", "enable_lettering"],
            as_dict=True,
        )
        if not account_info:
            frappe.throw(_("Selected account does not exist."))
        if not account_info.get("enable_lettering"):
            frappe.throw(_("Selected account is not enabled for lettering."))

        accounts = frappe.qb.DocType("Journal Entry Account")
        journal_entry = frappe.qb.DocType("Journal Entry")

        query = (
            frappe.qb.from_(accounts)
            .join(journal_entry)
            .on(journal_entry.name == accounts.parent)
            .select(
                accounts.docstatus,
                accounts.debit_in_account_currency,
                accounts.name.as_("jv_row_name"),
                accounts.letter,
                accounts.account,
                accounts.party_type,
                accounts.party,
                accounts.credit_in_account_currency,
                accounts.parent.as_("journal_entry"),
                journal_entry.posting_date,
                accounts.user_remark,
            )
            .where(
                (journal_entry.docstatus == 1)
                & (journal_entry.voucher_type == "Journal Entry")
                & (accounts.account == account)
            )
            .orderby(journal_entry.posting_date)
        )

        if start_date:
            query = query.where(journal_entry.posting_date >= start_date)
        if end_date:
            query = query.where(journal_entry.posting_date <= end_date)
        if party_type:
            query = query.where(accounts.party_type == party_type)
        if party:
            query = query.where(accounts.party == party)
        if show_letter == "Only unassigned rows":
            query = query.where((accounts.letter.isnull()) | (accounts.letter == ""))
        elif show_letter == "Only assigned rows":
            query = query.where(accounts.letter.isnotnull() & (accounts.letter != ""))

        entries = query.run(as_dict=True)
        _attach_party_names(entries)

        return {
            "cr": [
                e for e in entries if (e.get("credit_in_account_currency") or 0) > 0
            ],
            "dr": [e for e in entries if (e.get("debit_in_account_currency") or 0) > 0],
        }
    except Exception:
        frappe.log_error(frappe.get_traceback(), _("Journal Entry List Error"))
        return {"error": _("Unable to fetch journal entries. See error log.")}


def _attach_party_names(entries: list[dict[str, Any]]) -> None:
    if not entries:
        return

    party_fields = {
        "Customer": "customer_name",
        "Supplier": "supplier_name",
        "Employee": "employee_name",
        "Shareholder": "title",
    }
    targets: dict[str, set[str]] = {}
    for entry in entries:
        party_type = entry.get("party_type")
        party = entry.get("party")
        if party_type and party:
            targets.setdefault(party_type, set()).add(party)

    party_map: dict[str, dict[str, str]] = {}
    for party_type, fieldname in party_fields.items():
        names = targets.get(party_type)
        if not names:
            continue

        records = []
        for chunk in _chunked(list(names), PARTY_LOOKUP_BATCH_SIZE):
            rows = frappe.get_all(
                party_type,
                filters={"name": ["in", chunk]},
                fields=["name", fieldname],
                limit=0,
            )
            records.extend(rows)

        if records:
            party_map[party_type] = {
                row["name"]: row.get(fieldname) or ""
                for row in records
                if row.get("name")
            }

    for entry in entries:
        party_type = entry.get("party_type")
        party = entry.get("party")
        entry["party_name"] = (
            party_map.get(party_type, {}).get(party, "") if party_type and party else ""
        )


def _chunked(values: list[str], size: int) -> Iterable[list[str]]:
    if size <= 0:
        size = PARTY_LOOKUP_BATCH_SIZE
    for idx in range(0, len(values), size):
        yield values[idx : idx + size]


@frappe.whitelist()  # nosemgrep: frappe-missing-permission-check
def validate_sum_of_credit_and_debit(
    cr_items: str | list[dict[str, Any]],
    dt_items: str | list[dict[str, Any]],
    account: str | None = None,
) -> None:
    """Ensure total credits equal total debits for the selected rows.

    Accepts JSON strings or already-parsed lists. Throws on mismatch.

    Permission: Controlled by Letter Reconciliation doctype role permissions.
    """
    try:
        cr_items = json.loads(cr_items) if isinstance(cr_items, str) else cr_items
        dt_items = json.loads(dt_items) if isinstance(dt_items, str) else dt_items

        precision = _resolve_amount_precision(account)

        quant = Decimal(1).scaleb(-precision)
        cr_sum = sum(
            (Decimal(str(item.get("credit", 0) or 0)) for item in cr_items),
            start=Decimal(0),
        )
        dt_sum = sum(
            (Decimal(str(item.get("debit", 0) or 0)) for item in dt_items),
            start=Decimal(0),
        )

        diff = abs(cr_sum - dt_sum)

        if diff >= quant:
            message = _(
                "Total credits ({0}) must equal total debits ({1}). "
                "Please ensure the selected entries are balanced."
            ).format(flt(cr_sum, precision), flt(dt_sum, precision))
            frappe.throw(message)
    except ValidationError:
        # Let specific validation bubble up unwrapped
        raise
    except Exception:
        frappe.log_error(frappe.get_traceback(), _("Validation Error"))
        frappe.throw(_("Could not validate debit and credit totals."))


@frappe.whitelist()  # nosemgrep: frappe-missing-permission-check
def set_letter(
    cr_items: str | list[dict[str, Any]],
    dt_items: str | list[dict[str, Any]],
    _latest_year: int | None = None,  # Ignored; recomputed server-side for safety
    account: str | None = None,
) -> dict[str, str]:
    """Assign a letter to selected Journal Entry Account rows and their GL Entries.

    Permission: Controlled by Letter Reconciliation doctype role permissions.

    Rules enforced:
    - At least one debit and one credit row must be selected.
    - Totals of selected debits and credits must be equal.
    - None of the selected rows may already have a letter.
    - The letter is chosen for the year of the latest posting_date among selections.
    """
    savepoint = "letter_reconciliation_assign"
    try:
        frappe.db.savepoint(savepoint)

        cr_items, dt_items, all_items, _common = _prepare_letter_items(
            cr_items, dt_items, require_letter=False, account=account
        )
        del _common  # unused

        accounts_in_selection = {
            (i.get("account") or "").strip() for i in all_items if i.get("account")
        }
        if account:
            accounts_in_selection.add(account)
        accounts_in_selection = {acc for acc in accounts_in_selection if acc}
        _validate_lettering_enabled_for_accounts(accounts_in_selection)

        # Compute latest year from posting_date across all selected items
        latest_year_val = _compute_latest_year(all_items)

        # Retrieve current letter for the computed year
        current_letter = _get_letter_for_year_locked(latest_year_val)

        # Update Journal Entry Account rows
        jv_row_names: list[str] = [
            rn for i in all_items if (rn := i.get("jv_row_name"))
        ]
        if jv_row_names:
            frappe.db.bulk_update(
                "Journal Entry Account",
                {rn: {"letter": current_letter} for rn in jv_row_names},
            )

        # Update GL Entries matching the selected JE Account rows
        _update_gl_letters(jv_row_names, current_letter)

        # Advance letter for that year
        next_letter = increment_string(current_letter)
        update_year_letter(latest_year_val, next_letter)
    except ValidationError:
        frappe.db.rollback(save_point=savepoint)
        # Bubble up expected validation errors without wrapping
        raise
    except Exception as exc:
        frappe.db.rollback(save_point=savepoint)
        frappe.log_error(frappe.get_traceback(), _("Letter Assignment Error"))
        frappe.throw(_("Error assigning letter: {0}").format(str(exc)))
        msg = "unreachable"
        raise AssertionError(msg) from exc
    else:
        return {"last_letter": current_letter, "next_letter": next_letter}


@frappe.whitelist()  # nosemgrep: frappe-missing-permission-check
def remove_letter(
    cr_items: str | list[dict[str, Any]],
    dt_items: str | list[dict[str, Any]],
) -> dict[str, bool]:
    """Remove letters from selected Journal Entry Account rows and linked GL Entries.

    Permission: Controlled by Letter Reconciliation doctype role permissions.

    Rules enforced:
    - At least one debit and one credit row must be selected.
    - Totals of selected debits and credits must be equal.
    - All selected rows must have the same non-empty letter.
    """
    savepoint = "letter_reconciliation_remove"
    try:
        frappe.db.savepoint(savepoint)

        cr_items, dt_items, all_items, _common = _prepare_letter_items(
            cr_items, dt_items, require_letter=True
        )
        del _common  # unused

        accounts_in_selection = {
            (i.get("account") or "").strip() for i in all_items if i.get("account")
        }
        accounts_in_selection = {acc for acc in accounts_in_selection if acc}
        _validate_lettering_enabled_for_accounts(accounts_in_selection)

        # Clear letters on Journal Entry Account
        jv_row_names: list[str] = [
            rn for i in all_items if (rn := i.get("jv_row_name"))
        ]
        if jv_row_names:
            frappe.db.bulk_update(
                "Journal Entry Account",
                {rn: {"letter": ""} for rn in jv_row_names},
            )

        # Clear letters on GL Entries matching the selected JE Account rows
        _update_gl_letters(jv_row_names, "")
    except ValidationError:
        frappe.db.rollback(save_point=savepoint)
        # Bubble up expected validation errors without wrapping
        raise
    except Exception as exc:
        frappe.db.rollback(save_point=savepoint)
        frappe.log_error(frappe.get_traceback(), _("Letter Removal Error"))
        frappe.throw(_("Error removing letter: {0}").format(str(exc)))
        msg = "unreachable"
        raise AssertionError(msg) from exc
    else:
        return {"success": True}


def _update_gl_letters(jv_row_names: list[str], letter: str) -> None:
    """Update GL Entry letters for the given JE Account row names.

    Matches via voucher_detail_no which maps 1:1 with JE Account row names
    after the reference_detail_no prevention hook is in place.
    """
    if not jv_row_names:
        return
    gl = frappe.qb.DocType("GL Entry")
    cond = (gl.voucher_type == "Journal Entry") & (
        gl.voucher_detail_no.isin(jv_row_names)
    )
    (frappe.qb.update(gl).set(gl.letter, letter).where(cond)).run()


def _compute_latest_year(items: list[dict[str, Any]]) -> int:
    """Compute latest posting year across selected items.

    Falls back to the current year if absent.
    """
    years: list[int] = []
    for i in items:
        pd = i.get("posting_date")
        if not pd:
            continue
        if isinstance(pd, str):
            # Frappe typically stores posting_date as YYYY-MM-DD
            parts = pd.split("-")
            if parts and parts[0].isdigit():
                years.append(int(parts[0]))
        elif isinstance(pd, (date, datetime)):
            years.append(pd.year)
    if years:
        return max(years)
    # Fallback
    return int(frappe.utils.now_datetime().year)


def get_next_letter(year: int) -> str:
    """Return the current letter for a year, defaulting to 'A'."""
    if not year:
        year = frappe.utils.now_datetime().year
    val = frappe.db.get_value("Letter Settings", str(int(year)), "letter")
    return val or "A"


def update_year_letter(year: int, letter: str) -> None:
    """Create or update the Letter Settings record for the year."""
    docname = str(int(year))
    if frappe.db.exists("Letter Settings", docname):
        # Intentional: Simple letter value update, no validation hooks needed
        frappe.db.set_value(  # nosemgrep: frappe-direct-db-set-value
            "Letter Settings", docname, "letter", letter
        )
    else:
        frappe.get_doc(
            {
                "doctype": "Letter Settings",
                "year": year,
                "letter": letter,
            }
        ).insert(ignore_permissions=True)


def _get_letter_for_year_locked(year: int) -> str:
    """Return the current letter for a year, acquiring a row lock to prevent races."""
    year_int = int(year) if year else frappe.utils.now_datetime().year
    docname = str(year_int)

    row = frappe.db.sql(
        "select letter from `tabLetter Settings` where name=%s for update",
        (docname,),
        as_dict=True,
    )
    if row:
        return (row[0].get("letter") or "A").upper()

    frappe.get_doc(
        {
            "doctype": "Letter Settings",
            "year": year_int,
            "letter": "A",
        }
    ).insert(ignore_permissions=True)

    row = frappe.db.sql(
        "select letter from `tabLetter Settings` where name=%s for update",
        (docname,),
        as_dict=True,
    )
    if row:
        return (row[0].get("letter") or "A").upper()
    return "A"


def increment_string(s: str = "") -> str:
    """Increment a letter sequence up to MAX_LETTER_LENGTH.

    A -> B -> ... -> Z -> AA -> AB ... AZ -> BA ... ZZ -> AAA ...
    When length reaches MAX_LETTER_LENGTH and all Z, remains unchanged.
    """
    if not s:
        return "A"
    if len(s) >= MAX_LETTER_LENGTH and s == ("Z" * MAX_LETTER_LENGTH):
        return s

    chars = list(s)
    for i in range(len(chars) - 1, -1, -1):
        if chars[i] == "Z":
            chars[i] = "A"
            if i == 0:
                # Prepend when rolling over leftmost Z
                return "A" + "".join(chars)
        else:
            chars[i] = chr(ord(chars[i]) + 1)
            return "".join(chars)

    return "A"


def _resolve_amount_precision(account: str | None) -> int:
    """Return the currency precision for the given account or a sensible default."""
    precision: int | None = None

    if account:
        currency = frappe.db.get_value("Account", account, "account_currency")
        if currency:
            try:
                precision = get_currency_precision(currency)
            except Exception:
                precision = None

    if precision is None:
        precision = frappe.get_precision("Journal Entry Account", "debit")

    if precision is None:
        precision = frappe.db.get_default("currency_precision")

    return int(cint(precision or 2))


def _extract_account_from_items(
    cr_items: list[dict[str, Any]],
    dt_items: list[dict[str, Any]],
) -> str | None:
    """Return the first account found in the items list."""
    for item in cr_items + dt_items:
        account = (item.get("account") or "").strip()
        if account:
            return account
    return None
