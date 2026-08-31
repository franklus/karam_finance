from frappe import _
from frappe.utils import flt

from .tbfp_constants import ACCOUNT_CCY_VALUE_FIELDS, VALUE_FIELDS


def get_blank_row():
    blank_row: dict[str, object] = {"party": "", "bold": 1}
    for field in VALUE_FIELDS + ACCOUNT_CCY_VALUE_FIELDS:
        blank_row[field] = None
    return blank_row


def build_party_row(
    party, party_name, show_party_name, account_currency, company_currency, values
):
    row = {
        "party": party,
        "account_currency": account_currency,
        "currency": company_currency,
    }

    if show_party_name:
        row["party_name"] = party_name

    opening_debit = flt(values.get("opening_debit", 0))
    opening_credit = flt(values.get("opening_credit", 0))
    opening_debit, opening_credit = toggle_debit_credit(opening_debit, opening_credit)
    row["opening_debit"] = opening_debit
    row["opening_credit"] = opening_credit

    row["debit"] = flt(values.get("debit", 0))
    row["credit"] = flt(values.get("credit", 0))

    closing_debit = opening_debit + row["debit"]
    closing_credit = opening_credit + row["credit"]
    closing_debit, closing_credit = toggle_debit_credit(closing_debit, closing_credit)
    row["closing_debit"] = closing_debit
    row["closing_credit"] = closing_credit

    opening_debit_acc = flt(values.get("opening_debit_in_account_currency", 0))
    opening_credit_acc = flt(values.get("opening_credit_in_account_currency", 0))
    opening_debit_acc, opening_credit_acc = toggle_debit_credit(
        opening_debit_acc, opening_credit_acc
    )
    row["opening_debit_in_account_currency"] = opening_debit_acc
    row["opening_credit_in_account_currency"] = opening_credit_acc

    row["debit_in_account_currency"] = flt(values.get("debit_in_account_currency", 0))
    row["credit_in_account_currency"] = flt(values.get("credit_in_account_currency", 0))

    closing_debit_acc = opening_debit_acc + row["debit_in_account_currency"]
    closing_credit_acc = opening_credit_acc + row["credit_in_account_currency"]
    closing_debit_acc, closing_credit_acc = toggle_debit_credit(
        closing_debit_acc, closing_credit_acc
    )
    row["closing_debit_in_account_currency"] = closing_debit_acc
    row["closing_credit_in_account_currency"] = closing_credit_acc

    return row


def build_party_row_from_sources(
    party_meta,
    account_currency,
    company_currency,
    company_values,
    account_currency_values,
    *,
    show_party_label=True,
):
    row = {
        "party": party_meta["party"] if show_party_label else "",
        "account_currency": account_currency,
        "currency": company_currency,
    }

    if party_meta["show_party_name"]:
        row["party_name"] = party_meta["party_name"] if show_party_label else ""

    for field in VALUE_FIELDS:
        row[field] = flt(company_values.get(field, 0))

    opening_debit_acc = flt(
        account_currency_values.get("opening_debit_in_account_currency", 0)
    )
    opening_credit_acc = flt(
        account_currency_values.get("opening_credit_in_account_currency", 0)
    )
    opening_debit_acc, opening_credit_acc = toggle_debit_credit(
        opening_debit_acc, opening_credit_acc
    )
    row["opening_debit_in_account_currency"] = opening_debit_acc
    row["opening_credit_in_account_currency"] = opening_credit_acc

    row["debit_in_account_currency"] = flt(
        account_currency_values.get("debit_in_account_currency", 0)
    )
    row["credit_in_account_currency"] = flt(
        account_currency_values.get("credit_in_account_currency", 0)
    )

    closing_debit_acc = opening_debit_acc + row["debit_in_account_currency"]
    closing_credit_acc = opening_credit_acc + row["credit_in_account_currency"]
    closing_debit_acc, closing_credit_acc = toggle_debit_credit(
        closing_debit_acc, closing_credit_acc
    )
    row["closing_debit_in_account_currency"] = closing_debit_acc
    row["closing_credit_in_account_currency"] = closing_credit_acc

    return row


def build_total_row(
    company_currency, company_totals, account_currency_totals, account_currency
):
    row = {
        "party": "'" + _("Totals") + "'",
        "currency": company_currency,
        "account_currency": account_currency,
        "bold": 1,
    }

    for field in VALUE_FIELDS:
        row[field] = flt(company_totals.get(field, 0))

    for field in ACCOUNT_CCY_VALUE_FIELDS:
        value = account_currency_totals.get(field)
        row[field] = flt(value) if value is not None else None

    return row


def toggle_debit_credit(debit, credit):
    if flt(debit) > flt(credit):
        debit = flt(debit) - flt(credit)
        credit = 0.0
    else:
        credit = flt(credit) - flt(debit)
        debit = 0.0
    return debit, credit
