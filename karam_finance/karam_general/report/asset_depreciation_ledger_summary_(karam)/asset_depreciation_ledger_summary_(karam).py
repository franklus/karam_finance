"""As-of, one-row-per-asset depreciation summary."""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Count, IfNull, NullIf, Sum
from frappe.utils import cint, cstr, flt, getdate


def execute(
    filters: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], list[frappe._dict[str, Any]]]:
    """Return the established Script Report contract."""
    report_filters = frappe._dict(filters or {})
    return get_columns(), get_data(report_filters)


def get_data(filters: frappe._dict[str, Any]) -> list[frappe._dict[str, Any]]:
    """Build one summary row for every eligible Asset in bounded bulk queries."""
    finance_book = _resolve_finance_book(filters)
    finance_book_scope = _get_finance_book_scope(filters, finance_book)
    assets = _get_assets_details(filters, finance_book_scope)
    if not assets:
        return []

    asset_names = list(assets)
    schedules = _get_schedule_details(asset_names, finance_book_scope)
    gl_totals = _get_gl_totals(filters, asset_names, finance_book_scope)
    booked_counts = _get_booked_depreciation_counts(filters, schedules)

    return _build_summary_rows(
        filters,
        assets,
        schedules,
        gl_totals=gl_totals,
        booked_counts=booked_counts,
    )


def _build_asset_filters(filters: frappe._dict[str, Any]) -> dict[str, Any]:
    """Return the user-facing Asset constraints used by the report."""
    asset_filters: dict[str, Any] = {
        "company": filters.get("company"),
        "docstatus": 1,
        "purchase_date": ("<=", filters.get("to_date")),
        "status": ("not in", ["Draft", "Cancelled"]),
    }

    if filters.get("asset"):
        asset_filters["name"] = filters.get("asset")
    if filters.get("asset_category"):
        asset_filters["asset_category"] = filters.get("asset_category")
    if filters.get("status"):
        asset_filters["status"] = filters.get("status")

    return asset_filters


def _get_assets_details(
    filters: frappe._dict[str, Any],
    finance_book_scope: tuple[str, ...],
) -> dict[str, frappe._dict[str, Any]]:
    """Fetch eligible Asset metadata in one database query."""
    asset = frappe.qb.DocType("Asset")
    query = (
        frappe.qb.from_(asset)
        .select(
            asset.name.as_("asset"),
            asset.asset_name,
            asset.status,
            asset.asset_category,
            asset.purchase_date,
            asset.disposal_date,
            asset.net_purchase_amount,
            asset.opening_accumulated_depreciation,
            asset.opening_number_of_booked_depreciations,
            asset.total_number_of_depreciations,
        )
        .where(asset.company == filters.get("company"))
        .where(asset.docstatus == 1)
        .where(asset.purchase_date <= filters.get("to_date"))
        .where(asset.status.notin(["Draft", "Cancelled"]))
    )

    if filters.get("asset"):
        query = query.where(asset.name == filters.get("asset"))
    if filters.get("asset_category"):
        query = query.where(asset.asset_category == filters.get("asset_category"))
    if filters.get("status"):
        query = query.where(asset.status == filters.get("status"))

    # An explicit book must be backed by a matching v16 schedule. This keeps
    # a report for one book from returning assets that only have another book.
    selected_books = tuple(book for book in finance_book_scope if book)
    if selected_books:
        schedule = frappe.qb.DocType("Asset Depreciation Schedule")
        matching_schedules = (
            frappe.qb.from_(schedule)
            .select(schedule.asset)
            .where(schedule.docstatus == 1)
            .where(schedule.status == "Active")
        )
        if "" in finance_book_scope:
            matching_schedules = matching_schedules.where(
                schedule.finance_book.isnull()
                | schedule.finance_book.isin([*selected_books, ""])
            )
        else:
            matching_schedules = matching_schedules.where(
                schedule.finance_book.isin(selected_books)
            )
        query = query.where(asset.name.isin(matching_schedules))

    rows = query.orderby(asset.name).run(as_dict=True)
    return {row.asset: row for row in rows}


def _get_schedule_details(
    assets: list[str],
    finance_book_scope: tuple[str, ...],
) -> dict[str, frappe._dict[str, Any]]:
    """Fetch and select one active v16 schedule per Asset."""
    if not assets:
        return {}

    schedule = frappe.qb.DocType("Asset Depreciation Schedule")
    query = (
        frappe.qb.from_(schedule)
        .select(
            schedule.name,
            schedule.asset,
            schedule.finance_book,
            schedule.total_number_of_depreciations,
            schedule.opening_accumulated_depreciation,
            schedule.opening_number_of_booked_depreciations,
            schedule.idx,
        )
        .where(schedule.asset.isin(assets))
        .where(schedule.docstatus == 1)
        .where(schedule.status == "Active")
        .orderby(schedule.asset, schedule.idx)
    )
    named_books = [book for book in finance_book_scope if book]
    if "" in finance_book_scope:
        query = query.where(
            schedule.finance_book.isnull()
            | schedule.finance_book.isin([*named_books, ""])
        )
    else:
        query = query.where(schedule.finance_book.isin(named_books))

    return _select_schedule_rows(query.run(as_dict=True), finance_book_scope)


def _select_schedule_rows(
    candidates: list[frappe._dict[str, Any]],
    finance_book_scope: tuple[str, ...],
) -> dict[str, frappe._dict[str, Any]]:
    """Select one deterministic active schedule per asset."""
    selected: dict[str, frappe._dict[str, Any]] = {}
    selected_priority: dict[str, tuple[int, int, str]] = {}
    for row in candidates:
        asset_name = cstr(row.get("asset"))
        if not asset_name:
            continue

        row_book = cstr(row.get("finance_book"))
        if not _schedule_matches_scope(row_book, finance_book_scope):
            continue
        priority = _schedule_priority(row, row_book, finance_book_scope)
        if (
            asset_name in selected_priority
            and priority >= selected_priority[asset_name]
        ):
            continue
        selected[asset_name] = row
        selected_priority[asset_name] = priority

    return selected


def _schedule_matches_scope(
    finance_book: str,
    finance_book_scope: tuple[str, ...],
) -> bool:
    """Match blank/null and named Finance Books using one normalised rule."""
    return finance_book in finance_book_scope


def _schedule_priority(
    row: frappe._dict[str, Any],
    finance_book: str,
    finance_book_scope: tuple[str, ...],
) -> tuple[int, int, str]:
    """Prefer the requested named schedule over a default-book fallback."""
    named_books = [book for book in finance_book_scope if book]
    if finance_book in named_books:
        book_priority = named_books.index(finance_book)
    else:
        book_priority = len(named_books)
    return book_priority, cint(row.get("idx")), cstr(row.get("name"))


def _get_booked_depreciation_counts(
    filters: frappe._dict[str, Any],
    schedules: dict[str, frappe._dict[str, Any]],
) -> dict[str, tuple[int, int]]:
    """Bulk-count booked schedule rows before and through the report period."""
    if not schedules:
        return {}

    schedule = frappe.qb.DocType("Depreciation Schedule")
    opening_indicator: Any = (
        Case()
        .when(schedule.schedule_date < getdate(filters.get("from_date")), 1)
        .else_(0)
    )
    query = (
        frappe.qb.from_(schedule)
        .select(
            schedule.parent.as_("schedule"),
            Sum(opening_indicator).as_("opening_count"),
            Count("*").as_("booked_count"),
        )
        .where(schedule.parent.isin([row.name for row in schedules.values()]))
        .where(schedule.parenttype == "Asset Depreciation Schedule")
        .where(schedule.docstatus == 1)
        .where(schedule.schedule_date <= filters.get("to_date"))
        .where(schedule.journal_entry.isnotnull())
        .where(schedule.journal_entry != "")
    )

    counts_by_schedule = {
        cstr(row.schedule): (cint(row.opening_count), cint(row.booked_count))
        for row in query.groupby(schedule.parent).run(as_dict=True)
    }
    return {
        asset: counts_by_schedule.get(cstr(row.get("name")), (0, 0))
        for asset, row in schedules.items()
    }


def _get_gl_totals(
    filters: frappe._dict[str, Any],
    assets: list[str],
    finance_book_scope: tuple[str, ...],
) -> dict[str, frappe._dict[str, Any]]:
    """Aggregate expense and accumulated-depreciation activity per Asset."""
    if not assets:
        return {}

    asset = frappe.qb.DocType("Asset")
    gl_entry = frappe.qb.DocType("GL Entry")
    category_account = frappe.qb.DocType("Asset Category Account")
    company = frappe.qb.DocType("Company")
    account = frappe.qb.DocType("Account")

    depreciation_account = IfNull(
        NullIf(category_account.depreciation_expense_account, ""),
        company.depreciation_expense_account,
    )
    accumulated_account = IfNull(
        NullIf(category_account.accumulated_depreciation_account, ""),
        company.accumulated_depreciation_account,
    )
    expense_movement = (
        frappe.qb.terms.Case()
        .when(
            account.root_type == "Income",
            gl_entry.credit - gl_entry.debit,
        )
        .else_(gl_entry.debit - gl_entry.credit)
    )
    accumulated_movement = gl_entry.credit - gl_entry.debit

    def _sum_when(condition: Any, expression: Any, alias: str) -> Any:
        return IfNull(
            Sum(frappe.qb.terms.Case().when(condition, expression).else_(0)),
            0,
        ).as_(alias)

    query = (
        frappe.qb.from_(gl_entry)
        .join(asset)
        .on(gl_entry.against_voucher == asset.name)
        .left_join(category_account)
        .on(
            (category_account.parent == asset.asset_category)
            & (category_account.parenttype == "Asset Category")
            & (category_account.company_name == asset.company)
        )
        .join(company)
        .on(company.name == asset.company)
        .left_join(account)
        .on(account.name == depreciation_account)
        .select(
            asset.name.as_("asset"),
            _sum_when(
                (gl_entry.account == depreciation_account)
                & (gl_entry.posting_date >= filters.get("from_date"))
                & (gl_entry.posting_date <= filters.get("to_date")),
                expense_movement,
                "depreciation_amount",
            ),
            _sum_when(
                (gl_entry.account == accumulated_account)
                & (gl_entry.posting_date < filters.get("from_date")),
                accumulated_movement,
                "opening_accumulated_activity",
            ),
            _sum_when(
                (gl_entry.account == accumulated_account)
                & (gl_entry.posting_date >= filters.get("from_date"))
                & (gl_entry.posting_date <= filters.get("to_date")),
                accumulated_movement,
                "accumulated_activity",
            ),
        )
        .where(asset.name.isin(assets))
        .where(asset.company == filters.get("company"))
        .where(gl_entry.against_voucher_type == "Asset")
        .where(gl_entry.posting_date <= filters.get("to_date"))
        .where(gl_entry.is_cancelled == 0)
        .where(
            asset.disposal_date.isnull()
            | (gl_entry.posting_date <= asset.disposal_date)
        )
        .groupby(asset.name)
    )

    query = _apply_gl_finance_book_scope(query, gl_entry, finance_book_scope)
    rows = query.run(as_dict=True)
    return {row.asset: row for row in rows}


def _apply_gl_finance_book_scope(
    query: Any,
    gl_entry: Any,
    finance_book_scope: tuple[str, ...],
) -> Any:
    """Restrict ledger rows to the selected schedule book(s)."""
    named_books = [book for book in finance_book_scope if book]
    if "" in finance_book_scope:
        condition = gl_entry.finance_book.isnull() | gl_entry.finance_book.isin(
            [*named_books, ""]
        )
        return query.where(condition)

    # ERPNext's scrap/disposal Journal Entry is intentionally unbooked even
    # when the asset's active depreciation schedule has a named book. Include
    # that linked disposal movement without reopening unrelated default-book
    # activity.
    blank_disposal = (
        gl_entry.finance_book.isnull() | (gl_entry.finance_book == "")
    ) & (gl_entry.voucher_subtype == "Asset Disposal")
    return query.where(gl_entry.finance_book.isin(named_books) | blank_disposal)


def _build_summary_rows(
    filters: frappe._dict[str, Any],
    assets: dict[str, frappe._dict[str, Any]],
    schedules: dict[str, frappe._dict[str, Any]],
    *,
    gl_totals: dict[str, frappe._dict[str, Any]],
    booked_counts: dict[str, tuple[int, int]],
) -> list[frappe._dict[str, Any]]:
    """Combine bulk query results without per-asset database access."""
    to_date = getdate(filters.get("to_date"))
    rows: list[frappe._dict[str, Any]] = []

    for asset_name in sorted(assets):
        asset = assets[asset_name]
        schedule = schedules.get(asset_name, frappe._dict())
        totals = gl_totals.get(asset_name, frappe._dict())

        schedule_total = schedule.get("total_number_of_depreciations")
        total_depreciations = cint(
            schedule_total
            if schedule_total is not None
            else asset.get("total_number_of_depreciations")
        )
        schedule_opening_count = cint(
            schedule.get(
                "opening_number_of_booked_depreciations",
                asset.get("opening_number_of_booked_depreciations"),
            )
        )
        opening_booked, booked_through_to_date = booked_counts.get(asset_name, (0, 0))
        opening_count = schedule_opening_count + opening_booked
        booked_count = schedule_opening_count + booked_through_to_date

        schedule_opening_amount = flt(
            schedule.get(
                "opening_accumulated_depreciation",
                asset.get("opening_accumulated_depreciation"),
            )
        )
        opening_accumulated = schedule_opening_amount + flt(
            totals.get("opening_accumulated_activity")
        )
        accumulated = opening_accumulated + flt(totals.get("accumulated_activity"))
        purchase_amount = flt(asset.get("net_purchase_amount"))
        raw_disposal_date = asset.get("disposal_date")
        disposal_date = getdate(raw_disposal_date) if raw_disposal_date else None
        value_basis = (
            0
            if disposal_date and to_date and disposal_date <= to_date
            else purchase_amount
        )

        rows.append(
            frappe._dict(
                {
                    "asset": asset_name,
                    "asset_name": asset.get("asset_name"),
                    "status": asset.get("status"),
                    "asset_category": asset.get("asset_category"),
                    "purchase_date": asset.get("purchase_date"),
                    "purchase_amount": purchase_amount,
                    "total_no_of_depreciations": total_depreciations,
                    "opening_no_of_booked_depreciations": opening_count,
                    "pending_depreciations": max(0, total_depreciations - booked_count),
                    "opening_accumulated_depreciation": opening_accumulated,
                    "depreciation_amount": flt(totals.get("depreciation_amount")),
                    "accumulated_depreciation": accumulated,
                    "value_after_depreciation": value_basis - accumulated,
                }
            )
        )

    return rows


def _resolve_finance_book(filters: frappe._dict[str, Any]) -> str | None:
    """Resolve the report's effective Finance Book using ERPNext conventions."""
    company = cstr(filters.get("company"))
    company_finance_book = frappe.get_cached_value(
        "Company",
        company,
        "default_finance_book",
    )
    selected_finance_book = cstr(filters.get("finance_book"))

    if cint(filters.get("include_default_book_assets")) and company_finance_book:
        if selected_finance_book and selected_finance_book != cstr(
            company_finance_book
        ):
            frappe.throw(
                _(
                    "To use a different finance book, please uncheck "
                    "'Include Default FB Assets'"
                )
            )
        return cstr(company_finance_book)

    return selected_finance_book or None


def _get_finance_book_scope(
    filters: frappe._dict[str, Any],
    finance_book: str | None,
) -> tuple[str, ...]:
    """Return normalised schedule/ledger books, preserving default-book policy."""
    if not finance_book:
        return ("",)

    if cint(filters.get("include_default_book_assets")):
        return (finance_book, "")
    return (finance_book,)


def get_columns() -> list[dict[str, Any]]:
    """Return the established one-row-per-Asset columns."""
    return [
        {
            "label": _("Asset"),
            "fieldname": "asset",
            "fieldtype": "Link",
            "options": "Asset",
            "width": 140,
        },
        {
            "label": _("Asset Name"),
            "fieldname": "asset_name",
            "fieldtype": "Data",
            "width": 220,
        },
        {
            "label": _("Status"),
            "fieldname": "status",
            "fieldtype": "Data",
            "width": 130,
        },
        {
            "label": _("Asset Category"),
            "fieldname": "asset_category",
            "fieldtype": "Link",
            "options": "Asset Category",
            "width": 150,
        },
        {
            "label": _("Purchase Date"),
            "fieldname": "purchase_date",
            "fieldtype": "Date",
            "width": 120,
        },
        {
            "label": _("Purchase Amount"),
            "fieldname": "purchase_amount",
            "fieldtype": "Currency",
            "width": 150,
        },
        {
            "label": _("Total No of Depreciations"),
            "fieldname": "total_no_of_depreciations",
            "fieldtype": "Int",
            "width": 180,
        },
        {
            "label": _("Opening No of Booked Depreciations"),
            "fieldname": "opening_no_of_booked_depreciations",
            "fieldtype": "Int",
            "width": 220,
        },
        {
            "label": _("Pending Depreciations"),
            "fieldname": "pending_depreciations",
            "fieldtype": "Int",
            "width": 170,
        },
        {
            "label": _("Opening Accumulated Depreciation"),
            "fieldname": "opening_accumulated_depreciation",
            "fieldtype": "Currency",
            "width": 220,
        },
        {
            "label": _("Depreciation Amount"),
            "fieldname": "depreciation_amount",
            "fieldtype": "Currency",
            "width": 170,
        },
        {
            "label": _("Accumulated Depreciation"),
            "fieldname": "accumulated_depreciation",
            "fieldtype": "Currency",
            "width": 200,
        },
        {
            "label": _("Value after Depreciation"),
            "fieldname": "value_after_depreciation",
            "fieldtype": "Currency",
            "width": 180,
        },
    ]
