"""Site-free query contracts; SQLite checks predicates, not MariaDB lifecycle."""

import importlib
import sqlite3
from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import frappe
import pytest
from frappe.query_builder import Criterion
from frappe.query_builder.builder import MariaDB
from pypika.queries import QueryBuilder

query = importlib.import_module(
    "karam_finance.karam_general.report.general_ledger_(karam).gl_query"
)


@pytest.fixture(autouse=True)
def context(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setattr(frappe, "qb", MariaDB)
    monkeypatch.setattr(frappe, "db", SimpleNamespace(db_type="mariadb"))
    monkeypatch.setattr(
        frappe.local, "flags", frappe._dict(mute_messages=True), raising=False
    )
    monkeypatch.setattr(frappe.local, "message_log", [], raising=False)
    monkeypatch.setattr(frappe.local, "lang", "en", raising=False)
    meta = MagicMock()
    meta.has_field.side_effect = {"department", "remarks"}.__contains__
    monkeypatch.setattr(frappe, "get_meta", MagicMock(return_value=meta))
    monkeypatch.setattr(query, "get_accounting_dimensions", MagicMock(return_value=[]))
    monkeypatch.setattr(query, "build_match_conditions", MagicMock(return_value=""))
    monkeypatch.setattr(
        query,
        "get_currency",
        MagicMock(
            return_value={"company_currency": "USD", "presentation_currency": "USD"}
        ),
    )
    monkeypatch.setattr(
        query,
        "convert_to_presentation_currency",
        MagicMock(side_effect=unchanged_currency),
    )
    return SimpleNamespace(table=MariaDB.DocType("GL Entry"), meta=meta)


def filters(**options: Any) -> dict[str, Any]:
    return {
        "company": "Test",
        "from_date": "2026-01-01",
        "to_date": "2026-01-31",
        "presentation_currency": "USD",
    } | options


def selected_rows(conditions: Any, rows: list[dict[str, Any]]) -> list[str]:
    """Execute the actual predicate over synthetic records in an in-memory database."""
    with sqlite3.connect(":memory:") as db:
        db.execute(
            'CREATE TABLE "tabGL Entry" '
            "(name TEXT, company TEXT, posting_date TEXT, is_opening TEXT, "
            "finance_book TEXT, voucher_type TEXT, voucher_no TEXT, "
            "against_voucher TEXT, party_type TEXT, party TEXT, account TEXT, "
            "cost_center TEXT, project TEXT, department TEXT, "
            "is_cancelled INTEGER, manual_entry INTEGER, reporting_doe INTEGER)"
        )
        columns = [row[1] for row in db.execute('PRAGMA table_info("tabGL Entry")')]
        defaults = {"company": "Test", "posting_date": "2026-01-15", "is_cancelled": 0}
        db.executemany(
            'INSERT INTO "tabGL Entry" VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [tuple((defaults | row).get(column) for column in columns) for row in rows],
        )
        table = MariaDB.DocType("GL Entry")
        statement = (
            MariaDB.from_(table).select(table.name).where(Criterion.all(conditions))
        )
        return sorted(row[0] for row in db.execute(statement.get_sql()))


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, ["blank", "null"]),
        ({"finance_book": "Book"}, ["blank", "book", "null"]),
        (
            {"include_default_book_entries": 1, "company_fb": "Book"},
            ["blank", "book", "null"],
        ),
        (
            {
                "include_default_book_entries": 1,
                "company_fb": "Book",
                "finance_book": "Book",
            },
            ["blank", "book", "null"],
        ),
        (
            {"include_default_book_entries": 1, "finance_book": "Book"},
            ["blank", "book", "null"],
        ),
    ],
)
def test_finance_book_scope(
    context: SimpleNamespace, options: dict[str, Any], expected: list[str]
) -> None:
    rows = [
        {"name": name, "finance_book": book}
        for name, book in (
            ("blank", ""),
            ("null", None),
            ("book", "Book"),
            ("other", "Other"),
        )
    ]
    assert (
        selected_rows(
            [query._build_qb_finance_book_condition(options, context.table)], rows
        )
        == expected
    )


def test_conflicting_default_book_is_rejected(context: SimpleNamespace) -> None:
    with pytest.raises(frappe.ValidationError, match="different finance book"):
        query._build_qb_finance_book_condition(
            {
                "include_default_book_entries": 1,
                "finance_book": "Other",
                "company_fb": "Book",
            },
            context.table,
        )


@pytest.mark.parametrize("pairs", [{}, {("Journal Entry", "SHARED"): {}}])
def test_voucher_filter_matches_complete_pairs(
    context: SimpleNamespace, pairs: dict[Any, Any]
) -> None:
    rows = [
        {"name": "journal", "voucher_type": "Journal Entry", "voucher_no": "SHARED"},
        {"name": "invoice", "voucher_type": "Sales Invoice", "voucher_no": "SHARED"},
    ]
    condition = query._voucher_pair_condition(context.table, pairs)
    assert selected_rows([condition], rows) == (["journal"] if pairs else [])


def test_voucher_limit_fails_closed(
    context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(query, "_MAX_VOUCHER_FILTER_PAIRS", 1)
    with pytest.raises(frappe.ValidationError, match="too many vouchers"):
        query._voucher_pair_condition(
            context.table, {("Journal Entry", "A"): {}, ("Journal Entry", "B"): {}}
        )


@pytest.mark.parametrize("permission", ["", "`tabGL Entry`.`account`='Allowed'"])
@pytest.mark.parametrize("compact", [False, True])
def test_permission_predicate_reaches_every_query(
    monkeypatch: pytest.MonkeyPatch,
    *,
    permission: str,
    compact: bool,
) -> None:
    statements: list[str] = []

    def run(builder: QueryBuilder, **_kwargs: Any) -> list[Any]:
        statements.append(builder.get_sql())
        return []

    monkeypatch.setattr(QueryBuilder, "run", run)
    monkeypatch.setattr(
        query, "build_match_conditions", MagicMock(return_value=permission)
    )
    attach = MagicMock()
    monkeypatch.setattr(query, "_attach_series_translation", attach)
    options = filters(categorize_by="Categorise by Account")
    assert query.get_gl_entries(options, [], enrich_opening_entries=not compact) == []
    assert len(statements) == (2 if compact else 1)
    assert all(
        (permission in sql) if permission else "Allowed" not in sql
        for sql in statements
    )
    assert all("`company`='Test'" in sql for sql in statements)
    assert options["_bill_no_joined"] is False
    attach.assert_called_once_with([], preloaded_voucher_data=None)


@pytest.mark.parametrize("tree", [False, True])
def test_dimension_filter_expands_only_tree_values(
    context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, tree: bool
) -> None:
    dimension = frappe._dict(
        fieldname="department",
        document_type="Department",
        label="Department",
        disabled=0,
    )
    monkeypatch.setattr(frappe, "get_cached_value", MagicMock(return_value=tree))
    expand = MagicMock(return_value=["Parent", "Child"])
    monkeypatch.setattr(query, "get_dimension_with_children", expand)
    options = filters(department=["Parent"], _dimensions_meta=[dimension])
    rows = [{"name": name, "department": name} for name in ("Parent", "Child", "Other")]
    assert selected_rows(query._build_qb_conditions(options, context.table), rows) == (
        ["Child", "Parent"] if tree else ["Parent"]
    )
    assert expand.call_count == int(tree)


@pytest.mark.parametrize(
    "dimension",
    [
        frappe._dict(fieldname="department", document_type="Department", disabled=1),
        frappe._dict(fieldname="department", document_type="Finance Book", disabled=0),
        frappe._dict(fieldname="bad-name", document_type="Department", disabled=0),
    ],
)
def test_inactive_or_invalid_dimensions_are_not_interpolated(
    context: SimpleNamespace, dimension: Any
) -> None:
    options = filters(_dimensions_meta=[dimension])
    assert query._build_qb_dimension_conditions(options, context.table) == []


@pytest.mark.parametrize(
    ("mode", "dimensions", "expected"),
    [
        ("Categorize by Voucher", False, "`posting_date`,`voucher_type`,`voucher_no`"),
        ("Flat Chronological", False, "`posting_date`,`creation`"),
        ("Categorise by Account", False, "`account`,`posting_date`,`creation`"),
        ("Group by Account w/ Opening", False, "`account`,`posting_date`,`creation`"),
        (None, True, "`posting_date`,`creation`"),
        (None, False, "`posting_date`,`account`,`creation`"),
    ],
)
def test_query_and_compatibility_ordering(
    context: SimpleNamespace, *, mode: str | None, dimensions: bool, expected: str
) -> None:
    options = filters(categorize_by=mode, include_dimensions=dimensions)
    statement = query._apply_order_by(
        MariaDB.from_(context.table).select(context.table.name), context.table, options
    ).get_sql()
    assert statement.split(" ORDER BY ")[1] == expected
    assert query._get_order_by_clause(options) == "order by " + expected.replace(
        "`", ""
    ).replace(",", ", ").replace("posting_date", "gl.posting_date").replace(
        "voucher_type", "gl.voucher_type"
    ).replace("voucher_no", "gl.voucher_no").replace("creation", "gl.creation").replace(
        "account", "gl.account"
    )


@pytest.mark.parametrize(
    ("length", "fragment"),
    [
        (None, "`remarks`"),
        (4, "SUBSTRING(`remarks`,1,4)"),
        ("bad", "`remarks`"),
        (-1, "`remarks`"),
        (100001, "`remarks`"),
    ],
)
def test_projection_validates_dimensions_and_remark_length(
    context: SimpleNamespace, length: Any, fragment: str
) -> None:
    fields = query._select_fields(
        context.table,
        filters(
            show_remarks=1, _remarks_length=length, add_values_in_transaction_currency=1
        ),
        ["department", "missing", "bad-name"],
    )
    sql = MariaDB.from_(context.table).select(*fields).get_sql()
    assert fragment in sql
    assert "`department`" in sql
    assert "missing" not in sql and "bad-name" not in sql
    assert "`transaction_currency`" in sql
    assert "`debit_in_transaction_currency`" in sql
    assert "`debit`" in sql
    assert "NULL `karam_series`" in sql


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"ignore_err": 1}, ["Exchange Rate Revaluation", "Exchange Gain Or Loss"]),
        ({"ignore_cr_dr_notes": 1}, ["Credit Note", "Debit Note"]),
        (
            {"ignore_err": 1, "ignore_cr_dr_notes": 1},
            [
                "Exchange Rate Revaluation",
                "Exchange Gain Or Loss",
                "Credit Note",
                "Debit Note",
            ],
        ),
    ],
)
def test_exclusion_subqueries_keep_company_and_submission_scope(
    context: SimpleNamespace, options: dict[str, Any], expected: list[str]
) -> None:
    sql = str(query._build_qb_voucher_conditions(filters(**options), context.table)[0])
    assert all(value in sql for value in expected)
    assert "\"company\"='Test'" in sql
    assert '"docstatus"=1' in sql
    assert ("UNION" in sql) == (len(expected) == 4)
    assert ('"is_system_generated"=1' in sql) == bool(options.get("ignore_cr_dr_notes"))


@pytest.mark.parametrize(
    ("options", "fragment"),
    [
        ({"account": ["Parent"]}, "gl.account in %(account)s"),
        ({"cost_center": ["Root"]}, "gl.cost_center in %(cost_center)s"),
        ({"project": ["Project"]}, "gl.project in %(project)s"),
        ({"voucher_no": "INV"}, "gl.voucher_no=%(voucher_no)s"),
        ({"against_voucher_no": "ORDER"}, "gl.against_voucher=%(against_voucher_no)s"),
        ({"voucher_no_not_in": ["INV"]}, "gl.voucher_no not in %(voucher_no_not_in)s"),
        ({"letter": "A"}, "gl.letter=%(letter)s"),
        (
            {"show_letter": "Only assigned rows"},
            "(gl.letter is not null and gl.letter != '')",
        ),
        (
            {"show_letter": "Only unassigned rows"},
            "(gl.letter is null or gl.letter = '')",
        ),
        (
            {"categorize_by": "Categorize by Party"},
            "gl.party_type in ('Customer', 'Supplier')",
        ),
        ({"party_type": "Customer", "party": ["Buyer"]}, "gl.party in %(party)s"),
    ],
)
def test_legacy_filter_contracts(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, Any], fragment: str
) -> None:
    monkeypatch.setattr(
        query, "get_accounts_with_children", MagicMock(return_value=["Child"])
    )
    monkeypatch.setattr(
        query, "get_cost_centers_with_children", MagicMock(return_value=["Branch"])
    )
    sql = query.get_conditions(filters(**options))
    assert fragment in sql
    assert sql.startswith("and ")
    assert "gl.is_cancelled = 0" in sql


@pytest.mark.parametrize("ignore_opening", [False, True])
@pytest.mark.parametrize("account", [None, ["A"]])
def test_legacy_date_boundaries(
    *, ignore_opening: bool, account: list[str] | None
) -> None:
    clauses = query._build_date_conditions(filters(account=account), ignore_opening)
    assert any("from_date" in clause for clause in clauses) == (not account)
    assert "to_date" in clauses[-1]
    assert all(("is_opening" in clause) == (not ignore_opening) for clause in clauses)


@pytest.mark.parametrize(
    ("options", "parameter"),
    [
        ({}, None),
        ({"finance_book": "Book"}, "finance_book"),
        ({"include_default_book_entries": 1, "company_fb": "Book"}, "company_fb"),
        (
            {
                "include_default_book_entries": 1,
                "company_fb": "Book",
                "finance_book": "Book",
            },
            "finance_book",
        ),
        ({"include_default_book_entries": 1, "finance_book": "Book"}, "finance_book"),
    ],
)
def test_legacy_finance_books(options: dict[str, Any], parameter: str | None) -> None:
    clauses = query._build_finance_book_conditions(options)
    assert len(clauses) == 1
    assert "OR gl.finance_book IS NULL" in clauses[0]
    assert (
        ("%(" + parameter + ")s" in clauses[0])
        if parameter
        else clauses == ["(gl.finance_book in ('') OR gl.finance_book IS NULL)"]
    )


def test_legacy_conflicting_book_is_rejected() -> None:
    with pytest.raises(frappe.ValidationError, match="different finance book"):
        query._build_finance_book_conditions(
            {
                "include_default_book_entries": 1,
                "finance_book": "Other",
                "company_fb": "Book",
            }
        )


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"ignore_err": 1},
        {"ignore_cr_dr_notes": 1},
        {"ignore_err": 1, "ignore_cr_dr_notes": 1},
    ],
)
def test_legacy_exclusions_deduplicate_and_bound_lookups(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, Any]
) -> None:
    lookup = MagicMock(return_value=[["JE-1"], ["JE-2"]])
    monkeypatch.setattr(frappe, "get_all", lookup)
    values = filters(voucher_no_not_in=["JE-1"], **options)
    query._build_voucher_conditions(values)
    assert values["voucher_no_not_in"] == (["JE-1", "JE-2"] if options else ["JE-1"])
    assert lookup.call_count == len(options)
    for request in lookup.call_args_list:
        assert request.kwargs["filters"]["company"] == "Test"
        assert request.kwargs["filters"]["docstatus"] == 1
        assert request.kwargs["limit_page_length"] == 100000


@pytest.mark.parametrize("tree", [False, True])
def test_legacy_dimension_contract(monkeypatch: pytest.MonkeyPatch, tree: bool) -> None:
    monkeypatch.setattr(frappe, "get_cached_value", MagicMock(return_value=tree))
    monkeypatch.setattr(
        query,
        "get_dimension_with_children",
        MagicMock(return_value=["Parent", "Child"]),
    )
    active = frappe._dict(
        fieldname="department", document_type="Department", disabled=0
    )
    inactive = frappe._dict(fieldname="ignored", document_type="Department", disabled=1)
    values = filters(department=["Parent"], _dimensions_meta=[active, inactive])
    assert query._build_dimension_conditions(values) == [
        "gl.department in %(department)s"
    ]
    assert values["department"] == (["Parent", "Child"] if tree else ["Parent"])
    assert query._build_system_conditions({"show_cancelled_entries": 1}) == []


def test_empty_account_expansion_keeps_compatibility_contract(
    context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(query, "get_accounts_with_children", MagicMock(return_value=[]))
    assert query._build_account_conditions({"account": ["Missing"]}) == []
    assert (
        query._build_qb_account_conditions({"account": ["Missing"]}, context.table)
        == []
    )


@pytest.mark.parametrize("compact", [False, True])
def test_voucher_preload_and_party_names_are_reused(
    monkeypatch: pytest.MonkeyPatch, compact: bool
) -> None:
    rows = [
        frappe._dict(
            account="A",
            account_currency="USD",
            party_type="Customer",
            party="Buyer",
            posting_date=date(2026, 1, 1),
            creation="1",
        )
    ]
    result = MagicMock(return_value=rows)
    monkeypatch.setattr(QueryBuilder, "run", result)
    monkeypatch.setattr(frappe, "get_cached_value", MagicMock(return_value="Book"))
    pairs = {("Sales Invoice", "INV"): {"karam_series": "Series"}}
    monkeypatch.setattr(
        query, "_get_voucher_data_for_filters", MagicMock(return_value=pairs)
    )
    names = MagicMock(return_value={"Customer": {"Buyer": "Buyer Name"}})
    monkeypatch.setattr(query, "get_party_name_map", names)
    attach = MagicMock()
    monkeypatch.setattr(query, "_attach_series_translation", attach)
    values = filters(
        include_default_book_entries=1, karam_series="Series", _needs_party_name=1
    )
    assert query.get_gl_entries(values, [], enrich_opening_entries=not compact) == rows
    assert rows[0].party_name == "Buyer Name"
    assert values["company_fb"] == "Book"
    names.assert_called_once_with(rows)
    attach.assert_called_once_with(rows, preloaded_voucher_data=pairs)


@pytest.mark.parametrize(
    "options",
    [
        {"categorize_by": "Categorise by Account"},
        {
            "categorize_by": "Flat Chronological",
            "disable_opening_balance_calculation": 1,
            "_ignore_is_opening": 1,
        },
    ],
)
def test_unused_flat_openings_do_not_query(
    monkeypatch: pytest.MonkeyPatch, options: dict[str, Any]
) -> None:
    run = MagicMock()
    monkeypatch.setattr(QueryBuilder, "run", run)
    assert query.get_flat_account_currency_openings(filters(**options)) == {}
    run.assert_not_called()


def unchanged_currency(rows: list[Any], _currency: Any, _filters: Any) -> list[Any]:
    """External ERPNext conversion seam for query-only tests."""
    return rows


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, ["end", "future-opening", "opening", "start"]),
        ({"_ignore_is_opening": 1}, ["end", "start"]),
        (
            {"categorize_by": "Categorise by Account"},
            ["before", "end", "future-opening", "opening", "start"],
        ),
        (
            {"account": ["A"], "_ignore_is_opening": 1},
            ["before", "end", "opening", "start"],
        ),
        (
            {"disable_opening_balance_calculation": 1, "_ignore_is_opening": 1},
            ["end", "start"],
        ),
        ({"_flat_account_openings": True}, ["before", "future-opening", "opening"]),
        (
            {"_flat_account_openings": True, "show_opening_entries": 1},
            ["before", "opening"],
        ),
        (
            {"_flat_account_openings": True, "_ignore_is_opening": 1},
            ["before", "opening"],
        ),
        (
            {"_flat_account_openings": True, "disable_opening_balance_calculation": 1},
            ["opening"],
        ),
    ],
)
def test_karam_opening_date_contract(
    context: SimpleNamespace, options: dict[str, Any], expected: list[str]
) -> None:
    rows = [
        {"name": "before", "posting_date": "2025-12-31", "is_opening": "No"},
        {"name": "start", "posting_date": "2026-01-01", "is_opening": "No"},
        {"name": "end", "posting_date": "2026-01-31", "is_opening": "No"},
        {"name": "after", "posting_date": "2026-02-01", "is_opening": "No"},
        {"name": "opening", "posting_date": "2025-12-31", "is_opening": "Yes"},
        {"name": "future-opening", "posting_date": "2026-02-01", "is_opening": "Yes"},
    ]
    assert (
        selected_rows(
            query._build_qb_date_conditions(filters(**options), context.table), rows
        )
        == expected
    )


@pytest.mark.parametrize("cancelled", [False, True])
def test_company_and_cancellation_scope(
    context: SimpleNamespace, cancelled: bool
) -> None:
    rows = [
        {"name": "kept"},
        {"name": "cancelled", "is_cancelled": 1},
        {"name": "other-company", "company": "Other"},
    ]
    assert selected_rows(
        query._build_qb_conditions(
            filters(show_cancelled_entries=cancelled), context.table
        ),
        rows,
    ) == (["cancelled", "kept"] if cancelled else ["kept"])


def test_combined_account_party_and_voucher_filters(
    context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        query, "get_accounts_with_children", MagicMock(return_value=["Child"])
    )
    monkeypatch.setattr(
        query, "get_cost_centers_with_children", MagicMock(return_value=["Branch"])
    )
    options = filters(
        account=["Parent"],
        cost_center=["Root"],
        project=["Project"],
        party_type="Customer",
        party=["Buyer"],
        voucher_no="INV",
        against_voucher_no="ORDER",
    )
    row = {
        "name": "matching",
        "account": "Child",
        "cost_center": "Branch",
        "project": "Project",
        "party_type": "Customer",
        "party": "Buyer",
        "voucher_no": "INV",
        "against_voucher": "ORDER",
    }
    rows = [row] + [
        row | {"name": field, field: "Other"}
        for field in (
            "account",
            "cost_center",
            "project",
            "party_type",
            "party",
            "voucher_no",
            "against_voucher",
        )
    ]
    assert selected_rows(query._build_qb_conditions(options, context.table), rows) == [
        "matching"
    ]


def test_party_grouping_defaults_to_customer_and_supplier(
    context: SimpleNamespace,
) -> None:
    rows = [
        {"name": name, "party_type": name}
        for name in ("Customer", "Supplier", "Employee")
    ]
    assert selected_rows(
        query._build_qb_party_conditions(
            {"categorize_by": "Categorize by Party"}, context.table
        ),
        rows,
    ) == ["Customer", "Supplier"]


def test_explicit_voucher_exclusions(context: SimpleNamespace) -> None:
    rows = [
        {"name": "excluded", "voucher_no": "JE-1"},
        {"name": "kept", "voucher_no": "JE-2"},
    ]
    assert selected_rows(
        query._build_qb_voucher_conditions(
            {"voucher_no_not_in": ["JE-1"]}, context.table
        ),
        rows,
    ) == ["kept"]


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({"letter": "A"}, ["assigned"]),
        ({"show_letter": "Only assigned rows"}, ["assigned"]),
        ({"show_letter": "Only unassigned rows"}, ["blank", "null"]),
    ],
)
def test_letter_predicates(
    context: SimpleNamespace, options: dict[str, Any], expected: list[str]
) -> None:
    # Use a small explicit relation for the Karam-only letter field.
    with sqlite3.connect(":memory:") as db:
        db.execute('CREATE TABLE "tabGL Entry" (name TEXT, letter TEXT)')
        db.executemany(
            'INSERT INTO "tabGL Entry" VALUES (?,?)',
            [("assigned", "A"), ("blank", ""), ("null", None)],
        )
        conditions = query._build_qb_karam_conditions(options, context.table, None)
        sql = (
            MariaDB.from_(context.table)
            .select(context.table.name)
            .where(Criterion.all(conditions))
            .get_sql()
        )
        assert sorted(row[0] for row in db.execute(sql)) == expected


def test_joined_voucher_conditions_are_orred(context: SimpleNamespace) -> None:
    conditions = query._build_qb_karam_conditions(
        {},
        context.table,
        None,
        joined_voucher_conditions=[
            context.table.voucher_no == "A",
            context.table.voucher_no == "B",
        ],
    )
    assert selected_rows(
        conditions, [{"name": name, "voucher_no": name} for name in ("A", "B", "C")]
    ) == ["A", "B"]


@pytest.mark.parametrize(
    ("options", "company", "expected"),
    [
        (
            {"categorize_by": "Categorise by Account", "presentation_currency": "USD"},
            "USD",
            True,
        ),
        (
            {
                "categorize_by": "Group by Account w/ Opening",
                "presentation_currency": "USD",
            },
            "USD",
            True,
        ),
        (
            {"categorize_by": "Categorise by Account", "presentation_currency": "EUR"},
            "USD",
            False,
        ),
        (
            {"categorize_by": "Flat Chronological", "presentation_currency": "USD"},
            "USD",
            False,
        ),
        (
            {
                "categorize_by": "Categorise by Account",
                "presentation_currency": "USD",
                "include_dimensions": 1,
            },
            "USD",
            False,
        ),
        (
            {
                "categorize_by": "Categorise by Account",
                "presentation_currency": "USD",
                "add_values_in_transaction_currency": 1,
            },
            "USD",
            False,
        ),
        (
            {
                "categorize_by": "Categorise by Account",
                "presentation_currency": "USD",
                "show_net_values_in_party_account": 1,
            },
            "USD",
            False,
        ),
    ],
)
def test_compaction_requires_compatible_currency_and_options(
    options: dict[str, Any], company: str, expected: bool
) -> None:
    assert (
        query._can_compact_account_history(options, {"company_currency": company})
        is expected
    )


@pytest.mark.parametrize("presentation", [None, "EUR"])
def test_company_amounts_are_preserved_before_external_conversion(
    monkeypatch: pytest.MonkeyPatch, presentation: str | None
) -> None:
    row = frappe._dict(account_currency="USD", debit=20, credit=5)
    monkeypatch.setattr(QueryBuilder, "run", MagicMock(return_value=[row]))
    monkeypatch.setattr(query, "_attach_series_translation", MagicMock())
    converted = MagicMock(return_value=[row])
    monkeypatch.setattr(query, "convert_to_presentation_currency", converted)
    assert query.get_gl_entries(filters(presentation_currency=presentation), []) == [
        row
    ]
    assert row.debit_in_company_currency == 20
    assert row.credit_in_company_currency == 5
    assert converted.call_count == int(bool(presentation))


@pytest.mark.parametrize(
    "contribution",
    [
        "debit_in_account_currency",
        "credit_in_account_currency",
        "_account_currency_contribution",
    ],
)
def test_unknown_account_currency_is_flagged(contribution: str) -> None:
    row = frappe._dict(account_currency="", debit=0, credit=0, **{contribution: 1})
    query._prepare_currency_values([row], {})
    assert row._mixed_account_currency == 1


def test_compact_history_keeps_company_amounts_and_cancelled_currency_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = frappe._dict(
        account="A",
        account_currency="",
        posting_date=date(2025, 12, 31),
        creation="1",
        debit="1.25",
        credit="0.25",
        debit_in_account_currency="0",
        credit_in_account_currency="0",
        _account_currency_contribution=2,
    )
    movement = frappe._dict(
        account="A",
        account_currency="USD",
        posting_date=date(2026, 1, 1),
        creation="2",
        debit=0.5,
        credit=0,
    )
    responses = iter([[history], [movement]])
    monkeypatch.setattr(QueryBuilder, "run", MagicMock(side_effect=responses))
    attach = MagicMock()
    monkeypatch.setattr(query, "_attach_series_translation", attach)
    rows = query.get_gl_entries(
        filters(categorize_by="Categorise by Account"), [], enrich_opening_entries=False
    )
    assert rows == [history, movement]
    assert history.debit == 1.25
    assert history.credit == 0.25
    assert history.debit_in_company_currency == 1.25
    assert history._mixed_account_currency == 1
    assert history._account_currency_contribution is True
    attach.assert_called_once_with([movement], preloaded_voucher_data=None)


@pytest.mark.parametrize("extras", [False, True])
def test_flat_openings_use_real_filters_and_separate_currencies(
    monkeypatch: pytest.MonkeyPatch, extras: bool
) -> None:
    statements: list[str] = []

    def run(builder: QueryBuilder, **_kwargs: Any) -> list[Any]:
        statements.append(builder.get_sql())
        return [
            frappe._dict(
                account=account, account_currency=currency, opening_balance=balance
            )
            for account, currency, balance in (
                ("A", "USD", "100.25"),
                ("A", "EUR", "3.5"),
                (None, "USD", "999"),
            )
        ]

    monkeypatch.setattr(QueryBuilder, "run", run)
    monkeypatch.setattr(frappe, "get_cached_value", MagicMock(return_value="Book"))
    monkeypatch.setattr(
        query, "_get_voucher_data_for_filters", MagicMock(return_value={})
    )
    permission = "`tabGL Entry`.`account`='Allowed'" if extras else ""
    monkeypatch.setattr(
        query, "build_match_conditions", MagicMock(return_value=permission)
    )
    options = filters(categorize_by="Flat Chronological")
    if extras:
        options.update(include_default_book_entries=1, translation="Missing")
    original = options.copy()
    assert query.get_flat_account_currency_openings(options) == {
        ("A", "USD"): 100.25,
        ("A", "EUR"): 3.5,
    }
    assert options == original
    assert "`company`='Test'" in statements[0]
    if extras:
        assert permission in statements[0]
        assert "`name`=''" in statements[0]
