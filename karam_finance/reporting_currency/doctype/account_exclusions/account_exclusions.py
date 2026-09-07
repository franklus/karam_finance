# Copyright (c) 2026, Noospheric
# For license information, please see license.txt

"""Account Exclusions child table for Reporting Currency Settings."""

from frappe.model.document import Document


class AccountExclusions(Document):  # noqa: V102 - Frappe child DocType controller loader.
    """Child table listing accounts excluded from reporting currency conversion."""
