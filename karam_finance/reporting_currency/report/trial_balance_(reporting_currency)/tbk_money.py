"""Exact RC amounts until the report transport boundary."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from frappe.query_builder.functions import Cast_, Sum

from karam_finance.reporting_currency.report.reporting_source import amount


def decimal_amount(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def sum_amount(table: Any, field: str) -> Any:
    return Cast_(Sum(amount(table, field)), "varchar")


def prepare_display_amounts(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        display = {}
        for field, value in list(row.items()):
            if isinstance(value, Decimal):
                display[field] = str(
                    value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
                )
                row[field] = float(value)
        row["_display_amounts"] = display
