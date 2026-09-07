"""Override for Bank Clearance to populate party information."""

from __future__ import annotations

from typing import Any, override

import frappe
from erpnext.accounts.doctype.bank_clearance.bank_clearance import BankClearance

from karam_finance.common.party_utils import populate_party_names


class KaramBankClearance(BankClearance):  # noqa: V102 - hooks.override_doctype_class loader.
    """Bank Clearance with party_type, party, and party_name enrichment."""

    @override
    @frappe.whitelist()  # nosemgrep: frappe-missing-permission-check
    # Pyrefly loses override metadata through Frappe's untyped decorator in the full graph.
    def get_payment_entries(self) -> None:  # pyrefly: ignore[missing-override-decorator]
        """Extend parent to populate party fields after fetching entries."""
        super().get_payment_entries()
        self._enrich_with_party_info()

    def _enrich_with_party_info(self) -> None:
        """Batch-populate party_type, party, and party_name on child rows."""
        rows_by_type: dict[str | None, list[Any]] = {}
        for row in self.payment_entries:
            rows_by_type.setdefault(row.payment_document, []).append(row)

        je_rows = rows_by_type.get("Journal Entry", [])
        if je_rows:
            self._enrich_journal_entries(je_rows)

        pe_rows = rows_by_type.get("Payment Entry", [])
        if pe_rows:
            self._enrich_payment_entries(pe_rows)

        si_rows = rows_by_type.get("Sales Invoice", [])
        for row in si_rows:
            row.party_type = "Customer"
            row.party = row.against_account

        pi_rows = rows_by_type.get("Purchase Invoice", [])
        for row in pi_rows:
            row.party_type = "Supplier"
            row.party = row.against_account

        self._populate_party_names()

    def _enrich_journal_entries(self, rows: list[Any]) -> None:
        je_names = [r.payment_entry for r in rows]
        # Party lives on the contra line (receivable/payable), NOT the
        # bank-account line, so we omit the account filter here.
        party_data = frappe.db.sql(
            """
            SELECT parent, party_type, party
            FROM `tabJournal Entry Account`
            WHERE parent IN %(names)s
                AND ifnull(party, '') != ''
            """,
            {"names": je_names},
            as_dict=1,
        )
        party_map: dict[str, frappe._dict[str, Any]] = {}
        for d in party_data:
            party_map.setdefault(d.parent, d)

        for row in rows:
            info = party_map.get(row.payment_entry)
            if info:
                row.party_type = info.party_type
                row.party = info.party

    def _enrich_payment_entries(self, rows: list[Any]) -> None:
        pe_names = [r.payment_entry for r in rows]
        party_data = frappe.db.sql(
            """
            SELECT name, party_type, party, party_name
            FROM `tabPayment Entry`
            WHERE name IN %(names)s
                AND ifnull(party, '') != ''
            """,
            {"names": pe_names},
            as_dict=1,
        )
        party_map = {d.name: d for d in party_data}

        for row in rows:
            info = party_map.get(row.payment_entry)
            if info:
                row.party_type = info.party_type
                row.party = info.party
                row.party_name = info.party_name

    def _populate_party_names(self) -> None:
        """Batch-resolve party_name for rows that have party but no name yet."""
        populate_party_names(self.payment_entries)
