"""Stored company-currency balances alongside the reporting ledger."""

from typing import Any

from frappe import _

from .tbk_constants import VALUE_FIELDS
from .tbk_money import decimal_amount, sum_amount

COMPANY_CCY_VALUE_FIELDS = tuple(
    f"{field}_in_company_currency" for field in VALUE_FIELDS
)


def add_company_sums(query: Any, table: Any) -> Any:
    return query.select(
        sum_amount(table, "debit_in_company_currency").as_("debit_in_company_currency"),
        sum_amount(table, "credit_in_company_currency").as_(
            "credit_in_company_currency"
        ),
    )


def apply_company_balances(account: Any, opening: Any, period: Any) -> None:
    for side in ("debit", "credit"):
        field = f"{side}_in_company_currency"
        account[f"opening_{field}"] = decimal_amount(opening.get(f"opening_{field}"))
        account[field] = decimal_amount(period.get(field))
        account[f"closing_{field}"] = account[f"opening_{field}"] + account[field]


def net_company_balances(account: Any) -> None:
    for prefix in ("opening", "closing"):
        debit = f"{prefix}_debit_in_company_currency"
        credit = f"{prefix}_credit_in_company_currency"
        net = decimal_amount(account.get(debit)) - decimal_amount(account.get(credit))
        account[debit] = max(net, 0)
        account[credit] = max(-net, 0)


def company_columns() -> list[dict[str, Any]]:
    labels = (
        _("Company Opening (Dr)"),
        _("Company Opening (Cr)"),
        _("Company Debit"),
        _("Company Credit"),
        _("Company Closing (Dr)"),
        _("Company Closing (Cr)"),
    )
    return [
        {
            "fieldname": "company_currency",
            "label": _("Company Currency"),
            "fieldtype": "Link",
            "options": "Currency",
            "hidden": 1,
        },
        *[
            {
                "fieldname": f"{field}_in_company_currency",
                "label": label,
                "fieldtype": "Currency",
                "options": "company_currency",
                "precision": 2,
                "width": 140,
            }
            for field, label in zip(VALUE_FIELDS, labels, strict=True)
        ],
    ]
