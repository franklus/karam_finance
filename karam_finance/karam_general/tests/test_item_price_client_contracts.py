"""Client script contracts for the Item Price mismatch workflow."""

from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase


def _client_rate_mismatch_script() -> str:
    """Return the client rate-mismatch script source for static assertions."""
    script_directory = Path(
        frappe.get_app_path("karam_finance", "public", "js", "karam_general")
    )
    filenames = (
        "item_price_on_rate_mismatch_state.js",
        "item_price_on_rate_mismatch_prompt.js",
        "item_price_on_rate_mismatch.js",
    )
    return "\n".join(
        (script_directory / filename).read_text(encoding="utf-8")
        for filename in filenames
    )


class TestItemPriceClientContracts(FrappeTestCase):
    def test_client_script_shows_alert_when_new_item_price_is_created(self) -> None:
        """Keep a success alert in the client flow for newly created prices."""
        script = _client_rate_mismatch_script()

        assert "frappe.show_alert" in script
        assert "result.created" in script
        assert "result.updated" in script
        assert "result.reused" in script
        assert "get_item_price_mismatch_context" in script

    def test_client_script_saves_form_after_confirming_item_price_creation(
        self,
    ) -> None:
        """Persist the document immediately after the user confirms the prompt."""
        script = _client_rate_mismatch_script()

        assert "await frm.save()" in script
        assert "const confirmedRate = flt(row.price_list_rate);" in script
        assert "resetRowPricing(frm, cdt, cdn, confirmedRate);" in script

    def test_client_script_reverts_rate_when_prompt_is_rejected(self) -> None:
        """Restore the row to the default price list state on No or dismiss."""
        script = _client_rate_mismatch_script()

        assert "function revertRowPricing" in script
        assert "const previousPriceListRateByRow = new Map();" in script
        assert "price_list_rate: targetRate" in script
        assert "await revertRowPricing(frm, cdt, cdn, originalRate);" in script
        assert (
            "await revertRowPricing(frm, cdt, cdn, originalRate);\n"
            "        showRevertAlert();"
        ) in script
        assert "frappe.model.set_value" in script
        assert "const suppressedRows = new Set();" in script

    def test_client_script_includes_party_name_in_prompt_details(self) -> None:
        """Show party ID and display name in the Item Price prompt details."""
        script = _client_rate_mismatch_script()

        assert "frm.doc.supplier_name" in script
        assert "frm.doc.customer_name" in script
        assert '.join(": ")' in script

    def test_client_script_lets_frappe_show_duplicate_valid_from_exception(
        self,
    ) -> None:
        """Let Frappe show same-date duplicate blocking without generic alert."""
        script = _client_rate_mismatch_script()

        assert "with the same Valid From date" in script
        assert "function showDuplicateValidFromError" not in script
        assert "frappe.msgprint" not in script
        assert "if (isDuplicateValidFromError(error))" in script
        assert "function runAfterMessageDialogDismissal" in script
        assert "attempt < 5" in script
        assert "dialog.custom_onhide = () =>" in script
        assert "await revertRowPricing(frm, cdt, cdn, originalRate);" in script
        assert "Rate reverted to the existing Item Price value." in script
        assert "previousPriceListRateByRow.delete(row.name);" in script

    def test_client_script_prompts_only_from_price_list_rate_changes(self) -> None:
        """Run rate-mismatch prompts from Item Price fetches, not discounts."""
        script = _client_rate_mismatch_script()

        assert "price_list_rate(frm, cdt, cdn)" in script
        assert "\n      rate(frm, cdt, cdn)" not in script
        assert "discount_percentage(frm, cdt, cdn)" not in script
        assert "discount_amount(frm, cdt, cdn)" not in script
        assert "margin_type(frm, cdt, cdn)" not in script
        assert "margin_rate_or_amount(frm, cdt, cdn)" not in script
        assert "function markPricingAdjustment" in script
        assert '"discount_percentage", "discount_amount"' in script
        assert "function consumePricingAdjustment" not in script
        assert "function rememberUserPricingField" in script
        assert '"price_list_rate"' in script
