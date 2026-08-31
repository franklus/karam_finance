"""Constants shared across the Karam Series feature.

Keep shared lists here to avoid duplication and drift across modules.
"""

from __future__ import annotations

# Curated list of doctypes that support Karam Series fields/behaviour
KARAM_DOCTYPES: list[str] = [
    "Asset",
    "Asset Movement",
    "Delivery Note",
    "Exchange Rate Revaluation",
    "Expense Claim",
    "Journal Entry",
    "Landed Cost Voucher",
    "Payment Entry",
    "Payroll Entry",
    "Period Closing Voucher",
    "Purchase Invoice",
    "Purchase Order",
    "Purchase Receipt",
    "Salary Slip",
    "Sales Invoice",
    "Sales Order",
    "Stock Entry",
    "Stock Reconciliation",
    "Work Order",
]
