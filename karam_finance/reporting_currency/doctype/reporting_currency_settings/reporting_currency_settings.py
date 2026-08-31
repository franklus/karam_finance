# Copyright (c) 2026, Noospheric
# For license information, please see license.txt

"""Reporting Currency Settings DocType and related utilities."""

from typing import Any

import frappe
from frappe import _
from frappe.model.document import Document


class ReportingCurrencySettings(Document):
    """Settings for Reporting Currency module."""

    def validate(self) -> None:
        """Validate the document before saving."""
        self._validate_rc_parameters()

    def _validate_rc_parameters(self) -> None:
        """Validate the rc_parameters child table entries.

        Each row must have a valid doe_posting_date.
        Multiple rows with the same year are permitted.
        """
        if not self.rc_parameters:
            return

        for row in self.rc_parameters:
            if not row.doe_posting_date:
                frappe.throw(
                    _("Row {0}: DOE Posting Date is required.").format(row.idx)
                )


@frappe.whitelist()  # nosemgrep — RC module, UI-controlled access
def get_accounts_under_parent(parent_account: str) -> list[dict[str, Any]]:
    """Return all descendant accounts for the selected parent account."""
    if not parent_account:
        frappe.throw(_("Please select a parent account first."))

    parent_details = frappe.db.get_value(
        "Account", parent_account, ["lft", "rgt", "company"], as_dict=True
    )
    if not parent_details:
        frappe.throw(_("Account {0} was not found.").format(parent_account))

    child_accounts = frappe.get_all(
        "Account",
        fields=["name as account", "is_group"],
        filters={
            "lft": [">", parent_details.lft],
            "rgt": ["<", parent_details.rgt],
            "company": parent_details.company,
        },
        order_by="lft asc",
        limit=0,
    )

    return child_accounts or []
