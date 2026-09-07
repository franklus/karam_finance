from typing import Any

from frappe import _

ZERO_BALANCE_TOLERANCE = 0.005


def mark_has_value(rows: list[dict[str, Any]], period_list: list[Any]) -> None:
    for row in rows:
        row["has_value"] = False
        for period in period_list:
            val = float(row.get(period.key) or 0)
            if abs(val) >= ZERO_BALANCE_TOLERANCE:
                row["has_value"] = True
                break


def build_parent_children_map(
    cost_centres: list[dict[str, Any]],
) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for cc in cost_centres:
        parent = cc.get("parent_cost_center")
        if not parent:
            continue
        mapping.setdefault(parent, []).append(cc["name"])
    return mapping


def filter_out_zero_value_rows(
    rows: list[dict[str, Any]],
    parent_children_map: dict[str, list[str]],
) -> list[dict[str, Any]]:
    parent_by_child = {
        child: parent
        for parent, children in parent_children_map.items()
        for child in children
    }
    names_to_show: set[str] = set()
    for row in rows:
        if not row.get("has_value"):
            continue
        _include_cost_centre_ancestors(
            names_to_show, str(row.get("cost_center") or ""), parent_by_child
        )

    return [row for row in rows if str(row.get("cost_center") or "") in names_to_show]


def build_total_row_with_values(
    title: str,
    period_list: list[Any],
    values: list[float],
) -> dict[str, Any] | None:
    if not values:
        return None
    row: dict[str, Any] = {
        "cost_center": "'" + title + "'",
        "parent_cost_center": "",
        "indent": 0,
        "is_group": 0,
        "bold": True,
        "is_footer": True,
        "has_value": True,
    }
    for ix, period in enumerate(period_list):
        row[period.key] = float(values[ix] or 0)
    return row


def build_net_row_from_totals(
    period_list: list[Any],
    income_totals: list[float],
    expense_totals: list[float],
) -> dict[str, Any]:
    net_row: dict[str, Any] = {
        "cost_center": "'" + _("Net Profit/Loss") + "'",
        "parent_cost_center": "",
        "indent": 0,
        "is_group": 0,
        "bold": True,
        "warn_if_negative": True,
        "is_footer": True,
        "has_value": True,
    }
    for ix, period in enumerate(period_list):
        net_row[period.key] = float(income_totals[ix] or 0) - float(
            expense_totals[ix] or 0
        )
    return net_row


def _include_cost_centre_ancestors(
    names_to_show: set[str], cost_centre: str, parent_by_child: dict[str, str]
) -> None:
    names_to_show.add(cost_centre)
    parent = parent_by_child.get(cost_centre)
    while parent and parent not in names_to_show:
        names_to_show.add(parent)
        parent = parent_by_child.get(parent)
