from typing import Any

import frappe
from frappe import _


def finance_book_clause(
    gl: Any,
    *,
    company: str,
    finance_book: object,
    include_default_fb: bool,
) -> object:
    def norm(val: object) -> str:
        return str(val or "")

    allowed = [norm(finance_book), ""]

    if include_default_fb:
        company_fb = frappe.get_cached_value("Company", company, "default_finance_book")
        if finance_book and company_fb and norm(finance_book) != norm(company_fb):
            frappe.throw(
                _(
                    "To use a different finance book, please uncheck "
                    "'Include Default FB Entries'"
                )
            )
        allowed.insert(1, norm(company_fb))

    return (gl.finance_book.isin(allowed)) | (gl.finance_book.isnull())
