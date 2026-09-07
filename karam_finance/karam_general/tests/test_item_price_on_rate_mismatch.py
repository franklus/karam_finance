"""Tests for item price creation on transaction rate mismatch."""

from __future__ import annotations

import importlib
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import frappe
import pytest
from frappe.tests.utils import FrappeTestCase

if TYPE_CHECKING:
    from types import ModuleType

MODULE_NAME = "karam_finance.karam_general.utils.item_price_on_rate_mismatch"


def _load_module() -> ModuleType:
    return importlib.import_module(MODULE_NAME)


class TestItemPriceOnRateMismatch(FrappeTestCase):
    """Regression tests for the rate-mismatch item price flow."""

    @staticmethod
    def _settings_doc(
        module: ModuleType, enabled_doctypes: set[str]
    ) -> frappe._dict[str, Any]:
        """Build a Stock Settings-like document for unit tests."""
        return frappe._dict(
            {
                module.SETTINGS_TABLE_FIELD: [
                    frappe._dict(
                        {
                            "doctype_name": doctype_name,
                            "enabled": 1 if doctype_name in enabled_doctypes else 0,
                            "update_item_price": 0,
                            "throw_exception": 0,
                        }
                    )
                    for doctype_name in module.SUPPORTED_RATE_MISMATCH_DOCTYPES
                ]
            }
        )

    def test_setting_field_fixture_exists(self) -> None:
        """Ensure the Stock Settings fixture is stored in karam_general."""
        fixture_path = Path(
            frappe.get_app_path(
                "karam_finance",
                "karam_general",
                "custom_fields",
                "stock_settings.json",
            )
        )

        assert fixture_path.exists()

    def test_resolve_valid_from_uses_transaction_date_for_purchase_order(self) -> None:
        """Use transaction_date for Quotation, Sales Order, and Purchase Order."""
        module = _load_module()

        assert module.resolve_valid_from(
            doctype="Purchase Order",
            transaction_date="2024-05-06",
            posting_date="2026-04-17",
        ) == date(2024, 5, 6)

    def test_resolve_valid_from_uses_posting_date_for_purchase_receipt(self) -> None:
        """Use posting_date for the rest of the supported doctypes."""
        module = _load_module()

        assert module.resolve_valid_from(
            doctype="Purchase Receipt",
            transaction_date="2024-05-06",
            posting_date="2024-05-07",
        ) == date(2024, 5, 7)

    def test_build_rate_mismatch_doctype_rows_defaults_to_manual_enablement(
        self,
    ) -> None:
        """Seed supported doctypes as disabled with duplicate blocking enabled."""
        module = _load_module()

        settings_by_doctype: dict[str, Any] = {}
        rows = module.build_rate_mismatch_doctype_rows(
            settings_by_doctype, default_enabled=0
        )
        enabled_by_doctype = {row["doctype_name"]: row["enabled"] for row in rows}

        assert set(enabled_by_doctype) == set(module.SUPPORTED_RATE_MISMATCH_DOCTYPES)
        assert all(value == 0 for value in enabled_by_doctype.values())
        assert all(row["update_item_price"] == 0 for row in rows)
        assert all(row["throw_exception"] == 1 for row in rows)

    def test_sync_rows_seeds_all_supported_doctypes_when_table_is_empty(self) -> None:
        """Seed all supported doctypes as disabled when the table is empty."""
        module = _load_module()
        stock_settings: frappe._dict[str, Any] = frappe._dict(
            {
                module.SETTINGS_TABLE_FIELD: [],
            }
        )

        def _set_field(fieldname: str, value: object) -> None:
            cast("dict[str, Any]", stock_settings)[fieldname] = value

        cast("Any", stock_settings).set = _set_field

        module.sync_stock_settings_rate_mismatch_rows(stock_settings)

        assert len(stock_settings[module.SETTINGS_TABLE_FIELD]) == len(
            module.SUPPORTED_RATE_MISMATCH_DOCTYPES
        )
        assert all(
            row["enabled"] == 0 for row in stock_settings[module.SETTINGS_TABLE_FIELD]
        )
        assert all(
            row["throw_exception"] == 1
            for row in stock_settings[module.SETTINGS_TABLE_FIELD]
        )

    def test_sync_rows_preserves_manual_values_on_save(self) -> None:
        """Keep user-selected values during normal Stock Settings saves."""
        module = _load_module()
        stock_settings = self._settings_doc(module, {"Purchase Order", "Sales Order"})
        for row in stock_settings[module.SETTINGS_TABLE_FIELD]:
            if row.doctype_name == "Purchase Order":
                row.update_item_price = 1

        def _set_field(fieldname: str, value: object) -> None:
            cast("dict[str, Any]", stock_settings)[fieldname] = value

        cast("Any", stock_settings).set = _set_field

        module.sync_stock_settings_rate_mismatch_rows(stock_settings)

        enabled_doctypes = {
            row["doctype_name"]
            for row in stock_settings[module.SETTINGS_TABLE_FIELD]
            if row["enabled"] == 1
        }
        assert enabled_doctypes == {"Purchase Order", "Sales Order"}
        purchase_order_row = next(
            row
            for row in stock_settings[module.SETTINGS_TABLE_FIELD]
            if row["doctype_name"] == "Purchase Order"
        )
        assert purchase_order_row["update_item_price"] == 1
        assert purchase_order_row["throw_exception"] == 0

    def test_sync_rows_rejects_conflicting_duplicate_behaviours(self) -> None:
        """Do not allow update and exception handling at the same time."""
        module = _load_module()
        stock_settings = self._settings_doc(module, {"Purchase Order"})
        for row in stock_settings[module.SETTINGS_TABLE_FIELD]:
            if row.doctype_name == "Purchase Order":
                row.update_item_price = 1
                row.throw_exception = 1

        def _set_field(fieldname: str, value: object) -> None:
            cast("dict[str, Any]", stock_settings)[fieldname] = value

        cast("Any", stock_settings).set = _set_field

        with pytest.raises(frappe.ValidationError):
            module.sync_stock_settings_rate_mismatch_rows(stock_settings)

    def test_sync_rows_for_migrate_preserves_supported_doctype_values(
        self,
    ) -> None:
        """Preserve existing supported rows during migrate/install."""
        module = _load_module()
        stock_settings = self._settings_doc(module, {"Purchase Order", "Sales Order"})
        for row in stock_settings[module.SETTINGS_TABLE_FIELD]:
            if row.doctype_name == "Purchase Order":
                row.update_item_price = 1
            if row.doctype_name == "Sales Order":
                row.throw_exception = 1
        save_mock = MagicMock()
        cast("Any", stock_settings).save = save_mock

        def _set_field(fieldname: str, value: object) -> None:
            cast("dict[str, Any]", stock_settings)[fieldname] = value

        cast("Any", stock_settings).set = _set_field

        with (
            patch.object(module.frappe, "clear_cache"),
            patch.object(module.frappe, "get_single", return_value=stock_settings),
        ):
            module.sync_stock_settings_rate_mismatch_rows_for_migrate()

        purchase_order_row = next(
            row
            for row in stock_settings[module.SETTINGS_TABLE_FIELD]
            if row["doctype_name"] == "Purchase Order"
        )
        sales_order_row = next(
            row
            for row in stock_settings[module.SETTINGS_TABLE_FIELD]
            if row["doctype_name"] == "Sales Order"
        )
        assert purchase_order_row["enabled"] == 1
        assert purchase_order_row["update_item_price"] == 1
        assert purchase_order_row["throw_exception"] == 0
        assert sales_order_row["enabled"] == 1
        assert sales_order_row["update_item_price"] == 0
        assert sales_order_row["throw_exception"] == 1
        save_mock.assert_not_called()

    def test_sync_rows_for_migrate_seeds_missing_rows_with_duplicate_blocking(
        self,
    ) -> None:
        """Add missing supported rows during migrate without changing existing rows."""
        module = _load_module()
        stock_settings = frappe._dict(
            {
                module.SETTINGS_TABLE_FIELD: [
                    frappe._dict(
                        {
                            "doctype_name": "Purchase Order",
                            "enabled": 1,
                            "update_item_price": 0,
                            "throw_exception": 0,
                        }
                    )
                ]
            }
        )
        save_mock = MagicMock()
        cast("Any", stock_settings).save = save_mock

        def _set_field(fieldname: str, value: object) -> None:
            cast("dict[str, Any]", stock_settings)[fieldname] = value

        cast("Any", stock_settings).set = _set_field

        with (
            patch.object(module.frappe, "clear_cache"),
            patch.object(module.frappe, "get_single", return_value=stock_settings),
        ):
            module.sync_stock_settings_rate_mismatch_rows_for_migrate()

        purchase_order_row = next(
            row
            for row in stock_settings[module.SETTINGS_TABLE_FIELD]
            if row["doctype_name"] == "Purchase Order"
        )
        quotation_row = next(
            row
            for row in stock_settings[module.SETTINGS_TABLE_FIELD]
            if row["doctype_name"] == "Quotation"
        )
        assert purchase_order_row["enabled"] == 1
        assert purchase_order_row["throw_exception"] == 0
        assert quotation_row["enabled"] == 0
        assert quotation_row["update_item_price"] == 0
        assert quotation_row["throw_exception"] == 1
        save_mock.assert_called_once_with(ignore_permissions=True)

    def test_get_item_price_mismatch_context_respects_enabled_doctypes(self) -> None:
        """Skip the feature entirely when the current doctype is disabled."""
        module = _load_module()
        settings_doc = self._settings_doc(module, {"Purchase Order"})

        with patch.object(module.frappe, "get_single", return_value=settings_doc):
            context = module.get_item_price_mismatch_context(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                doctype="Purchase Receipt",
                transaction_date="2024-05-06",
                posting_date="2024-05-07",
            )

        assert context == {"enabled": False}

    def test_get_item_price_mismatch_context_returns_existing_valid_from_price(
        self,
    ) -> None:
        """Return existing same-date price context without blocking the prompt."""
        module = _load_module()
        settings_doc = self._settings_doc(module, {"Purchase Order"})

        with (
            patch.object(module.frappe, "get_single", return_value=settings_doc),
            patch.object(
                module,
                "find_item_price_with_same_valid_from",
                return_value=frappe._dict(
                    {"name": "ITEM-PRICE-0001", "price_list_rate": 115}
                ),
            ),
        ):
            context = module.get_item_price_mismatch_context(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                doctype="Purchase Order",
                transaction_date="2024-05-06",
                posting_date="2026-04-17",
            )

        assert context == {
            "enabled": True,
            "valid_from": "2024-05-06",
            "item_price_name": "ITEM-PRICE-0001",
            "item_price_rate": 115,
            "update_item_price": False,
            "throw_exception": False,
        }

    def test_get_context_throws_when_same_date_exception_is_enabled(self) -> None:
        """Block same-date Item Price creation when configured to throw."""
        module = _load_module()
        settings_doc = self._settings_doc(module, {"Purchase Order"})
        for row in settings_doc[module.SETTINGS_TABLE_FIELD]:
            if row.doctype_name == "Purchase Order":
                row.throw_exception = 1

        with (
            patch.object(module.frappe, "get_single", return_value=settings_doc),
            patch.object(
                module,
                "find_item_price_with_same_valid_from",
                return_value=frappe._dict(
                    {
                        "name": "ITEM-PRICE-0001",
                        "price_list_rate": 115,
                        "item_name": "_Test Item Name",
                        "supplier": "_Test Supplier",
                    }
                ),
            ),
            pytest.raises(frappe.ValidationError, match="already exists"),
        ):
            module.get_item_price_mismatch_context(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                doctype="Purchase Order",
                transaction_date="2024-05-06",
                supplier="_Test Supplier",
            )

    def test_duplicate_valid_from_error_uses_error_title(self) -> None:
        """Show duplicate Item Price validation with an Error dialog title."""
        module = _load_module()

        with (
            patch.object(module.frappe, "throw") as mock_throw,
            patch.object(
                module,
                "_get_party_display_name",
                return_value="_Test Supplier Name",
            ) as mock_get_party_display_name,
            patch.object(module, "formatdate", return_value="06-05-2024"),
        ):
            module._throw_duplicate_valid_from_error(
                item_code="_Test Item",
                item_name="_Test Item Name",
                valid_from=date(2024, 5, 6),
                supplier="_Test Supplier",
            )

        mock_throw.assert_called_once()
        message = mock_throw.call_args.args[0]
        assert "already exists with the same Valid From date" in message
        assert "<ul>" in message
        assert "<strong>Item:</strong> _Test Item: _Test Item Name" in message
        assert (
            "<strong>Supplier:</strong> _Test Supplier: _Test Supplier Name" in message
        )
        assert "<strong>Valid From:</strong> 06-05-2024" in message
        assert mock_throw.call_args.kwargs["title"] == "Error"
        mock_get_party_display_name.assert_called_once_with(
            customer=None,
            supplier="_Test Supplier",
        )

    def test_get_item_price_mismatch_context_checks_duplicate_for_party(
        self,
    ) -> None:
        """Use the transaction party when checking same-date Item Prices."""
        module = _load_module()
        settings_doc = self._settings_doc(module, {"Purchase Order"})

        with (
            patch.object(module.frappe, "get_single", return_value=settings_doc),
            patch.object(
                module,
                "find_item_price_with_same_valid_from",
                return_value=None,
            ) as mock_find_item_price,
        ):
            module.get_item_price_mismatch_context(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                doctype="Purchase Order",
                transaction_date="2024-05-06",
                supplier="_Test Supplier",
            )

        mock_find_item_price.assert_called_once_with(
            item_code="_Test Item",
            price_list="_Test Price List",
            currency="USD",
            stock_uom="Nos",
            valid_from=date(2024, 5, 6),
            customer=None,
            supplier="_Test Supplier",
            qty=None,
            batch_no=None,
        )

    def test_get_context_skips_prompt_when_price_already_exists(self) -> None:
        """Do not prompt when the entered price_list_rate already has an Item Price."""
        module = _load_module()
        settings_doc = self._settings_doc(module, {"Purchase Order"})

        with (
            patch.object(module.frappe, "get_single", return_value=settings_doc),
            patch.object(
                module,
                "find_item_price_with_matching_rate",
                return_value=frappe._dict({"name": "ITEM-PRICE-0001"}),
            ) as mock_find_matching_rate,
            patch.object(
                module,
                "find_item_price_with_same_valid_from",
                return_value=None,
            ) as mock_find_same_valid_from,
        ):
            context = module.get_item_price_mismatch_context(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                price_list_rate=115,
                doctype="Purchase Order",
                transaction_date="2024-05-06",
                supplier="_Test Supplier",
            )

        assert context == {"enabled": False, "price_exists": True}
        mock_find_matching_rate.assert_called_once()
        mock_find_same_valid_from.assert_not_called()

    def test_context_api_requires_doctype_read_permission(self) -> None:
        """Reject prompt checks when the user cannot read the transaction doctype."""
        module = _load_module()

        with (
            patch.object(module.frappe, "has_permission", return_value=False),
            pytest.raises(frappe.PermissionError),
        ):
            module.get_item_price_mismatch_context_api(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                doctype="Purchase Order",
                transaction_date="2024-05-06",
            )

    def test_context_api_requires_item_price_read_permission(self) -> None:
        """Reject prompt checks when the user cannot read Item Price records."""
        module = _load_module()

        def has_permission(doctype: str, _permission_type: str) -> bool:
            return doctype != "Item Price"

        with (
            patch.object(module.frappe, "has_permission", side_effect=has_permission),
            pytest.raises(frappe.PermissionError),
        ):
            module.get_item_price_mismatch_context_api(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                doctype="Purchase Order",
                transaction_date="2024-05-06",
            )

    def test_packing_specific_item_price_is_selected_for_divisible_quantity(
        self,
    ) -> None:
        """Prefer a packing-specific price when the row quantity is divisible."""
        module = _load_module()

        candidates = [
            frappe._dict({"name": "PACK-6", "packing_unit": 6}),
            frappe._dict({"name": "PACK-12", "packing_unit": 12}),
            frappe._dict({"name": "GLOBAL", "packing_unit": 0}),
        ]

        assert module._select_applicable_item_price(candidates, 12) == candidates[1]

    def test_generic_item_price_is_selected_when_quantity_is_not_divisible(
        self,
    ) -> None:
        """Fall back to the generic price when a packing unit does not divide qty."""
        module = _load_module()

        candidates = [
            frappe._dict({"name": "PACK-6", "packing_unit": 6}),
            frappe._dict({"name": "GLOBAL", "packing_unit": 0}),
        ]

        assert module._select_applicable_item_price(candidates, 5) == candidates[1]

    def test_non_divisible_quantity_does_not_select_packing_specific_price(
        self,
    ) -> None:
        """Do not treat a non-divisible packing-specific price as applicable."""
        module = _load_module()

        candidate = frappe._dict({"name": "PACK-6", "packing_unit": 6})

        assert module._select_applicable_item_price([candidate], 5) is None

    def test_create_item_price_for_rate_mismatch_sets_valid_from_from_document_date(
        self,
    ) -> None:
        """Persist valid_from from the document date instead of today."""
        module = _load_module()
        settings_doc = self._settings_doc(module, {"Purchase Order"})
        item_price = MagicMock()
        item_price.name = "ITEM-PRICE-0001"

        with (
            patch.object(module.frappe, "get_single", return_value=settings_doc),
            patch.object(
                module,
                "find_item_price_with_same_valid_from",
                return_value=None,
            ),
            patch.object(module.frappe, "has_permission", return_value=True),
            patch.object(module.frappe.db, "exists", return_value=True),
            patch.object(
                module.frappe,
                "get_doc",
                return_value=item_price,
            ) as mock_get_doc,
        ):
            result = module.create_item_price_for_rate_mismatch(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                conversion_factor=1,
                price_list_rate=115,
                rate=112,
                doctype="Purchase Order",
                transaction_date="2024-05-06",
                posting_date="2026-04-17",
            )

        item_price.insert.assert_called_once_with()
        mock_get_doc.assert_called_once_with(
            {
                "doctype": "Item Price",
                "item_code": "_Test Item",
                "price_list": "_Test Price List",
                "currency": "USD",
                "uom": "Nos",
                "price_list_rate": 115,
                "valid_from": date(2024, 5, 6),
                "packing_unit": 0,
            }
        )
        assert result == {
            "created": True,
            "item_price_name": "ITEM-PRICE-0001",
            "price_list_rate": 115,
            "valid_from": date(2024, 5, 6),
        }

    def test_create_item_price_for_rate_mismatch_sets_supplier_party(
        self,
    ) -> None:
        """Create supplier-specific Item Prices for buying transactions."""
        module = _load_module()
        settings_doc = self._settings_doc(module, {"Purchase Order"})
        item_price = MagicMock()
        item_price.name = "ITEM-PRICE-0001"

        with (
            patch.object(module.frappe, "get_single", return_value=settings_doc),
            patch.object(
                module,
                "find_item_price_with_same_valid_from",
                return_value=None,
            ),
            patch.object(module.frappe, "has_permission", return_value=True),
            patch.object(module.frappe.db, "exists", return_value=True),
            patch.object(
                module.frappe,
                "get_doc",
                return_value=item_price,
            ) as mock_get_doc,
        ):
            module.create_item_price_for_rate_mismatch(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                conversion_factor=1,
                price_list_rate=115,
                rate=112,
                doctype="Purchase Order",
                transaction_date="2024-05-06",
                supplier="_Test Supplier",
                batch_no="_Test Batch",
                qty=12,
            )

        mock_get_doc.assert_called_once_with(
            {
                "doctype": "Item Price",
                "item_code": "_Test Item",
                "price_list": "_Test Price List",
                "currency": "USD",
                "uom": "Nos",
                "price_list_rate": 115,
                "valid_from": date(2024, 5, 6),
                "supplier": "_Test Supplier",
                "batch_no": "_Test Batch",
                "packing_unit": 0,
            }
        )

    def test_create_item_price_for_rate_mismatch_reuses_same_rate_before_blocking(
        self,
    ) -> None:
        """A stale confirmation reuses an exact match even when blocking is enabled."""
        module = _load_module()
        settings_doc = self._settings_doc(module, {"Purchase Order"})
        for row in settings_doc[module.SETTINGS_TABLE_FIELD]:
            if row.doctype_name == "Purchase Order":
                row.throw_exception = 1

        with (
            patch.object(module.frappe, "get_single", return_value=settings_doc),
            patch.object(module, "_lock_item_price_scope"),
            patch.object(
                module,
                "find_item_price_with_same_valid_from",
                return_value=frappe._dict(
                    {"name": "ITEM-PRICE-0001", "price_list_rate": 118}
                ),
            ),
            patch.object(module.frappe, "has_permission", return_value=True),
        ):
            result = module.create_item_price_for_rate_mismatch(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                conversion_factor=1,
                price_list_rate=118,
                rate=118,
                doctype="Purchase Order",
                transaction_date="2024-05-06",
            )

        assert result["reused"] is True
        assert result["item_price_name"] == "ITEM-PRICE-0001"

    def test_create_item_price_rejects_mixed_party_scope(self) -> None:
        """A single Item Price cannot be both customer- and supplier-specific."""
        module = _load_module()

        with (
            patch.object(module.frappe, "has_permission", return_value=True),
            pytest.raises(frappe.ValidationError, match="both a Customer"),
        ):
            module.create_item_price_for_rate_mismatch(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                conversion_factor=1,
                price_list_rate=118,
                rate=118,
                doctype="Purchase Order",
                transaction_date="2024-05-06",
                customer="_Test Customer",
                supplier="_Test Supplier",
            )

    def test_create_item_price_for_rate_mismatch_updates_existing_same_date_price(
        self,
    ) -> None:
        """Update the same-date party price instead of throwing duplicate validation."""
        module = _load_module()
        settings_doc = self._settings_doc(module, {"Purchase Order"})
        for row in settings_doc[module.SETTINGS_TABLE_FIELD]:
            if row.doctype_name == "Purchase Order":
                row.update_item_price = 1
        item_price = frappe._dict({"name": "ITEM-PRICE-0001", "price_list_rate": 115})
        save_mock = MagicMock()
        cast("Any", item_price).save = save_mock

        with (
            patch.object(module.frappe, "get_single", return_value=settings_doc),
            patch.object(
                module,
                "find_item_price_with_same_valid_from",
                return_value=frappe._dict(
                    {"name": "ITEM-PRICE-0001", "price_list_rate": 115}
                ),
            ),
            patch.object(module.frappe, "has_permission", return_value=True),
            patch.object(module.frappe.db, "exists", return_value=True),
            patch.object(
                module.frappe,
                "get_doc",
                return_value=item_price,
            ) as mock_get_doc,
        ):
            result = module.create_item_price_for_rate_mismatch(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                conversion_factor=1,
                price_list_rate=118,
                rate=118,
                doctype="Purchase Order",
                transaction_date="2024-05-06",
                supplier="_Test Supplier",
            )

        mock_get_doc.assert_called_once_with("Item Price", "ITEM-PRICE-0001")
        assert item_price.price_list_rate == 118
        save_mock.assert_called_once_with()
        assert result == {
            "created": False,
            "updated": True,
            "item_price_name": "ITEM-PRICE-0001",
            "price_list_rate": 118,
            "valid_from": date(2024, 5, 6),
            "previous_price_list_rate": 115,
        }

    def test_create_item_price_creates_duplicate_when_update_is_disabled(
        self,
    ) -> None:
        """Create a new same-date Item Price when update behaviour is disabled."""
        module = _load_module()
        settings_doc = self._settings_doc(module, {"Purchase Order"})
        item_price = MagicMock()
        item_price.name = "ITEM-PRICE-0002"

        with (
            patch.object(module.frappe, "get_single", return_value=settings_doc),
            patch.object(
                module,
                "find_item_price_with_same_valid_from",
                return_value=frappe._dict(
                    {"name": "ITEM-PRICE-0001", "price_list_rate": 115}
                ),
            ),
            patch.object(module.frappe, "has_permission", return_value=True),
            patch.object(
                module.frappe,
                "get_doc",
                return_value=item_price,
            ) as mock_get_doc,
        ):
            result = module.create_item_price_for_rate_mismatch(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                conversion_factor=1,
                price_list_rate=118,
                rate=118,
                doctype="Purchase Order",
                transaction_date="2024-05-06",
                supplier="_Test Supplier",
            )

        item_price.insert.assert_called_once_with()
        assert mock_get_doc.call_args.args[0]["supplier"] == "_Test Supplier"
        assert result == {
            "created": True,
            "item_price_name": "ITEM-PRICE-0002",
            "price_list_rate": 118,
            "valid_from": date(2024, 5, 6),
        }

    def test_create_duplicate_preserves_selected_packing_unit(self) -> None:
        """Keep an applicable packing-specific scope when duplicate creation is allowed."""
        module = _load_module()
        settings_doc = self._settings_doc(module, {"Purchase Order"})
        item_price = MagicMock()
        item_price.name = "ITEM-PRICE-0002"

        with (
            patch.object(module.frappe, "get_single", return_value=settings_doc),
            patch.object(module, "_lock_item_price_scope"),
            patch.object(
                module,
                "find_item_price_with_same_valid_from",
                return_value=frappe._dict(
                    {
                        "name": "ITEM-PRICE-0001",
                        "price_list_rate": 115,
                        "packing_unit": 6,
                    }
                ),
            ),
            patch.object(module.frappe, "has_permission", return_value=True),
            patch.object(
                module.frappe,
                "get_doc",
                return_value=item_price,
            ) as mock_get_doc,
        ):
            module.create_item_price_for_rate_mismatch(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                conversion_factor=1,
                price_list_rate=118,
                rate=118,
                doctype="Purchase Order",
                transaction_date="2024-05-06",
                qty=12,
            )

        assert mock_get_doc.call_args.args[0]["packing_unit"] == 6

    def test_create_item_price_for_rate_mismatch_reuses_matching_same_date_price(
        self,
    ) -> None:
        """Reuse a same-date price when it already matches the entered rate."""
        module = _load_module()
        settings_doc = self._settings_doc(module, {"Purchase Order"})
        for row in settings_doc[module.SETTINGS_TABLE_FIELD]:
            if row.doctype_name == "Purchase Order":
                row.update_item_price = 1

        with (
            patch.object(module.frappe, "get_single", return_value=settings_doc),
            patch.object(
                module,
                "find_item_price_with_same_valid_from",
                return_value=frappe._dict(
                    {"name": "ITEM-PRICE-0001", "price_list_rate": 118}
                ),
            ),
            patch.object(module.frappe, "has_permission", return_value=True),
        ):
            result = module.create_item_price_for_rate_mismatch(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                conversion_factor=1,
                price_list_rate=118,
                rate=118,
                doctype="Purchase Order",
                transaction_date="2024-05-06",
                supplier="_Test Supplier",
            )

        assert result == {
            "created": False,
            "reused": True,
            "item_price_name": "ITEM-PRICE-0001",
            "price_list_rate": 118,
            "valid_from": date(2024, 5, 6),
        }

    def test_create_item_price_requires_doctype_write_permission(self) -> None:
        """Reject Item Price creation when the user cannot write the transaction."""
        module = _load_module()

        with (
            patch.object(module.frappe, "has_permission", return_value=False),
            pytest.raises(frappe.PermissionError),
        ):
            module.create_item_price_for_rate_mismatch(
                item_code="_Test Item",
                price_list="_Test Price List",
                currency="USD",
                stock_uom="Nos",
                conversion_factor=1,
                price_list_rate=112,
                rate=115,
                doctype="Purchase Order",
                transaction_date="2024-05-06",
            )

    def test_global_hook_references_rate_mismatch_script(self) -> None:
        """Load the split client runtime globally in dependency order."""
        hooks_module = importlib.import_module("karam_finance.hooks")
        asset_root = "/assets/karam_finance/js/karam_general"

        assert hooks_module.app_include_js[-3:] == [
            f"{asset_root}/item_price_on_rate_mismatch_state.js",
            f"{asset_root}/item_price_on_rate_mismatch_prompt.js",
            f"{asset_root}/item_price_on_rate_mismatch.js",
        ]
