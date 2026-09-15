"""Pure contracts for the asset depreciation ledger summary."""

from __future__ import annotations

import importlib
import sqlite3
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import frappe
import pytest
from frappe.query_builder.builder import MariaDB
from pypika import Case

MODULE = "karam_finance.karam_general.report.asset_depreciation_ledger_summary_(karam).asset_depreciation_ledger_summary_(karam)"


@pytest.fixture(scope="module")
def report() -> Any:
    return importlib.import_module(MODULE)


def test_summary_financial_totals_disposal_and_pending_clamp(report: Any) -> None:
    filters = frappe._dict(to_date="2026-01-31")
    assets = {
        "A": frappe._dict(
            asset_name="A",
            net_purchase_amount=1000,
            total_number_of_depreciations=2,
            opening_accumulated_depreciation=100,
            opening_number_of_booked_depreciations=1,
        ),
        "D": frappe._dict(
            net_purchase_amount=100,
            total_number_of_depreciations=1,
            disposal_date="2026-01-01",
        ),
    }
    rows = report._build_summary_rows(
        filters,
        assets,
        {"A": frappe._dict(total_number_of_depreciations=3)},
        gl_totals={
            "A": frappe._dict(
                opening_accumulated_activity=0,
                accumulated_activity=50,
                depreciation_amount=50,
            )
        },
        booked_counts={"A": (0, 1), "D": (0, 9)},
    )
    assert (
        rows[0].purchase_amount == 1000
        and rows[0].opening_accumulated_depreciation == 100
    )
    assert (
        rows[0].accumulated_depreciation == 150
        and rows[0].value_after_depreciation == 850
        and rows[0].pending_depreciations == 1
    )
    assert (
        rows[1].value_after_depreciation == -0.0 and rows[1].pending_depreciations == 0
    )


def test_schedule_selection_prefers_named_then_idx_then_name(report: Any) -> None:
    rows = [
        frappe._dict(asset="A", finance_book="", idx=1, name="default"),
        frappe._dict(asset="A", finance_book="Book", idx=9, name="later"),
        frappe._dict(asset="A", finance_book="Book", idx=1, name="first"),
        frappe._dict(asset="", finance_book="Book", idx=1, name="skip"),
        frappe._dict(asset="B", finance_book="Other", idx=1, name="skip"),
    ]
    assert report._select_schedule_rows(rows, ("Book", ""))["A"].name == "first"
    assert report._select_schedule_rows(rows, ("Book",))["A"].name == "first"


def test_finance_book_scope_conflict_and_empty_no_query(report: Any) -> None:
    assert report._get_finance_book_scope(frappe._dict(), None) == ("",)
    assert report._get_finance_book_scope(
        frappe._dict(include_default_book_assets=1), "Book"
    ) == ("Book", "")
    with (
        patch.object(report.frappe, "get_cached_value", return_value="Default"),
        patch.object(report.frappe, "throw", side_effect=RuntimeError("conflict")),
        pytest.raises(RuntimeError),
    ):
        report._resolve_finance_book(
            frappe._dict(
                company="K", finance_book="Other", include_default_book_assets=1
            )
        )
    with (
        patch.object(report, "_resolve_finance_book", return_value=None),
        patch.object(report, "_get_assets_details", return_value={}),
        patch.object(report, "_get_schedule_details") as schedules,
    ):
        assert report.get_data(frappe._dict(company="K")) == []
    schedules.assert_not_called()


def test_asset_filters_and_schedule_priority_contract(report: Any) -> None:
    filters = frappe._dict(
        company="K",
        to_date="2026-01-31",
        asset="A",
        asset_category="Cat",
        status="Submitted",
    )
    assert report._build_asset_filters(filters) == {
        "company": "K",
        "docstatus": 1,
        "purchase_date": ("<=", "2026-01-31"),
        "status": "Submitted",
        "name": "A",
        "asset_category": "Cat",
    }
    assert report._schedule_matches_scope("", ("Book", ""))
    assert not report._schedule_matches_scope("Other", ("Book", ""))
    assert report._schedule_priority(
        frappe._dict(idx=2, name="x"), "Book", ("Book", "")
    ) < report._schedule_priority(frappe._dict(idx=1, name="a"), "", ("Book", ""))


def test_booked_counts_empty_and_summary_execute_delegate(report: Any) -> None:
    assert report._get_booked_depreciation_counts(frappe._dict(), {}) == {}
    with (
        patch.object(report, "get_columns", return_value=[{"x": 1}]),
        patch.object(report, "get_data", return_value=[frappe._dict(asset="A")]),
    ):
        assert report.execute({"company": "K"}) == (
            [{"x": 1}],
            [frappe._dict(asset="A")],
        )


def test_asset_and_schedule_queries_compile_scope_and_select_rows(report: Any) -> None:
    asset_rows = [frappe._dict(asset="A", asset_name="A")]
    schedule_rows = [
        frappe._dict(asset="A", name="named", finance_book="Book", idx=2),
        frappe._dict(asset="A", name="default", finance_book="", idx=1),
    ]
    captured: list[str] = []
    query_type = type(MariaDB.from_(MariaDB.DocType("Asset")))

    def capture(query: Any, **_kwargs: Any) -> list[Any]:
        captured.append(query.get_sql())
        return asset_rows if len(captured) == 1 else schedule_rows

    asset = MariaDB.DocType("Asset")
    qb = SimpleNamespace(
        DocType=MariaDB.DocType,
        from_=MariaDB.from_,
        get_query=MagicMock(return_value=MariaDB.from_(asset).select(asset.name)),
    )
    filters = frappe._dict(
        company="K", to_date="2026-01-31", asset_category="Cat", status="Submitted"
    )
    with (
        patch.object(report.frappe, "qb", qb),
        patch.object(query_type, "run", capture),
    ):
        assert report._get_assets_details(filters, ("Book", "")) == {"A": asset_rows[0]}
        assert report._get_schedule_details(["A"], ("Book", ""))["A"].name == "named"
        assert report._get_schedule_details([], ("Book",)) == {}
    asset_sql, schedule_sql = captured
    for token in (
        "`company`='K'",
        "`docstatus`=1",
        "`purchase_date`<='2026-01-31'",
        "`status` NOT IN ('Draft','Cancelled')",
        "`asset_category`='Cat'",
        "`status`='Submitted'",
        "finance_book",
    ):
        assert token in asset_sql
    for token in (
        "`asset` IN ('A')",
        "`docstatus`=1",
        "`status`='Active'",
        "ORDER BY `asset`,`idx`",
        "finance_book",
    ):
        assert token in schedule_sql


@pytest.mark.parametrize(
    ("permitted_location", "selected_asset", "expected"),
    [
        ("Allowed", None, ["A"]),
        (None, None, ["A", "B"]),
        ("Allowed", "B", []),
        ("Missing", None, []),
    ],
)
def test_asset_visibility_filters_metadata_before_summary_and_related_reads(
    report: Any,
    *,
    permitted_location: str | None,
    selected_asset: str | None,
    expected: list[str],
) -> None:
    # Execute the report SQL against real rows; replace only Frappe's site-backed
    # permission query with the Location scope it would supply for this reader.
    asset = MariaDB.DocType("Asset")
    permitted = MariaDB.from_(asset).select(asset.name)
    if permitted_location is not None:
        permitted = permitted.where(asset.location == permitted_location)
    get_query = MagicMock(return_value=permitted)
    qb = SimpleNamespace(
        DocType=MariaDB.DocType, from_=MariaDB.from_, get_query=get_query
    )
    query_type = type(MariaDB.from_(asset))
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            'CREATE TABLE "tabAsset" (name TEXT, asset_name TEXT, company TEXT, '
            "docstatus INTEGER, status TEXT, asset_category TEXT, purchase_date TEXT, "
            "disposal_date TEXT, net_purchase_amount REAL, "
            "opening_accumulated_depreciation REAL, "
            "opening_number_of_booked_depreciations INTEGER, "
            "total_number_of_depreciations INTEGER, location TEXT)"
        )
        connection.executemany(
            'INSERT INTO "tabAsset" VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            [
                (
                    name,
                    name,
                    "K",
                    1,
                    "Submitted",
                    "Cat",
                    "2025-01-01",
                    None,
                    5000,
                    500,
                    1,
                    10,
                    location,
                )
                for name, location in (("A", "Allowed"), ("B", "Denied"))
            ],
        )

        def execute_query(query: Any, **_kwargs: Any) -> list[Any]:
            cursor = connection.execute(query.get_sql())
            columns = [column[0] for column in cursor.description]
            return [frappe._dict(zip(columns, row, strict=True)) for row in cursor]

        with (
            patch.object(report.frappe, "qb", qb),
            patch.object(query_type, "run", execute_query),
            patch.object(report, "_resolve_finance_book", return_value=None),
            patch.object(report, "_get_schedule_details", return_value={}) as schedules,
            patch.object(report, "_get_gl_totals", return_value={}) as totals,
            patch.object(report, "_get_booked_depreciation_counts", return_value={}),
        ):
            rows = report.get_data(
                frappe._dict(company="K", to_date="2026-01-31", asset=selected_asset)
            )
        assert [row.asset for row in rows] == expected
        assert all(row.purchase_amount == 5000 for row in rows)
        assert all(row.opening_accumulated_depreciation == 500 for row in rows)
        get_query.assert_called_once_with(
            "Asset", fields=["name"], ignore_permissions=False
        )
        if expected:
            assert schedules.call_args.args[0] == expected
            assert totals.call_args.args[1] == expected
        else:
            schedules.assert_not_called()
            totals.assert_not_called()


def test_summary_zero_schedule_and_disposal_dates(report: Any) -> None:
    assets = {
        "A": frappe._dict(
            net_purchase_amount=100,
            total_number_of_depreciations=2,
            opening_accumulated_depreciation=9,
            opening_number_of_booked_depreciations=3,
        ),
        "Future": frappe._dict(
            net_purchase_amount=100,
            total_number_of_depreciations=1,
            disposal_date="2026-02-01",
        ),
        "Now": frappe._dict(
            net_purchase_amount=100,
            total_number_of_depreciations=1,
            disposal_date="2026-01-31",
        ),
    }
    schedules = {
        "A": frappe._dict(
            total_number_of_depreciations=0,
            opening_accumulated_depreciation=0,
            opening_number_of_booked_depreciations=0,
        )
    }
    rows = report._build_summary_rows(
        frappe._dict(to_date="2026-01-31"),
        assets,
        schedules,
        gl_totals={"Now": frappe._dict(accumulated_activity=10)},
        booked_counts={},
    )
    assert (
        rows[0].total_no_of_depreciations == 0
        and rows[0].opening_accumulated_depreciation == 0
    )
    assert (
        rows[1].value_after_depreciation == 100
        and rows[2].value_after_depreciation == -10
    )


def test_booked_count_query_has_asof_scope_and_zero_fallback(report: Any) -> None:
    captured = []
    query_type = type(MariaDB.from_(MariaDB.DocType("Depreciation Schedule")))

    def capture(query: Any, **_kwargs: Any) -> list[Any]:
        captured.append(query.get_sql())
        return [frappe._dict(schedule="S-A", opening_count=2, booked_count=3)]

    qb = SimpleNamespace(DocType=MariaDB.DocType, from_=MariaDB.from_)
    schedules = {"A": frappe._dict(name="S-A"), "B": frappe._dict(name="S-B")}
    with (
        patch.object(report.frappe, "qb", qb),
        patch.object(query_type, "run", capture),
    ):
        assert report._get_booked_depreciation_counts(
            frappe._dict(from_date="2026-01-01", to_date="2026-01-31"), schedules
        ) == {"A": (2, 3), "B": (0, 0)}
    sql = captured[0]
    for token in (
        "`parent` IN ('S-A','S-B')",
        "`parenttype`='Asset Depreciation Schedule'",
        "`docstatus`=1",
        "`schedule_date`<'2026-01-01'",
        "`schedule_date`<='2026-01-31'",
        "`journal_entry` IS NOT NULL",
        "`journal_entry`<>''",
        "GROUP BY `parent`",
    ):
        assert token in sql


def test_gl_totals_query_scopes_disposal_signs_and_books(report: Any) -> None:
    captured = []
    query_type = type(MariaDB.from_(MariaDB.DocType("GL Entry")))

    def capture(query: Any, **_kwargs: Any) -> list[Any]:
        captured.append(query.get_sql())
        return [
            frappe._dict(
                asset="A",
                depreciation_amount=5,
                opening_accumulated_activity=10,
                accumulated_activity=15,
            )
        ]

    qb = SimpleNamespace(
        DocType=MariaDB.DocType,
        from_=MariaDB.from_,
        terms=SimpleNamespace(Case=Case),
    )
    filters = frappe._dict(company="K", from_date="2026-01-01", to_date="2026-01-31")
    with (
        patch.object(report.frappe, "qb", qb),
        patch.object(report.frappe, "has_permission", return_value=True),
        patch.object(report.frappe, "build_match_conditions", return_value=""),
        patch.object(query_type, "run", capture),
    ):
        assert (
            report._get_gl_totals(filters, ["A"], ("Book",))["A"].accumulated_activity
            == 15
        )
        assert report._get_gl_totals(filters, [], ("Book",)) == {}
        report._get_gl_totals(filters, ["A"], ("Book", ""))
    sql = captured[0]
    for token in (
        "CASE WHEN `tabAccount`.`root_type`='Income' THEN `tabGL Entry`.`credit`-`tabGL Entry`.`debit` ELSE `tabGL Entry`.`debit`-`tabGL Entry`.`credit` END",
        "`tabAsset`.`company`='K'",
        "`tabGL Entry`.`against_voucher_type`='Asset'",
        "`tabGL Entry`.`is_cancelled`=0",
        "THEN `tabGL Entry`.`credit`-`tabGL Entry`.`debit` ELSE 0 END",
        "`depreciation_amount",
        "`opening_accumulated_activity`",
        "`accumulated_activity`",
        "`posting_date`<'2026-01-01'",
        "`posting_date`>='2026-01-01'",
        "`posting_date`<='2026-01-31'",
        "`tabAsset`.`disposal_date` IS NULL",
        "`tabGL Entry`.`posting_date`<=`tabAsset`.`disposal_date`",
        "`tabGL Entry`.`finance_book` IN ('Book')",
        "`tabGL Entry`.`voucher_subtype`='Asset Disposal'",
    ):
        assert token in sql
    assert (
        "(`tabAsset`.`disposal_date` IS NULL OR `tabGL Entry`.`posting_date`<=`tabAsset`.`disposal_date`)"
        in sql
    )
    assert (
        "(`tabGL Entry`.`finance_book` IS NULL OR `tabGL Entry`.`finance_book`='') AND `tabGL Entry`.`voucher_subtype`='Asset Disposal'"
        in sql
    )
    default_sql = captured[1]
    assert "`tabGL Entry`.`finance_book` IS NULL" in default_sql
    assert "`tabGL Entry`.`finance_book` IN ('Book','')" in default_sql


def test_named_scope_ties_and_finance_resolution_branches(report: Any) -> None:
    tied = [
        frappe._dict(asset="A", finance_book="Book", idx=1, name="z"),
        frappe._dict(asset="A", finance_book="Book", idx=1, name="a"),
    ]
    assert report._select_schedule_rows(tied, ("Book",))["A"].name == "a"
    with patch.object(report.frappe, "get_cached_value", return_value="Default"):
        assert (
            report._resolve_finance_book(
                frappe._dict(company="K", include_default_book_assets=1)
            )
            == "Default"
        )
        assert (
            report._resolve_finance_book(
                frappe._dict(company="K", finance_book="Chosen")
            )
            == "Chosen"
        )
        assert report._resolve_finance_book(frappe._dict(company="K")) is None
    assert report._get_finance_book_scope(
        frappe._dict(include_default_book_assets=0), "Book"
    ) == ("Book",)


def test_asset_and_schedule_named_only_and_default_scope_branches(report: Any) -> None:
    captured = []
    query_type = type(MariaDB.from_(MariaDB.DocType("Asset")))

    def capture(query: Any, **_kwargs: Any) -> list[Any]:
        captured.append(query.get_sql())
        return [frappe._dict(asset="A", name="S", finance_book="Book", idx=1)]

    asset = MariaDB.DocType("Asset")
    qb = SimpleNamespace(
        DocType=MariaDB.DocType,
        from_=MariaDB.from_,
        get_query=MagicMock(return_value=MariaDB.from_(asset).select(asset.name)),
    )
    with (
        patch.object(report.frappe, "qb", qb),
        patch.object(query_type, "run", capture),
    ):
        report._get_assets_details(
            frappe._dict(company="K", to_date="2026-01-31", asset="A"), ("Book",)
        )
        report._get_assets_details(
            frappe._dict(company="K", to_date="2026-01-31"), ("",)
        )
        report._get_schedule_details(["A"], ("Book",))
    assert "`name`='A'" in captured[0] and "`finance_book` IN ('Book')" in captured[0]
    assert "tabAsset Depreciation Schedule" not in captured[1]
    assert "`finance_book` IN ('Book')" in captured[2]
