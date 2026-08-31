"""Query and condition builders for the Karam General Ledger report.

The report intentionally keeps the public condition helpers used by older
custom callers, but the execution path is built with Frappe Query Builder.
That gives us structured values, allowlisted dynamic identifiers, and native
v16 permission conditions without assembling SQL fragments from filters.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import frappe
from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
    get_accounting_dimensions,
    get_dimension_with_children,
)
from erpnext.accounts.report.financial_statements import get_cost_centers_with_children
from erpnext.accounts.report.utils import convert_to_presentation_currency, get_currency
from frappe import _
from frappe.database.query import RawCriterion
from frappe.query_builder import Criterion, NullValue
from frappe.query_builder.functions import Substring
from frappe.utils import cstr

from .gl_enrichment import (
    _attach_series_translation,
    _get_voucher_data_for_filters,
    get_party_name_map,
)
from .gl_filters import get_accounts_with_children

if TYPE_CHECKING:
    pass

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_MAX_LOOKUP_VALUES = 100_000
_MAX_VOUCHER_FILTER_PAIRS = 10_000


def get_gl_entries(
    filters: dict, accounting_dimensions: list[str]
) -> list[frappe._dict]:
    """Fetch the ledger rows required by the requested report.

    The GL table is the only row source for the report. Karam fields are
    hydrated in one union query after the rows are fetched; when a Series or
    Translation filter is active, the same union first narrows the GL query
    to matching voucher pairs. This avoids a Journal Entry-only join and
    avoids per-row lookups for every supported voucher type.
    """
    currency_map = get_currency(filters)

    if filters.get("include_default_book_entries"):
        company = filters.get("company") or ""
        filters["company_fb"] = frappe.get_cached_value(
            "Company", company, "default_finance_book"
        )

    gl = frappe.qb.DocType("GL Entry")
    voucher_data = None
    fields = _select_fields(
        gl,
        filters,
        accounting_dimensions,
        include_karam_fields=False,
    )
    query = frappe.qb.from_(gl).select(*fields)

    if filters.get("karam_series") or filters.get("translation"):
        voucher_data = _get_voucher_data_for_filters(filters)

    criteria = _build_qb_conditions(filters, gl, voucher_data)

    # Use Query Builder for all report predicates. Frappe's permission helper
    # returns a framework-owned condition; retaining it as a RawCriterion
    # preserves the exact v16 report permission contract without interpolating
    # any report value or identifier.
    from frappe.desk.reportview import build_match_conditions

    if match_conditions := build_match_conditions("GL Entry"):
        criteria.append(RawCriterion(f"({match_conditions})"))
    query = query.where(Criterion.all(criteria))
    query = _apply_order_by(query, gl, filters)
    gl_entries = query.run(as_dict=True)
    filters["_bill_no_joined"] = False

    party_name_map = (
        get_party_name_map(gl_entries) if _should_attach_party_names(filters) else {}
    )
    for gl_entry in gl_entries:
        if party_name_map and gl_entry.party_type and gl_entry.party:
            gl_entry.party_name = party_name_map.get(gl_entry.party_type, {}).get(
                gl_entry.party
            )
        gl_entry.debit_in_company_currency = gl_entry.debit
        gl_entry.credit_in_company_currency = gl_entry.credit

    if filters.get("presentation_currency"):
        gl_entries = convert_to_presentation_currency(gl_entries, currency_map, filters)

    _attach_series_translation(gl_entries, preloaded_voucher_data=voucher_data)
    return gl_entries


def _select_fields(
    gl,
    filters: dict,
    accounting_dimensions: list[str],
    *,
    include_karam_fields: bool = True,
) -> list:
    """Return the static and validated dynamic GL projection."""
    fields = [
        gl.name.as_("gl_entry"),
        gl.posting_date,
        *(
            [NullValue().as_("karam_series"), NullValue().as_("translation")]
            if include_karam_fields
            else []
        ),
        gl.letter,
        gl.account,
        gl.party_type,
        gl.party,
        gl.voucher_type,
        gl.voucher_subtype,
        gl.voucher_no,
        gl.cost_center,
        gl.project,
        gl.against_voucher_type,
        gl.against_voucher,
        gl.account_currency,
        gl.against,
        gl.is_opening,
        gl.creation,
        gl.debit,
        gl.credit,
        gl.debit_in_account_currency,
        gl.credit_in_account_currency,
    ]

    meta = frappe.get_meta("GL Entry")
    fields.extend(
        gl[fieldname]
        for fieldname in accounting_dimensions
        if _valid_identifier(fieldname) and meta.has_field(fieldname)
    )

    if filters.get("add_values_in_transaction_currency"):
        fields.extend(
            [
                gl.debit_in_transaction_currency,
                gl.credit_in_transaction_currency,
                gl.transaction_currency,
            ]
        )

    if filters.get("show_remarks"):
        remarks_length = _safe_positive_int(filters.get("_remarks_length"))
        if remarks_length:
            fields.append(Substring(gl.remarks, 1, remarks_length).as_("remarks"))
        else:
            fields.append(gl.remarks)

    return fields


def _apply_order_by(query, gl, filters: dict):
    """Apply ERPNext v16 ordering plus Karam's chronological mode."""
    categorize_by = _canonical_categorize_by(filters.get("categorize_by"))
    if categorize_by == "Categorise by Voucher":
        order_fields = (gl.posting_date, gl.voucher_type, gl.voucher_no)
    elif categorize_by == "Flat Chronological":
        order_fields = (gl.posting_date, gl.creation)
    elif categorize_by in ("Categorise by Account", "Group by Account w/ Opening"):
        order_fields = (gl.account, gl.posting_date, gl.creation)
    elif filters.get("include_dimensions"):
        order_fields = (gl.posting_date, gl.creation)
    else:
        order_fields = (gl.posting_date, gl.account, gl.creation)

    for field in order_fields:
        query = query.orderby(field)
    return query


def _build_qb_conditions(
    filters: dict,
    gl,
    voucher_data=None,
    joined_voucher_conditions=None,
) -> list:
    """Build all execution criteria as Query Builder expressions."""
    conditions = [gl.company == filters.get("company")]
    conditions.extend(_build_qb_account_conditions(filters, gl))
    conditions.extend(_build_qb_voucher_conditions(filters, gl))
    conditions.extend(_build_qb_party_conditions(filters, gl))
    conditions.extend(_build_qb_date_conditions(filters, gl))
    conditions.append(_build_qb_finance_book_condition(filters, gl))
    if not filters.get("show_cancelled_entries"):
        conditions.append(gl.is_cancelled == 0)
    conditions.extend(
        _build_qb_karam_conditions(filters, gl, voucher_data, joined_voucher_conditions)
    )
    conditions.extend(_build_qb_dimension_conditions(filters, gl))
    return conditions


def _build_qb_account_conditions(filters: dict, gl) -> list:
    conditions = []
    if filters.get("account"):
        filters["account"] = get_accounts_with_children(filters["account"])
        if filters["account"]:
            conditions.append(gl.account.isin(filters["account"]))
    if filters.get("cost_center"):
        filters["cost_center"] = get_cost_centers_with_children(filters["cost_center"])
        conditions.append(gl.cost_center.isin(filters["cost_center"]))
    if filters.get("project"):
        conditions.append(gl.project.isin(filters["project"]))
    return conditions


def _build_qb_voucher_conditions(filters: dict, gl) -> list:
    conditions = []
    if filters.get("voucher_no"):
        conditions.append(gl.voucher_no == filters["voucher_no"])
    if filters.get("against_voucher_no"):
        conditions.append(gl.against_voucher == filters["against_voucher_no"])
    voucher_no_not_in = _get_voucher_no_not_in_query(filters)
    if voucher_no_not_in is not None:
        conditions.append(~gl.voucher_no.isin(voucher_no_not_in))
    elif filters.get("voucher_no_not_in"):
        conditions.append(~gl.voucher_no.isin(filters["voucher_no_not_in"]))
    return conditions


def _build_qb_party_conditions(filters: dict, gl) -> list:
    conditions = []
    if _canonical_categorize_by(
        filters.get("categorize_by")
    ) == "Categorise by Party" and not filters.get("party_type"):
        conditions.append(gl.party_type.isin(["Customer", "Supplier"]))
    if filters.get("party_type"):
        conditions.append(gl.party_type == filters["party_type"])
    if filters.get("party"):
        conditions.append(gl.party.isin(filters["party"]))
    return conditions


def _build_qb_finance_book_condition(filters: dict, gl):
    if filters.get("include_default_book_entries"):
        if filters.get("finance_book"):
            if filters.get("company_fb") and cstr(filters["finance_book"]) != cstr(
                filters["company_fb"]
            ):
                frappe.throw(
                    _(
                        "To use a different finance book, please uncheck "
                        "'Include Default FB Entries'"
                    )
                )
            finance_books = [cstr(filters["finance_book"]), ""]
        else:
            finance_books = [cstr(filters.get("company_fb")), ""]
    elif filters.get("finance_book"):
        finance_books = [cstr(filters["finance_book"]), ""]
    else:
        finance_books = [""]
    return gl.finance_book.isin(finance_books) | gl.finance_book.isnull()


def _build_qb_date_conditions(filters: dict, gl) -> list:
    """Build v16 opening-balance semantics, including its disable switch."""
    ignore_is_opening = bool(filters.get("_ignore_is_opening"))
    from_date = filters.get("from_date")
    to_date = filters.get("to_date")
    conditions = []

    opening_aware = not (
        filters.get("account")
        or filters.get("party")
        or _canonical_categorize_by(filters.get("categorize_by"))
        in ("Categorise by Account", "Categorise by Party")
    )
    if filters.get("disable_opening_balance_calculation") or opening_aware:
        lower_bound = gl.posting_date >= from_date
        if not ignore_is_opening and not filters.get(
            "disable_opening_balance_calculation"
        ):
            lower_bound = lower_bound | (gl.is_opening == "Yes")
        conditions.append(lower_bound)

    upper_bound = gl.posting_date <= to_date
    if not ignore_is_opening:
        upper_bound = upper_bound | (gl.is_opening == "Yes")
    conditions.append(upper_bound)
    return conditions


def _build_qb_karam_conditions(
    filters: dict, gl, voucher_data, joined_voucher_conditions=None
) -> list:
    """Build GL Letter and cross-voucher Karam filters."""
    conditions = []
    if filters.get("letter"):
        conditions.append(gl.letter == filters["letter"])

    show_letter = filters.get("show_letter", "")
    if show_letter == "Only unassigned rows":
        conditions.append(gl.letter.isnull() | (gl.letter == ""))
    elif show_letter == "Only assigned rows":
        conditions.append(gl.letter.notnull() & (gl.letter != ""))

    if joined_voucher_conditions:
        conditions.append(Criterion.any(joined_voucher_conditions))
    elif filters.get("karam_series") or filters.get("translation"):
        pairs = list(voucher_data or {})
        if len(pairs) > _MAX_VOUCHER_FILTER_PAIRS:
            frappe.throw(
                _(
                    "The Series or Translation filter matches too many vouchers. "
                    "Please use a more specific filter."
                )
            )
        if pairs:
            pair_conditions = [
                (gl.voucher_type == doctype) & (gl.voucher_no == name)
                for doctype, name in pairs
            ]
            conditions.append(Criterion.any(pair_conditions))
        else:
            # A requested Series/Translation with no source matches is an
            # empty result, not an unfiltered ledger.
            conditions.append(gl.name == "")

    return conditions


def _build_qb_dimension_conditions(filters: dict, gl) -> list:
    """Apply only active, real GL Accounting Dimension fields."""
    conditions = []
    meta = frappe.get_meta("GL Entry")
    dimensions = filters.get("_dimensions_meta") or get_accounting_dimensions(
        as_list=False
    )
    for dimension in dimensions:
        fieldname = dimension.fieldname
        if (
            getattr(dimension, "disabled", 0)
            or dimension.document_type == "Finance Book"
            or not _valid_identifier(fieldname)
            or not meta.has_field(fieldname)
            or not filters.get(fieldname)
        ):
            continue

        values = filters[fieldname]
        if frappe.get_cached_value("DocType", dimension.document_type, "is_tree"):
            values = get_dimension_with_children(dimension.document_type, values)
            filters[fieldname] = values
        conditions.append(gl[fieldname].isin(values))
    return conditions


def _get_voucher_no_not_in_query(filters: dict):
    """Return a lazy, unioned voucher subquery for ignore filters."""
    queries = []
    je = frappe.qb.DocType("Journal Entry")
    company = filters.get("company")
    if filters.get("ignore_err"):
        queries.append(
            frappe.qb.from_(je)
            .select(je.name)
            .where(
                Criterion.all(
                    [
                        je.company == company,
                        je.docstatus == 1,
                        je.voucher_type.isin(
                            ["Exchange Rate Revaluation", "Exchange Gain Or Loss"]
                        ),
                    ]
                )
            )
        )

    if filters.get("ignore_cr_dr_notes"):
        queries.append(
            frappe.qb.from_(je)
            .select(je.name)
            .where(
                Criterion.all(
                    [
                        je.company == company,
                        je.docstatus == 1,
                        je.voucher_type.isin(["Credit Note", "Debit Note"]),
                        je.is_system_generated == 1,
                    ]
                )
            )
        )

    if not queries:
        return None

    query = queries[0]
    for additional_query in queries[1:]:
        query = query.union(additional_query)
    return query


def _valid_identifier(value: str) -> bool:
    return bool(value and _IDENTIFIER.fullmatch(value))


def _safe_positive_int(value) -> int:
    try:
        value = int(value or 0)
    except TypeError, ValueError:
        return 0
    return value if 0 < value <= _MAX_LOOKUP_VALUES else 0


def _canonical_categorize_by(value):
    if not value:
        return value
    return str(value).replace("Categorize", "Categorise")


# ---------------------------------------------------------------------------
# Compatibility condition helpers
# ---------------------------------------------------------------------------


def _should_attach_party_names(filters: dict) -> bool:
    """Attach party names only when requested columns or grouping need them."""
    if filters.get("_needs_party_name"):
        return True
    return (
        _canonical_categorize_by(filters.get("categorize_by")) == "Categorise by Party"
    )


def _get_order_by_clause(filters: dict) -> str:
    """Return the historical order clause for external callers/tests."""
    categorize_by = _canonical_categorize_by(filters.get("categorize_by", ""))
    if categorize_by == "Categorise by Voucher":
        return "order by gl.posting_date, gl.voucher_type, gl.voucher_no"
    if categorize_by == "Flat Chronological":
        return "order by gl.posting_date, gl.creation"
    if categorize_by in ("Categorise by Account", "Group by Account w/ Opening"):
        return "order by gl.account, gl.posting_date, gl.creation"
    if filters.get("include_dimensions"):
        return "order by gl.posting_date, gl.creation"
    return "order by gl.posting_date, gl.account, gl.creation"


def get_conditions(filters: dict) -> str:
    """Build the legacy condition string retained for compatibility callers."""
    conditions = []
    ignore_is_opening = filters.get("_ignore_is_opening", False)
    conditions.extend(_build_account_conditions(filters))
    conditions.extend(_build_voucher_conditions(filters))
    conditions.extend(_build_karam_conditions(filters))
    conditions.extend(_build_party_conditions(filters))
    conditions.extend(_build_date_conditions(filters, ignore_is_opening))
    conditions.extend(_build_finance_book_conditions(filters))
    conditions.extend(_build_dimension_conditions(filters))
    conditions.extend(_build_system_conditions(filters))
    return "and {}".format(" and ".join(conditions)) if conditions else ""


def _build_account_conditions(filters):
    conditions = []
    if filters.get("account"):
        filters["account"] = get_accounts_with_children(filters["account"])
        if filters["account"]:
            conditions.append("gl.account in %(account)s")
    if filters.get("cost_center"):
        filters["cost_center"] = get_cost_centers_with_children(filters["cost_center"])
        conditions.append("gl.cost_center in %(cost_center)s")
    if filters.get("project"):
        conditions.append("gl.project in %(project)s")
    return conditions


def _build_voucher_conditions(filters):
    conditions = []
    if filters.get("voucher_no"):
        conditions.append("gl.voucher_no=%(voucher_no)s")
    if filters.get("against_voucher_no"):
        conditions.append("gl.against_voucher=%(against_voucher_no)s")
    if filters.get("ignore_err") or filters.get("ignore_cr_dr_notes"):
        excluded = list(filters.get("voucher_no_not_in") or [])
        if filters.get("ignore_err"):
            excluded.extend(
                row[0]
                for row in frappe.get_all(
                    "Journal Entry",
                    filters={
                        "company": filters.get("company"),
                        "docstatus": 1,
                        "voucher_type": (
                            "in",
                            ["Exchange Rate Revaluation", "Exchange Gain Or Loss"],
                        ),
                    },
                    fields=["name"],
                    as_list=True,
                    limit_page_length=_MAX_LOOKUP_VALUES,
                )
            )
        if filters.get("ignore_cr_dr_notes"):
            excluded.extend(
                row[0]
                for row in frappe.get_all(
                    "Journal Entry",
                    filters={
                        "company": filters.get("company"),
                        "docstatus": 1,
                        "voucher_type": ("in", ["Credit Note", "Debit Note"]),
                        "is_system_generated": 1,
                    },
                    fields=["name"],
                    as_list=True,
                    limit_page_length=_MAX_LOOKUP_VALUES,
                )
            )
        filters["voucher_no_not_in"] = list(dict.fromkeys(excluded))
    if filters.get("voucher_no_not_in"):
        conditions.append("gl.voucher_no not in %(voucher_no_not_in)s")
    return conditions


def _build_karam_conditions(filters):
    conditions = []
    if filters.get("letter"):
        conditions.append("gl.letter=%(letter)s")
    show_letter = filters.get("show_letter", "")
    if show_letter == "Only unassigned rows":
        conditions.append("(gl.letter is null or gl.letter = '')")
    elif show_letter == "Only assigned rows":
        conditions.append("(gl.letter is not null and gl.letter != '')")
    return conditions


def _build_party_conditions(filters):
    conditions = []
    if _canonical_categorize_by(
        filters.get("categorize_by")
    ) == "Categorise by Party" and not filters.get("party_type"):
        conditions.append("gl.party_type in ('Customer', 'Supplier')")
    if filters.get("party_type"):
        conditions.append("gl.party_type=%(party_type)s")
    if filters.get("party"):
        conditions.append("gl.party in %(party)s")
    return conditions


def _build_date_conditions(filters, ignore_is_opening):
    conditions = []
    opening_aware = not (
        filters.get("account")
        or filters.get("party")
        or _canonical_categorize_by(filters.get("categorize_by"))
        in ["Categorise by Account", "Categorise by Party"]
    )
    if filters.get("disable_opening_balance_calculation") or opening_aware:
        if not ignore_is_opening and not filters.get(
            "disable_opening_balance_calculation"
        ):
            conditions.append(
                "(gl.posting_date >=%(from_date)s or gl.is_opening = 'Yes')"
            )
        else:
            conditions.append("gl.posting_date >=%(from_date)s")
    if not ignore_is_opening:
        conditions.append("(gl.posting_date <=%(to_date)s or gl.is_opening = 'Yes')")
    else:
        conditions.append("gl.posting_date <=%(to_date)s")
    return conditions


def _build_finance_book_conditions(filters):
    conditions = []
    if filters.get("include_default_book_entries"):
        if filters.get("finance_book"):
            if filters.get("company_fb") and cstr(filters.get("finance_book")) != cstr(
                filters.get("company_fb")
            ):
                frappe.throw(
                    _(
                        "To use a different finance book, please uncheck "
                        "'Include Default FB Entries'"
                    )
                )
            conditions.append(
                "(gl.finance_book in (%(finance_book)s, '') OR gl.finance_book IS NULL)"
            )
        else:
            conditions.append(
                "(gl.finance_book in (%(company_fb)s, '') OR gl.finance_book IS NULL)"
            )
    elif filters.get("finance_book"):
        conditions.append(
            "(gl.finance_book in (%(finance_book)s, '') OR gl.finance_book IS NULL)"
        )
    else:
        conditions.append("(gl.finance_book in ('') OR gl.finance_book IS NULL)")
    return conditions


def _build_dimension_conditions(filters):
    conditions = []
    accounting_dimensions = filters.get(
        "_dimensions_meta"
    ) or get_accounting_dimensions(as_list=False)
    for dimension in accounting_dimensions:
        if (
            not dimension.disabled
            and dimension.document_type != "Finance Book"
            and _valid_identifier(dimension.fieldname)
            and filters.get(dimension.fieldname)
        ):
            if frappe.get_cached_value("DocType", dimension.document_type, "is_tree"):
                filters[dimension.fieldname] = get_dimension_with_children(
                    dimension.document_type, filters[dimension.fieldname]
                )
            conditions.append(f"gl.{dimension.fieldname} in %({dimension.fieldname})s")
    return conditions


def _build_system_conditions(filters):
    conditions = []
    if not filters.get("show_cancelled_entries"):
        conditions.append("gl.is_cancelled = 0")
    return conditions
