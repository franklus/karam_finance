"""Database access for the Cost Centre Profit and Loss report.

The old report ran one ledger query for every period and then ran two more
queries per period for the footer.  This module uses one bounded,
database-side aggregation and groups by a single period bucket.  Account
currency is included only when presentation-currency conversion actually
needs it.
"""

from collections import defaultdict
from typing import Any, TypedDict, Unpack, cast

import frappe
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
    get_accounting_dimensions,
    get_dimension_with_children,
)
from erpnext.accounts.report.utils import convert as convert_currency
from erpnext.accounts.report.utils import get_currency
from frappe.query_builder import Case, DocType
from frappe.query_builder.functions import Sum

from .pnlcc_finance import finance_book_clause
from .pnlcc_parsing import parse_multiselect


class LedgerFilters(TypedDict):
    """Shared ledger scope passed unchanged through the aggregation helpers."""

    company: str
    finance_book: object
    include_default_fb: bool
    project_filters: object
    restrict_cost_centers: list[str] | None


ROOT_TYPES = ("Income", "Expense")


def get_cost_centres(
    company: str,
    required_cost_centers: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return the relevant part of the company's cost-centre tree.

    A zero-filtered report only needs cost centres represented by the ledger
    aggregate and their nested-set ancestors.  Restricting that query avoids
    materialising the full company tree when most centres are inactive for
    the requested period.  ``None`` keeps the complete-tree behaviour for
    callers that explicitly request zero-valued rows.
    """
    cost_center = DocType("Cost Center")

    fields = (
        cost_center.name,
        cost_center.parent_cost_center,
        cost_center.is_group,
        cost_center.lft,
        cost_center.rgt,
    )
    if required_cost_centers is None:
        query = (
            frappe.qb.from_(cost_center)
            .select(*fields)
            .where(cost_center.company == company)
            .orderby(cost_center.lft)
        )
    else:
        if not required_cost_centers:
            return []
        active = DocType("Cost Center").as_("active_cost_center")
        query = (
            frappe.qb.from_(cost_center)
            .join(active)
            .on(
                (cost_center.company == company)
                & (active.company == company)
                & (cost_center.lft <= active.lft)
                & (cost_center.rgt >= active.rgt)
                & active.name.isin(required_cost_centers)
            )
            .select(*fields)
            .distinct()
            .orderby(cost_center.lft)
        )
    rows = query.run(as_dict=True)
    active_right_edges: list[int] = []
    for row in rows:
        right_edge = int(row.get("rgt") or 0)
        while active_right_edges and active_right_edges[-1] < right_edge:
            active_right_edges.pop()
        row["indent"] = len(active_right_edges)
        active_right_edges.append(right_edge)
    return rows


def get_report_amounts(
    *,
    periods: list[Any],
    dimension_filters: object = None,
    presentation_currency: str | None = None,
    show_amount_in_company_currency: bool = False,
    **ledger_filters: Unpack[LedgerFilters],
) -> tuple[dict[str, dict[str, float]], dict[str, list[float]]]:
    """Return cost-centre net values and positive Income/Expense totals.

    The returned cost-centre value is ``income - expense``.  Income is
    therefore positive and expenses reduce the cost-centre result, while the
    separate root totals remain positive for presentation and margin views.
    When requested, amounts are grouped by account currency so presentation
    conversion follows ERPNext's v16 currency contract rather than labelling
    company-currency values as another currency.
    """
    if not periods:
        return {}, {root_type: [] for root_type in ROOT_TYPES}

    needs_account_currency = bool(
        presentation_currency and not show_amount_in_company_currency
    )
    rows = _run_amount_query(
        company=ledger_filters["company"],
        periods=periods,
        finance_book=ledger_filters["finance_book"],
        include_default_fb=ledger_filters["include_default_fb"],
        project_filters=ledger_filters["project_filters"],
        restrict_cost_centers=ledger_filters["restrict_cost_centers"],
        dimension_filters=dimension_filters,
        needs_account_currency=needs_account_currency,
    )

    currency_info = None
    if presentation_currency:
        currency_info = get_currency(
            frappe._dict(
                company=ledger_filters["company"],
                presentation_currency=presentation_currency,
                period_end_date=periods[-1].to_date,
            )
        )

    account_currencies = (
        {str(row.get("account_currency") or "") for row in rows}
        if needs_account_currency
        else set()
    )
    use_account_currency = bool(
        presentation_currency
        and not show_amount_in_company_currency
        and len(account_currencies) == 1
        and next(iter(account_currencies), None) == presentation_currency
    )

    cost_centre_amounts: dict[str, dict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    totals: dict[str, list[float]] = {
        root_type: [0.0] * len(periods) for root_type in ROOT_TYPES
    }
    period_indexes = {str(period.key): index for index, period in enumerate(periods)}

    for row in rows:
        root_type = str(row.get("root_type") or "")
        if root_type not in ROOT_TYPES:
            continue

        cost_center = str(row.get("cost_center") or "")
        period_key = str(row.get("period_key") or "")
        period_index = period_indexes.get(period_key)
        if period_index is None:
            continue

        base_value = float(row.get("base_amount") or 0.0)
        account_value = float(row.get("account_amount") or 0.0)
        value = _convert_amount(
            base_value=base_value,
            account_value=account_value,
            use_account_currency=use_account_currency,
            currency_info=currency_info,
        )

        # GL stores debit - credit.  Income is presented as credit -
        # debit, while expenses lower the net cost-centre result.
        positive_root_value = -value if root_type == "Income" else value
        totals[root_type][period_index] += positive_root_value
        if cost_center:
            cost_centre_amounts[cost_center][period_key] += (
                positive_root_value if root_type == "Income" else -positive_root_value
            )

    return (
        {name: dict(values) for name, values in cost_centre_amounts.items()},
        totals,
    )


def _run_amount_query(
    *,
    periods: list[Any],
    dimension_filters: object,
    needs_account_currency: bool,
    **ledger_filters: Unpack[LedgerFilters],
) -> list[dict[str, Any]]:
    """Aggregate ledger movement once for all periods and root types.

    A bucketed result avoids one wide ``SUM(CASE ...)`` expression for every
    period.  That keeps the database expression small for monthly reports and
    lets Python pivot the already-aggregated rows into the report's public
    period-keyed shape.  Account currency is deliberately absent from the
    select and grouping clauses unless conversion needs account-currency
    amounts.
    """
    gl = DocType("GL Entry")
    account = DocType("Account")

    period_bucket = _build_period_bucket(gl, periods)
    select_fields = _build_amount_select_fields(
        gl=gl,
        account=account,
        period_bucket=period_bucket,
        needs_account_currency=needs_account_currency,
    )

    query = (
        frappe.qb.from_(gl)
        .join(account)
        .on(account.name == gl.account)
        .select(*select_fields)
        .where(gl.company == ledger_filters["company"])
        .where(gl.is_cancelled == 0)
        .where(gl.voucher_type != "Period Closing Voucher")
        .where(gl.posting_date >= periods[0].from_date)
        .where(gl.posting_date <= periods[-1].to_date)
        .where(account.root_type.isin(ROOT_TYPES))
    )

    query = _apply_optional_filters(
        query,
        gl=gl,
        company=ledger_filters["company"],
        finance_book=ledger_filters["finance_book"],
        include_default_fb=ledger_filters["include_default_fb"],
        project_filters=ledger_filters["project_filters"],
        restrict_cost_centers=ledger_filters["restrict_cost_centers"],
    )
    query = _apply_dimension_filters(query, gl, dimension_filters)

    group_fields: list[Any] = [gl.cost_center, account.root_type, period_bucket]
    if needs_account_currency:
        group_fields.append(gl.account_currency)
    query = query.groupby(*group_fields)
    return query.run(as_dict=True)


def _build_period_bucket(gl: Any, periods: list[Any]) -> Any:
    period_bucket = Case()
    for period in periods:
        period_criterion = (gl.posting_date >= period.from_date) & (
            gl.posting_date <= period.to_date
        )
        period_bucket = period_bucket.when(period_criterion, str(period.key))
    return period_bucket.else_(None)


def _build_amount_select_fields(
    *,
    gl: Any,
    account: Any,
    period_bucket: Any,
    needs_account_currency: bool,
) -> list[Any]:
    fields: list[Any] = [
        gl.cost_center,
        account.root_type,
        period_bucket.as_("period_key"),
    ]
    if needs_account_currency:
        fields.append(gl.account_currency)
    fields.append(Sum(cast(Any, gl.debit - gl.credit)).as_("base_amount"))
    if needs_account_currency:
        fields.append(
            Sum(
                cast(Any, gl.debit_in_account_currency - gl.credit_in_account_currency)
            ).as_("account_amount")
        )
    return fields


def _apply_optional_filters(
    query: Any,
    *,
    gl: Any,
    **ledger_filters: Unpack[LedgerFilters],
) -> Any:
    if ledger_filters["restrict_cost_centers"]:
        query = query.where(
            gl.cost_center.isin(ledger_filters["restrict_cost_centers"])
        )

    projects = parse_multiselect(ledger_filters["project_filters"])
    if projects:
        query = query.where(gl.project.isin(projects))

    return query.where(
        finance_book_clause(
            gl,
            company=ledger_filters["company"],
            finance_book=ledger_filters["finance_book"],
            include_default_fb=ledger_filters["include_default_fb"],
        )
    )


def _apply_dimension_filters(query: Any, gl: Any, dimension_filters: object) -> Any:
    for dimension in get_accounting_dimensions(as_list=False) or []:
        fieldname = getattr(dimension, "fieldname", None)
        if not fieldname:
            continue
        selected = parse_multiselect(_get_filter_value(dimension_filters, fieldname))
        if not selected:
            continue
        if frappe.get_cached_value("DocType", dimension.document_type, "is_tree"):
            selected = get_dimension_with_children(dimension.document_type, selected)
        if selected:
            query = query.where(gl[fieldname].isin(selected))
    return query


def _get_filter_value(filters: object, fieldname: str) -> object:
    if filters is None:
        return None
    if isinstance(filters, dict):
        return filters.get(fieldname)
    return getattr(filters, fieldname, None)


def _convert_amount(
    *,
    base_value: float,
    account_value: float,
    use_account_currency: bool,
    currency_info: dict[str, Any] | None,
) -> float:
    if use_account_currency:
        return account_value
    if currency_info:
        return float(
            convert_currency(
                base_value,
                currency_info["presentation_currency"],
                currency_info["company_currency"],
                currency_info["report_date"],
            )
        )
    return base_value


def get_amounts_by_cost_centre(
    *,
    from_date: object,
    to_date: object,
    **ledger_filters: Unpack[LedgerFilters],
) -> dict[str, float]:
    """Compatibility wrapper for callers needing one period of net values."""
    period = frappe._dict(from_date=from_date, to_date=to_date, key="single_period")
    amounts, _totals = get_report_amounts(
        company=ledger_filters["company"],
        periods=[period],
        finance_book=ledger_filters["finance_book"],
        include_default_fb=ledger_filters["include_default_fb"],
        project_filters=ledger_filters["project_filters"],
        restrict_cost_centers=ledger_filters["restrict_cost_centers"],
    )
    period_key = str(period.key)
    return {name: values.get(period_key, 0.0) for name, values in amounts.items()}


def get_total_by_root_type(
    *,
    from_date: object,
    to_date: object,
    root_type: str,
    **ledger_filters: Unpack[LedgerFilters],
) -> float:
    """Compatibility wrapper for a positive one-period root total."""
    if root_type not in ROOT_TYPES:
        return 0.0
    period = frappe._dict(from_date=from_date, to_date=to_date, key="single_period")
    _amounts, totals = get_report_amounts(
        company=ledger_filters["company"],
        periods=[period],
        finance_book=ledger_filters["finance_book"],
        include_default_fb=ledger_filters["include_default_fb"],
        project_filters=ledger_filters["project_filters"],
        restrict_cost_centers=ledger_filters["restrict_cost_centers"],
    )
    return totals[root_type][0] if totals[root_type] else 0.0
