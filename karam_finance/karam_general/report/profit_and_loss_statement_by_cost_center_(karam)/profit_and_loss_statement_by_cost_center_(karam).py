"""Profit and loss by cost centre report utilities."""

from typing import TYPE_CHECKING, Any

import frappe
from erpnext.accounts.report.financial_statements import (
    get_cost_centers_with_children,
    get_period_list,
)
from frappe import _

if TYPE_CHECKING:
    # These report folders contain parentheses; Pyrefly resolves their helpers
    # through the report search paths configured in pyrefly.toml.
    import pnlcc_columns
    import pnlcc_data
    import pnlcc_finance
    import pnlcc_parsing
    import pnlcc_rows
else:
    from . import pnlcc_columns, pnlcc_data, pnlcc_finance, pnlcc_parsing, pnlcc_rows


MIN_PERIODS_FOR_GROWTH = 2


def execute(
    filters: frappe._dict[str, Any] | dict[str, Any] | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    None,
    dict[str, Any],
    list[dict[str, Any]],
    float,
]:
    filters = normalise_report_filters(filters)
    company = str(filters.get("company") or "")
    if not company:
        frappe.throw(_("Company is mandatory"))
    selected_view = (
        pnlcc_parsing.normalise_scalar(filters.get("selected_view")) or "Report"
    )
    accumulated_values = bool(filters.get("accumulated_values"))
    analysis_accumulated_values = accumulated_values and selected_view != "Growth"
    period_list = get_report_periods(
        filters, accumulated_values=analysis_accumulated_values
    )
    allowed_cc = get_allowed_cost_centers(filters)

    period_amounts, root_totals = get_report_amounts(
        company=company,
        periods=period_list,
        finance_book=filters.get("finance_book"),
        include_default_fb=bool(filters.get("include_default_book_entries")),
        project_filters=filters.get("project"),
        restrict_cost_centers=allowed_cc,
        dimension_filters=filters,
        presentation_currency=filters.get("presentation_currency"),
        show_amount_in_company_currency=bool(
            filters.get("show_amount_in_company_currency")
        ),
    )
    cost_centres, _resolved_allowed = get_report_cost_centres(
        filters,
        required_cost_centers=list(period_amounts),
        allowed_cost_centers=allowed_cc,
    )
    if analysis_accumulated_values:
        period_amounts = accumulate_cost_centre_values(period_amounts, period_list)
        root_totals = accumulate_root_totals(root_totals)

    rows = build_cost_centre_rows(cost_centres, period_amounts, period_list)
    mark_has_value(rows, period_list)
    if not filters.get("show_zero_values"):
        rows = filter_out_zero_value_rows(rows, build_parent_children_map(cost_centres))

    income_totals = root_totals.get("Income", [])
    expense_totals = root_totals.get("Expense", [])
    net_totals = [
        income_totals[ix] - expense_totals[ix] for ix in range(len(period_list))
    ]
    footer_rows = build_footer_rows(period_list, income_totals, expense_totals)
    currency = filters.presentation_currency or frappe.get_cached_value(
        "Company", company, "default_currency"
    )
    report_summary, primitive_summary = get_report_summary(
        period_list=period_list,
        periodicity=filters.periodicity,
        totals=(income_totals, expense_totals, net_totals),
        currency=currency,
        accumulated_values=analysis_accumulated_values,
    )
    chart = get_chart_data(
        filters=filters,
        period_list=period_list,
        totals=(income_totals, expense_totals, net_totals),
        currency=currency,
    )
    apply_selected_view(
        selected_view,
        rows,
        footer_rows,
        period_list=period_list,
        income_totals=income_totals,
    )
    append_footer_payload(rows, footer_rows)
    return (
        get_columns(period_list),
        rows,
        None,
        chart,
        report_summary,
        primitive_summary,
    )


def normalise_report_filters(
    filters: frappe._dict[str, Any] | dict[str, Any] | None,
) -> frappe._dict[str, Any]:
    """Normalise saved report filters without changing their public names."""
    normalised = (
        filters if isinstance(filters, frappe._dict) else frappe._dict(filters or {})
    )
    filter_based_on = pnlcc_parsing.normalise_scalar(normalised.get("filter_based_on"))
    normalised.filter_based_on = (
        filter_based_on
        if filter_based_on in {"Fiscal Year", "Date Range"}
        else "Fiscal Year"
    )
    for fieldname in ("from_fiscal_year", "to_fiscal_year"):
        value = pnlcc_parsing.normalise_scalar(normalised.get(fieldname))
        setattr(normalised, fieldname, value or normalised.get(fieldname))
    return normalised


def get_report_periods(
    filters: frappe._dict[str, Any], *, accumulated_values: bool | None = None
) -> list[Any]:
    return get_period_list(
        filters.from_fiscal_year,
        filters.to_fiscal_year,
        filters.period_start_date,
        filters.period_end_date,
        filters.filter_based_on,
        filters.periodicity,
        accumulated_values=(
            bool(filters.get("accumulated_values"))
            if accumulated_values is None
            else accumulated_values
        ),
        company=filters.company,
    )


def get_report_cost_centres(
    filters: frappe._dict[str, Any],
    *,
    required_cost_centers: list[str] | None = None,
    allowed_cost_centers: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str] | None]:
    company = str(filters.get("company") or "")
    all_rows_requested = bool(filters.get("show_zero_values"))
    cost_centres = get_cost_centres(
        company,
        required_cost_centers=None if all_rows_requested else required_cost_centers,
    )
    allowed = allowed_cost_centers
    if allowed is None and filters.get("cost_center"):
        allowed = get_cost_centers_with_children(filters.get("cost_center"))
    if not allowed:
        return cost_centres, None
    allowed_set = set(allowed)
    return [cc for cc in cost_centres if cc["name"] in allowed_set], allowed


def get_allowed_cost_centers(filters: frappe._dict[str, Any]) -> list[str] | None:
    """Resolve an explicit cost-centre filter once before the ledger query."""
    if not filters.get("cost_center"):
        return None
    allowed = get_cost_centers_with_children(filters.get("cost_center"))
    return allowed or None


def build_cost_centre_rows(
    cost_centres: list[dict[str, Any]],
    period_amounts: dict[str, dict[str, float]],
    period_list: list[Any],
) -> list[dict[str, Any]]:
    period_keys = [period.key for period in period_list]
    rows: list[dict[str, Any]] = []
    row_by_name: dict[str, dict[str, Any]] = {}
    for cost_center in cost_centres:
        row = {
            "cost_center": cost_center["name"],
            "parent_cost_center": cost_center.get("parent_cost_center") or "",
            "indent": cost_center.get("indent", 0),
            "is_group": int(cost_center.get("is_group") or 0),
        }
        values = period_amounts.get(cost_center["name"], {})
        for key in period_keys:
            row[key] = values.get(key, 0.0)
        rows.append(row)
        row_by_name[cost_center["name"]] = row

    for cost_center in reversed(cost_centres):
        parent = cost_center.get("parent_cost_center")
        if not parent or parent not in row_by_name:
            continue
        child_row = row_by_name[cost_center["name"]]
        parent_row = row_by_name[parent]
        for key in period_keys:
            parent_row[key] += child_row.get(key, 0.0)
    return rows


def build_footer_rows(
    period_list: list[Any],
    income_totals: list[float],
    expense_totals: list[float],
) -> list[dict[str, Any]]:
    return [
        row
        for row in (
            build_total_row_with_values(
                _("Total Income (Credit)"), period_list, income_totals
            ),
            build_total_row_with_values(
                _("Total Expense (Debit)"), period_list, expense_totals
            ),
            build_net_row_from_totals(period_list, income_totals, expense_totals),
        )
        if row is not None
    ]


def apply_selected_view(
    selected_view: str,
    rows: list[dict[str, Any]],
    footer_rows: list[dict[str, Any]],
    *,
    period_list: list[Any],
    income_totals: list[float],
) -> None:
    if selected_view == "Growth":
        apply_growth_view(rows, period_list)
        apply_growth_view(footer_rows, period_list)
    elif selected_view == "Margin":
        apply_margin_view(rows, period_list, income_totals)
        apply_margin_view(footer_rows, period_list, income_totals)


def append_footer_payload(
    rows: list[dict[str, Any]], footer_rows: list[dict[str, Any]]
) -> None:
    if footer_rows:
        rows.append(
            {
                "cost_center": "",
                "is_spacer": True,
                "is_footer_payload": True,
                "has_value": True,
                "footer_rows": footer_rows,
            }
        )


def apply_growth_view(rows: list[dict[str, Any]], period_list: list[Any]) -> None:
    """Convert period values to period-on-period growth percentages."""
    if len(period_list) < MIN_PERIODS_FOR_GROWTH:
        return

    data_copy = [row.copy() for row in rows]
    for row_idx, source_row in enumerate(data_copy):
        target_row = rows[row_idx]
        for period_idx in range(1, len(period_list)):
            previous_key = period_list[period_idx - 1].key
            current_key = period_list[period_idx].key
            current_value = source_row.get(current_key)
            previous_value = source_row.get(previous_key)

            if current_value is None:
                target_row[current_key] = None
                continue

            # A zero (or missing) prior period has no meaningful growth
            # denominator.  Returning ``None`` lets the report render NA
            # instead of inventing a 100% result.
            if previous_value in (None, 0):
                target_row[current_key] = None
                continue

            growth = (current_value - previous_value) / previous_value
            target_row[current_key] = round(growth * 100, 2)


def apply_margin_view(
    rows: list[dict[str, Any]],
    period_list: list[Any],
    income_totals: list[float],
) -> None:
    """Convert period values to margin percentages against total income."""
    if not period_list:
        return

    income_by_key = {
        period.key: income_totals[ix] or 0.0 for ix, period in enumerate(period_list)
    }
    for row in rows:
        for period in period_list:
            period_key = period.key
            base_value = income_by_key.get(period_key, 0.0)
            current_value = row.get(period_key)
            if current_value is None or base_value <= 0:
                row[period_key] = None
                continue
            # Cost-centre rows can carry the opposite sign to the positive
            # income base.  Margin is a share of income and is therefore
            # presented as a positive magnitude.
            row[period_key] = round(
                (abs(float(current_value)) / abs(base_value)) * 100,
                2,
            )


def get_chart_data(
    *,
    totals: tuple[list[float], list[float], list[float]],
    filters: frappe._dict[str, Any],
    period_list: list[Any],
    currency: str,
) -> dict[str, Any]:
    income_totals, expense_totals, net_totals = totals
    labels = [period.label for period in period_list]
    datasets: list[dict[str, Any]] = []
    if income_totals:
        datasets.append({"name": _("Income"), "values": income_totals})
    if expense_totals:
        datasets.append({"name": _("Expense"), "values": expense_totals})
    if net_totals:
        datasets.append({"name": _("Net Profit/Loss"), "values": net_totals})

    chart: dict[str, Any] = {"data": {"labels": labels, "datasets": datasets}}
    selected_view = pnlcc_parsing.normalise_scalar(filters.get("selected_view"))
    chart["type"] = (
        "line"
        if bool(filters.get("accumulated_values")) and selected_view != "Growth"
        else "bar"
    )
    chart["fieldtype"] = "Currency"
    chart["options"] = "currency"
    chart["currency"] = currency
    return chart


def get_report_summary(
    *,
    totals: tuple[list[float], list[float], list[float]],
    period_list: list[Any],
    periodicity: str | None,
    currency: str,
    accumulated_values: bool,
) -> tuple[list[dict[str, Any]], float]:
    income_totals, expense_totals, net_totals = totals
    if accumulated_values and net_totals:
        net_income = income_totals[-1] if income_totals else 0.0
        net_expense = expense_totals[-1] if expense_totals else 0.0
        net_profit = net_totals[-1]
    else:
        net_income = float(sum(income_totals))
        net_expense = float(sum(expense_totals))
        net_profit = float(sum(net_totals))

    if len(period_list) == 1 and periodicity == "Yearly":
        income_label = _("Total Income This Year")
        expense_label = _("Total Expense This Year")
        profit_label = _("Profit This Year")
    else:
        income_label = _("Total Income")
        expense_label = _("Total Expense")
        profit_label = _("Net Profit")

    report_summary = [
        {
            "value": net_income,
            "label": income_label,
            "datatype": "Currency",
            "currency": currency,
        },
        {"type": "separator", "value": "-"},
        {
            "value": net_expense,
            "label": expense_label,
            "datatype": "Currency",
            "currency": currency,
        },
        {"type": "separator", "value": "=", "color": "blue"},
        {
            "value": net_profit,
            "indicator": "Green" if net_profit > 0 else "Red",
            "label": profit_label,
            "datatype": "Currency",
            "currency": currency,
        },
    ]
    return report_summary, net_profit


def _parse_multiselect(value: object) -> list[str]:
    return pnlcc_parsing.parse_multiselect(value)


def _normalise_scalar(value: object) -> str | None:
    return pnlcc_parsing.normalise_scalar(value)


def _finance_book_clause(
    gl: Any,
    *,
    company: str,
    finance_book: object,
    include_default_fb: bool,
) -> object:
    return pnlcc_finance.finance_book_clause(
        gl,
        company=company,
        finance_book=finance_book,
        include_default_fb=include_default_fb,
    )


def get_columns(period_list: list[Any]) -> list[dict[str, Any]]:
    return pnlcc_columns.get_columns(period_list)


def get_cost_centres(
    company: str,
    required_cost_centers: list[str] | None = None,
) -> list[dict[str, Any]]:
    return pnlcc_data.get_cost_centres(
        company,
        required_cost_centers=required_cost_centers,
    )


get_amounts_by_cost_centre = pnlcc_data.get_amounts_by_cost_centre


get_report_amounts = pnlcc_data.get_report_amounts


def accumulate_cost_centre_values(
    amounts: dict[str, dict[str, float]], period_list: list[Any]
) -> dict[str, dict[str, float]]:
    """Turn discrete cost-centre values into running balances."""
    accumulated: dict[str, dict[str, float]] = {}
    for cost_center, values in amounts.items():
        running = 0.0
        accumulated[cost_center] = {}
        for period in period_list:
            running += values.get(period.key, 0.0) or 0.0
            accumulated[cost_center][period.key] = running
    return accumulated


def accumulate_root_totals(
    totals: dict[str, list[float]],
) -> dict[str, list[float]]:
    """Turn discrete root totals into running balances."""
    accumulated: dict[str, list[float]] = {}
    for root_type, values in totals.items():
        running = 0.0
        accumulated[root_type] = []
        for value in values:
            running += value or 0.0
            accumulated[root_type].append(running)
    return accumulated


def mark_has_value(rows: list[dict[str, Any]], period_list: list[Any]) -> None:
    pnlcc_rows.mark_has_value(rows, period_list)


def build_parent_children_map(
    cost_centres: list[dict[str, Any]],
) -> dict[str, list[str]]:
    return pnlcc_rows.build_parent_children_map(cost_centres)


def filter_out_zero_value_rows(
    rows: list[dict[str, Any]],
    parent_children_map: dict[str, list[str]],
) -> list[dict[str, Any]]:
    return pnlcc_rows.filter_out_zero_value_rows(rows, parent_children_map)


def build_total_row_with_values(
    title: str,
    period_list: list[Any],
    values: list[float],
) -> dict[str, Any] | None:
    return pnlcc_rows.build_total_row_with_values(title, period_list, values)


def build_net_row_from_totals(
    period_list: list[Any],
    income_totals: list[float],
    expense_totals: list[float],
) -> dict[str, Any]:
    return pnlcc_rows.build_net_row_from_totals(
        period_list, income_totals, expense_totals
    )


get_total_by_root_type = pnlcc_data.get_total_by_root_type
