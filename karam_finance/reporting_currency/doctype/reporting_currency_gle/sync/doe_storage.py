"""Explicit DOE storage mapping and bounded bulk insertion."""

from typing import Any

import frappe
from frappe.utils import flt, now

DOCTYPE_RC_GLE = "Reporting Currency GLE"


def bulk_insert_doe_records(records: list[dict[str, Any]]) -> None:
    """Bulk insert DOE records into RC GLE table."""
    # Define fields to insert (excluding 'doctype' as it's a meta field)
    fields = [
        "name",
        "reporting_doe",
        "is_opening",
        "posting_date",
        "fiscal_year",
        "account",
        "account_currency",
        "party_type",
        "party",
        "against",
        "voucher_type",
        "voucher_no",
        "reporting_currency",
        "reporting_debit",
        "reporting_credit",
        "debit",
        "credit",
        "debit_amount_in_account_currency",
        "credit_amount_in_account_currency",
        "total_debit_default_currency",
        "total_credit_default_currency",
        "difference_default_currency",
        "reporting_debit_total",
        "reporting_credit_total",
        "difference_reporting_currency",
        "reporting_doe_difference",
        "company",
        "docstatus",
        "manual_entry",
        "creation",
        "modified",
        "owner",
        "modified_by",
    ]

    # Prepare values
    values = []
    for record in records:
        row = [
            record.get("name"),
            record.get("reporting_doe"),
            record.get("is_opening", "No"),
            record.get("posting_date"),
            record.get("fiscal_year"),
            record.get("account"),
            record.get("account_currency"),
            record.get("party_type"),
            record.get("party"),
            record.get("against"),
            record.get("voucher_type"),
            record.get("voucher_no"),
            record.get("reporting_currency"),
            _amount(record, "reporting_debit"),
            _amount(record, "reporting_credit"),
            _amount(record, "debit"),
            _amount(record, "credit"),
            _amount(record, "debit_amount_in_account_currency"),
            _amount(record, "credit_amount_in_account_currency"),
            _amount(record, "total_debit_default_currency"),
            _amount(record, "total_credit_default_currency"),
            _amount(record, "difference_default_currency"),
            _amount(record, "reporting_debit_total"),
            _amount(record, "reporting_credit_total"),
            _amount(record, "difference_reporting_currency"),
            _amount(record, "reporting_doe_difference"),
            record.get("company"),
            record.get("docstatus", 0),
            0,  # manual_entry - DOE records are never manual
            now(),  # creation
            now(),  # modified
            frappe.session.user,  # owner
            frappe.session.user,  # modified_by
        ]
        values.append(row)

    # Bulk insert in chunks of 1000
    chunk_size = 1000
    for i in range(0, len(values), chunk_size):
        chunk = values[i : i + chunk_size]
        frappe.db.bulk_insert(DOCTYPE_RC_GLE, fields, chunk)


def _amount(record: dict[str, Any], fieldname: str) -> float:
    return flt(record.get(fieldname) or 0, 9)
