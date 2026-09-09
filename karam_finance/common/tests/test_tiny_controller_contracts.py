"""Import contracts for intentionally minimal DocType controllers."""

from __future__ import annotations

import importlib

import pytest
from frappe.model.document import Document


@pytest.mark.parametrize(
    ("module_name", "controller_name"),
    [
        (
            "karam_finance.karam_general.doctype.item_price_mismatch_doctype.item_price_mismatch_doctype",
            "ItemPriceMismatchDoctype",
        ),
        (
            "karam_finance.reporting_currency.doctype.account_exclusions.account_exclusions",
            "AccountExclusions",
        ),
        (
            "karam_finance.reporting_currency.doctype.reporting_currency_parameters.reporting_currency_parameters",
            "ReportingCurrencyParameters",
        ),
        (
            "karam_finance.letter_reconciliation.doctype.jv_letter_credit.jv_letter_credit",
            "JVLetterCredit",
        ),
        (
            "karam_finance.letter_reconciliation.doctype.jv_letter_debit.jv_letter_debit",
            "JVLetterDebit",
        ),
        (
            "karam_finance.letter_reconciliation.doctype.letter_settings.letter_settings",
            "LetterSettings",
        ),
    ],
)
def test_tiny_doctype_controller_inherits_document(
    module_name: str,
    controller_name: str,
) -> None:
    """Each discovered controller remains loadable by Frappe's DocType loader."""
    module = importlib.import_module(module_name)

    assert issubclass(getattr(module, controller_name), Document)
