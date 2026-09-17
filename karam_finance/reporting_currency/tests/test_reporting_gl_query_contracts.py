"""Site-free query contracts; SQLite checks predicates, not MariaDB lifecycle."""

import importlib
import sqlite3
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import frappe
import pytest
from frappe.query_builder import Criterion
from frappe.query_builder.builder import MariaDB
from pypika.queries import QueryBuilder

query = importlib.import_module(
    "karam_finance.reporting_currency.report.general_ledger_(reporting_currency).gl_query"
)
reporting_source = importlib.import_module(
    "karam_finance.reporting_currency.report.reporting_source"
)
gl_currency = importlib.import_module(
    "karam_finance.reporting_currency.report.general_ledger_(reporting_currency).gl_currency"
)
aggregation = importlib.import_module(
    "karam_finance.reporting_currency.report.general_ledger_(reporting_currency).gl_aggregation"
)


def _raise_value(message: str) -> None:
    raise ValueError(message)


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
    # Native permission tests cover the added source boundary; retain synthetic query contracts.
    monkeypatch.setattr(
        query, "source_visibility", MagicMock(return_value=Criterion.all([]))
    )
    return SimpleNamespace(table=MariaDB.DocType("Reporting Currency GLE"), meta=meta)


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
            'CREATE TABLE "tabReporting Currency GLE" '
            "(name TEXT, company TEXT, posting_date TEXT, is_opening TEXT, "
            "finance_book TEXT, voucher_type TEXT, voucher_no TEXT, "
            "against_voucher TEXT, party_type TEXT, party TEXT, account TEXT, "
            "cost_center TEXT, project TEXT, department TEXT, "
            "is_cancelled INTEGER, manual_entry INTEGER, reporting_doe INTEGER)"
        )
        columns = [
            row[1]
            for row in db.execute('PRAGMA table_info("tabReporting Currency GLE")')
        ]
        defaults = {"company": "Test", "posting_date": "2026-01-15", "is_cancelled": 0}
        db.executemany(
            'INSERT INTO "tabReporting Currency GLE" VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [tuple((defaults | row).get(column) for column in columns) for row in rows],
        )
        table = MariaDB.DocType("Reporting Currency GLE")
        statement = (
            MariaDB.from_(table).select(table.name).where(Criterion.all(conditions))
        )
        return sorted(row[0] for row in db.execute(statement.get_sql()))


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, ["doe", "manual", "synced"]),
        ({"entry_type": "Reporting DOE"}, ["doe"]),
        ({"entry_type": "Manual"}, ["manual"]),
        ({"entry_type": "Synced GL"}, ["synced"]),
        ({"reporting_doe": 1}, ["doe"]),
        ({"manual_entry": 1}, ["manual"]),
        ({"exclude_reporting_doe": 1}, ["manual", "synced"]),
        ({"exclude_manual_entries": 1}, ["doe", "synced"]),
        ({"rc_entry": "manual"}, ["manual"]),
        ({"show_cancelled_entries": 1}, ["cancelled", "doe", "manual", "synced"]),
    ],
)
def test_entry_scope(
    context: SimpleNamespace, options: dict[str, Any], expected: list[str]
) -> None:
    rows = [
        {"name": "synced"},
        {"name": "manual", "manual_entry": 1},
        {"name": "doe", "reporting_doe": 1},
        {"name": "other-company", "company": "Other"},
        {"name": "cancelled", "is_cancelled": 1},
    ]
    assert (
        selected_rows(
            query._build_qb_conditions(filters(**options), context.table), rows
        )
        == expected
    )


def test_prepare_filters_stops_before_querying_when_reporting_currency_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = MagicMock()
    database.get_single_value.return_value = None
    query_builder = MagicMock()
    monkeypatch.setattr(reporting_source.frappe, "db", database)
    monkeypatch.setattr(reporting_source.frappe, "qb", query_builder)
    monkeypatch.setattr(
        reporting_source.frappe,
        "throw",
        MagicMock(side_effect=_raise_value),
    )

    with pytest.raises(ValueError, match="Configure Reporting Currency"):
        reporting_source.prepare_filters({"company": "Test"})

    database.get_single_value.assert_called_once_with(
        "Reporting Currency Settings", "reporting_currency"
    )
    query_builder.DocType.assert_not_called()


def test_report_currencies_uses_selected_account_currency_when_data_is_empty() -> None:
    assert gl_currency._report_currencies([], {"account_currency": "USD"}) == {"USD"}


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        ({}, ["before", "end", "opening", "start"]),
        ({"disable_opening_balance_calculation": 1}, ["end", "opening", "start"]),
        ({"_flat_account_openings": True}, ["before", "opening"]),
        ({"_flat_account_openings": True, "show_opening_entries": 1}, ["before"]),
        ({"_flat_account_openings": True, "_ignore_is_opening": 1}, ["before"]),
        (
            {"_flat_account_openings": True, "disable_opening_balance_calculation": 1},
            [],
        ),
    ],
)
def test_date_boundaries(
    context: SimpleNamespace, options: dict[str, Any], expected: list[str]
) -> None:
    rows = [
        {"name": "before", "posting_date": "2025-12-31"},
        {"name": "start", "posting_date": "2026-01-01"},
        {"name": "end", "posting_date": "2026-01-31"},
        {"name": "after", "posting_date": "2026-02-01"},
        {"name": "opening", "is_opening": "Yes"},
        {"name": "future-opening", "posting_date": "2026-02-01", "is_opening": "Yes"},
    ]
    assert (
        selected_rows(
            query._build_qb_date_conditions(filters(**options), context.table), rows
        )
        == expected
    )


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


def test_voucher_exclusion_keeps_other_types_and_manual_rows(
    context: SimpleNamespace,
) -> None:
    rows = [
        {"name": "excluded", "voucher_type": "Journal Entry", "voucher_no": "SHARED"},
        {"name": "invoice", "voucher_type": "Sales Invoice", "voucher_no": "SHARED"},
        {"name": "manual"},
        {"name": "no-number", "voucher_type": "Journal Entry"},
        {"name": "kept", "voucher_type": "Journal Entry", "voucher_no": "KEPT"},
    ]
    conditions = query._build_qb_voucher_conditions(
        {"voucher_no_not_in": ["SHARED"]}, context.table
    )
    assert selected_rows(conditions, rows) == ["invoice", "kept", "manual", "no-number"]


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


@pytest.mark.parametrize(
    "permission", ["", "`tabReporting Currency GLE`.`account`='Allowed'"]
)
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


@pytest.mark.parametrize(
    ("values", "label"),
    [
        ({"manual_entry": 1}, "Manual"),
        (
            {"reporting_doe": 1, "manual_entry": 1},
            "Reporting DOE",
        ),
        (
            {"account_currency": "USD"},
            "Synced GL",
        ),
        (
            {"account_currency": "EUR"},
            "Synced GL",
        ),
    ],
)
def test_currency_provenance(*, values: dict[str, Any], label: str) -> None:
    row = frappe._dict(
        {
            "reporting_currency": "USD",
            "debit": "51309440814079.5444",
            "source_exchange_rate": "10",
            "exchange_rate_application": "Inverse",
            "currency_exchange": "RATE",
            "exchange_rate_date": "2026-01-01",
        }
        | values
    )
    query._prepare_currency_values([row], {})
    assert row.debit == Decimal("51309440814079.5444")
    assert row.entry_type == label
    assert "conversion_basis" not in row
    assert "exchange_rate" not in row
    assert row.source_exchange_rate == "10"
    assert row.exchange_rate_application == "Inverse"
    assert row.exchange_rate_date == "2026-01-01"
    assert row.currency_exchange == "RATE"


@pytest.mark.parametrize("manual", [0, 1])
def test_copied_source_rate_does_not_invent_a_conversion_method(manual: int) -> None:
    row = frappe._dict(
        manual_entry=manual,
        source_exchange_rate=1,
        exchange_rate_application="",
        account_currency="USD",
        reporting_currency="USD",
    )
    query._prepare_currency_values([row], {})
    assert row.source_exchange_rate == 1
    assert row.exchange_rate_application == ""
    assert "exchange_rate" not in row


@pytest.mark.parametrize("different", [False, True])
def test_consolidated_exchange_details_require_a_shared_snapshot(
    *, different: bool
) -> None:
    original = {
        "currency_exchange": "RATE",
        "exchange_rate_date": "2026-01-01",
        "source_exchange_rate": 89500,
        "exchange_rate_application": "Inverse",
    }
    combined = original.copy()
    incoming = original.copy()
    if different:
        incoming["source_exchange_rate"] = 90000
    aggregation._merge_exchange_details(combined, incoming)
    aggregation._merge_exchange_details(combined, original)
    assert {field: combined[field] for field in original} == (
        dict.fromkeys(original) if different else original
    )


@pytest.mark.parametrize("value", [None, "", " "])
@pytest.mark.parametrize(
    "contribution",
    [
        "debit_in_account_currency",
        "credit_in_account_currency",
        "_account_currency_contribution",
    ],
)
def test_unknown_currency_keeps_contribution_warning(
    value: str | None, contribution: str
) -> None:
    row = frappe._dict(account_currency=value, **{contribution: 1})
    query._prepare_currency_entry(row, {})
    assert row._mixed_account_currency == 1


def test_combined_filters_keep_the_exact_scope(
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
        voucher_type="Sales Invoice",
        voucher_no="INV",
        against_voucher_no="ORDER",
    )
    matching = {
        "name": "matching",
        "account": "Child",
        "cost_center": "Branch",
        "project": "Project",
        "party_type": "Customer",
        "party": "Buyer",
        "voucher_type": "Sales Invoice",
        "voucher_no": "INV",
        "against_voucher": "ORDER",
    }
    rows = [matching] + [
        matching | {"name": key, key: "Other"}
        for key in (
            "account",
            "cost_center",
            "project",
            "party_type",
            "party",
            "voucher_type",
            "voucher_no",
            "against_voucher",
        )
    ]
    assert selected_rows(query._build_qb_conditions(options, context.table), rows) == [
        "matching"
    ]
    assert options["account"] == ["Child"]
    assert options["cost_center"] == ["Branch"]


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


def test_unsynchronised_dimension_fails_before_execution(
    context: SimpleNamespace,
) -> None:
    dimension = frappe._dict(
        fieldname="missing", document_type="Department", label="Missing", disabled=0
    )
    with pytest.raises(frappe.ValidationError, match="not synchronised"):
        query._build_qb_dimension_conditions(
            filters(missing=["X"], _dimensions_meta=[dimension]), context.table
        )


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
    assert "`debit_amount_in_transaction_currency`" in sql
    assert "`reporting_debit`" in sql
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


def test_compact_history_preserves_exact_amounts_and_currency_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history: Any = frappe._dict(
        account="A",
        account_currency="",
        posting_date=date(2025, 12, 31),
        creation="1",
        debit="100.0001",
        credit="0",
        debit_in_account_currency="0",
        credit_in_account_currency="0",
        debit_in_company_currency="1000.0010",
        credit_in_company_currency="0",
        _account_currency_contribution=20,
    )
    movement: Any = frappe._dict(
        account="A",
        account_currency="USD",
        posting_date=date(2026, 1, 1),
        creation="2",
        debit="0",
        credit="0.0001",
    )
    responses = iter([[history], [movement]])
    statements: list[str] = []

    def run(builder: QueryBuilder, **_kwargs: Any) -> list[Any]:
        statements.append(builder.get_sql())
        return next(responses)

    monkeypatch.setattr(QueryBuilder, "run", run)
    monkeypatch.setattr(query, "_attach_series_translation", MagicMock())
    rows = query.get_gl_entries(
        filters(categorize_by="Categorise by Account"), [], enrich_opening_entries=False
    )
    assert rows == [history, movement]
    assert history.debit == Decimal("100.0001")
    assert history.debit_in_company_currency == Decimal("1000.0010")
    assert history._account_currency_contribution is True
    assert history._mixed_account_currency == 1
    assert history.debit - movement.credit == Decimal("100.0000")
    assert "<'2026-01-01'" in statements[0]
    assert ">='2026-01-01'" in statements[1]
    assert "`manual_entry`,`tabReporting Currency GLE`.`reporting_doe`" in statements[0]


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
    "permission", ["", "`tabReporting Currency GLE`.`account`='Allowed'"]
)
def test_flat_openings_accumulate_by_account_and_currency(
    monkeypatch: pytest.MonkeyPatch, permission: str
) -> None:
    statements: list[str] = []

    def run(builder: QueryBuilder, **_kwargs: Any) -> list[Any]:
        statements.append(builder.get_sql())
        return [
            frappe._dict(
                account=account, account_currency=currency, opening_balance=balance
            )
            for account, currency, balance in (
                ("A", "USD", "100.0001"),
                ("A", "USD", "0.0001"),
                ("A", "EUR", "3.00"),
                (None, "USD", "999"),
            )
        ]

    monkeypatch.setattr(QueryBuilder, "run", run)
    monkeypatch.setattr(
        query, "build_match_conditions", MagicMock(return_value=permission)
    )
    monkeypatch.setattr(frappe, "get_cached_value", MagicMock(return_value="Book"))
    monkeypatch.setattr(
        query, "_get_voucher_data_for_filters", MagicMock(return_value={})
    )
    values = filters(
        categorize_by="Flat Chronological",
        include_default_book_entries=1,
        translation="Missing",
    )
    original = values.copy()
    assert query.get_flat_account_currency_openings(values) == {
        ("A", "USD"): Decimal("100.0002"),
        ("A", "EUR"): Decimal("3.00"),
    }
    assert values == original
    assert permission in statements[0]
    assert "`name`=''" in statements[0]


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


def test_joined_voucher_compatibility_combines_alternative_matches(
    context: SimpleNamespace,
) -> None:
    table = context.table
    conditions = query._build_qb_karam_conditions(
        filters(karam_series="S"),
        table,
        None,
        joined_voucher_conditions=[
            table.voucher_type == "Sales Invoice",
            table.voucher_no == "J1",
        ],
    )
    assert selected_rows(
        conditions,
        [
            {"name": "invoice", "voucher_type": "Sales Invoice", "voucher_no": "I1"},
            {"name": "journal", "voucher_type": "Journal Entry", "voucher_no": "J1"},
            {"name": "unrelated", "voucher_type": "Journal Entry", "voucher_no": "J2"},
        ],
    ) == ["invoice", "journal"]
