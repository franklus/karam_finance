"""Currency provenance and running balances for the Karam GL report."""

from typing import Any

from frappe.utils import flt

type ReportRow = dict[str, Any]
type AccountCurrencyKey = tuple[str | None, str | None]

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


_ACCOUNT_GROUP_MODES = frozenset(
    {"Categorise by Account", "Group by Account w/ Opening"}
)


_ACCOUNT_CURRENCY_FIELDS: tuple[str, ...] = (
    "debit_in_account_currency",
    "credit_in_account_currency",
    "balance_in_account_currency",
)


_FLAT_OPENING_ACCOUNT_CURRENCY_FIELD = "_opening_balance_in_account_currency"


_CURRENCY_DEBIT_CREDIT_PAIRS: tuple[tuple[str, str], ...] = (
    ("debit", "credit"),
    ("debit_in_account_currency", "credit_in_account_currency"),
    ("debit_in_company_currency", "credit_in_company_currency"),
)


def _blank_separator_fields(row: ReportRow) -> None:
    """Ensure separator rows render as complete blanks in report output."""
    for fieldname in _SEPARATOR_BLANK_FIELDS:
        row[fieldname] = ""


def _normalise_key_part(value: str | None) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _track_account_currency(target: ReportRow, source: ReportRow) -> None:
    """Attach one currency to an aggregate, or mark it as mixed."""
    # Mixed provenance cannot become single-currency again as rows accumulate.
    if target.get("_mixed_account_currency"):
        return
    if not (
        flt(source.get("debit_in_account_currency"))
        or flt(source.get("credit_in_account_currency"))
        or source.get("_account_currency_contribution")
    ):
        return

    if source.get("_mixed_account_currency"):
        target["account_currency"] = ""
        target["_mixed_account_currency"] = 1
        return

    incoming_currency = _normalise_key_part(source.get("account_currency"))
    if not incoming_currency:
        target["account_currency"] = ""
        target["_mixed_account_currency"] = 1
        return

    current_currency = _normalise_key_part(target.get("account_currency"))
    if current_currency and current_currency != incoming_currency:
        target["account_currency"] = ""
        target["_mixed_account_currency"] = 1
        return

    target["account_currency"] = incoming_currency


def _net_debit_credit_pair(row: ReportRow, debit_field: str, credit_field: str) -> None:
    """Net one debit/credit pair without borrowing another currency's sign."""
    net_value = flt(row.get(debit_field)) - flt(row.get(credit_field))
    if net_value < 0:
        row[debit_field] = 0
        row[credit_field] = abs(net_value)
    else:
        row[debit_field] = abs(net_value)
        row[credit_field] = 0


def _net_currency_layers(row: ReportRow) -> None:
    """Net presentation, account and company currency independently."""
    for debit_field, credit_field in _CURRENCY_DEBIT_CREDIT_PAIRS:
        _net_debit_credit_pair(row, debit_field, credit_field)


def _blank_account_currency_fields(row: ReportRow) -> None:
    """Blank account-currency amounts that combine different currencies."""
    for fieldname in _ACCOUNT_CURRENCY_FIELDS:
        row[fieldname] = ""
    row["account_currency"] = ""


def _account_currency_key(row: ReportRow) -> AccountCurrencyKey:
    """Return the stable account-currency key for a ledger row."""
    return (
        _normalise_key_part(row.get("account")),
        _normalise_key_part(row.get("account_currency")),
    )


def _attach_flat_account_currency_openings(
    entries: list[ReportRow], opening_balances: dict[AccountCurrencyKey, float]
) -> None:
    """Seed flat rows with their account's opening balance in account currency."""
    for entry in entries:
        entry[_FLAT_OPENING_ACCOUNT_CURRENCY_FIELD] = opening_balances.get(
            _account_currency_key(entry),
            0,
        )


def _set_account_currency_summary_values(
    row: ReportRow,
    *,
    currency: str,
    debit: float,
    credit: float,
) -> None:
    """Set coherent account-currency values on one flat summary row."""
    row["account_currency"] = currency
    row["debit_in_account_currency"] = debit
    row["credit_in_account_currency"] = credit
    row["balance_in_account_currency"] = debit - credit
    row.pop("_mixed_account_currency", None)


def _flat_contributing_currencies(
    detail_rows: list[ReportRow], opening_balances: dict[AccountCurrencyKey, float]
) -> set[str]:
    currencies = {
        currency
        for (_account, currency), balance in opening_balances.items()
        if currency and flt(balance) != 0
    }
    currencies.update(
        currency
        for row in detail_rows
        if (currency := _normalise_key_part(row.get("account_currency")))
        if _has_account_movement(row)
    )

    return currencies


def _has_account_movement(row: ReportRow) -> bool:
    return (
        flt(row.get("debit_in_account_currency")) != 0
        or flt(row.get("credit_in_account_currency")) != 0
    )


def _has_unresolved_flat_currency(
    detail_rows: list[ReportRow], opening_balances: dict[AccountCurrencyKey, float]
) -> bool:
    return any(
        not _normalise_key_part(currency) and flt(balance) != 0
        for (_account, currency), balance in opening_balances.items()
    ) or any(
        not _normalise_key_part(row.get("account_currency"))
        and _has_account_movement(row)
        for row in detail_rows
    )


def _apply_flat_account_currency_summaries(
    data: list[ReportRow], opening_balances: dict[AccountCurrencyKey, float]
) -> None:
    """Rebuild flat summary values from opening history and visible movements."""
    detail_rows = [row for row in data if row.get("posting_date")]
    summary_rows = {
        row.get("row_type"): row
        for row in data
        if row.get("row_type") in {"opening", "report_total", "closing"}
    }

    currencies = _flat_contributing_currencies(detail_rows, opening_balances)
    has_unresolved_currency = _has_unresolved_flat_currency(
        detail_rows, opening_balances
    )

    if has_unresolved_currency or len(currencies) != 1:
        for row in summary_rows.values():
            row["_mixed_account_currency"] = 1
        return

    _set_flat_summaries(
        detail_rows, opening_balances, summary_rows, currency=next(iter(currencies))
    )


def _set_flat_summaries(
    detail_rows: list[ReportRow],
    opening_balances: dict[AccountCurrencyKey, float],
    summary_rows: dict[str, ReportRow],
    *,
    currency: str,
) -> None:
    currency_openings = [
        balance
        for (_account, opening_currency), balance in opening_balances.items()
        if opening_currency == currency
    ]
    opening_debit = sum(
        max(opening_balance, 0) for opening_balance in currency_openings
    )
    opening_credit = sum(
        max(-opening_balance, 0) for opening_balance in currency_openings
    )
    movement_debit = sum(row.get("debit_in_account_currency", 0) for row in detail_rows)
    movement_credit = sum(
        row.get("credit_in_account_currency", 0) for row in detail_rows
    )

    if opening := summary_rows.get("opening"):
        _set_account_currency_summary_values(
            opening,
            currency=currency,
            debit=opening_debit,
            credit=opening_credit,
        )
    if report_total := summary_rows.get("report_total"):
        _set_account_currency_summary_values(
            report_total,
            currency=currency,
            debit=movement_debit,
            credit=movement_credit,
        )
    if closing := summary_rows.get("closing"):
        _set_account_currency_summary_values(
            closing,
            currency=currency,
            debit=opening_debit + movement_debit,
            credit=opening_credit + movement_credit,
        )


def _contributing_currencies(data: list[ReportRow]) -> set[str]:
    return {
        currency
        for row in data
        if row.get("posting_date")
        if (currency := _normalise_key_part(row.get("account_currency")))
        if flt(row.get("debit_in_account_currency"))
        or flt(row.get("credit_in_account_currency"))
    }


def _prepare_currency_row(row: ReportRow, currencies: set[str]) -> bool:
    """Resolve a summary's currency and blank amounts with mixed provenance."""
    if (
        not row.get("posting_date")
        and not row.get("account_currency")
        and not row.get("_mixed_account_currency")
    ) and len(currencies) == 1:
        row["account_currency"] = next(iter(currencies))
    mixed = bool(row.pop("_mixed_account_currency", 0)) or (
        not row.get("posting_date")
        and len(currencies) > 1
        and not row.get("account_currency")
    )
    if mixed:
        _blank_account_currency_fields(row)
    return mixed


def _flat_account_balance(
    row: ReportRow, balances: dict[AccountCurrencyKey, float]
) -> float | str:
    opening = row.pop(_FLAT_OPENING_ACCOUNT_CURRENCY_FIELD, 0) or 0
    key = _account_currency_key(row)
    if not all(key):
        return ""
    balances.setdefault(key, opening)
    balances[key] += flt(row.get("debit_in_account_currency")) - flt(
        row.get("credit_in_account_currency")
    )
    return balances[key]


def _is_visual_separator(row: ReportRow) -> bool:
    if row.get("is_separator") or row.get("row_type") == "separator":
        row["is_separator"] = 1
        row["row_type"] = "separator"
        return True
    return row.get("row_type") == "account_header"


def apply_running_balances(
    data: list[ReportRow], filters: ReportRow
) -> list[ReportRow]:
    """Render independent currency balances, resetting at group boundaries."""
    balances = dict.fromkeys(
        ("balance", "balance_in_company_currency", "balance_in_account_currency"), 0.0
    )
    flat_balances: dict[AccountCurrencyKey, float] = {}
    currencies = _report_currencies(data, filters)
    can_accumulate = (
        len(currencies) <= 1 or filters.get("categorize_by") in _ACCOUNT_GROUP_MODES
    )
    for row in data:
        row["company"] = filters.get("company")
        if _resets_running_balance(row):
            balances = dict.fromkeys(balances, 0.0)
        if _is_visual_separator(row):
            _blank_separator_fields(row)
            continue
        mixed = _prepare_currency_row(row, currencies)
        _update_company_balances(row, balances)
        row["presentation_currency"] = filters.get("presentation_currency")
        if _is_flat_detail(row, filters):
            row["balance_in_account_currency"] = _flat_account_balance(
                row, flat_balances
            )
        else:
            _update_account_balance(row, balances, can_accumulate and not mixed)
        row.pop(_FLAT_OPENING_ACCOUNT_CURRENCY_FIELD, None)
    return data


def _update_company_balances(row: ReportRow, balances: dict[str, float]) -> None:
    for field, debit, credit in (
        ("balance", "debit", "credit"),
        (
            "balance_in_company_currency",
            "debit_in_company_currency",
            "credit_in_company_currency",
        ),
    ):
        balances[field] += flt(row.get(debit)) - flt(row.get(credit))
        row[field] = balances[field]


def _update_account_balance(
    row: ReportRow, balances: dict[str, float], can_accumulate: bool
) -> None:
    if not can_accumulate:
        row["balance_in_account_currency"] = ""
        return
    balances["balance_in_account_currency"] += flt(
        row.get("debit_in_account_currency")
    ) - flt(row.get("credit_in_account_currency"))
    row["balance_in_account_currency"] = balances["balance_in_account_currency"]


def _report_currencies(data: list[ReportRow], filters: ReportRow) -> set[str]:
    currencies = _contributing_currencies(data)
    if not currencies and filters.get("account_currency"):
        currencies.add(filters["account_currency"])
    return currencies


def _is_flat_detail(row: ReportRow, filters: ReportRow) -> bool:
    return filters.get("categorize_by") == "Flat Chronological" and bool(
        row.get("posting_date")
    )


def _resets_running_balance(row: ReportRow) -> bool:
    return row.get("row_type") != "account_header" and not row.get("posting_date")
