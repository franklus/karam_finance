"""Aggregation pipeline for GL report output."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import frappe
from erpnext.accounts.utils import get_currency_precision
from frappe import _
from frappe.utils import cstr, flt, getdate

_SEPARATOR_BLANK_FIELDS: tuple[str, ...] = (
    "debit",
    "credit",
    "debit_in_account_currency",
    "credit_in_account_currency",
    "debit_in_company_currency",
    "credit_in_company_currency",
    "balance_in_company_currency",
    "debit_in_transaction_currency",
    "credit_in_transaction_currency",
    "balance",
    "account_currency",
    "transaction_currency",
    "presentation_currency",
)
_FOOTER_ROW_TYPES = frozenset({"report_total", "closing"})
_OPENING_BALANCE_FIELDS: tuple[str, ...] = (
    "debit",
    "credit",
    "debit_in_account_currency",
    "credit_in_account_currency",
    "debit_in_company_currency",
    "credit_in_company_currency",
    "balance_in_company_currency",
)


def _make_group_separator_row() -> dict[str, object]:
    """Create a logical separator marker row for grouped output."""
    return {"is_separator": 1, "row_type": "separator"}


def _make_account_header_row(account: str) -> dict[str, object]:
    """Create an account label row without financial amounts."""
    row: dict[str, object] = {"account": account, "row_type": "account_header"}
    _blank_separator_fields(row)
    return row


def _blank_separator_fields(row: dict) -> None:
    """Ensure separator rows render as complete blanks in report output."""
    for fieldname in _SEPARATOR_BLANK_FIELDS:
        row[fieldname] = ""


def _normalise_key_part(value):
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _normalise_group_value(value):
    return _normalise_key_part(value)


def _has_displayed_opening_balance(opening: dict, precision: int | None) -> bool:
    """Return whether any opening amount remains non-zero when displayed."""
    for field in _OPENING_BALANCE_FIELDS:
        value = opening.get(field)
        if value is None:
            continue
        if flt(value, precision) != 0:
            return True
    return False


def _get_totals_dict(total_row_type: str = "group_total"):
    """Create template dict with Opening/Total/Closing summary rows."""

    def _debit_credit_dict(label, row_type: str):
        return frappe._dict(
            account=f"'{label}'",
            debit=0.0,
            credit=0.0,
            debit_in_account_currency=0.0,
            credit_in_account_currency=0.0,
            debit_in_company_currency=0.0,
            credit_in_company_currency=0.0,
            balance_in_company_currency=0.0,
            debit_in_transaction_currency=None,
            credit_in_transaction_currency=None,
            row_type=row_type,
        )

    return frappe._dict(
        opening=_debit_credit_dict(_("Opening"), "opening"),
        total=_debit_credit_dict(_("Total"), total_row_type),
        closing=_debit_credit_dict(_("Closing (Opening + Total)"), "closing"),
    )


def _group_by_field(categorise_by: str) -> str:
    if categorise_by == "Categorise by Party":
        return "party"
    if categorise_by in (
        "Categorise by Voucher (Consolidated)",
        "Categorise by Account",
        "Group by Account w/ Opening",
        "Flat Chronological",
    ):
        return "account"
    return "voucher_no"


def _init_gle_map(gl_entries, filters, totals_dict):
    gle_map = {}
    if filters.get("categorize_by") in (
        "Categorise by Voucher (Consolidated)",
        "Flat Chronological",
    ):
        return gle_map

    group_by = _group_by_field(filters.get("categorize_by", ""))
    for gle in gl_entries:
        group_value = _normalise_group_value(gle.get(group_by))
        if group_value not in gle_map:
            gle_map[group_value] = frappe._dict(
                totals=_clone_totals_dict(totals_dict), entries=[]
            )
    return gle_map


def _clone_totals_dict(template):
    """Clone the scalar summary rows without the cost of recursive deepcopy."""
    return frappe._dict(
        **{key: frappe._dict(row.copy()) for key, row in template.items()}
    )


def _get_account_type_map(
    company: str, account_names: set[str] | None = None
) -> frappe._dict:
    filters: dict[str, object] = {"company": company}
    if account_names:
        filters["name"] = ["in", sorted(account_names)]
    return frappe._dict(
        frappe.get_all(
            "Account",
            fields=["name", "account_type"],
            filters=filters,
            as_list=1,
            limit_page_length=max(1, len(account_names or []))
            if account_names
            else 100_000,
        )
    )


def _consolidated_key(
    gle, immutable_ledger, include_dims, accounting_dimensions, *, normalise=True
):
    """Build a composite key for voucher-consolidated grouping."""
    if not normalise:
        key_parts = [
            gle.get("posting_date"),
            gle.get("voucher_type"),
            gle.get("voucher_no"),
            gle.get("account"),
            gle.get("party_type"),
            gle.get("party"),
        ]
        if immutable_ledger:
            key_parts.append(gle.get("creation"))
        if include_dims:
            key_parts.extend(gle.get(dim) for dim in accounting_dimensions)
            key_parts.append(gle.get("cost_center"))
            key_parts.append(gle.get("project"))
        return tuple(key_parts)

    key_parts = [
        _normalise_key_part(gle.get("posting_date")),
        _normalise_key_part(gle.get("voucher_type")),
        _normalise_key_part(gle.get("voucher_no")),
        _normalise_key_part(gle.get("account")),
        _normalise_key_part(gle.get("party_type")),
        _normalise_key_part(gle.get("party")),
    ]
    if immutable_ledger:
        key_parts.append(_normalise_key_part(gle.get("creation")))
    if include_dims:
        key_parts.extend(
            _normalise_key_part(gle.get(dim)) for dim in accounting_dimensions
        )
        key_parts.append(_normalise_key_part(gle.get("cost_center")))
        key_parts.append(_normalise_key_part(gle.get("project")))
    return tuple(key_parts)


def _translate_value(value, translation_cache: dict):
    """Translate a repeated report value once per execution."""
    try:
        return translation_cache[value]
    except KeyError:
        translated = _(value)
        translation_cache[value] = translated
        return translated


def _translate_dimension_values(gle, accounting_dimensions, translation_cache):
    """Translate dimension values for display in the report."""
    for dimension in [*accounting_dimensions, "cost_center", "project"]:
        if val := gle.get(dimension):
            gle[dimension] = _translate_value(val, translation_cache)


def _build_aggregation_state(
    filters, accounting_dimensions, gl_entries, gle_map, totals
):
    """Create the mutable state shared by the aggregation helpers."""
    account_type_map = {}
    if filters.get("show_net_values_in_party_account"):
        account_type_map = _get_account_type_map(
            filters.get("company"),
            {gle.get("account") for gle in gl_entries if gle.get("account")},
        )

    state: Any = SimpleNamespace(
        entries=[],
        consolidated_gle={},
        against_voucher_lists={},
        group_by=_group_by_field(filters.get("categorize_by", "")),
        group_by_voucher_consolidated=(
            filters.get("categorize_by") == "Categorise by Voucher (Consolidated)"
        ),
        flat_chronological=filters.get("categorize_by") == "Flat Chronological",
        include_dimensions=filters.get("include_dimensions"),
        immutable_ledger=frappe.db.get_single_value(
            "Accounts Settings", "enable_immutable_ledger"
        ),
        add_transaction_currency=bool(
            filters.get("add_values_in_transaction_currency")
        ),
        show_net_party_values=bool(filters.get("show_net_values_in_party_account")),
        account_type_map=account_type_map,
        gle_map=gle_map,
        totals=totals,
        accounting_dimensions=accounting_dimensions,
    )

    def update_value_in_dict(
        data, key, gle, show_net_values=False, collect_against=False
    ):
        target = data[key]
        target.debit += gle.debit
        target.credit += gle.credit

        target.debit_in_account_currency += gle.debit_in_account_currency
        target.credit_in_account_currency += gle.credit_in_account_currency
        target.debit_in_company_currency += gle.debit_in_company_currency
        target.credit_in_company_currency += gle.credit_in_company_currency

        if state.add_transaction_currency and key not in (
            "opening",
            "closing",
            "total",
        ):
            target.debit_in_transaction_currency += gle.debit_in_transaction_currency
            target.credit_in_transaction_currency += gle.credit_in_transaction_currency

        if show_net_values or (
            state.show_net_party_values
            and state.account_type_map.get(target.account) in ("Receivable", "Payable")
        ):
            _apply_net_values(target)

        if collect_against and gle.against_voucher:
            state.against_voucher_lists.setdefault(key, []).append(gle.against_voucher)

    state.update_value_in_dict = update_value_in_dict
    return state


def _apply_net_values(target) -> None:
    """Replace debit/credit pairs with their signed net value."""
    net_value = target.debit - target.credit
    net_value_in_account_currency = (
        target.debit_in_account_currency - target.credit_in_account_currency
    )
    net_company = target.debit_in_company_currency - target.credit_in_company_currency

    if net_value < 0:
        dr_or_cr, rev_dr_or_cr = "credit", "debit"
    else:
        dr_or_cr, rev_dr_or_cr = "debit", "credit"

    target[dr_or_cr] = abs(net_value)
    target[dr_or_cr + "_in_account_currency"] = abs(net_value_in_account_currency)
    target[dr_or_cr + "_in_company_currency"] = abs(net_company)
    target[rev_dr_or_cr] = 0
    target[rev_dr_or_cr + "_in_account_currency"] = 0
    target[rev_dr_or_cr + "_in_company_currency"] = 0


def _prepare_gle_for_output(gle, translation_cache: dict) -> None:
    """Apply translations before a row participates in grouping."""
    gle.setdefault("row_type", "entry")
    gle.voucher_subtype = _translate_value(gle.voucher_subtype, translation_cache)
    gle.against_voucher_type = _translate_value(
        gle.against_voucher_type, translation_cache
    )
    gle.remarks = _translate_value(gle.remarks, translation_cache)
    gle.party_type = _translate_value(gle.party_type, translation_cache)


def _is_opening_entry(gle, from_date, show_opening_entries, disable_opening_balance):
    return gle.posting_date < from_date or (
        cstr(gle.is_opening) == "Yes"
        and not show_opening_entries
        and not disable_opening_balance
    )


def _is_report_entry(gle, to_date, show_opening_entries):
    return gle.posting_date <= to_date or (
        cstr(gle.is_opening) == "Yes" and show_opening_entries
    )


def _process_opening_entry(gle, group_by_value, state) -> None:
    if not state.group_by_voucher_consolidated and not state.flat_chronological:
        state.update_value_in_dict(
            state.gle_map[group_by_value].totals, "opening", gle, True
        )
        state.update_value_in_dict(
            state.gle_map[group_by_value].totals, "closing", gle, True
        )

    state.update_value_in_dict(state.totals, "opening", gle, True)
    state.update_value_in_dict(state.totals, "closing", gle, True)


def _process_report_entry(gle, group_by_value, state) -> None:
    if state.group_by_voucher_consolidated:
        _process_consolidated_entry(gle, state)
    elif state.flat_chronological:
        _process_flat_entry(gle, state)
    else:
        _process_grouped_entry(gle, group_by_value, state)


def _process_grouped_entry(gle, group_by_value, state) -> None:
    group_totals = state.gle_map[group_by_value].totals
    state.update_value_in_dict(group_totals, "total", gle)
    state.update_value_in_dict(group_totals, "closing", gle)
    state.update_value_in_dict(state.totals, "total", gle)
    state.update_value_in_dict(state.totals, "closing", gle)
    state.gle_map[group_by_value].entries.append(gle)


def _process_flat_entry(gle, state) -> None:
    state.update_value_in_dict(state.totals, "total", gle)
    state.update_value_in_dict(state.totals, "closing", gle)
    state.entries.append(gle)


def _process_consolidated_entry(gle, state) -> None:
    key = _consolidated_key(
        gle,
        state.immutable_ledger,
        state.include_dimensions,
        state.accounting_dimensions,
        normalise=False,
    )
    if key not in state.consolidated_gle:
        state.consolidated_gle[key] = gle
        if gle.against_voucher:
            state.against_voucher_lists.setdefault(key, []).append(gle.against_voucher)
    else:
        state.update_value_in_dict(
            state.consolidated_gle, key, gle, collect_against=True
        )


def _append_consolidated_entries(state) -> None:
    for key, value in state.consolidated_gle.items():
        if key in state.against_voucher_lists:
            vouchers = state.against_voucher_lists[key]
            value.against_voucher = (
                vouchers[0]
                if len(vouchers) == 1
                else ", ".join(dict.fromkeys(vouchers))
            )
        state.update_value_in_dict(state.totals, "total", value)
        state.update_value_in_dict(state.totals, "closing", value)
        state.entries.append(value)


def _aggregate_consolidated_rows(
    state,
    gl_entries,
    from_date,
    to_date,
    show_opening_entries,
    disable_opening_balance,
) -> None:
    """Aggregate the common voucher-consolidated mode without per-row dispatch."""
    consolidated = state.consolidated_gle
    against_vouchers = state.against_voucher_lists
    update_value = state.update_value_in_dict
    totals = state.totals
    translation_cache = {}

    for gle in gl_entries:
        _prepare_gle_for_output(gle, translation_cache)
        is_opening = gle.posting_date < from_date or (
            cstr(gle.is_opening) == "Yes"
            and not show_opening_entries
            and not disable_opening_balance
        )
        if is_opening:
            update_value(totals, "opening", gle, True)
            update_value(totals, "closing", gle, True)
        elif gle.posting_date <= to_date or (
            cstr(gle.is_opening) == "Yes" and show_opening_entries
        ):
            key = _consolidated_key(
                gle,
                state.immutable_ledger,
                state.include_dimensions,
                state.accounting_dimensions,
                normalise=False,
            )
            if key in consolidated:
                update_value(consolidated, key, gle, collect_against=True)
            else:
                consolidated[key] = gle
                if gle.against_voucher:
                    against_vouchers.setdefault(key, []).append(gle.against_voucher)

        if state.include_dimensions:
            _translate_dimension_values(
                gle, state.accounting_dimensions, translation_cache
            )


def _aggregate_non_consolidated_rows(
    state,
    gl_entries,
    from_date,
    to_date,
    show_opening_entries,
    disable_opening_balance,
) -> None:
    """Aggregate flat and grouped modes through their shared row contract."""
    translation_cache = {}
    for gle in gl_entries:
        _prepare_gle_for_output(gle, translation_cache)
        group_by_value = _normalise_group_value(gle.get(state.group_by))
        if _is_opening_entry(
            gle, from_date, show_opening_entries, disable_opening_balance
        ):
            _process_opening_entry(gle, group_by_value, state)
        elif _is_report_entry(gle, to_date, show_opening_entries):
            _process_report_entry(gle, group_by_value, state)

        if state.include_dimensions:
            _translate_dimension_values(
                gle, state.accounting_dimensions, translation_cache
            )


def _get_account_wise_gle(filters, accounting_dimensions, gl_entries, gle_map, totals):
    state = _build_aggregation_state(
        filters, accounting_dimensions, gl_entries, gle_map, totals
    )

    from_date, to_date = getdate(filters.from_date), getdate(filters.to_date)
    show_opening_entries = filters.get("show_opening_entries")
    disable_opening_balance = filters.get("disable_opening_balance_calculation")

    aggregate_rows = (
        _aggregate_consolidated_rows
        if state.group_by_voucher_consolidated
        else _aggregate_non_consolidated_rows
    )
    aggregate_rows(
        state,
        gl_entries,
        from_date,
        to_date,
        show_opening_entries,
        disable_opening_balance,
    )

    _append_consolidated_entries(state)
    return totals, state.entries


def _set_bill_no(gl_entries: list[frappe._dict]) -> None:
    """Set bill number from Purchase Invoice against-voucher values."""
    pi_names: set[str] = set()
    for gle in gl_entries:
        against_voucher = gle.get("against_voucher")
        if (
            gle.get("against_voucher_type") == "Purchase Invoice"
            and isinstance(against_voucher, str)
            and against_voucher
        ):
            pi_names.add(against_voucher)
    if not pi_names:
        for gle in gl_entries:
            gle["bill_no"] = ""
        return

    purchase_invoice = frappe.qb.DocType("Purchase Invoice")
    rows = (
        frappe.qb.from_(purchase_invoice)
        .select(purchase_invoice.name, purchase_invoice.bill_no)
        .where(
            purchase_invoice.name.isin(sorted(pi_names))
            & (purchase_invoice.docstatus == 1)
            & purchase_invoice.bill_no.notnull()
            & (purchase_invoice.bill_no != "")
        )
        .run(as_dict=True)
    )
    bill_map = {row.name: row.bill_no for row in rows}

    for gle in gl_entries:
        gle["bill_no"] = bill_map.get(gle.get("against_voucher"), "")


def get_data_with_opening_closing(
    filters,
    accounting_dimensions,
    gl_entries,
):
    """Assemble GL data with opening/closing balances."""
    data = []
    group_totals_dict = _get_totals_dict()
    totals_dict = _get_totals_dict(total_row_type="report_total")

    if not filters.get("_bill_no_joined"):
        _set_bill_no(gl_entries)

    gle_map = _init_gle_map(gl_entries, filters, group_totals_dict)

    totals, entries = _get_account_wise_gle(
        filters, accounting_dimensions, gl_entries, gle_map, totals_dict
    )

    categorise_by = filters.get("categorize_by", "")
    opening_balance_precision = (
        get_currency_precision()
        if categorise_by == "Group by Account w/ Opening"
        else None
    )

    data.append(totals.opening)

    if categorise_by not in (
        "Categorise by Voucher (Consolidated)",
        "Flat Chronological",
    ):
        for account, acc_dict in gle_map.items():
            include_opening_only_account = (
                categorise_by == "Group by Account w/ Opening"
                and _has_displayed_opening_balance(
                    acc_dict.totals.opening,
                    opening_balance_precision,
                )
            )
            if acc_dict.entries or include_opening_only_account:
                data.append(_make_group_separator_row())
                if (not categorise_by and not filters.get("voucher_no")) or (
                    categorise_by and categorise_by != "Categorise by Voucher"
                ):
                    data.append(acc_dict.totals.opening)

                if include_opening_only_account and not acc_dict.entries:
                    data.append(_make_account_header_row(account))

                data += acc_dict.entries

                if acc_dict.entries and (
                    categorise_by or not filters.get("voucher_no")
                ):
                    data.append(acc_dict.totals.total)

                if (not categorise_by and not filters.get("voucher_no")) or (
                    categorise_by and categorise_by != "Categorise by Voucher"
                ):
                    data.append(acc_dict.totals.closing)

        data.append(_make_group_separator_row())
    else:
        data += entries

    data.append(totals.total)
    data.append(totals.closing)

    return data


def get_result_as_list(data, filters):
    """Convert data to result list with running balance."""
    balance = 0.0
    balance_in_company_currency = 0.0
    presentation_currency = filters.get("presentation_currency")

    for d in data:
        if d.get("is_separator") or d.get("row_type") == "separator":
            d["is_separator"] = 1
            d["row_type"] = "separator"
            balance = 0.0
            balance_in_company_currency = 0.0
            _blank_separator_fields(d)
            continue

        if d.get("row_type") == "account_header":
            _blank_separator_fields(d)
            continue

        if not d.get("posting_date"):
            balance = 0.0
            balance_in_company_currency = 0.0

        balance += d.get("debit", 0) - d.get("credit", 0)
        balance_in_company_currency += d.get("debit_in_company_currency", 0) - d.get(
            "credit_in_company_currency", 0
        )
        d["balance"] = balance
        d["balance_in_company_currency"] = balance_in_company_currency
        if not d.get("account_currency"):
            d["account_currency"] = filters.get("account_currency")
        d["presentation_currency"] = presentation_currency

    return _insert_footer_separator(data)


def _insert_footer_separator(result: list[dict]) -> list[dict]:
    """Insert a blank separator row above trailing footer rows only."""
    if not result:
        return result

    footer_start_idx = None
    for idx in range(len(result) - 1, -1, -1):
        row = result[idx]
        row_type = row.get("row_type")
        if row_type:
            if row_type in _FOOTER_ROW_TYPES:
                footer_start_idx = idx
                continue
            if footer_start_idx is not None:
                break
            continue

        account_value = row.get("account", "")
        if isinstance(account_value, str) and (
            account_value.strip().strip("'").startswith("Total")
            or account_value.strip().strip("'").startswith("Closing")
        ):
            footer_start_idx = idx
            continue
        if footer_start_idx is not None:
            break

    if footer_start_idx is None:
        return result

    if footer_start_idx > 0 and (
        result[footer_start_idx - 1].get("is_separator")
        or result[footer_start_idx - 1].get("row_type") == "separator"
    ):
        return result

    data_rows = result[:footer_start_idx]
    footer_rows = result[footer_start_idx:]
    blank_row: dict = dict.fromkeys(result[0], "")
    blank_row["is_separator"] = 1
    blank_row["row_type"] = "separator"

    return [*data_rows, blank_row, *footer_rows]
