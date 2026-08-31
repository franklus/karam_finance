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

import frappe


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
    groups: dict[tuple, list[dict]] = {}
    for row in merged_gl:
        key = (
            row["voucher_no"],
            row["account"],
            row["party_type"],
            row["party"],
            row["cost_center"],
        )
        groups.setdefault(key, []).append(row)

    split_count = 0
    for (
        voucher_no,
        account,
        party_type,
        party,
        cost_center,
    ), gl_rows in groups.items():
        # Fetch the JE Account rows that should replace this merged GL entry
        jea_rows = frappe.db.sql(
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

        if len(jea_rows) <= 1:
            # Single row — just backfill voucher_detail_no directly
            if jea_rows:
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
            continue

        # Idempotency: if split entries already exist (from a prior partial
        # run), skip creation and just delete the stale originals.
        jea_names = [r["name"] for r in jea_rows]
        already_split = frappe.get_all(
            "GL Entry",
            filters={
                "voucher_no": voucher_no,
                "voucher_detail_no": ["in", jea_names],
                "is_cancelled": 0,
            },
            fields=["name"],
            limit=1,
        )
        if already_split:
            for gl_row in gl_rows:
                frappe.db.delete("GL Entry", {"name": gl_row["name"]})
            split_count += len(gl_rows)
            continue

        # Multiple JEA rows → split from the first GL entry only,
        # delete the rest (they are redundant merged copies).
        _split_single_gl_entry(gl_rows[0], jea_rows)
        split_count += 1
        for gl_row in gl_rows[1:]:
            frappe.db.delete("GL Entry", {"name": gl_row["name"]})
            split_count += 1

    if split_count:
        frappe.db.commit()

    return split_count


def _split_single_gl_entry(
    gl_row: dict,
    jea_rows: list[dict],
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
            new_gle = frappe.new_doc("GL Entry")
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

            new_gle.debit = jea.get("debit") or 0
            new_gle.credit = jea.get("credit") or 0
            new_gle.debit_in_account_currency = (
                jea.get("debit_in_account_currency") or 0
            )
            new_gle.credit_in_account_currency = (
                jea.get("credit_in_account_currency") or 0
            )
            new_gle.voucher_detail_no = jea["name"]
            new_gle.letter = jea.get("letter") or ""
            new_gle.against_voucher = (
                jea.get("reference_name") or original.get("against_voucher") or ""
            )
            new_gle.against_voucher_type = (
                jea.get("reference_type") or original.get("against_voucher_type") or ""
            )
            new_gle.is_advance = jea.get("is_advance") or "No"

            orig_total = abs(original.get("debit") or 0) + abs(
                original.get("credit") or 0
            )
            if orig_total and original.get("transaction_currency"):
                row_total = abs(jea.get("debit") or 0) + abs(jea.get("credit") or 0)
                ratio = row_total / orig_total if orig_total else 0
                new_gle.debit_in_transaction_currency = (
                    original.get("debit_in_transaction_currency") or 0
                ) * ratio
                new_gle.credit_in_transaction_currency = (
                    original.get("credit_in_transaction_currency") or 0
                ) * ratio

            new_gle.flags.notify_update = False
            new_gle.flags.ignore_validate = True
            new_gle.insert(ignore_permissions=True)

        frappe.db.delete("GL Entry", {"name": gl_name})
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        frappe.log_error(
            frappe.get_traceback(),
            f"GL split failed for {gl_name}",
        )


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
    from karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.reconcile_gl_entry_links import (  # noqa: PLC0415, E501
        reconcile_gl_entry_links,
    )

    reconcile_gl_entry_links(commit=False)


def diagnose_gl_split_issues() -> dict:
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


def repair_duplicate_gl_entries() -> dict:
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
