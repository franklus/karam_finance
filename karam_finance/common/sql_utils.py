"""SQL safety utilities for dynamic query construction.

Provides validation helpers for SQL identifiers (column names, table names)
to prevent SQL injection when using string interpolation in queries.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import frappe

if TYPE_CHECKING:
    from collections.abc import Iterable

# Valid SQL identifiers: alphanumeric + underscores, not starting with digit
_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Common SQL reserved words that should not be used as bare identifiers
_SQL_RESERVED = frozenset(
    {
        "select",
        "from",
        "where",
        "and",
        "or",
        "not",
        "in",
        "is",
        "null",
        "like",
        "between",
        "join",
        "left",
        "right",
        "inner",
        "outer",
        "on",
        "order",
        "by",
        "group",
        "having",
        "limit",
        "offset",
        "union",
        "insert",
        "update",
        "delete",
        "drop",
        "create",
        "alter",
        "table",
        "index",
        "as",
    }
)


def validate_sql_identifier(value: str, context: str = "identifier") -> str:
    """Validate that a string is a safe SQL identifier.

    Args:
        value: The string to validate (column name, table name, etc.)
        context: Description for error messages (e.g., "column name", "table name")

    Returns:
        The validated identifier string.

    Raises:
        frappe.ValidationError: If the value is not a valid SQL identifier.

    Example:
        >>> validate_sql_identifier("account_currency")
        'account_currency'
        >>> validate_sql_identifier("1invalid")  # raises ValidationError
    """
    if not value:
        frappe.throw(
            frappe._("Invalid {0}: must be a non-empty string").format(context),
            frappe.ValidationError,
        )

    value = str(value).strip()

    if not _IDENTIFIER_PATTERN.match(value):
        msg = frappe._("Invalid {0} '{1}': only letters, numbers, underscores allowed")
        frappe.throw(msg.format(context, value), frappe.ValidationError)

    return value


def validate_sql_identifiers(
    values: Iterable[str], context: str = "identifier"
) -> list[str]:
    """Validate multiple SQL identifiers.

    Args:
        values: Iterable of strings to validate.
        context: Description for error messages.

    Returns:
        List of validated identifier strings.
    """
    return [validate_sql_identifier(v, context) for v in values]


def safe_column_list(columns: Iterable[str], table_alias: str = "") -> str:
    """Build a safe comma-separated column list for SELECT clauses.

    Args:
        columns: Iterable of column names to include.
        table_alias: Optional table alias prefix (e.g., "gl" becomes "gl.column").

    Returns:
        Comma-separated string of validated column names.

    Example:
        >>> safe_column_list(["account", "posting_date"], "gl")
        'gl.account, gl.posting_date'
    """
    validated = validate_sql_identifiers(columns, "column name")

    if table_alias:
        table_alias = validate_sql_identifier(table_alias, "table alias")
        return ", ".join(f"{table_alias}.{col}" for col in validated)

    return ", ".join(validated)


def safe_int(value: int | str | None, default: int = 0) -> int:
    """Safely convert a value to integer for SQL interpolation.

    Args:
        value: Value to convert (from database settings, filters, etc.)
        default: Default value if conversion fails.

    Returns:
        Integer value safe for SQL interpolation.

    Example:
        >>> safe_int(frappe.db.get_single_value("Settings", "limit"))
        100
    """
    if value is None:
        return default

    try:
        result = int(value)
    except (ValueError, TypeError):
        return default
    else:
        return default if result < 0 else result
