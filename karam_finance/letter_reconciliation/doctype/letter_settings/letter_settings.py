"""Letter Settings DocType for managing year letters."""

from __future__ import annotations

from frappe.model.document import Document


class LetterSettings(Document):
    """Stores per-year letter sequence."""
