"""Site-free regressions for reconciliation arithmetic and write boundaries."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, call, patch

import frappe
import pytest

from . import letter_reconciliation as lr


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    database = MagicMock()
    monkeypatch.setattr(frappe, "db", database)
    monkeypatch.setattr(frappe, "log_error", MagicMock())
    monkeypatch.setattr(
        frappe.local, "flags", frappe._dict(mute_messages=True), raising=False
    )
    monkeypatch.setattr(frappe.local, "message_log", [], raising=False)
    monkeypatch.setattr(frappe.local, "lang", "en", raising=False)
    return database


@pytest.mark.parametrize(("units", "precision"), [(1, 0), (100, 2), (1000, 3)])
def test_precision_uses_account_currency_subunits(
    database: MagicMock, units: int, precision: int
) -> None:
    database.get_value.side_effect = ["TEST", units]
    with patch.object(frappe, "get_precision") as field_precision:
        assert lr._resolve_amount_precision("Account") == precision
    assert database.get_value.call_args_list == [
        call("Account", "Account", "account_currency"),
        call("Currency", "TEST", "fraction_units"),
    ]
    field_precision.assert_not_called()


@pytest.mark.parametrize(
    ("field_precision", "system_precision", "expected"),
    [
        (0, 3, 0),
        (3, 2, 3),
        (None, "0", 0),
        (None, "3", 3),
        (None, None, 2),
        (None, "", 2),
    ],
)
def test_precision_fallbacks_preserve_zero(
    database: MagicMock,
    *,
    field_precision: int | None,
    system_precision: str | int | None,
    expected: int,
) -> None:
    database.get_value.side_effect = ["TEST", None]
    database.get_default.return_value = system_precision
    # Exercise Frappe's actual wrapper and its signature, substituting only metadata.
    with (
        patch.object(frappe, "get_meta") as get_meta,
        patch(
            "frappe.model.meta.get_field_precision",
            autospec=True,
            return_value=field_precision,
        ),
    ):
        assert lr._resolve_amount_precision("Account") == expected
    get_meta.assert_called_once_with("Journal Entry Account")
    get_meta.return_value.get_field.assert_called_once_with("debit")


@pytest.mark.parametrize("units", [1, 100, 1000])
@pytest.mark.parametrize(
    "multiple", [Decimal(0), Decimal("0.9"), Decimal(1), Decimal("1.1")]
)
def test_balance_tolerance_at_currency_boundary(
    database: MagicMock, units: int, multiple: Decimal
) -> None:
    database.get_value.side_effect = ["TEST", units]
    quantum = Decimal(1) / units
    credit = Decimal(9007199254740993)
    debit = credit + quantum * multiple
    args = {
        "cr_items": [{"credit": str(credit)}],
        "dt_items": [{"debit": str(debit)}],
        "account": "Account",
    }
    if multiple >= 1:
        with pytest.raises(frappe.ValidationError, match="must equal total debits"):
            lr.validate_sum_of_credit_and_debit(**args)
    else:
        lr.validate_sum_of_credit_and_debit(**args)


@pytest.mark.parametrize("value", ["{", "{}", "[1]", 1])
def test_malformed_selection_is_rejected(database: MagicMock, value: Any) -> None:
    with pytest.raises(frappe.ValidationError, match="Invalid data received"):
        lr._coerce_items(value)
    database.bulk_update.assert_not_called()


@pytest.mark.parametrize("value", [None, "", [], "[]"])
def test_empty_selection_is_rejected_before_writes(
    database: MagicMock, value: Any
) -> None:
    with pytest.raises(frappe.ValidationError, match="at least one credit entry"):
        lr.set_letter(value, value)
    database.bulk_update.assert_not_called()
    database.rollback.assert_called_once_with(save_point="letter_reconciliation_assign")


@pytest.mark.parametrize(("existing", "removing"), [("A", False), ("", True)])
def test_letter_state_validation(
    database: MagicMock, existing: str, removing: bool
) -> None:
    with (
        patch.object(lr, "validate_sum_of_credit_and_debit"),
        pytest.raises(frappe.ValidationError),
    ):
        lr._prepare_letter_items(
            [{"letter": existing}], [{"letter": existing}], require_letter=removing
        )
    database.bulk_update.assert_not_called()


@pytest.mark.parametrize("count", [0, 1, 1000, 1001])
def test_party_enrichment_queries_are_batched(database: MagicMock, count: int) -> None:
    entries = [
        {"party_type": "Customer", "party": f"C-{index}"} for index in range(count)
    ]
    entries += [dict(entry) for entry in entries]

    def get_parties(
        _doctype: str, *, filters: dict[str, Any], **_kwargs: object
    ) -> list[dict[str, str]]:
        return [
            {"name": name, "customer_name": f"Name {name}"}
            for name in filters["name"][1]
        ]

    with patch.object(frappe, "get_all", side_effect=get_parties) as get_all:
        lr._attach_party_names(entries)
    assert get_all.call_count == (count + 999) // 1000
    assert all(
        len(request.kwargs["filters"]["name"][1]) <= 1000
        for request in get_all.call_args_list
    )
    assert all(entry["party_name"] == f"Name {entry['party']}" for entry in entries)
    database.commit.assert_not_called()


def test_party_types_and_unknown_parties_keep_their_mapping(
    database: MagicMock,
) -> None:
    entries = [
        {"party_type": "Customer", "party": "same"},
        {"party_type": "Supplier", "party": "same"},
        {"party_type": "Other", "party": "same"},
        {"party_type": "", "party": ""},
    ]
    with patch.object(
        frappe,
        "get_all",
        side_effect=[
            [{"name": "same", "customer_name": "Customer name"}],
            [{"name": "same", "supplier_name": "Supplier name"}],
        ],
    ):
        lr._attach_party_names(entries)
    assert [entry["party_name"] for entry in entries] == [
        "Customer name",
        "Supplier name",
        "",
        "",
    ]
    database.commit.assert_not_called()


@pytest.mark.parametrize("removing", [False, True])
def test_gl_failure_rolls_back_selected_row_updates(
    database: MagicMock, removing: bool
) -> None:
    rows = [
        {"account": "Account", "jv_row_name": "ROW-1"},
        {"account": "Account", "jv_row_name": "ROW-2"},
    ]
    with (
        patch.object(
            lr, "_prepare_letter_items", return_value=(rows[:1], rows[1:], rows, "B")
        ),
        patch.object(lr, "_validate_lettering_enabled_for_accounts"),
        patch.object(lr, "_compute_latest_year", return_value=2026),
        patch.object(lr, "_get_letter_for_year_locked", return_value="B"),
        patch.object(
            lr, "_update_gl_letters", side_effect=RuntimeError("write failed")
        ),
        patch.object(lr, "update_year_letter") as advance,
        pytest.raises(frappe.ValidationError, match="write failed"),
    ):
        if removing:
            lr.remove_letter(rows[:1], rows[1:])
        else:
            lr.set_letter(rows[:1], rows[1:])
    expected_letter = "" if removing else "B"
    database.bulk_update.assert_called_once_with(
        "Journal Entry Account",
        {"ROW-1": {"letter": expected_letter}, "ROW-2": {"letter": expected_letter}},
    )
    savepoint = (
        "letter_reconciliation_remove" if removing else "letter_reconciliation_assign"
    )
    database.rollback.assert_called_once_with(save_point=savepoint)
    advance.assert_not_called()
    database.commit.assert_not_called()


def test_latest_year_accepts_date_objects_and_ignores_missing_dates(
    database: MagicMock,
) -> None:
    assert (
        lr._compute_latest_year(
            [
                {"posting_date": date(2024, 1, 1)},
                {"posting_date": datetime(2026, 1, 1, tzinfo=UTC)},
                {"posting_date": "2025-02-01"},
                {"posting_date": "invalid"},
                {},
            ]
        )
        == 2026
    )
    database.commit.assert_not_called()


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([], "does not exist"),
        ([{"name": "Cash", "enable_lettering": 0}], "not enabled"),
    ],
)
def test_ineligible_accounts_cannot_be_lettered(
    database: MagicMock, rows: list[dict[str, Any]], message: str
) -> None:
    with (
        patch.object(frappe, "get_all", return_value=rows),
        pytest.raises(frappe.ValidationError, match=message),
    ):
        lr._validate_lettering_enabled_for_accounts({"Cash"})
    database.bulk_update.assert_not_called()


@pytest.mark.parametrize(
    ("label", "predicate"),
    [
        ("Only unassigned rows", '"letter" IS NULL OR "letter"=\'\''),
        ("Only assigned rows", '"letter" IS NOT NULL AND "letter"<>\'\''),
    ],
)
def test_letter_filter_keeps_null_and_empty_semantics(
    database: MagicMock, label: str, predicate: str
) -> None:
    from frappe.query_builder.builder import MariaDB  # noqa: PLC0415

    table = MariaDB.DocType("Journal Entry Account")
    query = MariaDB.from_(table).select(table.name)
    with patch.object(frappe, "qb", MariaDB):
        result = lr._filter_journal_entries(
            query,
            {
                "start_date": "",
                "end_date": "",
                "party_type": "",
                "party": "",
                "show_letter": label,
            },
        )
    assert predicate in result.get_sql().replace("`", '"')
    database.sql.assert_not_called()
