"""Tests for party-name population helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from frappe.tests.utils import FrappeTestCase

from karam_finance.common.party_utils import populate_party_names


class _Row:
    def __init__(self, party_type: str, party: str, party_name: str | None) -> None:
        self.party_type = party_type
        self.party = party
        self.party_name = party_name

    def get(self, key: str) -> str | None:
        return getattr(self, key, None)


class TestPartyUtils(FrappeTestCase):
    """Unit tests for ``populate_party_names``."""

    def test_populate_party_names_sets_attribute_for_document_like_rows(self) -> None:
        """Populate ``party_name`` on non-dict rows via attribute assignment."""
        row = _Row("Supplier", "SUP-0001", None)
        meta = MagicMock()
        meta.get_title_field.return_value = "supplier_name"

        supplier = MagicMock()
        supplier.name = "SUP-0001"
        supplier.get.side_effect = {"supplier_name": "Acme Supplies"}.get

        with (
            patch(
                "karam_finance.common.party_utils.frappe.get_meta",
                return_value=meta,
            ) as mock_get_meta,
            patch(
                "karam_finance.common.party_utils.frappe.get_all",
                return_value=[supplier],
            ) as mock_get_all,
        ):
            populate_party_names([row])

        assert row.party_name == "Acme Supplies"
        mock_get_meta.assert_called_once_with("Supplier")
        mock_get_all.assert_called_once()

    def test_populate_party_names_sets_value_for_dict_rows(self) -> None:
        """Populate ``party_name`` on dict rows via key assignment."""
        row = {"party_type": "Customer", "party": "CUST-0001", "party_name": None}
        meta = MagicMock()
        meta.get_title_field.return_value = "name"

        with (
            patch(
                "karam_finance.common.party_utils.frappe.get_meta",
                return_value=meta,
            ) as mock_get_meta,
            patch("karam_finance.common.party_utils.frappe.get_all") as mock_get_all,
        ):
            populate_party_names([row])

        assert row["party_name"] == "CUST-0001"
        mock_get_meta.assert_called_once_with("Customer")
        mock_get_all.assert_not_called()
