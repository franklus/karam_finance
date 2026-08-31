"""Backfill GL Entry.letter from Journal Entry Account rows."""

from __future__ import annotations

import frappe


def execute() -> None:
    """Fill missing GL Entry letters for Journal Entries."""
    frappe.db.sql(
        """
        update `tabGL Entry` gl
        inner join (
            select
                parent,
                account,
                max(letter) as letter,
                count(distinct letter) as letter_count
            from `tabJournal Entry Account`
            where letter is not null and letter != ''
            group by parent, account
        ) jea
            on gl.voucher_no = jea.parent
            and gl.account = jea.account
        set gl.letter = jea.letter
        where gl.voucher_type = 'Journal Entry'
          and (gl.letter is null or gl.letter = '')
          and (gl.voucher_detail_no is null or gl.voucher_detail_no = '')
          and jea.letter_count = 1
        """
    )
