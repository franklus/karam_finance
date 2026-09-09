"""Pure reporting-currency Trial Balance contracts."""

from __future__ import annotations

import importlib
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import frappe
import pytest

MODULE = "karam_finance.reporting_currency.report.trial_balance_(reporting_currency).tbk_data"


def _identity_text(message: str) -> str:
    return message


def _identity_query(query: Any, *_args: Any, **_kwargs: Any) -> Any:
    return query


def _filters_module() -> Any:
    return importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_filters")


def _raise_validation(*_args: Any, **_kwargs: Any) -> None:
    raise frappe.ValidationError


def test_filter_checkboxes_normalise_all_supported_controls() -> None:
    module = _filters_module()
    filters = frappe._dict(
        ignore_fiscal_year="1",
        show_zero_values="0",
        show_unclosed_fy_pl_balances="1",
        with_period_closing_entry_for_opening="0",
        with_period_closing_entry_for_current_period="1",
        include_default_book_entries="0",
        show_net_values="1",
        show_group_accounts="0",
        exclude_reporting_doe="1",
        exclude_manual_entries="0",
    )
    module._normalise_checkboxes(filters)
    assert tuple(
        filters[key]
        for key in (
            "ignore_fiscal_year",
            "show_zero_values",
            "show_unclosed_fy_pl_balances",
            "with_period_closing_entry_for_opening",
            "with_period_closing_entry_for_current_period",
            "include_default_book_entries",
            "show_net_values",
            "show_group_accounts",
            "exclude_reporting_doe",
            "exclude_manual_entries",
        )
    ) == (1, 0, 1, 0, 1, 0, 1, 0, 1, 0)
    omitted = frappe._dict()
    module._normalise_checkboxes(omitted)
    assert all(value == 0 for value in omitted.values())


def test_filter_requires_company_and_fiscal_year_and_known_year() -> None:
    module = _filters_module()
    with (
        patch.object(module, "_", side_effect=_identity_text),
        patch.object(module.frappe, "throw", side_effect=_raise_validation),
        pytest.raises(frappe.ValidationError),
    ):
        module.validate_filters(frappe._dict())
    with (
        patch.object(module, "_", side_effect=_identity_text),
        patch.object(module.frappe, "throw", side_effect=_raise_validation),
        pytest.raises(frappe.ValidationError),
    ):
        module.validate_filters(frappe._dict(company="K", fiscal_year=""))
    with (
        patch.object(module, "_", side_effect=_identity_text),
        patch.object(module.frappe, "get_cached_value", return_value=None),
        patch.object(module.frappe, "throw", side_effect=_raise_validation),
        pytest.raises(frappe.ValidationError),
    ):
        module.validate_filters(frappe._dict(company="K", fiscal_year="Missing"))


def test_fiscal_year_filters_default_dates_and_clamp_each_boundary() -> None:
    module = _filters_module()
    fiscal_year = frappe._dict(year_start_date="2026-01-01", year_end_date="2026-12-31")
    defaults = frappe._dict(company="K", fiscal_year="FY")
    outside = frappe._dict(
        company="K", fiscal_year="FY", from_date="2025-12-31", to_date="2027-01-01"
    )
    notices: list[str] = []
    with (
        patch.object(module.frappe, "get_cached_value", return_value=fiscal_year),
        patch.object(module, "formatdate", side_effect=str),
        patch.object(module.frappe, "msgprint", side_effect=notices.append),
    ):
        module.validate_filters(defaults)
        module.validate_filters(outside)
    assert (defaults.from_date, defaults.to_date) == (
        module.getdate("2026-01-01"),
        module.getdate("2026-12-31"),
    )
    assert (outside.from_date, outside.to_date) == (
        module.getdate("2026-01-01"),
        module.getdate("2026-12-31"),
    )
    assert len(notices) == 2


def test_fiscal_year_filters_reject_invalid_and_reversed_dates() -> None:
    module = _filters_module()
    fiscal_year = frappe._dict(year_start_date="2026-01-01", year_end_date="2026-12-31")
    for filters in (
        frappe._dict(company="K", fiscal_year="FY", from_date="0000-00-00"),
        frappe._dict(
            company="K", fiscal_year="FY", from_date="2026-02-01", to_date="2026-01-01"
        ),
    ):
        with (
            patch.object(module, "_", side_effect=_identity_text),
            patch.object(module.frappe, "get_cached_value", return_value=fiscal_year),
            patch.object(module.frappe, "throw", side_effect=_raise_validation),
            pytest.raises(frappe.ValidationError),
        ):
            module.validate_filters(filters)


def test_ignore_fiscal_year_requires_dates_validates_range_and_gets_fiscal_bounds() -> (
    None
):
    module = _filters_module()
    for filters in (
        frappe._dict(company="K", ignore_fiscal_year=1, to_date="2026-01-31"),
        frappe._dict(company="K", ignore_fiscal_year=1, from_date="2026-01-01"),
        frappe._dict(
            company="K",
            ignore_fiscal_year=1,
            from_date="2026-02-01",
            to_date="2026-01-01",
        ),
    ):
        with (
            patch.object(module, "_", side_effect=_identity_text),
            patch.object(module.frappe, "throw", side_effect=_raise_validation),
            pytest.raises(frappe.ValidationError),
        ):
            module.validate_filters(filters)
    filters = frappe._dict(
        company="K",
        ignore_fiscal_year="1",
        from_date="2026-01-01",
        to_date="2026-01-31",
    )
    with patch.object(
        module,
        "get_fiscal_year",
        return_value=("FY", "2026-01-01", "2026-12-31"),
    ) as fiscal_year:
        module.validate_filters(filters)
    fiscal_year.assert_called_once_with(
        module.getdate("2026-01-01"), company="K", verbose=0
    )
    assert (filters.year_start_date, filters.year_end_date) == (
        module.getdate("2026-01-01"),
        module.getdate("2026-12-31"),
    )


def test_ignore_fiscal_year_unclosed_skips_fiscal_lookup() -> None:
    module = _filters_module()
    filters = frappe._dict(
        company="K",
        ignore_fiscal_year=1,
        show_unclosed_fy_pl_balances="1",
        from_date="2026-01-01",
        to_date="2026-01-31",
    )
    with patch.object(module, "get_fiscal_year") as fiscal_year:
        module.validate_filters(filters)
    fiscal_year.assert_not_called()
    assert (filters.year_start_date, filters.year_end_date) == (None, None)


def _aggregation_module() -> Any:
    return importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_aggregation")


def _tb_account(name: str = "A", **values: Any) -> Any:
    return frappe._dict(name=name, root_type="Asset", account_currency="EUR", **values)


def test_legacy_gl_helper_keeps_decimal_gross_and_nets_account_currency() -> None:
    aggregation = _aggregation_module()
    account = _tb_account()
    aggregation.apply_gl_data_to_accounts(
        [account],
        {
            "A": {
                "opening_debit": "10.25",
                "opening_credit": "3.00",
                "debit": "2.50",
                "credit": "1.25",
                "opening_debit_in_account_currency": "9.25",
                "opening_credit_in_account_currency": "3.00",
                "debit_in_account_currency": "2.50",
                "credit_in_account_currency": "1.25",
                "account_currencies": {"EUR"},
            }
        },
        show_net_values=True,
    )
    assert (account.opening_debit, account.opening_credit) == (
        Decimal("10.25"),
        Decimal("3.00"),
    )
    assert (account.closing_debit, account.closing_credit) == (
        Decimal("12.75"),
        Decimal("4.25"),
    )
    assert (
        account.opening_debit_in_account_currency,
        account.opening_credit_in_account_currency,
    ) == (Decimal("6.25"), 0)
    assert (
        account.closing_debit_in_account_currency,
        account.closing_credit_in_account_currency,
    ) == (Decimal("7.50"), 0)


def test_legacy_account_currency_helper_filters_opening_only_when_requested() -> None:
    aggregation = _aggregation_module()
    entries = {
        "A": [
            frappe._dict(
                is_opening="Yes",
                account_currency="EUR",
                debit_in_account_currency=3,
                credit_in_account_currency=0,
            ),
            frappe._dict(
                is_opening="No",
                account_currency="",
                debit_in_account_currency=2,
                credit_in_account_currency=1,
            ),
        ]
    }
    opening = {
        "A": {
            "opening_debit_in_account_currency": 10,
            "opening_credit_in_account_currency": 4,
            "account_currencies": {"EUR"},
        }
    }
    filtered = _tb_account()
    included = _tb_account()
    aggregation.apply_account_currency_data_to_accounts(
        [filtered], entries, opening, show_net_values=False, ignore_is_opening=0
    )
    aggregation.apply_account_currency_data_to_accounts(
        [included], entries, opening, show_net_values=False, ignore_is_opening=1
    )
    assert (
        filtered.debit_in_account_currency,
        filtered.credit_in_account_currency,
    ) == (2, 1)
    assert (
        included.debit_in_account_currency,
        included.credit_in_account_currency,
    ) == (5, 1)
    assert filtered._account_currencies == included._account_currencies == {"EUR"}


def test_finalise_account_currency_blanks_empty_and_mixed_provenance() -> None:
    aggregation = _aggregation_module()
    empty = _tb_account(_account_currencies=set(), _raw_account_currency_values={})
    mixed = _tb_account(
        _account_currencies={"EUR", "USD"},
        _raw_account_currency_values={"debit_in_account_currency": 7},
    )
    aggregation.finalize_account_currency_values([empty, mixed])
    for account in (empty, mixed):
        assert account.account_currency == ""
        assert all(
            account[field] is None
            for field in (
                "opening_debit_in_account_currency",
                "opening_credit_in_account_currency",
                "debit_in_account_currency",
                "credit_in_account_currency",
                "closing_debit_in_account_currency",
                "closing_credit_in_account_currency",
            )
        )


def test_netting_handles_asset_liability_and_no_root_without_account_currency_change() -> (
    None
):
    aggregation = _aggregation_module()
    asset = frappe._dict(
        root_type="Asset",
        opening_debit=2,
        opening_credit=5,
        closing_debit=5,
        closing_credit=2,
        opening_debit_in_account_currency=11,
        opening_credit_in_account_currency=2,
        closing_debit_in_account_currency=12,
        closing_credit_in_account_currency=3,
    )
    liability = frappe._dict(
        root_type="Liability",
        opening_debit=5,
        opening_credit=2,
        closing_debit=2,
        closing_credit=5,
    )
    no_root = frappe._dict(opening_debit=2, opening_credit=5)
    aggregation.prepare_opening_closing(asset, include_account_currency=False)
    aggregation.prepare_opening_closing(liability, include_account_currency=False)
    aggregation.prepare_opening_closing(no_root)
    assert (asset.opening_debit, asset.opening_credit) == (0, 3)
    assert (asset.closing_debit, asset.closing_credit) == (3, 0)
    assert (
        asset.opening_debit_in_account_currency,
        asset.closing_debit_in_account_currency,
    ) == (11, 12)
    assert (liability.opening_debit, liability.opening_credit) == (3, 0)
    assert (liability.closing_debit, liability.closing_credit) == (0, 3)
    assert (no_root.opening_debit, no_root.opening_credit) == (2, 5)


def test_account_currency_netting_flips_negative_asset_and_skips_missing_root() -> None:
    aggregation = _aggregation_module()
    asset = frappe._dict(
        root_type="Asset",
        opening_debit_in_account_currency=2,
        opening_credit_in_account_currency=5,
        closing_debit_in_account_currency=5,
        closing_credit_in_account_currency=2,
    )
    no_root = frappe._dict(opening_debit_in_account_currency=2)
    aggregation.prepare_account_currency_opening_closing(asset)
    aggregation.prepare_account_currency_opening_closing(no_root)
    assert (
        asset.opening_debit_in_account_currency,
        asset.opening_credit_in_account_currency,
    ) == (0, 3)
    assert (
        asset.closing_debit_in_account_currency,
        asset.closing_credit_in_account_currency,
    ) == (3, 0)
    assert no_root.opening_debit_in_account_currency == 2
    via_outer_netting = frappe._dict(
        root_type="Asset",
        opening_debit=2,
        opening_credit=1,
        closing_debit=3,
        closing_credit=1,
        opening_debit_in_account_currency=2,
        opening_credit_in_account_currency=1,
        closing_debit_in_account_currency=3,
        closing_credit_in_account_currency=1,
    )
    aggregation.prepare_opening_closing(via_outer_netting)
    assert via_outer_netting.opening_debit_in_account_currency == 1


def _rows_module() -> Any:
    return importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_rows")


def _row_account(name: str, **values: Any) -> Any:
    rows = _rows_module()
    fields = (
        rows.VALUE_FIELDS
        + rows.ACCOUNT_CCY_VALUE_FIELDS
        + rows.COMPANY_CCY_VALUE_FIELDS
    )
    return frappe._dict(
        {
            **dict.fromkeys(fields, 0),
            "name": name,
            "parent_account": None,
            "indent": 0,
            "is_group": 0,
            "account_name": name,
            "account_number": "",
            "root_type": "Asset",
            "account_currency": "EUR",
            "_account_currencies": {"EUR"},
            **values,
        }
    )


def test_rows_blank_and_total_root_leaf_and_mixed_currency_contracts() -> None:
    rows = _rows_module()
    blank = rows.get_blank_row()
    assert (blank["account"], blank["is_spacer"], blank["has_value"]) == (
        "",
        True,
        True,
    )
    assert all(
        blank[field] is None
        for field in rows.VALUE_FIELDS
        + rows.ACCOUNT_CCY_VALUE_FIELDS
        + rows.COMPANY_CCY_VALUE_FIELDS
    )
    root = _row_account("Root", is_group=1, debit=2, debit_in_account_currency=3)
    leaf = _row_account(
        "Leaf", parent_account="Root", debit=5, debit_in_account_currency=7
    )
    by_roots = rows.calculate_total_row([root, leaf], "USD", show_group_accounts=True)
    by_leaves = rows.calculate_total_row([root, leaf], "USD", show_group_accounts=False)
    assert (by_roots["debit"], by_leaves["debit"]) == (2, 5)
    assert (by_roots["account_currency"], by_leaves["account_currency"]) == (
        "EUR",
        "EUR",
    )
    mixed = _row_account("Mixed", debit=1, _account_currencies={"EUR", "USD"})
    total = rows.calculate_total_row([mixed], "USD")
    assert total["account_currency"] == ""
    assert all(total[field] is None for field in rows.ACCOUNT_CCY_VALUE_FIELDS)


def test_rows_prepare_data_nets_labels_and_hides_groups() -> None:
    rows = _rows_module()
    root = _row_account(
        "Root",
        is_group=1,
        account_number="100",
        opening_debit=2,
        opening_credit=5,
        closing_debit=5,
        closing_credit=2,
        opening_debit_in_company_currency=2,
        opening_credit_in_company_currency=5,
        closing_debit_in_company_currency=5,
        closing_credit_in_company_currency=2,
        opening_debit_in_account_currency=2,
        opening_credit_in_account_currency=5,
        closing_debit_in_account_currency=5,
        closing_credit_in_account_currency=2,
    )
    leaf = _row_account("Leaf", parent_account="Root", indent=1, debit=1)
    with patch.object(rows, "get_zero_cutoff", return_value=Decimal("0.005")):
        data = rows.prepare_data(
            [root, leaf],
            frappe._dict(
                from_date="2026-01-01",
                to_date="2026-01-31",
                show_group_accounts=0,
                show_net_values=1,
            ),
            {None: [root], "Root": [leaf]},
            company_currency="USD",
        )
    assert [row["account"] for row in data] == ["Leaf", "'Total'"]
    assert data[0]["indent"] == 0
    assert root.opening_credit == 3 and root.closing_debit == 3


def test_rows_value_threshold_none_and_account_only_values() -> None:
    rows = _rows_module()
    account_only = _row_account("A", debit_in_account_currency=8)
    reporting = _row_account("B", debit=Decimal("0.005"))
    none_values = _row_account("C", debit=None, debit_in_company_currency=None)
    with patch.object(rows, "get_zero_cutoff", return_value=Decimal("0.005")):
        prepared = rows.prepare_data(
            [account_only, reporting, none_values],
            frappe._dict(from_date="a", to_date="b", show_group_accounts=1),
            {None: [account_only, reporting, none_values]},
            company_currency="USD",
        )
    row_by_account = {row["account"]: row for row in prepared}
    assert not row_by_account["A"]["has_value"]
    assert row_by_account["B"]["has_value"]
    assert row_by_account["C"]["debit"] is None


def test_rows_filter_keeps_ancestors_and_handles_show_all_orphan_and_cycle() -> None:
    rows = _rows_module()
    data = [
        {"account": "Root", "has_value": False},
        {"account": "Child", "has_value": True},
        {"account": "Orphan", "has_value": False},
    ]
    parents = {None: [{"name": "Root"}], "Root": [{"name": "Child"}]}
    assert [
        row["account"] for row in rows.filter_out_zero_value_rows(data, parents)
    ] == ["Root", "Child"]
    assert rows.filter_out_zero_value_rows(data, parents, show_zero_values=True) == data
    shown: set[str | None] = set()
    rows._include_account_ancestors(shown, "A", {"A": "B", "B": "A"})
    assert shown == {"A", "B"}


def test_money_display_formats_large_half_cent_and_skips_none() -> None:
    money = importlib.import_module(MODULE.rsplit(".", 1)[0] + ".tbk_money")
    values = [{"amount": Decimal("51309440814079.5450"), "missing": None, "label": "A"}]
    money.prepare_display_amounts(values)
    assert values[0]["_display_amounts"] == {"amount": "51309440814079.55"}
    assert values[0]["missing"] is None


def test_rows_netting_skips_blank_account_currency() -> None:
    rows = _rows_module()
    blank_currency = _row_account("Blank", account_currency="")
    with patch.object(rows, "get_zero_cutoff", return_value=Decimal("0.005")):
        prepared = rows.prepare_data(
            [blank_currency],
            frappe._dict(
                from_date="2026-01-01",
                to_date="2026-01-31",
                show_net_values=1,
            ),
            {None: [blank_currency]},
            company_currency="USD",
        )
    assert prepared[0]["account_currency"] == ""


def test_public_trial_balance_wrappers_forward_arguments_and_return_values() -> None:
    report = importlib.import_module(
        MODULE.rsplit(".", 1)[0] + ".trial_balance_(reporting_currency)"
    )
    filters = frappe._dict(company="K")
    accounts = [frappe._dict(name="A")]
    mapping = {"A": accounts[0]}
    with (
        patch.object(report, "_validate_filters", return_value="valid") as validate,
        patch.object(report, "_get_data", return_value="data") as get_data,
        patch.object(report, "_get_gl_data_optimised", return_value={"A": {}}) as query,
        patch.object(
            report, "_apply_gl_data_to_accounts", return_value="applied"
        ) as apply,
        patch.object(
            report, "_accumulate_values_into_parents", return_value="parents"
        ) as accumulate,
        patch.object(report, "_get_blank_row", return_value={"blank": 1}) as blank,
        patch.object(
            report, "_calculate_total_row", return_value={"total": 1}
        ) as total,
        patch.object(report, "_prepare_data", return_value=[{"row": 1}]) as prepare,
        patch.object(report, "_prepare_opening_closing", return_value="net") as net,
    ):
        assert report.validate_filters(filters) == "valid"
        assert report.get_data(filters) == "data"
        assert report.get_gl_data_optimised(filters) == {"A": {}}
        assert report.apply_gl_data_to_accounts(accounts, {"A": {}}, True) == "applied"
        assert report.accumulate_values_into_parents(accounts, mapping) == "parents"
        assert report.get_blank_row() == {"blank": 1}
        assert report.calculate_total_row(accounts, "USD", False) == {"total": 1}
        assert report.prepare_data(
            accounts, filters, mapping, company_currency="USD"
        ) == [{"row": 1}]
        assert report.prepare_opening_closing(accounts[0]) == "net"
    validate.assert_called_once_with(filters)
    get_data.assert_called_once_with(filters)
    query.assert_called_once_with(filters)
    apply.assert_called_once_with(accounts, {"A": {}}, True)
    accumulate.assert_called_once_with(accounts, mapping)
    blank.assert_called_once_with()
    total.assert_called_once_with(accounts, "USD", show_group_accounts=False)
    prepare.assert_called_once_with(accounts, filters, mapping, company_currency="USD")
    net.assert_called_once_with(accounts[0])
