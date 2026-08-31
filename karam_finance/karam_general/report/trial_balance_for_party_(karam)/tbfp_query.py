"""Query helpers for Trial Balance for Party (Karam).

The upstream report runs one query for opening balances and another for period
movement. Karam needs both company-currency and account-currency values, so the
report uses one grouped, conditional aggregate instead. The grouping is
performed by the database and the small result set is hydrated into the
party/currency index consumed by the report.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import frappe
from frappe.query_builder import Case
from frappe.query_builder.functions import Max, Min, Sum
from frappe.utils import flt
from pypika.terms import Field

if TYPE_CHECKING:
    from collections.abc import Mapping


def get_party_currency_balances(filters, account_filter=None):
    """Return conditional GL aggregates keyed by party and account currency.

    The predicates intentionally mirror ERPNext v16's Party Trial Balance
    helpers. Keeping opening and movement expressions in one query avoids
    scanning the same GL population twice while preserving the upstream
    treatment of opening entries. Opening sides are aggregated as one net
    value; ERPNext's GL validation keeps debit and credit non-negative, so
    this is algebraically equivalent to summing both sides and netting them in
    Python while reducing the aggregate work.
    """
    rows = _run_party_currency_query_with_currency_fallback(filters, account_filter)
    return _index_rows(rows)


def get_party_currency_balances_with_names(
    filters, account_filter=None, party_name_field="name"
):
    """Return balances and master names for parties with qualifying GL rows.

    The normal v16 report always performs a separate party-master query. For
    the common non-zero report path, the GL result already identifies the
    parties that can be emitted, so joining the relevant party table keeps the
    report to one data query without changing its row contract. The caller
    uses the separate path when zero-value master rows must be shown.
    """
    rows = _run_party_currency_query_with_currency_fallback(
        filters, account_filter, party_name_field
    )
    balances = _index_rows(rows)
    return balances, _party_names(rows)


def get_party_currency_balances_with_all_names(
    filters, account_filter=None, party_name_field="name"
):
    """Return balances and every party name, including parties with no GL rows."""
    rows = _run_party_currency_query(
        filters,
        account_filter,
        party_name_field,
        include_all_parties=True,
    )
    return _index_rows(rows), _party_names(rows)


def _run_party_currency_query_with_currency_fallback(
    filters, account_filter=None, party_name_field=None
):
    """Use party grouping when each party has one account currency.

    Most party ledgers have one account currency per party. Grouping by party
    avoids a database temporary table and filesort. The first query carries
    both currency extrema; a second, fully grouped query is used only when a
    party spans more than one currency, preserving the multi-currency row
    contract without paying that cost for every report run.
    """
    rows = _run_party_currency_query(
        filters,
        account_filter,
        party_name_field,
        group_by_account_currency=False,
    )
    if not _has_mixed_account_currencies(rows):
        return rows

    return _run_party_currency_query(
        filters,
        account_filter,
        party_name_field,
    )


def _run_party_currency_query(
    filters,
    account_filter=None,
    party_name_field=None,
    *,
    include_all_parties=False,
    group_by_account_currency=True,
):
    gl_entry = frappe.qb.DocType("GL Entry")
    party_entry = (
        frappe.qb.DocType(filters.party_type)
        if party_name_field or include_all_parties
        else None
    )
    opening_condition = (gl_entry.posting_date < filters.from_date) | (
        gl_entry.is_opening == "Yes"
    )
    movement_condition = (
        (gl_entry.posting_date >= filters.from_date)
        & (gl_entry.posting_date <= filters.to_date)
        & (gl_entry.is_opening == "No")
    )

    # ``GL Entry.is_opening`` is the standard ``No``/``Yes`` Select. With
    # either value, every valid row through ``to_date`` belongs to exactly
    # one of the opening or movement CASE expressions above. Keeping only the
    # date upper bound in the SQL scope removes the OR from the residual
    # predicate; the CASE expressions still retain the upstream classification
    # rules.
    gl_scope = (
        (gl_entry.company == filters.company)
        & (gl_entry.is_cancelled == 0)
        & (gl_entry.party_type == filters.party_type)
        & (gl_entry.party != "")
        & (gl_entry.posting_date <= filters.to_date)
    )

    select_fields, group_fields = _party_fields(
        gl_entry, party_entry, party_name_field, include_all_parties
    )
    select_fields.extend(
        _balance_fields(
            gl_entry,
            opening_condition,
            movement_condition,
            group_by_account_currency=group_by_account_currency,
        )
    )
    query = _party_query(
        gl_entry,
        party_entry,
        gl_scope,
        filters,
        account_filter,
        include_all_parties,
    )
    query = query.select(*select_fields)
    if group_by_account_currency:
        query = query.groupby(*group_fields, gl_entry.account_currency)
    else:
        query = query.groupby(*group_fields)
    return query.run(as_dict=True)


def _party_fields(gl_entry, party_entry, party_name_field, include_all_parties):
    if include_all_parties:
        if party_entry is None:
            raise RuntimeError("Party master is required for all-party queries")
        select_fields = [party_entry.name.as_("party")]
        group_fields = [party_entry.name]
    else:
        select_fields = [gl_entry.party]
        group_fields = [gl_entry.party]

    if party_entry:
        party_name = party_entry[party_name_field or "name"]
        select_fields.append(party_name.as_("party_name"))
        group_fields.append(party_name)

    return select_fields, group_fields


def _balance_fields(
    gl_entry,
    opening_condition,
    movement_condition,
    *,
    group_by_account_currency,
):
    account_currency_fields = (
        (gl_entry.account_currency,)
        if group_by_account_currency
        else (
            Min(gl_entry.account_currency).as_("account_currency"),
            Max(gl_entry.account_currency).as_("account_currency_max"),
        )
    )
    return [
        *account_currency_fields,
        _conditional_sum(
            opening_condition,
            gl_entry.debit - gl_entry.credit,
            "opening_net",
        ),
        _conditional_sum(movement_condition, gl_entry.debit, "debit"),
        _conditional_sum(movement_condition, gl_entry.credit, "credit"),
        _conditional_sum(
            opening_condition,
            gl_entry.debit_in_account_currency - gl_entry.credit_in_account_currency,
            "opening_net_in_account_currency",
        ),
        _conditional_sum(
            movement_condition,
            gl_entry.debit_in_account_currency,
            "debit_in_account_currency",
        ),
        _conditional_sum(
            movement_condition,
            gl_entry.credit_in_account_currency,
            "credit_in_account_currency",
        ),
    ]


def _party_query(
    gl_entry,
    party_entry,
    gl_scope,
    filters,
    account_filter,
    include_all_parties,
):
    if include_all_parties:
        if party_entry is None:
            raise RuntimeError("Party master is required for all-party queries")
        query = frappe.qb.from_(party_entry).left_join(gl_entry)
        join_condition = (party_entry.name == gl_entry.party) & gl_scope
        if account_filter:
            join_condition &= gl_entry.account.isin(account_filter)
        query = query.on(join_condition)
        if filters.get("party"):
            query = query.where(party_entry.name == filters.party)
        return query

    # ERPNext creates this composite index for GL Entry. It keeps the
    # historical opening scan on the same selective party-type/party access
    # path used by the upstream opening-balance query, while retaining the
    # bounded date predicate for the current period.
    query = frappe.qb.from_(gl_entry).force_index("party_type_party_index")
    if party_entry:
        query = query.join(party_entry).on(gl_entry.party == party_entry.name)
    where_condition = gl_scope
    if filters.get("party"):
        where_condition &= gl_entry.party == filters.party
    if account_filter:
        where_condition &= gl_entry.account.isin(account_filter)
    return query.where(where_condition)


def _conditional_sum(condition, field, alias):
    conditional_term = Case().when(condition, field).else_(0)
    return Sum(cast("Field", conditional_term)).as_(alias)


def _index_rows(rows: list[Mapping[str, object]]):
    """Hydrate database rows into ``party -> currency -> values``."""
    balances = {}

    for row in rows:
        party = row.get("party")
        account_currency = row.get("account_currency")
        if not party or not account_currency:
            continue

        party_balances = balances.setdefault(party, {})
        opening_net = flt(_numeric(row.get("opening_net")))
        opening_debit, opening_credit = _net_sides(opening_net)
        opening_net_in_account_currency = flt(
            _numeric(row.get("opening_net_in_account_currency"))
        )
        (
            opening_debit_in_account_currency,
            opening_credit_in_account_currency,
        ) = _net_sides(opening_net_in_account_currency)
        party_balances[account_currency] = {
            "opening_debit": opening_debit,
            "opening_credit": opening_credit,
            "debit": flt(_numeric(row.get("debit"))),
            "credit": flt(_numeric(row.get("credit"))),
            "opening_debit_in_account_currency": opening_debit_in_account_currency,
            "opening_credit_in_account_currency": opening_credit_in_account_currency,
            "debit_in_account_currency": flt(
                _numeric(row.get("debit_in_account_currency"))
            ),
            "credit_in_account_currency": flt(
                _numeric(row.get("credit_in_account_currency"))
            ),
        }

    return balances


def _has_mixed_account_currencies(rows: list[Mapping[str, object]]) -> bool:
    return any(
        row.get("account_currency_max") is None
        or row.get("account_currency") != row.get("account_currency_max")
        for row in rows
    )


def _party_names(rows):
    return {row.get("party"): row.get("party_name") for row in rows if row.get("party")}


def _numeric(value: object) -> float | int | str | None:
    return cast("float | int | str | None", value)


def _net_sides(value):
    value = flt(value)
    if value > 0:
        return value, 0.0
    if value < 0:
        return 0.0, -value
    return 0.0, 0.0
