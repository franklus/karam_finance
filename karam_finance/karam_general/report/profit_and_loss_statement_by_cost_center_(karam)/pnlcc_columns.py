from typing import Any

from frappe import _


def get_columns(period_list: list[Any]) -> list[dict[str, Any]]:
    columns = [
        {
            "fieldname": "cost_center",
            "label": _("Cost Center"),
            "fieldtype": "Link",
            "options": "Cost Center",
            "width": 420,
        },
    ]
    columns.extend(
        {
            "fieldname": p.key,
            "label": p.label,
            "fieldtype": "Currency",
            "options": "currency",
            "width": 180,
        }
        for p in period_list
    )
    return columns
