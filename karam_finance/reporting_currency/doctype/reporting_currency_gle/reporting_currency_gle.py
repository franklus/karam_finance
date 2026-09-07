"""Reporting Currency GLE DocType controller."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime

if TYPE_CHECKING:
    from datetime import date

    from frappe.types import DF

DOCTYPE_RC_GLE = "Reporting Currency GLE"

# Minimum parts in manual entry name (KE-RCMAN-{YYYY}-{#####})
MANUAL_NAME_MIN_PARTS = 4


class ReportingCurrencyGLE(Document):
    """Shadow copy of GL Entry for reporting currency conversions."""

    if TYPE_CHECKING:
        gl_entry: DF.Link | None
        posting_date: DF.Date | None
        reporting_currency: DF.Link | None
        reporting_doe: DF.Check
        manual_entry: DF.Check

    def autoname(self) -> None:  # noqa: V105 - Frappe document naming callback.
        """Generate naming series for manual entries.

        Sync-created records have their name set before insert.
        Manual entries (no gl_entry) get the KE-RCMAN series.
        """
        if not self.name and not self.gl_entry:
            self.name = _generate_manual_entry_name(self.posting_date)

    def before_insert(self) -> None:  # noqa: V105 - Frappe document lifecycle callback.
        """Mark manually created records."""
        # Records created via sync will have gl_entry set before insert.
        # If gl_entry is not set, this is a manual entry.
        if not self.gl_entry:
            self.manual_entry = 1

    def before_save(self) -> None:  # noqa: V105 - Frappe document lifecycle callback.
        """Prevent editing of DOE records."""
        # Check if this is an existing DOE record being modified
        if not self.is_new() and self.reporting_doe == 1:
            # Get the original value from database
            # nosemgrep: frappe-get-doc-without-check (self.name exists in controller)
            old_doc = frappe.get_doc("Reporting Currency GLE", cast(str, self.name))
            if old_doc.get("reporting_doe") == 1:
                frappe.throw(
                    frappe._(
                        "DOE records cannot be edited. Please use Sync to regenerate."
                    ),
                    title=frappe._("Cannot Edit DOE Record"),
                )

    def validate(self) -> None:
        """Enforce the configured currency and prevent editing of DOE records."""
        currency = frappe.db.get_single_value(
            "Reporting Currency Settings", "reporting_currency"
        )
        if not currency:
            frappe.throw(
                frappe._(
                    "Configure Reporting Currency in Reporting Currency Settings first."
                )
            )
        # Currency Link defaults can prefill company currency on a new manual row.
        if self.is_new() and not self.gl_entry and not self.reporting_doe:
            self.reporting_currency = currency
        if self.reporting_currency and self.reporting_currency != currency:
            frappe.throw(
                frappe._(
                    "Reporting Currency must be {0}, as configured in Reporting Currency Settings. Verify the currency and amounts before regenerating or correcting this entry."
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

    def on_trash(self) -> None:  # noqa: V105 - Frappe document lifecycle callback.
        """Prevent manual deletion of DOE records."""
        if self.reporting_doe == 1:
            frappe.throw(
                frappe._(
                    "DOE records cannot be deleted manually. "
                    "They are regenerated during sync."
                ),
                title=frappe._("Cannot Delete DOE Record"),
            )


def _generate_manual_entry_name(posting_date: str | date | None) -> str:
    """Generate a unique name for manual RC GLE entries.

    Pattern: KE-RCMAN-{YYYY}-{#####}
    """
    posting_datetime = get_datetime(posting_date) if posting_date else get_datetime()
    if posting_datetime is None:
        message = frappe._(
            "Invalid Posting Date for a manual reporting currency entry."
        )
        raise frappe.ValidationError(message)
    year = posting_datetime.year

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

    next_number = _next_manual_number(last_name)

    return f"KE-RCMAN-{year}-{next_number:05d}"


def _next_manual_number(last_name: list[tuple[str]]) -> int:
    if not last_name or not last_name[0][0]:
        return 1
    parts = last_name[0][0].split("-")
    if len(parts) < MANUAL_NAME_MIN_PARTS:
        return 1
    try:
        return int(parts[3]) + 1
    except ValueError, IndexError:
        return 1
