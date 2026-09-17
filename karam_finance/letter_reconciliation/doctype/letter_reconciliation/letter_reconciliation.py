"""Controller and helpers for Letter Reconciliation."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

import frappe
from frappe import _
from frappe.exceptions import ValidationError
from frappe.model.document import Document
from frappe.utils import cint, flt

from .selection import load_selection, validate_client_snapshot

# Maximum length for letter sequences (A -> ZZ -> ... -> ZZZZZZ)
MAX_LETTER_LENGTH = 6

LetterRow = dict[str, object]
PARTY_LOOKUP_BATCH_SIZE = 1000


class LetterReconciliation(Document):  # noqa: V102 - Frappe loads the DocType controller by name.
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
        except json.JSONDecodeError:
            frappe.throw(_("Invalid data received; please reload and try again."))

    if isinstance(parsed, (list, tuple)):
        items: list[LetterRow] = []
        for item in parsed:
            if isinstance(item, Mapping):
                items.append(dict(item))
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
    permission: str = "write",
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

    snapshot = cr_items + dt_items
    cr_items, dt_items = load_selection(
        cr_items, dt_items, account=account, permission=permission
    )
    account_for_precision = _extract_account_from_items(cr_items, dt_items)
    precision = _resolve_amount_precision(account_for_precision)
    validate_client_snapshot(snapshot, cr_items + dt_items, precision=precision)

    _validate_lettering_enabled_for_accounts(_selected_accounts(cr_items + dt_items))
    _validate_totals(
        cr_items, dt_items, account=account_for_precision, precision=precision
    )

    all_items = cr_items + dt_items
    letters = {_letter_text(i.get("letter")) for i in all_items}

    if require_letter is False:
        if letters - {""}:
            message = _(
                "One or more selected entries already have a letter. "
                "Please remove existing letters first."
            )
            frappe.throw(message)
        return cr_items, dt_items, all_items, None

    if require_letter is True:
        if len(letters) != 1 or "" in letters:
            message = _(
                "Letters can only be removed when all selected entries "
                "have the same letter."
            )
            frappe.throw(message)
        shared_letter = letters.pop()
        return cr_items, dt_items, all_items, shared_letter

    return cr_items, dt_items, all_items, None


def _letter_text(value: object) -> str:
    """Narrow external letter values before applying string operations."""
    if not value:
        return ""
    if isinstance(value, str):
        return value.strip()
    frappe.throw(_("Invalid data received; please reload and try again."))
    return ""


@frappe.whitelist()  # nosemgrep: frappe-missing-permission-check  # noqa: V103 - Desk calls this whitelisted endpoint by dotted path.
def get_adjacent_account(
    current_account: str = "",
    direction: str = "next",
    company: str = "",
) -> str | None:
    """Return the next or previous lettering-enabled account by account_number.

    Permission: Controlled by Letter Reconciliation doctype role permissions.
    """
    frappe.has_permission("Letter Reconciliation", "read", throw=True)
    if current_account:
        frappe.has_permission("Account", doc=current_account, throw=True)
    if company:
        frappe.has_permission("Company", doc=company, throw=True)
    if direction == "previous" and not current_account:
        return None

    filters: dict[str, Any] = {"enable_lettering": 1, "is_group": 0}
    if company:
        filters["company"] = company

    number_filter, order = _adjacent_account_position(current_account, direction)
    if not order:
        return None
    if number_filter:
        filters["account_number"] = number_filter

    result = frappe.get_list(
        "Account",
        filters=filters,
        fields=["name"],
        order_by=order,
        limit=1,
    )
    return result[0]["name"] if result else None


def _adjacent_account_position(
    current_account: str, direction: str
) -> tuple[list[str] | None, str]:
    if not current_account:
        return None, "account_number asc"
    number = frappe.db.get_value("Account", current_account, "account_number")
    if not number:
        return None, ""
    if direction == "next":
        return [">", number], "account_number asc"
    return ["<", number], "account_number desc"


@frappe.whitelist()  # nosemgrep: frappe-missing-permission-check  # noqa: V103 - Desk calls this whitelisted endpoint by dotted path.
# Public whitelisted API retains its positional argument contract.
def journal_entry_list(  # noqa: PLR0913, PLR0917
    account: str,
    start_date: str = "",
    end_date: str = "",
    party_type: str = "",
    party: str = "",
    show_letter: str = "",
) -> dict[str, list[dict[str, Any]] | str]:
    """Return credit/debit rows from Journal Entry Accounts for a given account.

    Filters by posting date range when provided.
    Only submitted Journal Entry rows are included.
    Uses Query Builder and returns two lists keyed as "cr" and "dr".

    Permission: Controlled by Letter Reconciliation doctype role permissions.
    """
    frappe.has_permission("Letter Reconciliation", "read", throw=True)
    frappe.has_permission("Account", doc=account, throw=True)
    try:
        query = _journal_entry_query(account)
        query = _filter_journal_entries(
            query,
            {
                "start_date": start_date,
                "end_date": end_date,
                "party_type": party_type,
                "party": party,
                "show_letter": show_letter,
            },
        )
        entries = query.run(as_dict=True)
        _attach_party_names(entries)

        return {
            "cr": [
                e for e in entries if (e.get("credit_in_account_currency") or 0) > 0
            ],
            "dr": [e for e in entries if (e.get("debit_in_account_currency") or 0) > 0],
        }
    except frappe.PermissionError:
        raise
    except Exception:  # noqa: BLE001 - API returns its established error response.
        frappe.log_error(frappe.get_traceback(), _("Journal Entry List Error"))
        return {"error": _("Unable to fetch journal entries. See error log.")}


def _journal_entry_query(account: str) -> Any:
    # Optional safety: ensure account is eligible for lettering
    account_info = frappe.db.get_value(
        "Account",
        account,
        ["name", "enable_lettering", "company"],
        as_dict=True,
    )
    if not account_info:
        frappe.throw(_("Selected account does not exist."))
    if not account_info.get("enable_lettering"):
        frappe.throw(_("Selected account is not enabled for lettering."))
    frappe.has_permission("Company", doc=account_info.get("company"), throw=True)

    accounts = frappe.qb.DocType("Journal Entry Account")
    journal_entry = frappe.qb.DocType("Journal Entry")
    permitted = frappe.qb.get_query(
        "Journal Entry", fields=["name"], ignore_permissions=False
    )

    return (
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
            & journal_entry.name.isin(permitted)
        )
        .orderby(journal_entry.posting_date)
    )


def _filter_journal_entries(query: Any, filters: dict[str, str]) -> Any:
    accounts = frappe.qb.DocType("Journal Entry Account")
    journal_entry = frappe.qb.DocType("Journal Entry")
    if filters["start_date"]:
        query = query.where(journal_entry.posting_date >= filters["start_date"])
    if filters["end_date"]:
        query = query.where(journal_entry.posting_date <= filters["end_date"])
    for field in ("party_type", "party"):
        if filters[field]:
            query = query.where(accounts[field] == filters[field])
    letter_filter = filters["show_letter"]
    if letter_filter == "Only unassigned rows":
        query = query.where(accounts.letter.isnull() | (accounts.letter == ""))
    elif letter_filter == "Only assigned rows":
        query = query.where(accounts.letter.isnotnull() & (accounts.letter != ""))
    return query


def _attach_party_names(entries: list[dict[str, Any]]) -> None:
    if not entries:
        return

    party_fields = {
        "Customer": "customer_name",
        "Supplier": "supplier_name",
        "Employee": "employee_name",
        "Shareholder": "title",
    }
    targets = _party_targets(entries)

    party_map = {
        party_type: _party_names(party_type, fieldname, targets.get(party_type, set()))
        for party_type, fieldname in party_fields.items()
        if targets.get(party_type)
    }
    for entry in entries:
        party_type = entry.get("party_type")
        party = entry.get("party")
        if party_type and party:
            entry["party_name"] = party_map.get(party_type, {}).get(party, "")
        else:
            entry["party_name"] = ""


def _party_targets(entries: list[dict[str, Any]]) -> dict[str, set[str]]:
    targets: dict[str, set[str]] = {}
    for entry in entries:
        party_type = entry.get("party_type")
        party = entry.get("party")
        if party_type and party:
            targets.setdefault(party_type, set()).add(party)

    return targets


def _party_names(party_type: str, fieldname: str, names: set[str]) -> dict[str, str]:
    records = []
    for chunk in _chunked(list(names), PARTY_LOOKUP_BATCH_SIZE):
        # One query per 1,000 distinct names; bounded bulk lookup, not per entry.
        # nosemgrep: frappe-n-plus-one-read-in-loop
        rows = frappe.get_all(
            party_type,
            filters={"name": ["in", chunk]},
            fields=["name", fieldname],
            limit=0,
        )
        records.extend(rows)
    return {row["name"]: row.get(fieldname) or "" for row in records if row.get("name")}


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
    frappe.has_permission("Letter Reconciliation", "read", throw=True)
    _prepare_letter_items(cr_items, dt_items, account=account, permission="read")


def _validate_totals(
    cr_items: list[dict[str, Any]],
    dt_items: list[dict[str, Any]],
    *,
    account: str | None,
    precision: int | None = None,
) -> None:
    """Compare authoritative account-currency amounts using currency precision."""
    try:
        precision = (
            precision if precision is not None else _resolve_amount_precision(account)
        )

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
            ).format(flt(str(cr_sum), precision), flt(str(dt_sum), precision))
            frappe.throw(message)
    except ValidationError:
        # Let specific validation bubble up unwrapped
        raise
    except Exception:  # noqa: BLE001 - validation boundary logs and translates invalid totals.
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
    frappe.has_permission("Letter Reconciliation", "write", throw=True)
    savepoint = "letter_reconciliation_assign"
    try:
        frappe.db.savepoint(savepoint)

        cr_items, dt_items, all_items, _common = _prepare_letter_items(
            cr_items, dt_items, require_letter=False, account=account
        )
        del _common  # unused

        # Compute latest year from posting_date across all selected items
        latest_year_val = _compute_latest_year(all_items)

        # Retrieve current letter for the computed year
        current_letter = _get_letter_for_year_locked(latest_year_val)

        # Advance letter for that year
        next_letter = increment_string(current_letter)
        if next_letter == current_letter:
            frappe.throw(_("The letter sequence for this year is exhausted."))
        _write_selected_letters(all_items, current_letter)
        update_year_letter(latest_year_val, next_letter)
    except frappe.QueryDeadlockError:
        frappe.db.rollback()
        raise frappe.ValidationError(
            _("Another reconciliation changed these records. Reload and try again.")
        ) from None
    except ValidationError, frappe.PermissionError:
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


@frappe.whitelist()  # nosemgrep: frappe-missing-permission-check  # noqa: V103 - Desk calls this whitelisted endpoint by dotted path.
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
    frappe.has_permission("Letter Reconciliation", "write", throw=True)
    savepoint = "letter_reconciliation_remove"
    try:
        frappe.db.savepoint(savepoint)

        cr_items, dt_items, all_items, _common = _prepare_letter_items(
            cr_items, dt_items, require_letter=True
        )
        del _common  # unused

        _write_selected_letters(all_items, "")
    except frappe.QueryDeadlockError:
        frappe.db.rollback()
        raise frappe.ValidationError(
            _("Another reconciliation changed these records. Reload and try again.")
        ) from None
    except ValidationError, frappe.PermissionError:
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


def _selected_accounts(
    items: list[dict[str, Any]], account: str | None = None
) -> set[str]:
    accounts = {
        (item.get("account") or "").strip() for item in items if item.get("account")
    }
    if account:
        accounts.add(account)
    return {name for name in accounts if name}


def _write_selected_letters(items: list[dict[str, Any]], letter: str) -> None:
    row_names = [name for item in items if (name := item.get("jv_row_name"))]
    if row_names:
        frappe.db.bulk_update(
            "Journal Entry Account", {name: {"letter": letter} for name in row_names}
        )
    _update_gl_letters(row_names, letter)


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
    years = [
        year
        for item in items
        if (year := _posting_year(item.get("posting_date"))) is not None
    ]
    return max(years) if years else frappe.utils.now_datetime().year


def _posting_year(value: object) -> int | None:
    if isinstance(value, str):
        year = value.split("-", 1)[0]
        return int(year) if year.isdigit() else None
    if isinstance(value, (date, datetime)):
        return value.year
    return None


def get_next_letter(year: int) -> str:  # noqa: V103 - retained public lettering utility.
    """Return the current letter for a year, defaulting to 'A'."""
    if not year:
        year = frappe.utils.now_datetime().year
    val = frappe.db.get_value("Letter Settings", str(year), "letter")
    return val or "A"


def update_year_letter(year: int, letter: str) -> None:
    """Create or update the Letter Settings record for the year."""
    docname = str(year)
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
    year_int = year or frappe.utils.now_datetime().year
    docname = str(year_int)

    # Insert first: a SELECT FOR UPDATE on a missing year creates competing gap
    # locks. The native upsert serialises first allocation as well as later ones.
    values = {
        "name": docname,
        "year": year_int,
        "now": frappe.utils.now(),
        "user": frappe.session.user,
    }
    frappe.db.multisql(
        {
            "mariadb": """INSERT INTO `tabLetter Settings`
            (name, year, letter, creation, modified, owner, modified_by, docstatus)
            VALUES (%(name)s, %(year)s, 'A', %(now)s, %(now)s, %(user)s, %(user)s, 0)
            ON DUPLICATE KEY UPDATE name=name""",
            "postgres": """INSERT INTO "tabLetter Settings"
            (name, year, letter, creation, modified, owner, modified_by, docstatus)
            VALUES (%(name)s, %(year)s, 'A', %(now)s, %(now)s, %(user)s, %(user)s, 0)
            ON CONFLICT (name) DO NOTHING""",
        },
        values,
    )
    row = frappe.db.sql(
        "select letter from `tabLetter Settings` where name=%s for update",
        (docname,),
        as_dict=True,
    )
    return (row[0].get("letter") or "A").upper()


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
    precision = _account_currency_precision(account)

    if precision is None:
        precision = frappe.get_precision("Journal Entry Account", "debit")

    if precision in (None, ""):
        precision = frappe.db.get_default("currency_precision")

    return max(0, cint(precision)) if precision not in (None, "") else 2


def _account_currency_precision(account: str | None) -> int | None:
    if not account:
        return None
    currency = frappe.db.get_value("Account", account, "account_currency")
    if not currency:
        return None
    fraction_units = frappe.db.get_value("Currency", currency, "fraction_units")
    if fraction_units in (None, ""):
        return None
    units = cint(fraction_units)
    return math.ceil(math.log10(units)) if units > 1 else 0


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
