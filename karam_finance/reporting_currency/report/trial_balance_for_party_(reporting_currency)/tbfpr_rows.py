"""Row builders for Trial Balance for Party (Reporting Currency)."""

from typing import Any

from frappe import _
from frappe.utils import flt

from .tbfpr_constants import VALUE_FIELDS

TOTAL_FIELDS = ("opening_debit", "opening_credit", "debit", "credit")


def get_blank_row() -> Any:
    blank_row: dict[str, Any] = {"party": "", "bold": 1}
    for field in VALUE_FIELDS:
        blank_row[field] = None
    return blank_row


def build_total_row(reporting_currency: Any, totals: Any) -> Any:
    row: dict[str, Any] = {
        "party": "'" + _("Totals") + "'",
        "currency": reporting_currency,
        "bold": 1,
    }

    for field in TOTAL_FIELDS:
        row[field] = flt(totals.get(field, 0))

    closing_debit, closing_credit = toggle_debit_credit(
        row["opening_debit"] + row["debit"],
        row["opening_credit"] + row["credit"],
    )
    row["closing_debit"] = closing_debit
    row["closing_credit"] = closing_credit

    return row


def toggle_debit_credit(debit: Any, credit: Any) -> Any:
    if flt(debit) > flt(credit):
        debit = flt(debit) - flt(credit)
        credit = 0.0
    else:
        credit = flt(credit) - flt(debit)
        debit = 0.0
    return debit, credit
