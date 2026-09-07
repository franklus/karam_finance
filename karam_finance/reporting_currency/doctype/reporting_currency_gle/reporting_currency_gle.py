"""Reporting Currency GLE DocType controller."""

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime

DOCTYPE_RC_GLE = "Reporting Currency GLE"

# Minimum parts in manual entry name (KE-RCMAN-{YYYY}-{#####})
MANUAL_NAME_MIN_PARTS = 4


class ReportingCurrencyGLE(Document):
    """Shadow copy of GL Entry for reporting currency conversions."""

    def autoname(self) -> None:
        """Generate naming series for manual entries.

        Sync-created records have their name set before insert.
        Manual entries (no gl_entry) get the KE-RCMAN series.
        """
        if not self.name and not self.gl_entry:
            self.name = _generate_manual_entry_name(self.posting_date)

    def before_insert(self) -> None:
        """Mark manually created records."""
        # Records created via sync will have gl_entry set before insert.
        # If gl_entry is not set, this is a manual entry.
        if not self.gl_entry:
            self.manual_entry = 1

    def before_save(self) -> None:
        """Prevent editing of DOE records."""
        # Check if this is an existing DOE record being modified
        if not self.is_new() and self.reporting_doe == 1:
            # Get the original value from database
            # nosemgrep: frappe-get-doc-without-check (self.name exists in controller)
            old_doc = frappe.get_doc("Reporting Currency GLE", self.name)
            if old_doc.reporting_doe == 1:
                frappe.throw(
                    frappe._(
                        "DOE records cannot be edited. Please use Sync to regenerate."
                    ),
                    title=frappe._("Cannot Edit DOE Record"),
                )

    def validate(self) -> None:
        """Prevent editing of DOE records."""
        if self.manual_entry or (
            self.is_new() and not self.gl_entry and not self.reporting_doe
        ):
            currency = frappe.db.get_single_value(
                "Reporting Currency Settings", "reporting_currency"
            )
            if not currency:
                frappe.throw(
                    frappe._(
                        "Configure Reporting Currency before creating a manual entry."
                    )
                )
            if self.reporting_currency and self.reporting_currency != currency:
                frappe.throw(
                    frappe._(
                        "Manual reporting entries must use {0}. Verify the currency and amount before correcting an existing entry."
                    ).format(currency)
                )
            self.reporting_currency = currency
        if self.reporting_doe == 1 and not self.is_new():
            frappe.throw(
                frappe._(
                    "DOE records cannot be edited. Please use Sync to regenerate."
                ),
                title=frappe._("Cannot Edit DOE Record"),
            )

    def on_trash(self) -> None:
        """Prevent manual deletion of DOE records."""
        if self.reporting_doe == 1:
            frappe.throw(
                frappe._(
                    "DOE records cannot be deleted manually. "
                    "They are regenerated during sync."
                ),
                title=frappe._("Cannot Delete DOE Record"),
            )


def _generate_manual_entry_name(posting_date: str | None) -> str:
    """Generate a unique name for manual RC GLE entries.

    Pattern: KE-RCMAN-{YYYY}-{#####}
    """
    year = get_datetime(posting_date).year if posting_date else get_datetime().year

    # Get the last number used for this year (year is int, DOCTYPE_RC_GLE is constant)
    last_name = frappe.db.sql(  # nosemgrep
        f"""
        SELECT name
        FROM `tab{DOCTYPE_RC_GLE}`
        WHERE name LIKE 'KE-RCMAN-{year}-%'
        ORDER BY name DESC
        LIMIT 1
        """  # noqa: S608
    )

    if last_name and last_name[0][0]:
        parts = last_name[0][0].split("-")
        if len(parts) >= MANUAL_NAME_MIN_PARTS:
            try:
                next_number = int(parts[3]) + 1
            except ValueError, IndexError:
                next_number = 1
        else:
            next_number = 1
    else:
        next_number = 1

    return f"KE-RCMAN-{year}-{next_number:05d}"
