"""Shared enrichment contracts exercised independently against both GL modules."""

import importlib
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import frappe
import pytest
from frappe.query_builder.builder import MariaDB
from pypika.queries import QueryBuilder


@pytest.fixture(
    params=[
        "karam_general.report.general_ledger_(karam)",
        "reporting_currency.report.general_ledger_(reporting_currency)",
    ]
)
def enrichment(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> ModuleType:
    module = importlib.import_module(
        "karam_finance." + request.param + ".gl_enrichment"
    )
    monkeypatch.setattr(module, "_KARAM_FIELDS_CACHE", {})
    monkeypatch.setattr(frappe, "qb", MariaDB)
    monkeypatch.setattr(frappe.local, "flags", frappe._dict(), raising=False)
    monkeypatch.setattr(frappe, "db", MagicMock(db_type="mariadb"))
    monkeypatch.setattr(frappe.local, "db", frappe.db, raising=False)
    return module


@pytest.mark.parametrize("count", [0, 1, 1000, 1001])
def test_party_lookup_is_complete_and_deduplicated(
    enrichment: ModuleType, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    def get_all(
        _doctype: str, *, fields: list[str], filters: dict[str, Any], **_kwargs: Any
    ) -> list[Any]:
        return [
            {"name": name, fields[1]: "Name " + name} for name in filters["name"][1]
        ]

    lookup = MagicMock(side_effect=get_all)
    monkeypatch.setattr(frappe, "get_all", lookup)
    rows = [
        frappe._dict(party_type="Customer", party=str(index)) for index in range(count)
    ]
    result = enrichment.get_party_name_map(rows + rows)
    assert result == (
        {"Customer": {str(i): "Name " + str(i) for i in range(count)}} if count else {}
    )
    assert lookup.call_count == int(bool(count))
    if count:
        assert lookup.call_args.kwargs["limit_page_length"] == count
        assert len(lookup.call_args.kwargs["filters"]["name"][1]) == count


def test_party_types_and_missing_names_do_not_cross(
    enrichment: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = MagicMock(
        side_effect=[
            [{"name": "same", "customer_name": "Buyer"}],
            [{"name": "same"}],
            [{"name": "same", "employee_name": "Employee"}, {}],
        ]
    )
    monkeypatch.setattr(frappe, "get_all", lookup)
    rows = [
        frappe._dict(party_type=t, party="same")
        for t in ("Customer", "Supplier", "Employee", "Unknown")
    ]
    rows += [frappe._dict(party_type="Customer"), frappe._dict(party="same")]
    assert enrichment.get_party_name_map(rows) == {
        "Customer": {"same": "Buyer"},
        "Supplier": {"same": ""},
        "Employee": {"same": "Employee"},
    }
    assert [call.args[0] for call in lookup.call_args_list] == [
        "Customer",
        "Supplier",
        "Employee",
    ]


@pytest.mark.parametrize("include_journals", [False, True])
def test_voucher_targets_preserve_type_and_duplicate_rows(
    enrichment: ModuleType, include_journals: bool
) -> None:
    invoice = frappe._dict(voucher_type="Sales Invoice", voucher_no="same")
    journal = frappe._dict(voucher_type="Journal Entry", voucher_no="same")
    ignored = [
        frappe._dict(),
        frappe._dict(voucher_type="Unknown", voucher_no="same"),
        frappe._dict(
            voucher_type="Sales Invoice",
            voucher_no="done",
            karam_series="S",
            translation="T",
        ),
    ]
    targets, index = enrichment._collect_voucher_targets(
        [invoice, invoice, journal, *ignored], include_journal_entries=include_journals
    )
    expected: dict[str, set[str]] = {"Sales Invoice": {"same"}} | (
        {"Journal Entry": {"same"}} if include_journals else {}
    )
    assert targets == expected
    assert (
        enrichment._collect_voucher_names(
            [invoice, journal, *ignored], include_journal_entries=include_journals
        )
        == expected
    )
    assert index[("Sales Invoice", "same")] == [invoice, invoice]


@pytest.mark.parametrize("preloaded", [False, True])
def test_hydration_fills_only_missing_values(
    enrichment: ModuleType, monkeypatch: pytest.MonkeyPatch, preloaded: bool
) -> None:
    invoice = frappe._dict(
        voucher_type="Sales Invoice", voucher_no="same", karam_series="Existing"
    )
    journal = frappe._dict(
        voucher_type="Journal Entry", voucher_no="same", translation="Existing text"
    )
    missing = frappe._dict(voucher_type="Sales Invoice", voucher_no="missing")
    data = {
        ("Sales Invoice", "same"): {
            "karam_series": "Wrong overwrite",
            "translation": "Invoice",
        },
        ("Journal Entry", "same"): {
            "karam_series": "Journal",
            "translation": "Wrong overwrite",
        },
    }
    fetch = MagicMock(return_value=data)
    monkeypatch.setattr(enrichment, "_fetch_voucher_data", fetch)
    enrichment._attach_series_translation(
        [invoice, journal, missing, frappe._dict()],
        preloaded_voucher_data=data if preloaded else None,
    )
    assert (invoice.karam_series, invoice.translation) == ("Existing", "Invoice")
    assert (journal.karam_series, journal.translation) == ("Journal", "Existing text")
    assert "translation" not in missing
    assert fetch.call_count == int(not preloaded)


def test_no_hydration_queries_without_targets(
    enrichment: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetch = MagicMock()
    monkeypatch.setattr(enrichment, "_fetch_voucher_data", fetch)
    enrichment._attach_series_translation([])
    enrichment._attach_series_translation(
        [frappe._dict(voucher_type="Unknown", voucher_no="X")]
    )
    enrichment._attach_series_translation(
        [frappe._dict(voucher_type="Sales Invoice", voucher_no="X")],
        preloaded_voucher_data={},
    )
    fetch.assert_not_called()


def test_compatibility_hydration_keeps_existing_fields(
    enrichment: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    row = frappe._dict(translation="Keep")
    missing = frappe._dict()
    monkeypatch.setattr(
        enrichment,
        "_fetch_voucher_data",
        MagicMock(
            return_value={
                ("Sales Invoice", "A"): {"karam_series": "S", "translation": "Replace"}
            }
        ),
    )
    enrichment._hydrate_entries_from_doctype(
        {("Sales Invoice", "A"): [row], ("Sales Invoice", "missing"): [missing]},
        "Sales Invoice",
        {"A", "missing"},
    )
    assert row == {"translation": "Keep", "karam_series": "S"}
    assert missing == {}


@pytest.mark.parametrize(
    "fields", [[], ["karam_series"], ["translation"], ["karam_series", "translation"]]
)
def test_metadata_cache_is_per_doctype(
    enrichment: ModuleType, monkeypatch: pytest.MonkeyPatch, fields: list[str]
) -> None:
    meta = MagicMock()
    meta.has_field.side_effect = fields.__contains__
    lookup = MagicMock(return_value=meta)
    monkeypatch.setattr(frappe, "get_meta", lookup)
    expected: list[str] = ["name", *fields] if fields else []
    assert enrichment._get_karam_fields_for_doctype("Sales Invoice") == expected
    assert enrichment._get_karam_fields_for_doctype("Sales Invoice") == expected
    assert enrichment._get_karam_fields_for_doctype("Journal Entry") == expected
    assert lookup.call_count == 2


def test_only_absent_optional_metadata_is_swallowed(
    enrichment: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = MagicMock(side_effect=frappe.DoesNotExistError("Optional"))
    monkeypatch.setattr(frappe, "get_meta", lookup)
    monkeypatch.setattr(frappe, "logger", MagicMock())
    assert enrichment._get_karam_fields_for_doctype("Absent") == []
    assert enrichment._get_karam_fields_for_doctype("Absent") == []
    lookup.assert_called_once()
    lookup.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        enrichment._get_karam_fields_for_doctype("Other")


@pytest.mark.parametrize(
    "case",
    [
        ([], None, None, None, False),
        (["name", "translation"], {}, None, None, False),
        (["name", "translation"], None, "S", None, False),
        (["name", "karam_series"], None, None, "T", False),
        (["name", "translation"], {"Sales Invoice": {"A"}}, None, "T", True),
        (["name", "karam_series"], None, "S", None, True),
    ],
)
def test_unsupported_voucher_queries_do_not_execute(
    enrichment: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[Any, ...],
) -> None:
    fields, names, series, translation, supported = case
    monkeypatch.setattr(
        enrichment, "_get_karam_fields_for_doctype", MagicMock(return_value=fields)
    )
    statement = enrichment._build_voucher_query(
        "Sales Invoice", names, series=series, translation=translation
    )
    assert (statement is not None) is supported
    if supported:
        assert "tabSales Invoice" in statement.get_sql()


@pytest.mark.parametrize(
    "fields",
    [
        ["name"],
        ["name", "karam_series"],
        ["name", "translation"],
        ["name", "karam_series", "translation"],
    ],
)
def test_projection_and_nonempty_value_predicate(
    enrichment: ModuleType, fields: list[str]
) -> None:
    table = MariaDB.DocType("Sales Invoice")
    projection = (
        MariaDB.from_(table)
        .select(*enrichment._voucher_query_projection(table, fields))
        .get_sql()
    )
    for field in ("karam_series", "translation"):
        assert (f"NULL `{field}`" in projection) == (field not in fields)
    conditions = enrichment._voucher_query_criteria(
        table, fields, None, series=None, translation=None
    )
    assert bool(conditions) == (len(fields) > 1)


@pytest.mark.parametrize("named", [False, True])
def test_union_query_keeps_voucher_identity(
    enrichment: ModuleType, monkeypatch: pytest.MonkeyPatch, named: bool
) -> None:
    monkeypatch.setattr(
        enrichment, "KARAM_DOCTYPES", ["Sales Invoice", "Journal Entry"]
    )
    monkeypatch.setattr(
        enrichment,
        "_get_karam_fields_for_doctype",
        MagicMock(return_value=["name", "karam_series", "translation"]),
    )
    rows = [
        {"_doctype": dt, "name": "same", "karam_series": dt}
        for dt in ("Sales Invoice", "Journal Entry")
    ]
    rows += [{"name": "invalid"}, {"_doctype": "Sales Invoice"}]
    execute = MagicMock(return_value=rows)
    monkeypatch.setattr(QueryBuilder, "run", execute)
    monkeypatch.setattr(frappe.db, "sql", execute)
    names = {"Sales Invoice": {"same"}, "Journal Entry": {"same"}} if named else None
    result = enrichment._fetch_voucher_data(names, series="S", translation="50%_off")
    assert set(result) == {("Sales Invoice", "same"), ("Journal Entry", "same")}
    assert result[("Sales Invoice", "same")]["karam_series"] == "Sales Invoice"
    execute.assert_called_once()


def test_single_source_fetch_limits_names_and_fields(
    enrichment: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        enrichment,
        "_get_karam_fields_for_doctype",
        MagicMock(return_value=["name", "translation"]),
    )
    get_all = MagicMock(return_value=[{"name": "B", "translation": "Text"}, {}])
    monkeypatch.setattr(frappe, "get_all", get_all)
    assert enrichment._fetch_voucher_data({"Sales Invoice": {"B", "A"}}) == {
        ("Sales Invoice", "B"): {
            "name": "B",
            "translation": "Text",
            "karam_series": None,
        }
    }
    get_all.assert_called_once_with(
        "Sales Invoice",
        filters={"name": ["in", ["A", "B"]]},
        fields=["name", "translation"],
        limit_page_length=2,
    )
    assert enrichment._fetch_voucher_data({"Sales Invoice": set()}) == {}


def test_unavailable_sources_produce_no_results(
    enrichment: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        enrichment, "_get_karam_fields_for_doctype", MagicMock(return_value=[])
    )
    assert enrichment._fetch_voucher_data({"Sales Invoice": {"A"}}) == {}
    assert enrichment._get_voucher_data_for_filters({"karam_series": "S"}) == {}
    assert enrichment._fetch_voucher_data({"Unsupported": {"X"}}) == {}


@pytest.mark.parametrize("size", [0, 1, 1000])
def test_chunking_keeps_every_value_once(enrichment: ModuleType, size: int) -> None:
    values = [str(index) for index in range(1001)]
    chunks = list(enrichment._chunked(values, size))
    assert [value for chunk in chunks for value in chunk] == values
    assert max(len(chunk) for chunk in chunks) <= (size or 1000)
    assert list(enrichment._chunked([], size)) == []


def test_compatibility_enrichment_preserves_existing_fields(
    enrichment: ModuleType,
) -> None:
    entry = frappe._dict(karam_series="Existing", translation="")
    enrichment._apply_voucher_data_to_entries(
        {("Sales Invoice", "V"): [entry]},
        {("Sales Invoice", "V"): {"karam_series": "Fetched", "translation": "Text"}},
    )
    assert entry == {"karam_series": "Existing", "translation": "Text"}


def test_supplier_only_lookup_does_not_create_customer_map(
    enrichment: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        frappe,
        "get_all",
        MagicMock(return_value=[{"name": "S", "supplier_name": "Supplier"}]),
    )
    assert enrichment.get_party_name_map(
        [frappe._dict(party_type="Supplier", party="S")]
    ) == {"Supplier": {"S": "Supplier"}}


def test_different_doctypes_do_not_reuse_cached_field_sets(
    enrichment: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    invoice = MagicMock()
    invoice.has_field.side_effect = [True, False]
    journal = MagicMock()
    journal.has_field.side_effect = [False, True]
    monkeypatch.setattr(frappe, "get_meta", MagicMock(side_effect=[invoice, journal]))
    assert enrichment._get_karam_fields_for_doctype("Sales Invoice") == [
        "name",
        "karam_series",
    ]
    assert enrichment._get_karam_fields_for_doctype("Journal Entry") == [
        "name",
        "translation",
    ]
    assert enrichment._get_karam_fields_for_doctype("Sales Invoice") == [
        "name",
        "karam_series",
    ]
