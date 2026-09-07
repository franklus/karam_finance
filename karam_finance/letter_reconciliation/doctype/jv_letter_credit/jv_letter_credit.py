"""JV Letter Credit DocType."""

from __future__ import annotations

from frappe.model.document import Document


class JVLetterCredit(Document):  # noqa: V102 - Frappe loads this DocType controller by name.
    """Holds credit letter data for Journal Entries."""
