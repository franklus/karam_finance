"""Exact monetary aggregation and a separate two-decimal display boundary."""

from decimal import ROUND_HALF_UP, Decimal
from typing import Any


def decimal_amount(value: Any) -> Decimal:
    return Decimal(str(value or 0))


def prepare_display_amounts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        display = {}
        for field, value in list(row.items()):
            if isinstance(value, Decimal):
                display[field] = str(
                    value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                )
                row[field] = float(value)
        row["_display_amounts"] = display
    return rows
