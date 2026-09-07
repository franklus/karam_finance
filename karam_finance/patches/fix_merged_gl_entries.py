"""Split merged GL entries and backfill voucher_detail_no.

When multiple Journal Entry Account rows share the same account+party,
ERPNext's merge_similar_entries() collapses them into a single GL Entry
if reference_detail_no is empty.  This patch:

1. Backfills reference_detail_no on all JE Account rows (= row name).
2. Backfills voucher_detail_no on non-merged GL entries (1:1 mapping).
3. Splits merged GL entries into individual rows matching JE Account data.
4. Syncs letters from JE Account to the resulting GL entries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import frappe

if TYPE_CHECKING:
    from erpnext.accounts.doctype.gl_entry.gl_entry import GLEntry

type _GLRow = dict[str, Any]
type _MergeKey = tuple[str, str, str, str, str]


def execute() -> None:
    """Split merged GL entries and backfill voucher_detail_no."""
    _backfill_reference_detail_no()
    _backfill_voucher_detail_no_single_match()
    _split_merged_gl_entries()
    _fix_unrenamed_gl_entries()


def _backfill_reference_detail_no() -> None:
    """Set reference_detail_no = name on all JE Account rows where it is empty."""
    frappe.db.sql(
        """
        UPDATE `tabJournal Entry Account`
        SET reference_detail_no = name
        WHERE reference_detail_no IS NULL OR reference_detail_no = ''
        """
    )


def _backfill_voucher_detail_no_single_match() -> None:
    """Backfill voucher_detail_no on GL entries with a 1:1 JE Account mapping.

    Only updates GL entries where exactly one JE Account row matches on
    (voucher_no, account, party_type, party, cost_center).
    """
    frappe.db.sql(
        """
        UPDATE `tabGL Entry` gl
        INNER JOIN (
            SELECT
                jea.parent,
                jea.account,
                IFNULL(jea.party_type, '') AS party_type,
                IFNULL(jea.party, '') AS party,
                IFNULL(jea.cost_center, '') AS cost_center,
                jea.name AS jea_name,
                jea.letter
            FROM `tabJournal Entry Account` jea
            INNER JOIN (
                SELECT parent, account,
                       IFNULL(party_type, '') AS party_type,
                       IFNULL(party, '') AS party,
                       IFNULL(cost_center, '') AS cost_center
                FROM `tabJournal Entry Account`
                GROUP BY parent, account,
                         IFNULL(party_type, ''),
                         IFNULL(party, ''),
                         IFNULL(cost_center, '')
                HAVING COUNT(*) = 1
            ) singles
                ON jea.parent = singles.parent
                AND jea.account = singles.account
                AND IFNULL(jea.party_type, '') = singles.party_type
                AND IFNULL(jea.party, '') = singles.party
                AND IFNULL(jea.cost_center, '') = singles.cost_center
        ) matched
            ON gl.voucher_no = matched.parent
            AND gl.account = matched.account
            AND IFNULL(gl.party_type, '') = matched.party_type
            AND IFNULL(gl.party, '') = matched.party
            AND IFNULL(gl.cost_center, '') = matched.cost_center
        SET
            gl.voucher_detail_no = matched.jea_name,
            gl.letter = IFNULL(matched.letter, gl.letter)
        WHERE gl.voucher_type = 'Journal Entry'
          AND (gl.voucher_detail_no IS NULL OR gl.voucher_detail_no = '')
        """
    )


def _split_merged_gl_entries() -> int:
    """Split merged GL entries where multiple JE Account rows exist.

    Identifies GL entries with empty voucher_detail_no (i.e. not resolved
    by the single-match backfill), finds the corresponding JE Account rows,
    creates individual GL entries for each, and deletes the merged original.
    """
    merged_gl = frappe.db.sql(
        """
        SELECT gl.name, gl.voucher_no, gl.account,
               IFNULL(gl.party_type, '') AS party_type,
               IFNULL(gl.party, '') AS party,
               IFNULL(gl.cost_center, '') AS cost_center,
               gl.is_cancelled
        FROM `tabGL Entry` gl
        WHERE gl.voucher_type = 'Journal Entry'
          AND (gl.voucher_detail_no IS NULL OR gl.voucher_detail_no = '')
          AND gl.is_cancelled = 0
        """,
        as_dict=True,
    )

    if not merged_gl:
        return 0

    # Group by (voucher_no, account, party_type, party, cost_center)
    groups: dict[_MergeKey, list[_GLRow]] = {}
    for row in merged_gl:
        key = (
            row["voucher_no"],
            row["account"],
            row["party_type"],
            row["party"],
            row["cost_center"],
        )
        groups.setdefault(key, []).append(row)

    split_count = sum(_process_merge_group(key, rows) for key, rows in groups.items())

    if split_count:
        frappe.db.commit()

    return split_count


def _matching_jea_rows(key: _MergeKey) -> list[_GLRow]:
    voucher_no, account, party_type, party, cost_center = key
    return frappe.db.sql(
        """
        SELECT name, debit_in_account_currency, credit_in_account_currency,
               debit, credit, letter, exchange_rate, project,
               reference_type, reference_name,
               against_account, is_advance,
               reference_detail_no
        FROM `tabJournal Entry Account`
        WHERE parent = %(voucher_no)s
          AND account = %(account)s
          AND IFNULL(party_type, '') = %(party_type)s
          AND IFNULL(party, '') = %(party)s
          AND IFNULL(cost_center, '') = %(cost_center)s
        ORDER BY idx
        """,
        {
            "voucher_no": voucher_no,
            "account": account,
            "party_type": party_type,
            "party": party,
            "cost_center": cost_center,
        },
        as_dict=True,
    )


def _process_merge_group(key: _MergeKey, gl_rows: list[_GLRow]) -> int:
    jea_rows = _matching_jea_rows(key)
    if len(jea_rows) <= 1:
        _backfill_single_group(gl_rows, jea_rows)
        return 0

    # Idempotency: if split entries already exist (from a prior partial
    # run), skip creation and just delete the stale originals.
    jea_names = [r["name"] for r in jea_rows]
    already_split = frappe.get_all(
        "GL Entry",
        filters={
            "voucher_no": key[0],
            "voucher_detail_no": ["in", jea_names],
            "is_cancelled": 0,
        },
        fields=["name"],
        limit=1,
    )
    if already_split:
        _delete_merged_rows(gl_rows)
        return len(gl_rows)

    # Multiple JEA rows → split from the first GL entry only,
    # delete the rest (they are redundant merged copies).
    _split_single_gl_entry(gl_rows[0], jea_rows)
    _delete_merged_rows(gl_rows[1:])
    return len(gl_rows)


def _backfill_single_group(gl_rows: list[_GLRow], jea_rows: list[_GLRow]) -> None:
    if not jea_rows:
        return
    for gl_row in gl_rows:
        frappe.db.set_value(  # nosemgrep: frappe-direct-db-set-value
            "GL Entry",
            gl_row["name"],
            {
                "voucher_detail_no": jea_rows[0]["name"],
                "letter": jea_rows[0].get("letter") or "",
            },
            update_modified=False,
        )


def _delete_merged_rows(rows: list[_GLRow]) -> None:
    for row in rows:
        frappe.db.delete("GL Entry", {"name": row["name"]})


def _split_single_gl_entry(
    gl_row: _GLRow,
    jea_rows: list[_GLRow],
) -> None:
    """Replace one merged GL entry with individual entries per JEA row.

    Uses a savepoint so that a failed delete rolls back the inserts,
    preventing duplicate GL entries.
    """
    original = frappe.db.get_value(
        "GL Entry",
        gl_row["name"],
        ["*"],
        as_dict=True,
    )
    if not original:
        return

    gl_name = gl_row["name"]
    savepoint = f"split_gl_{gl_name.replace('-', '_')}"
    frappe.db.savepoint(savepoint)

    try:
        for jea in jea_rows:
            _insert_split_entry(original, jea)

        frappe.db.delete("GL Entry", {"name": gl_name})
    except Exception:  # noqa: BLE001 - rollback all split rows and retain the original on failure.
        frappe.db.rollback(save_point=savepoint)
        frappe.log_error(
            frappe.get_traceback(),
            f"GL split failed for {gl_name}",
        )


def _insert_split_entry(original: _GLRow, jea: _GLRow) -> None:
    new_gle = cast("GLEntry", frappe.new_doc("GL Entry"))
    _copy_original_fields(new_gle, original)

    new_gle.debit = jea.get("debit") or 0
    new_gle.credit = jea.get("credit") or 0
    new_gle.debit_in_account_currency = jea.get("debit_in_account_currency") or 0
    new_gle.credit_in_account_currency = jea.get("credit_in_account_currency") or 0
    new_gle.voucher_detail_no = jea["name"]
    new_gle.set("letter", jea.get("letter") or "")
    new_gle.against_voucher = (
        jea.get("reference_name") or original.get("against_voucher") or ""
    )
    new_gle.against_voucher_type = (
        jea.get("reference_type") or original.get("against_voucher_type") or ""
    )
    new_gle.is_advance = jea.get("is_advance") or "No"  # noqa: V101 - ERPNext consumes this GL field or document flag during insertion.

    _distribute_transaction_amounts(new_gle, original, jea)

    new_gle.flags.notify_update = False  # noqa: V101 - ERPNext consumes this GL field or document flag during insertion.
    new_gle.flags.ignore_validate = True  # noqa: V101 - ERPNext consumes this GL field or document flag during insertion.
    new_gle.insert(ignore_permissions=True)


def _copy_original_fields(new_gle: GLEntry, original: _GLRow) -> None:
    for field in original:
        skip = (
            "name",
            "creation",
            "modified",
            "modified_by",
            "owner",
            "idx",
            "to_rename",
        )
        if field in skip:
            continue
        if hasattr(new_gle, field):
            new_gle.set(field, original[field])


def _distribute_transaction_amounts(
    new_gle: GLEntry, original: _GLRow, jea: _GLRow
) -> None:
    orig_total = abs(original.get("debit") or 0) + abs(original.get("credit") or 0)
    if orig_total and original.get("transaction_currency"):
        row_total = abs(jea.get("debit") or 0) + abs(jea.get("credit") or 0)
        ratio = row_total / orig_total
        new_gle.debit_in_transaction_currency = (
            original.get("debit_in_transaction_currency") or 0
        ) * ratio
        new_gle.credit_in_transaction_currency = (
            original.get("credit_in_transaction_currency") or 0
        ) * ratio


def _fix_unrenamed_gl_entries() -> None:
    """Rename hash-named GL entries to the ACC-GLE series.

    Prior runs of this patch copied to_rename=0 from already-renamed
    originals, leaving new entries stuck with temporary hash names.
    Flag them and invoke the ERPNext renamer immediately.
    """
    frappe.db.sql(
        """
        UPDATE `tabGL Entry`
        SET to_rename = 1
        WHERE to_rename = 0
          AND name NOT LIKE 'ACC-GLE-%%'
          AND CHAR_LENGTH(name) = 10
        """
    )

    from erpnext.accounts.doctype.gl_entry.gl_entry import (  # noqa: PLC0415
        rename_temporarily_named_docs,
    )

    rename_temporarily_named_docs("GL Entry")

    # Reconcile RC GLE records whose gl_entry links broke during renaming
    from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.reconcile_gl_entry_links import (  # noqa: PLC0415
        reconcile_gl_entry_links,
    )

    reconcile_gl_entry_links(commit=False)


def diagnose_gl_split_issues() -> dict[str, dict[str, int | list[str]]]:  # noqa: V103 - retained bench diagnostic entry point.
    """Detect duplicate or orphaned GL entries from a prior split migration.

    Returns a dict with three categories:
    - duplicates: GL entries sharing the same voucher_no + voucher_detail_no
    - unprocessed: GL entries still missing voucher_detail_no
    - each category includes a count and sample voucher_nos
    """
    duplicates = frappe.db.sql(
        """
        SELECT voucher_no, account, voucher_detail_no, COUNT(*) AS cnt
        FROM `tabGL Entry`
        WHERE voucher_type = 'Journal Entry'
          AND voucher_detail_no IS NOT NULL
          AND voucher_detail_no != ''
          AND is_cancelled = 0
        GROUP BY voucher_no, account, voucher_detail_no
        HAVING COUNT(*) > 1
        LIMIT 50
        """,
        as_dict=True,
    )

    unprocessed = frappe.db.sql(
        """
        SELECT voucher_no, account, COUNT(*) AS cnt
        FROM `tabGL Entry`
        WHERE voucher_type = 'Journal Entry'
          AND (voucher_detail_no IS NULL OR voucher_detail_no = '')
          AND is_cancelled = 0
        GROUP BY voucher_no, account
        LIMIT 50
        """,
        as_dict=True,
    )

    return {
        "duplicates": {
            "count": len(duplicates),
            "samples": [
                f"{r['voucher_no']} / {r['account']} ({r['cnt']}x)"
                for r in duplicates[:10]
            ],
        },
        "unprocessed": {
            "count": len(unprocessed),
            "samples": [
                f"{r['voucher_no']} / {r['account']} ({r['cnt']})"
                for r in unprocessed[:10]
            ],
        },
    }


def repair_duplicate_gl_entries() -> dict[str, int]:  # noqa: V103 - retained explicit repair entry point; never auto-invoked.
    """Delete duplicate GL entries created by a prior buggy split migration.

    For each (voucher_no, account, voucher_detail_no) group with count > 1,
    keeps the earliest entry (by creation) and deletes the rest.
    Returns a count of deleted entries.
    """
    duplicates = frappe.db.sql(
        """
        SELECT voucher_no, account, voucher_detail_no
        FROM `tabGL Entry`
        WHERE voucher_type = 'Journal Entry'
          AND voucher_detail_no IS NOT NULL
          AND voucher_detail_no != ''
          AND is_cancelled = 0
        GROUP BY voucher_no, account, voucher_detail_no
        HAVING COUNT(*) > 1
        """,
        as_dict=True,
    )

    deleted = 0
    for dup in duplicates:
        entries = frappe.db.sql(
            """
            SELECT name
            FROM `tabGL Entry`
            WHERE voucher_type = 'Journal Entry'
              AND voucher_no = %(voucher_no)s
              AND account = %(account)s
              AND voucher_detail_no = %(voucher_detail_no)s
              AND is_cancelled = 0
            ORDER BY creation ASC
            """,
            dup,
            as_dict=True,
        )
        # Keep the first, delete the rest
        for entry in entries[1:]:
            frappe.db.delete("GL Entry", {"name": entry["name"]})
            deleted += 1

    if deleted:
        frappe.db.commit()

    return {"deleted": deleted}
