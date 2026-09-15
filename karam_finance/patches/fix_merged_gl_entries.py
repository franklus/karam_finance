"""Explicit legacy GL repair entry points; never registered as an automatic patch."""

import frappe

from karam_finance.patches.safe_gl_repair import repair_merged_vouchers


def execute(voucher_nos: list[str] | None = None) -> None:
    """Repair provable legacy merges through the supported voucher repost path."""
    repair_merged_vouchers(voucher_nos)


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
