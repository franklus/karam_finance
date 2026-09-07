"""JV Letter Debit DocType."""

from __future__ import annotations

from frappe.model.document import Document


class JVLetterDebit(Document):  # noqa: V102 - Frappe loads this DocType controller by name.
    """Holds debit letter data for Journal Entries."""
