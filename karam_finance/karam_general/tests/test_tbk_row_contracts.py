"""Trial Balance columns and group-row rendering contracts."""

import importlib
from unittest.mock import patch

import frappe


def test_tbk_columns_and_prepare_data_hide_group_entry_path() -> None:
    cols = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_columns"
    ).get_columns()
    assert (
        cols[0]["fieldname"] == "account"
        and any(
            c["fieldname"] == "closing_debit" and c["options"] == "currency"
            for c in cols
        )
        and any(
            c["fieldname"] == "closing_debit_in_account_currency"
            and c["options"] == "account_currency"
            for c in cols
        )
    )
    r = importlib.import_module(
        "karam_finance.karam_general.report.trial_balance_(karam).tbk_rows"
    )
    group = frappe._dict(
        name="G",
        parent_account=None,
        indent=0,
        is_group=1,
        account_name="G",
        account_number="",
        opening_debit=0,
        opening_credit=0,
        debit=0,
        credit=0,
        closing_debit=0,
        closing_credit=0,
    )
    leaf = frappe._dict(
        name="L",
        parent_account="G",
        indent=2,
        is_group=0,
        account_name="L",
        account_number="",
        opening_debit=1,
        opening_credit=0,
        debit=0,
        credit=0,
        closing_debit=1,
        closing_credit=0,
    )
    for a in (group, leaf):
        a.update(
            dict.fromkeys(
                (
                    "opening_debit_in_account_currency",
                    "opening_credit_in_account_currency",
                    "debit_in_account_currency",
                    "credit_in_account_currency",
                    "closing_debit_in_account_currency",
                    "closing_credit_in_account_currency",
                ),
                0,
            )
        )
    with patch.object(r, "get_zero_cutoff", return_value=0.005):
        rows = r.prepare_data(
            [group, leaf],
            frappe._dict(
                show_group_accounts=0, from_date="2026-01-01", to_date="2026-01-31"
            ),
            {},
            company_currency="USD",
        )
    assert [x["account"] for x in rows] == ["L", "'Total'"] and rows[0]["indent"] == 0
