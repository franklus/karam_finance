"""Database schema utilities for Karam Finance.

Cross-cutting infrastructure for ensuring database columns have the correct
precision and capacity across all modules.
"""

from __future__ import annotations

import threading

import click
import frappe

# Thread-safe flag so we only widen columns once per worker process
_currency_columns_verified = threading.local()

# Decimal column width (total digits) for currency/float fields
DECIMAL_WIDTH = 30

# Decimal precision (decimal places) for DECIMAL(30,4) column
# Using 4 decimal places provides 26 integer digits capacity
# (compared to 21 with precision=9), preventing "out of range" errors
# for extremely large currency values whilst maintaining sufficient
# decimal accuracy for financial calculations.
DECIMAL_PRECISION = 4

# Fields requiring wider decimal columns, keyed by DocType
WIDENED_FLOAT_FIELDS: dict[str, list[str]] = {
    "Journal Entry Account": [
        "exchange_rate",
        "debit",
        "credit",
        "debit_in_account_currency",
        "credit_in_account_currency",
    ],
    "GL Entry": [
        "credit",
        "debit",
        "credit_in_account_currency",
        "debit_in_account_currency",
        "credit_in_transaction_currency",
        "debit_in_transaction_currency",
        "transaction_exchange_rate",
    ],
    "Account Closing Balance": [
        "credit",
        "debit",
        "credit_in_account_currency",
        "debit_in_account_currency",
    ],
}


# Property configurations for decimal field overrides
_PROPERTY_CONFIGS = [
    ("length", "Int", str(DECIMAL_WIDTH)),
    ("precision", "Small Text", str(DECIMAL_PRECISION)),
]


def _ensure_property_setters() -> None:
    """Create or update Property Setters for ERPNext DocType field overrides.

    Property Setters are required because GL Entry, Journal Entry Account, and
    Account Closing Balance are ERPNext core DocTypes. Without these overrides,
    Frappe's schema sync would attempt to revert columns to DECIMAL(21,9),
    which fails if data exceeds 12 integer digits.

    Behaviour:
    1. Checks if Property Setter records already exist
    2. Verifies values match code specification; reverts user modifications
    3. Recreates any missing Property Setter records
    """
    click.echo(
        f"Ensuring Property Setters for DECIMAL({DECIMAL_WIDTH},{DECIMAL_PRECISION})"
    )

    created, updated, unchanged = 0, 0, 0

    targets: list[dict[str, str]] = []
    for doctype, fields in WIDENED_FLOAT_FIELDS.items():
        for fieldname in fields:
            for prop, prop_type, value in _PROPERTY_CONFIGS:
                targets.append(
                    {
                        "name": f"{doctype}-{fieldname}-{prop}",
                        "doc_type": doctype,
                        "field_name": fieldname,
                        "property": prop,
                        "property_type": prop_type,
                        "value": value,
                    }
                )

    existing_rows = frappe.get_all(
        "Property Setter",
        filters={"name": ["in", [t["name"] for t in targets]]},
        fields=["name", "value"],
        limit=0,
    )
    existing_values = {row["name"]: row.get("value") for row in existing_rows}

    updates: dict[str, dict[str, str]] = {}
    inserts: list[dict[str, str]] = []
    for target in targets:
        ps_name = target["name"]
        current_value = existing_values.get(ps_name)
        if current_value is None:
            inserts.append(target)
            continue

        if current_value != target["value"]:
            updates[ps_name] = {"value": target["value"]}
            click.echo(f"  UPDATED {ps_name}: {current_value} -> {target['value']}")
            updated += 1
        else:
            unchanged += 1

    if inserts:
        now = frappe.utils.now()
        session = getattr(frappe, "session", None)
        user = session.user if session and session.user else "Administrator"
        if not user:
            user = "Administrator"
        fields = [
            "name",
            "owner",
            "creation",
            "modified",
            "modified_by",
            "doc_type",
            "field_name",
            "property",
            "property_type",
            "value",
            "doctype_or_field",
        ]
        values = [
            [
                target["name"],
                user,
                now,
                now,
                user,
                target["doc_type"],
                target["field_name"],
                target["property"],
                target["property_type"],
                target["value"],
                "DocField",
            ]
            for target in inserts
        ]
        frappe.db.bulk_insert("Property Setter", fields=fields, values=values)
        for target in inserts:
            click.echo(f"  CREATED {target['name']} = {target['value']}")
        created += len(inserts)

    if updates:
        frappe.db.bulk_update("Property Setter", updates)

    frappe.db.commit()  # nosemgrep — CLI migration
    click.echo(
        f"Property Setters: created={created}, updated={updated}, unchanged={unchanged}"
    )


def ensure_currency_columns_capacity() -> None:
    """Ensure currency columns can store large values with DECIMAL(30,4).

    Two-phase approach:
    1. Create/update Property Setters so Frappe's schema sync uses DECIMAL(30,4)
    2. Directly ALTER existing columns to handle data that exceeds defaults

    DECIMAL(30,4) provides 26 integer digits capacity (vs 12 with default 21,9).
    """
    if getattr(_currency_columns_verified, "done", False):
        return

    _ensure_property_setters()

    # Combine ERPNext DocTypes with karam_finance-specific fields
    tables = {f"tab{dt}": fields for dt, fields in WIDENED_FLOAT_FIELDS.items()}
    tables["tabReporting Currency GLE"] = [
        "credit",
        "debit",
        "credit_amount_in_account_currency",
        "debit_amount_in_account_currency",
        "credit_in_transaction_currency",
        "debit_amount_in_transaction_currency",
        "reporting_credit",
        "reporting_debit",
        "total_debit_default_currency",
        "total_credit_default_currency",
        "difference_default_currency",
        "reporting_debit_total",
        "reporting_credit_total",
        "difference_reporting_currency",
        "reporting_doe_difference",
        "exchange_rate",
        "transaction_exchange_rate",
    ]

    for table_name, columns in tables.items():  # nosemgrep: frappe-db-commit-in-loop
        if not columns:
            continue

        existing_columns = {
            row.column_name: row
            for row in frappe.db.sql(
                """
                SELECT column_name, column_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                    AND table_name = %(table)s
                    AND column_name IN %(columns)s
                """,
                {"table": table_name, "columns": tuple(columns)},
                as_dict=True,
            )
        }

        target_type = f"decimal({DECIMAL_WIDTH},{DECIMAL_PRECISION})"

        for column in columns:  # nosemgrep: frappe-db-commit-in-loop
            col_info = existing_columns.get(column)
            if not col_info:
                continue

            if col_info.column_type != target_type or col_info.is_nullable == "YES":
                if col_info.is_nullable == "YES":
                    frappe.db.sql(
                        f"UPDATE `{table_name}` SET `{column}` = 0 "  # noqa: S608
                        f"WHERE `{column}` IS NULL"
                    )
                    frappe.db.commit()  # nosemgrep — DDL requires committed data
                frappe.db.sql(
                    f"ALTER TABLE `{table_name}` MODIFY `{column}` "
                    f"DECIMAL({DECIMAL_WIDTH},{DECIMAL_PRECISION}) NOT NULL DEFAULT 0"
                )

    _currency_columns_verified.done = True
