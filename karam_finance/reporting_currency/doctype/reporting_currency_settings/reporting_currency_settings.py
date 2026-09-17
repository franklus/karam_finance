# Copyright (c) 2026, Noospheric
# For license information, please see license.txt

"""Reporting Currency Settings DocType and related utilities."""

from __future__ import annotations

from math import isfinite
from typing import TYPE_CHECKING, Any

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from karam_finance.reporting_currency.ledger_lock import hold_ledger_lock
from karam_finance.reporting_currency.offset_accounts import validate_offset_accounts

if TYPE_CHECKING:
    from frappe.types import DF


def validate_doe_exchange_rates(rows: list[Any]) -> None:
    """Validate every parameter before a DOE run can replace existing entries."""
    for row in rows:
        rate = flt(row.exchange_rate)
        if not isfinite(rate) or rate <= 0:
            frappe.throw(
                _(
                    "Row {0}: DOE Exchange Rate must be a finite number greater than zero."
                ).format(row.idx)
            )


class ReportingCurrencySettings(Document):
    """Settings for Reporting Currency module."""

    rc_parameters: list[Any]
    if TYPE_CHECKING:
        reporting_currency: DF.Link | None
        last_sync_timestamp: DF.Datetime | None
        last_ce_sync_timestamp: DF.Datetime | None  # noqa: V107 - Frappe metadata field.

    def validate(self) -> None:
        """Validate the document before saving."""
        hold_ledger_lock()
        self._validate_currency_change()
        self._validate_rc_parameters()

    def onload(self) -> None:  # noqa: V105 - Frappe loads form context through this hook.
        has_reporting_entries = bool(frappe.db.exists("Reporting Currency GLE", {}))
        self.set_onload(
            "has_reporting_entries",
            has_reporting_entries,
        )
        self.set_onload("saved_reporting_currency", self.reporting_currency)

    def _validate_currency_change(self) -> None:
        previous = frappe.db.get_single_value(
            "Reporting Currency Settings", "reporting_currency", cache=False
        )
        if previous == self.reporting_currency:
            return
        if frappe.db.exists("Reporting Currency GLE", {}):
            frappe.throw(
                _(
                    "Confirm the reporting currency change from Reporting Currency "
                    "Settings so the existing reporting ledger can be rebuilt."
                )
            )
        # Even an empty reporting ledger must rebuild all historical source rows.
        self.last_sync_timestamp = None
        self.set("last_ce_sync_timestamp", None)

    def _validate_rc_parameters(self) -> None:
        """Validate the rc_parameters child table entries.

        Each row needs a posting date and a positive, finite exchange rate.
        Multiple rows with the same year are permitted.
        """
        if not self.rc_parameters:
            return

        validate_doe_exchange_rates(self.rc_parameters)

        for row in self.rc_parameters:
            if not row.doe_posting_date:
                frappe.throw(
                    _("Row {0}: DOE Posting Date is required.").format(row.idx)
                )

        validate_offset_accounts(self.rc_parameters)


@frappe.whitelist()  # noqa: V103 - whitelisted Settings client lookup.
def get_accounts_under_parent(parent_account: str) -> list[dict[str, Any]]:
    """Return all descendant accounts for the selected parent account."""
    frappe.only_for("System Manager")
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
