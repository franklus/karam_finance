"""Letter Settings DocType for managing year letters."""

from __future__ import annotations

from frappe.model.document import Document


class LetterSettings(Document):  # noqa: V102 - Frappe loads this DocType controller by name.
    """Stores per-year letter sequence."""
