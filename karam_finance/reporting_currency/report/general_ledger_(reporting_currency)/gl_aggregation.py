"""Aggregation pipeline for GL report output."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any, cast

import frappe
from erpnext.accounts.utils import get_currency_precision
from frappe import _
from frappe.utils import cstr, flt, getdate

from .gl_currency import (
    _net_currency_layers,
    _track_account_currency,
    apply_running_balances,
)
from .gl_money import decimal_amount, prepare_display_amounts

_SEPARATOR_BLANK_FIELDS: tuple[str, ...] = (
    "debit",
    "credit",
    "debit_in_account_currency",
    "credit_in_account_currency",
    "balance_in_account_currency",
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


def _make_account_header_row(account: str | None) -> dict[str, object]:
    """Create an account label row without financial amounts."""
    row: dict[str, object] = {"account": account, "row_type": "account_header"}
    _blank_separator_fields(row)
    return row


def _blank_separator_fields(row: dict[str, Any]) -> None:
    """Ensure separator rows render as complete blanks in report output."""
    for fieldname in _SEPARATOR_BLANK_FIELDS:
        row[fieldname] = ""


def _normalise_key_part[T](value: T) -> T | str | None:
    if isinstance(value, str):
        return value.strip() or None
    return value


def _normalise_group_value(value: str | None) -> str | None:
    return _normalise_key_part(value)


def _has_displayed_opening_balance(
    opening: dict[str, Any], precision: int | None
) -> bool:
    """Return whether any opening amount remains non-zero when displayed."""
    for field in _OPENING_BALANCE_FIELDS:
        value = opening.get(field)
        if value is None:
            continue
        if flt(value, precision) != 0:
            return True
    return False


def _get_totals_dict(
    total_row_type: str = "group_total",
) -> frappe._dict[str, frappe._dict[str, Any]]:
    """Create template dict with Opening/Total/Closing summary rows."""

    def _debit_credit_dict(label: str, row_type: str) -> frappe._dict[str, Any]:
        return frappe._dict(
            account=f"'{label}'",
            debit=decimal_amount(0),
            credit=decimal_amount(0),
            debit_in_account_currency=decimal_amount(0),
            credit_in_account_currency=decimal_amount(0),
            debit_in_company_currency=decimal_amount(0),
            credit_in_company_currency=decimal_amount(0),
            balance_in_company_currency=decimal_amount(0),
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


def _group_identity(gle: Any, field: str) -> Any:
    value = _normalise_group_value(gle.get(field))
    if field == "party":
        return (gle.get("party_type"), value)
    if field == "voucher_no":
        return (gle.get("voucher_type"), value or gle.get("gl_entry"))
    return value


def _init_gle_map(
    gl_entries: list[frappe._dict[str, Any]],
    filters: dict[str, Any],
    totals_dict: frappe._dict[str, frappe._dict[str, Any]],
) -> dict[str | None, frappe._dict[str, Any]]:
    gle_map = {}
    if filters.get("categorize_by") in (
        "Categorise by Voucher (Consolidated)",
        "Flat Chronological",
    ):
        return gle_map

    group_by = _group_by_field(filters.get("categorize_by", ""))
    for gle in gl_entries:
        group_value = _group_identity(gle, group_by)
        if group_value not in gle_map:
            gle_map[group_value] = frappe._dict(
                totals=_clone_totals_dict(totals_dict), entries=[]
            )
    return gle_map


def _clone_totals_dict(
    template: frappe._dict[str, frappe._dict[str, Any]],
) -> frappe._dict[str, frappe._dict[str, Any]]:
    """Clone the scalar summary rows without the cost of recursive deepcopy."""
    return frappe._dict(
        **{key: frappe._dict(row.copy()) for key, row in template.items()}
    )


def _get_account_type_map(
    company: str, account_names: set[str] | None = None
) -> frappe._dict[str, Any]:
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
    gle: frappe._dict[str, Any],
    immutable_ledger: bool | int | None,
    include_dims: bool | int | None,
    *,
    accounting_dimensions: list[str],
    normalise: bool = True,
) -> tuple[str | date | None, ...]:
    """Build a composite key for voucher-consolidated grouping."""
    if gle.get("manual_entry") or gle.get("reporting_doe"):
        return (gle.get("gl_entry"),)
    fields = [
        "account_currency",
        "transaction_currency",
        "posting_date",
        "voucher_type",
        "voucher_no",
        "account",
        "party_type",
        "party",
    ]
    if immutable_ledger:
        fields.append("creation")
    if include_dims:
        fields.extend([*accounting_dimensions, "cost_center", "project"])
    key_parts = [gle.get(field) for field in fields]
    if normalise:
        return tuple(_normalise_key_part(value) for value in key_parts)
    return tuple(key_parts)


def _translate_value(
    value: str | None, translation_cache: dict[str | None, str | None]
) -> str | None:
    """Translate a repeated report value once per execution."""
    try:
        return translation_cache[value]
    except KeyError:
        translated = _(value) if value is not None else None
        translation_cache[value] = translated
        return translated


def _translate_dimension_values(
    gle: frappe._dict[str, Any],
    accounting_dimensions: list[str],
    translation_cache: dict[str | None, str | None],
) -> None:
    """Translate dimension values for display in the report."""
    for dimension in [*accounting_dimensions, "cost_center", "project"]:
        if val := gle.get(dimension):
            gle[dimension] = _translate_value(val, translation_cache)


def _build_aggregation_state(
    filters: dict[str, Any],
    accounting_dimensions: list[str],
    gl_entries: list[frappe._dict[str, Any]],
    *,
    gle_map: dict[str | None, frappe._dict[str, Any]],
    totals: frappe._dict[str, frappe._dict[str, Any]],
) -> SimpleNamespace:
    """Create the mutable state shared by the aggregation helpers."""
    account_type_map: dict[str, Any] = {}
    if filters.get("show_net_values_in_party_account"):
        account_type_map = _get_account_type_map(
            filters["company"],
            {gle.get("account") for gle in gl_entries if gle.get("account")},
        )

    state = SimpleNamespace(
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
        data: dict[str | tuple[str | date | None, ...], frappe._dict[str, Any]],
        key: str | tuple[str | date | None, ...],
        gle: frappe._dict[str, Any],
        *,
        show_net_values: bool = False,
        collect_against: bool = False,
    ) -> None:
        target = data[key]
        _add_currency_amounts(
            target,
            gle,
            add_transaction_currency=(
                state.add_transaction_currency
                and key not in ("opening", "closing", "total")
            ),
        )

        if _should_net_values(target, state, show_net_values):
            _apply_net_values(target)

        if collect_against and gle.against_voucher:
            state.against_voucher_lists.setdefault(key, []).append(gle.against_voucher)

    state.update_value_in_dict = update_value_in_dict
    return state


def _add_currency_amounts(
    target: frappe._dict[str, Any],
    gle: frappe._dict[str, Any],
    *,
    add_transaction_currency: bool,
) -> None:
    if not gle.get("manual_entry") and not gle.get("reporting_doe"):
        target["_has_source_amounts"] = True
    _track_account_currency(target, gle)
    for field in (
        "debit",
        "credit",
        "debit_in_account_currency",
        "credit_in_account_currency",
        "debit_in_company_currency",
        "credit_in_company_currency",
    ):
        target[field] += gle[field]
    if add_transaction_currency:
        target["debit_in_transaction_currency"] += gle["debit_in_transaction_currency"]
        target["credit_in_transaction_currency"] += gle[
            "credit_in_transaction_currency"
        ]


def _should_net_values(
    target: frappe._dict[str, Any], state: SimpleNamespace, show_net_values: bool
) -> bool:
    return show_net_values or (
        state.show_net_party_values
        and state.account_type_map.get(target.account) in ("Receivable", "Payable")
    )


def _apply_net_values(target: frappe._dict[str, Any]) -> None:
    """Net each currency layer using its own signed balance."""
    _net_currency_layers(target)


def _prepare_gle_for_output(
    gle: frappe._dict[str, Any], translation_cache: dict[str | None, str | None]
) -> None:
    """Apply translations before a row participates in grouping."""
    gle.setdefault("row_type", "entry")
    gle.voucher_subtype = _translate_value(gle.voucher_subtype, translation_cache)
    gle.remarks = _translate_value(gle.remarks, translation_cache)


def _is_opening_entry(
    gle: frappe._dict[str, Any],
    from_date: date,
    show_opening_entries: bool | int | None,
    *,
    disable_opening_balance: bool | int | None,
) -> bool:
    return gle["posting_date"] < from_date or (
        cstr(gle.is_opening) == "Yes"
        and not show_opening_entries
        and not disable_opening_balance
    )


def _is_report_entry(
    gle: frappe._dict[str, Any], to_date: date, show_opening_entries: bool | int | None
) -> bool:
    return gle["posting_date"] <= to_date or (
        cstr(gle.is_opening) == "Yes" and bool(show_opening_entries)
    )


def _process_opening_entry(
    gle: frappe._dict[str, Any], group_by_value: str | None, state: SimpleNamespace
) -> None:
    if not state.group_by_voucher_consolidated and not state.flat_chronological:
        state.update_value_in_dict(
            state.gle_map[group_by_value].totals, "opening", gle, show_net_values=True
        )
        state.update_value_in_dict(
            state.gle_map[group_by_value].totals, "closing", gle, show_net_values=True
        )

    state.update_value_in_dict(state.totals, "opening", gle, show_net_values=True)
    state.update_value_in_dict(state.totals, "closing", gle, show_net_values=True)


def _process_report_entry(
    gle: frappe._dict[str, Any], group_by_value: str | None, state: SimpleNamespace
) -> None:
    if state.group_by_voucher_consolidated:
        _process_consolidated_entry(gle, state)
    elif state.flat_chronological:
        _process_flat_entry(gle, state)
    else:
        _process_grouped_entry(gle, group_by_value, state)


def _process_grouped_entry(
    gle: frappe._dict[str, Any], group_by_value: str | None, state: SimpleNamespace
) -> None:
    group_totals = state.gle_map[group_by_value].totals
    state.update_value_in_dict(group_totals, "total", gle)
    state.update_value_in_dict(group_totals, "closing", gle)
    state.update_value_in_dict(state.totals, "total", gle)
    state.update_value_in_dict(state.totals, "closing", gle)
    state.gle_map[group_by_value].entries.append(gle)


def _process_flat_entry(gle: frappe._dict[str, Any], state: SimpleNamespace) -> None:
    state.update_value_in_dict(state.totals, "total", gle)
    state.update_value_in_dict(state.totals, "closing", gle)
    state.entries.append(gle)


def _process_consolidated_entry(
    gle: frappe._dict[str, Any], state: SimpleNamespace
) -> None:
    key = _consolidated_key(
        gle,
        state.immutable_ledger,
        state.include_dimensions,
        accounting_dimensions=state.accounting_dimensions,
        normalise=False,
    )
    if key not in state.consolidated_gle:
        state.consolidated_gle[key] = gle
        if gle.against_voucher:
            state.against_voucher_lists.setdefault(key, []).append(gle.against_voucher)
    else:
        _merge_exchange_details(state.consolidated_gle[key], gle)
        state.update_value_in_dict(
            state.consolidated_gle, key, gle, collect_against=True
        )


def _merge_exchange_details(target: dict[str, Any], incoming: dict[str, Any]) -> None:
    """A consolidated row may only display exchange details shared by every row."""
    fields = (
        "currency_exchange",
        "exchange_rate_date",
        "source_exchange_rate",
        "exchange_rate_application",
    )
    if any(target.get(field) != incoming.get(field) for field in fields):
        for field in fields:
            target[field] = None


def _append_consolidated_entries(state: SimpleNamespace) -> None:
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
    state: SimpleNamespace, gl_entries: list[frappe._dict[str, Any]]
) -> None:
    """Aggregate the common voucher-consolidated mode without per-row dispatch."""
    from_date, to_date = state.from_date, state.to_date
    show_opening_entries = state.show_opening_entries
    disable_opening_balance = state.disable_opening_balance
    update_value = state.update_value_in_dict
    totals = state.totals
    translation_cache: dict[str | None, str | None] = {}

    for gle in gl_entries:
        if gle["posting_date"] > to_date:
            continue
        _prepare_gle_for_output(gle, translation_cache)
        if _is_opening_entry(
            gle,
            from_date,
            show_opening_entries,
            disable_opening_balance=disable_opening_balance,
        ):
            update_value(totals, "opening", gle, show_net_values=True)
            update_value(totals, "closing", gle, show_net_values=True)
        elif _is_report_entry(gle, to_date, show_opening_entries):
            _process_consolidated_entry(gle, state)

        if state.include_dimensions:
            _translate_dimension_values(
                gle, state.accounting_dimensions, translation_cache
            )


def _aggregate_non_consolidated_rows(
    state: SimpleNamespace, gl_entries: list[frappe._dict[str, Any]]
) -> None:
    """Aggregate flat and grouped modes through their shared row contract."""
    from_date, to_date = state.from_date, state.to_date
    show_opening_entries = state.show_opening_entries
    disable_opening_balance = state.disable_opening_balance
    translation_cache: dict[str | None, str | None] = {}
    for gle in gl_entries:
        if gle["posting_date"] > to_date:
            continue
        _prepare_gle_for_output(gle, translation_cache)
        group_by_value = _group_identity(gle, state.group_by)
        if _is_opening_entry(
            gle,
            from_date,
            show_opening_entries,
            disable_opening_balance=disable_opening_balance,
        ):
            _process_opening_entry(gle, group_by_value, state)
        elif _is_report_entry(gle, to_date, show_opening_entries):
            _process_report_entry(gle, group_by_value, state)

        if state.include_dimensions:
            _translate_dimension_values(
                gle, state.accounting_dimensions, translation_cache
            )


def _get_account_wise_gle(
    filters: dict[str, Any],
    accounting_dimensions: list[str],
    gl_entries: list[frappe._dict[str, Any]],
    *,
    gle_map: dict[str | None, frappe._dict[str, Any]],
    totals: frappe._dict[str, frappe._dict[str, Any]],
) -> tuple[frappe._dict[str, frappe._dict[str, Any]], list[frappe._dict[str, Any]]]:
    state = _build_aggregation_state(
        filters, accounting_dimensions, gl_entries, gle_map=gle_map, totals=totals
    )

    from_date, to_date = (
        cast(date, getdate(filters["from_date"])),
        cast(date, getdate(filters["to_date"])),
    )
    show_opening_entries = filters.get("show_opening_entries") or filters.get(
        "_ignore_is_opening"
    )
    disable_opening_balance = filters.get("disable_opening_balance_calculation")

    aggregate_rows = (
        _aggregate_consolidated_rows
        if state.group_by_voucher_consolidated
        else _aggregate_non_consolidated_rows
    )
    state.from_date, state.to_date = from_date, to_date
    state.show_opening_entries = show_opening_entries
    state.disable_opening_balance = disable_opening_balance
    aggregate_rows(state, gl_entries)

    _append_consolidated_entries(state)
    return totals, state.entries


def _set_bill_no(gl_entries: list[frappe._dict[str, Any]]) -> None:
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


def _prepare_decimal_entries(gl_entries: list[frappe._dict[str, Any]]) -> None:
    for entry in gl_entries:
        for field in (
            "debit",
            "credit",
            "debit_in_account_currency",
            "credit_in_account_currency",
            "debit_in_company_currency",
            "credit_in_company_currency",
            "debit_in_transaction_currency",
            "credit_in_transaction_currency",
        ):
            if field in entry:
                entry[field] = decimal_amount(entry[field])


def get_data_with_opening_closing(
    filters: dict[str, Any],
    accounting_dimensions: list[str],
    gl_entries: list[frappe._dict[str, Any]],
) -> list[dict[str, Any]]:
    """Assemble GL data with opening/closing balances."""
    _prepare_decimal_entries(gl_entries)
    data: list[dict[str, Any]] = []
    group_totals_dict = _get_totals_dict()
    totals_dict = _get_totals_dict(total_row_type="report_total")

    if not filters.get("_bill_no_joined"):
        _set_bill_no(gl_entries)

    gle_map = _init_gle_map(gl_entries, filters, group_totals_dict)

    totals, entries = _get_account_wise_gle(
        filters, accounting_dimensions, gl_entries, gle_map=gle_map, totals=totals_dict
    )

    categorise_by = filters.get("categorize_by", "")
    opening_balance_precision = (
        get_currency_precision()
        if categorise_by == "Group by Account w/ Opening"
        else None
    )

    data.append(totals["opening"])

    if not categorise_by:
        for acc_dict in gle_map.values():
            data += acc_dict["entries"]
    elif categorise_by not in (
        "Categorise by Voucher (Consolidated)",
        "Flat Chronological",
    ):
        _append_group_rows(
            data,
            gle_map,
            categorise_by,
            opening_balance_precision=opening_balance_precision,
        )

        data.append(_make_group_separator_row())
    else:
        data += entries

    data.append(totals["total"])
    _apply_net_values(totals["closing"])
    data.append(totals["closing"])

    return data


def get_result_as_list(
    data: list[dict[str, Any]], filters: dict[str, Any]
) -> list[dict[str, Any]]:
    """Render currency balances and retain the V16 footer separator."""
    rows = apply_running_balances(data, filters)
    for row in rows:
        if not row.get("posting_date") and not row.get("_has_source_amounts"):
            for field in (
                "debit_in_company_currency",
                "credit_in_company_currency",
                "balance_in_company_currency",
            ):
                row[field] = ""
        row.pop("_has_source_amounts", None)
    return prepare_display_amounts(_insert_footer_separator(rows))


def _insert_footer_separator(result: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Insert a blank separator row above trailing footer rows only."""
    if not result:
        return result

    footer_start_idx = None
    for idx in range(len(result) - 1, -1, -1):
        row = result[idx]
        if _is_footer_row(row):
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
    blank_row: dict[str, Any] = dict.fromkeys(result[0], "")
    blank_row["is_separator"] = 1
    blank_row["row_type"] = "separator"

    return [*data_rows, blank_row, *footer_rows]


def _append_group_rows(
    data: list[dict[str, Any]],
    gle_map: dict[str | None, frappe._dict[str, Any]],
    categorise_by: str,
    *,
    opening_balance_precision: int | None,
) -> None:
    """Append visible groups with their opening, movement and closing rows."""
    show_balances = categorise_by != "Categorise by Voucher"
    for account, acc_dict in gle_map.items():
        opening_only = (
            categorise_by == "Group by Account w/ Opening"
            and _has_displayed_opening_balance(
                acc_dict["totals"]["opening"], opening_balance_precision
            )
        )
        if not acc_dict["entries"] and not opening_only:
            continue
        _append_visible_group(data, account, acc_dict, show_balances=show_balances)


def _append_visible_group(
    data: list[dict[str, Any]],
    account: str | None,
    acc_dict: frappe._dict[str, Any],
    *,
    show_balances: bool,
) -> None:
    data.append(_make_group_separator_row())
    if show_balances:
        data.append(acc_dict["totals"]["opening"])
    if not acc_dict["entries"]:
        data.append(_make_account_header_row(account))
    data.extend(acc_dict["entries"])
    if acc_dict["entries"]:
        data.append(acc_dict["totals"]["total"])
    if show_balances:
        _apply_net_values(acc_dict["totals"]["closing"])
        data.append(acc_dict["totals"]["closing"])


def _is_footer_row(row: dict[str, Any]) -> bool:
    """Recognise typed footer rows or their legacy account labels."""
    if row_type := row.get("row_type"):
        return row_type in _FOOTER_ROW_TYPES
    account = row.get("account", "")
    return isinstance(account, str) and account.strip().strip("'").startswith(
        ("Total", "Closing")
    )
