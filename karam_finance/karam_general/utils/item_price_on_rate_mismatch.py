"""Helpers for creating Item Prices from transaction rate mismatches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, Unpack, cast

import frappe
from frappe import _
from frappe.query_builder.functions import IfNull
from frappe.utils import cint, escape_html, flt, formatdate, getdate

if TYPE_CHECKING:
    from datetime import date

    from frappe.model.document import Document

SETTINGS_TABLE_FIELD = "ka_item_price_mismatch_doctypes"
MAX_ITEM_PRICE_SCOPE_CANDIDATES = 100
type RowValue = str | float | int | None


class ItemPriceRecord(TypedDict):
    """Fields selected from an applicable Item Price query."""

    name: str
    price_list_rate: float | int
    item_name: str | None
    packing_unit: float | int | None


class ItemPriceParty(TypedDict):
    """Optional party scope shared by lookups and duplicate messages."""

    customer: NotRequired[str | None]
    supplier: NotRequired[str | None]


SUPPORTED_RATE_MISMATCH_DOCTYPES = [
    "Quotation",
    "Sales Order",
    "Delivery Note",
    "Sales Invoice",
    "POS Invoice",
    "Purchase Order",
    "Purchase Receipt",
    "Purchase Invoice",
]
TRANSACTION_DATE_DOCTYPES = {
    "Quotation",
    "Sales Order",
    "Purchase Order",
}


@dataclass(frozen=True, kw_only=True)
class RateMismatchCreateRequest:
    """Normalised request for confirmed Item Price mismatch handling."""

    item_code: str
    price_list: str
    currency: str
    stock_uom: str
    conversion_factor: float | int | None
    price_list_rate: float | int
    rate: float | int
    doctype: str
    transaction_date: str | date | None = None
    posting_date: str | date | None = None
    customer: str | None = None
    supplier: str | None = None
    batch_no: str | None = None
    qty: float | int | None = None


@dataclass(frozen=True, kw_only=True)
class ItemPriceScope:
    """Fields that make two Item Prices applicable to the same transaction."""

    item_code: str
    price_list: str
    currency: str
    stock_uom: str
    customer: str | None = None
    supplier: str | None = None
    batch_no: str | None = None
    packing_unit: float | int | None = None


def build_rate_mismatch_doctype_rows(
    existing_rows: dict[str, dict[str, int]],
    *,
    default_enabled: int,
    default_throw_exception: int = 1,
) -> list[dict[str, Any]]:
    """Build canonical Stock Settings child rows for supported doctypes."""
    return [
        {
            "doctype_name": doctype_name,
            "enabled": cint(
                existing_rows.get(doctype_name, {}).get("enabled", default_enabled)
            ),
            "update_item_price": cint(
                existing_rows.get(doctype_name, {}).get("update_item_price", 0)
            ),
            "throw_exception": cint(
                existing_rows.get(doctype_name, {}).get(
                    "throw_exception", default_throw_exception
                )
            ),
        }
        for doctype_name in SUPPORTED_RATE_MISMATCH_DOCTYPES
    ]


def sync_stock_settings_rate_mismatch_rows(
    doc: Document | None = None,
    _method: str | None = None,
) -> None:
    """Ensure Stock Settings contains the supported doctype rows."""
    stock_settings = doc or frappe.get_single("Stock Settings")
    rows = getattr(stock_settings, SETTINGS_TABLE_FIELD, None) or []
    existing_rows = _build_existing_rate_mismatch_rows(rows)

    synced_rows = build_rate_mismatch_doctype_rows(
        existing_rows,
        default_enabled=0,
    )
    stock_settings.set(SETTINGS_TABLE_FIELD, synced_rows)
    _validate_rate_mismatch_behaviour(stock_settings)


def sync_stock_settings_rate_mismatch_rows_for_migrate() -> None:
    """Seed missing supported doctypes during migrate without resetting user choices."""
    frappe.clear_cache(doctype="Stock Settings")
    stock_settings = frappe.get_single("Stock Settings")
    before_rows = _serialise_doctype_rows(stock_settings)
    rows = getattr(stock_settings, SETTINGS_TABLE_FIELD, None) or []
    existing_rows = _build_existing_rate_mismatch_rows(rows)
    stock_settings.set(
        SETTINGS_TABLE_FIELD,
        build_rate_mismatch_doctype_rows(existing_rows, default_enabled=0),
    )
    _validate_rate_mismatch_behaviour(stock_settings)
    after_rows = _serialise_doctype_rows(stock_settings)

    if before_rows != after_rows:
        stock_settings.save(ignore_permissions=True)

    frappe.clear_cache(doctype="Stock Settings")


def resolve_valid_from(
    *,
    doctype: str,
    transaction_date: str | date | None = None,
    posting_date: str | date | None = None,
) -> date | None:
    """Resolve the Item Price valid_from date from the document type."""
    raw_value = (
        transaction_date if doctype in TRANSACTION_DATE_DOCTYPES else posting_date
    )
    return getdate(raw_value) if raw_value else None


def find_item_price_with_same_valid_from(  # noqa: PLR0913 - preserve the keyword contract of the Frappe request adapter.
    *,
    item_code: str,
    price_list: str,
    currency: str,
    stock_uom: str,
    valid_from: date,
    customer: str | None = None,
    supplier: str | None = None,
    batch_no: str | None = None,
    qty: float | int | None = None,
    for_update: bool = False,
) -> ItemPriceRecord | None:
    """Return an applicable Item Price with the same valid_from."""
    return _find_applicable_item_price(
        scope=ItemPriceScope(
            item_code=item_code,
            price_list=price_list,
            currency=currency,
            stock_uom=stock_uom,
            customer=customer,
            supplier=supplier,
            batch_no=batch_no,
        ),
        valid_from=valid_from,
        quantity=qty,
        for_update=for_update,
    )


def find_item_price_with_matching_rate(  # noqa: PLR0913 - preserve the keyword contract of the Frappe request adapter.
    *,
    item_code: str,
    price_list: str,
    currency: str,
    stock_uom: str,
    price_list_rate: float | int,
    customer: str | None = None,
    supplier: str | None = None,
    batch_no: str | None = None,
    qty: float | int | None = None,
    valid_from: date | None = None,
) -> ItemPriceRecord | None:
    """Return an applicable Item Price with the entered price_list_rate."""
    return _find_applicable_item_price(
        scope=ItemPriceScope(
            item_code=item_code,
            price_list=price_list,
            currency=currency,
            stock_uom=stock_uom,
            customer=customer,
            supplier=supplier,
            batch_no=batch_no,
        ),
        valid_from=valid_from,
        price_list_rate=flt(price_list_rate),
        quantity=qty,
    )


def get_item_price_mismatch_context(  # noqa: PLR0913
    *,
    item_code: str,
    price_list: str,
    currency: str,
    stock_uom: str,
    doctype: str,
    price_list_rate: float | int | None = None,
    transaction_date: str | date | None = None,
    posting_date: str | date | None = None,
    customer: str | None = None,
    supplier: str | None = None,
    batch_no: str | None = None,
    qty: float | int | None = None,
) -> dict[str, Any]:
    """Return the prompt context for a transaction row rate mismatch."""
    _validate_party_scope(customer=customer, supplier=supplier)
    settings = get_rate_mismatch_settings(doctype)
    if not settings.enabled:
        return {"enabled": False}

    valid_from = resolve_valid_from(
        doctype=doctype,
        transaction_date=transaction_date,
        posting_date=posting_date,
    )
    if not valid_from:
        return {"enabled": True, "valid_from": None}

    if price_list_rate and find_item_price_with_matching_rate(
        item_code=item_code,
        price_list=price_list,
        currency=currency,
        stock_uom=stock_uom,
        price_list_rate=price_list_rate,
        customer=customer,
        supplier=supplier,
        batch_no=batch_no,
        qty=qty,
        valid_from=valid_from,
    ):
        return {"enabled": False, "price_exists": True}

    existing_item_price = find_item_price_with_same_valid_from(
        item_code=item_code,
        price_list=price_list,
        currency=currency,
        stock_uom=stock_uom,
        valid_from=valid_from,
        customer=customer,
        supplier=supplier,
        batch_no=batch_no,
        qty=qty,
    )

    return _build_item_price_mismatch_context(
        existing_item_price=existing_item_price,
        settings=settings,
        item_code=item_code,
        valid_from=valid_from,
        customer=customer,
        supplier=supplier,
    )


def _build_item_price_mismatch_context(
    *,
    existing_item_price: ItemPriceRecord | None,
    settings: frappe._dict[str, int],
    item_code: str,
    valid_from: date,
    **party: Unpack[ItemPriceParty],
) -> dict[str, Any]:
    """Build prompt context and apply configured duplicate blocking."""
    context: dict[str, Any] = {"enabled": True, "valid_from": valid_from.isoformat()}
    if existing_item_price:
        if settings.throw_exception:
            _throw_duplicate_valid_from_error(
                item_code=item_code,
                item_name=existing_item_price.get("item_name"),
                valid_from=valid_from,
                customer=party.get("customer"),
                supplier=party.get("supplier"),
            )
        context["item_price_name"] = existing_item_price.get("name")
        context["item_price_rate"] = existing_item_price.get("price_list_rate")
        context["update_item_price"] = bool(settings.update_item_price)
        context["throw_exception"] = bool(settings.throw_exception)
    else:
        context["update_item_price"] = bool(settings.update_item_price)
        context["throw_exception"] = bool(settings.throw_exception)

    return context


@frappe.whitelist()  # nosemgrep: frappe-missing-permission-check
def get_item_price_mismatch_context_api(  # noqa: PLR0913
    *,
    item_code: str,
    price_list: str,
    currency: str,
    stock_uom: str,
    doctype: str,
    price_list_rate: float | int | None = None,
    transaction_date: str | date | None = None,
    posting_date: str | date | None = None,
    customer: str | None = None,
    supplier: str | None = None,
    batch_no: str | None = None,
    qty: float | int | None = None,
) -> dict[str, Any]:
    """Whitelisted wrapper for the client-side prompt pre-check."""
    if not frappe.has_permission(doctype, "read"):
        frappe.throw(
            _("You do not have permission to read {0}.").format(doctype),
            frappe.PermissionError,
        )
    if not frappe.has_permission("Item Price", "read"):
        frappe.throw(
            _("You do not have permission to read Item Price records."),
            frappe.PermissionError,
        )
    _ensure_supported_doctype(doctype)

    return get_item_price_mismatch_context(
        item_code=item_code,
        price_list=price_list,
        currency=currency,
        stock_uom=stock_uom,
        doctype=doctype,
        price_list_rate=price_list_rate,
        transaction_date=transaction_date,
        posting_date=posting_date,
        customer=customer,
        supplier=supplier,
        batch_no=batch_no,
        qty=qty,
    )


@frappe.whitelist()  # nosemgrep: frappe-missing-permission-check
def create_item_price_for_rate_mismatch(  # noqa: PLR0913
    *,
    item_code: str,
    price_list: str,
    currency: str,
    stock_uom: str,
    conversion_factor: float | int | None,
    price_list_rate: float | int,
    rate: float | int,
    doctype: str,
    transaction_date: str | date | None = None,
    posting_date: str | date | None = None,
    customer: str | None = None,
    supplier: str | None = None,
    batch_no: str | None = None,
    qty: float | int | None = None,
) -> dict[str, Any]:
    """Create an Item Price for a confirmed transaction-rate mismatch."""
    if not frappe.has_permission(doctype, "write"):
        frappe.throw(
            _("You do not have permission to write {0}.").format(doctype),
            frappe.PermissionError,
        )
    if not frappe.has_permission("Item Price", "write"):
        frappe.throw(
            _("You do not have permission to write Item Price records."),
            frappe.PermissionError,
        )
    request = RateMismatchCreateRequest(**locals())
    return _create_item_price_for_validated_rate_mismatch(request)


def _create_item_price_for_validated_rate_mismatch(
    request: RateMismatchCreateRequest,
) -> dict[str, Any]:
    """Create, reuse, or update an Item Price for a normalised request."""
    _validate_rate_mismatch_create_request(request)
    resolved_valid_from = _resolve_required_valid_from(request)
    effective_rate = _get_effective_rate(request)
    _lock_item_price_scope(request)
    settings = _get_enabled_rate_mismatch_settings(request.doctype)
    existing_item_price = find_item_price_with_same_valid_from(
        item_code=request.item_code,
        price_list=request.price_list,
        currency=request.currency,
        stock_uom=request.stock_uom,
        valid_from=resolved_valid_from,
        customer=request.customer,
        supplier=request.supplier,
        batch_no=request.batch_no,
        qty=request.qty,
        for_update=True,
    )

    if existing_item_price:
        result = _apply_existing_item_price_policy(
            existing_item_price=existing_item_price,
            settings=settings,
            request=request,
            valid_from=resolved_valid_from,
            effective_rate=effective_rate,
        )
        if result:
            return result

    return _create_new_item_price_for_rate_mismatch(
        request=request,
        valid_from=resolved_valid_from,
        effective_rate=effective_rate,
        packing_unit=(
            cint(existing_item_price.get("packing_unit") or 0)
            if existing_item_price
            else 0
        ),
    )


def _validate_rate_mismatch_create_request(
    request: RateMismatchCreateRequest,
) -> None:
    """Validate permissions, support, required values, and changed rate."""
    if not frappe.has_permission(request.doctype, "write"):
        frappe.throw(
            _("You do not have permission to write {0}.").format(request.doctype),
            frappe.PermissionError,
        )
    if not frappe.has_permission("Item Price", "write"):
        frappe.throw(
            _("You do not have permission to create Item Price records."),
            frappe.PermissionError,
        )
    _ensure_supported_doctype(request.doctype)
    _validate_party_scope(customer=request.customer, supplier=request.supplier)

    required_values = {
        "item_code": request.item_code,
        "price_list": request.price_list,
        "currency": request.currency,
        "stock_uom": request.stock_uom,
        "doctype": request.doctype,
    }
    missing_values = [key for key, value in required_values.items() if not value]
    if missing_values:
        frappe.throw(
            _("Missing required values: {0}").format(", ".join(missing_values))
        )

    if not flt(request.price_list_rate):
        frappe.throw(_("Set the price list rate before creating a new Item Price."))


def _get_enabled_rate_mismatch_settings(doctype: str) -> frappe._dict[str, int]:
    """Return enabled mismatch settings or throw for disabled doctypes."""
    settings = get_rate_mismatch_settings(doctype)
    if not settings.enabled:
        frappe.throw(
            _(
                "Enable the current doctype in Stock Settings before "
                "creating Item Price records."
            )
        )
    return settings


def _resolve_required_valid_from(request: RateMismatchCreateRequest) -> date:
    """Resolve and require the Item Price valid_from date."""
    valid_from = resolve_valid_from(
        doctype=request.doctype,
        transaction_date=request.transaction_date,
        posting_date=request.posting_date,
    )
    if not valid_from:
        frappe.throw(_("Set the document date before creating a new Item Price."))

    return cast("date", valid_from)


def _get_effective_rate(request: RateMismatchCreateRequest) -> float:
    """Convert the entered price_list_rate into the Item Price UOM rate."""
    return flt(request.price_list_rate) / flt(request.conversion_factor or 1)


def _apply_existing_item_price_policy(
    *,
    existing_item_price: ItemPriceRecord,
    settings: frappe._dict[str, int],
    request: RateMismatchCreateRequest,
    valid_from: date,
    effective_rate: float,
) -> dict[str, Any] | None:
    """Apply configured same-date Item Price behaviour."""
    if flt(existing_item_price.get("price_list_rate")) == effective_rate:
        return {
            "created": False,
            "reused": True,
            "item_price_name": existing_item_price.get("name"),
            "price_list_rate": effective_rate,
            "valid_from": valid_from,
        }

    if settings.throw_exception:
        _throw_duplicate_valid_from_error(
            item_code=request.item_code,
            item_name=existing_item_price.get("item_name"),
            valid_from=valid_from,
            customer=request.customer,
            supplier=request.supplier,
        )

    if not settings.update_item_price:
        return None

    item_price_name = existing_item_price.get("name")
    if not frappe.db.exists("Item Price", item_price_name):
        frappe.throw(_("Item Price {0} no longer exists.").format(item_price_name))

    item_price = frappe.get_doc(  # nosemgrep: frappe-get-doc-without-check
        "Item Price",
        item_price_name,
    )
    item_price.update({"price_list_rate": effective_rate})
    item_price.save()
    return {
        "created": False,
        "updated": True,
        "item_price_name": item_price_name,
        "price_list_rate": effective_rate,
        "valid_from": valid_from,
        "previous_price_list_rate": existing_item_price.get("price_list_rate"),
    }


def _create_new_item_price_for_rate_mismatch(
    *,
    request: RateMismatchCreateRequest,
    valid_from: date,
    effective_rate: float,
    packing_unit: int = 0,
) -> dict[str, Any]:
    """Insert a new Item Price for the confirmed mismatch."""
    item_price_values = {
        "doctype": "Item Price",
        "item_code": request.item_code,
        "price_list": request.price_list,
        "currency": request.currency,
        "uom": request.stock_uom,
        "price_list_rate": effective_rate,
        "valid_from": valid_from,
        "packing_unit": packing_unit,
    }
    if request.customer:
        item_price_values["customer"] = request.customer
    elif request.supplier:
        item_price_values["supplier"] = request.supplier
    if request.batch_no:
        item_price_values["batch_no"] = request.batch_no

    item_price = frappe.get_doc(item_price_values)
    item_price.insert()

    return {
        "created": True,
        "item_price_name": item_price.name,
        "price_list_rate": effective_rate,
        "valid_from": valid_from,
    }


def get_rate_mismatch_settings(doctype: str) -> frappe._dict[str, int]:
    """Return rate-mismatch behaviour settings for the given doctype."""
    stock_settings = frappe.get_single("Stock Settings")
    rows = getattr(stock_settings, SETTINGS_TABLE_FIELD, None) or []
    for row in rows:
        if str(row.doctype_name) == doctype:
            return frappe._dict(
                {
                    "enabled": cint(row.enabled),
                    "update_item_price": cint(getattr(row, "update_item_price", 0)),
                    "throw_exception": cint(getattr(row, "throw_exception", 1)),
                }
            )

    return frappe._dict({"enabled": 0, "update_item_price": 0, "throw_exception": 1})


def is_rate_mismatch_enabled(doctype: str) -> bool:  # noqa: V103 - retained public settings lookup API.
    """Return whether the feature is enabled for the given doctype."""
    return bool(get_rate_mismatch_settings(doctype).enabled)


def _build_existing_rate_mismatch_rows(
    rows: list[Document],
) -> dict[str, dict[str, int]]:
    """Index existing settings rows while preserving explicit user choices."""
    return {
        str(_get_row_value(row, "doctype_name")): {
            "enabled": cint(_get_row_value(row, "enabled", 0)),
            "update_item_price": cint(_get_row_value(row, "update_item_price", 0)),
            "throw_exception": cint(_get_row_value(row, "throw_exception", 1)),
        }
        for row in rows
        if _get_row_value(row, "doctype_name")
    }


def _ensure_supported_doctype(doctype: str) -> None:
    """Reject doctypes outside the supported mismatch flow."""
    if doctype not in SUPPORTED_RATE_MISMATCH_DOCTYPES:
        frappe.throw(_("Unsupported doctype for Item Price rate mismatch flow."))


def _validate_party_scope(
    *,
    customer: str | None = None,
    supplier: str | None = None,
) -> None:
    """Keep customer, supplier, and Global Item Price scopes disjoint."""
    if customer and supplier:
        frappe.throw(
            _("An Item Price cannot be scoped to both a Customer and a Supplier.")
        )


def _build_item_price_scope_query(
    *,
    scope: ItemPriceScope,
    valid_from: date | None = None,
    price_list_rate: float | int | None = None,
    for_update: bool = False,
    limit: int | None = 1,
) -> Any:
    """Build one bounded query for the complete Item Price scope."""
    item_price = frappe.qb.DocType("Item Price")
    customer = _normalise_scope_value(scope.customer)
    supplier = _normalise_scope_value(scope.supplier)
    batch_no = _normalise_scope_value(scope.batch_no)

    query = (
        frappe.qb.from_(item_price)
        .select(
            item_price.name,
            item_price.price_list_rate,
            item_price.item_name,
            item_price.packing_unit,
        )
        .where(
            (item_price.item_code == scope.item_code)
            & (item_price.price_list == scope.price_list)
            & (item_price.currency == scope.currency)
            & (item_price.uom == scope.stock_uom)
            & (IfNull(item_price.customer, "") == customer)
            & (IfNull(item_price.supplier, "") == supplier)
            & (IfNull(item_price.batch_no, "") == batch_no)
        )
    )

    if scope.packing_unit is not None:
        query = query.where(item_price.packing_unit == cint(scope.packing_unit))
    if valid_from is not None:
        query = query.where(item_price.valid_from == valid_from)
    if price_list_rate is not None:
        query = query.where(item_price.price_list_rate == price_list_rate)
    query = query.orderby(IfNull(item_price.packing_unit, 0), order=frappe.qb.desc)
    query = query.orderby(item_price.name)
    if for_update:
        query = query.for_update()

    return query.limit(limit) if limit is not None else query


def _find_applicable_item_price(
    *,
    scope: ItemPriceScope,
    valid_from: date | None = None,
    price_list_rate: float | int | None = None,
    quantity: float | int | None = None,
    for_update: bool = False,
) -> ItemPriceRecord | None:
    """Select the most specific Item Price applicable to the row quantity."""
    query = _build_item_price_scope_query(
        scope=scope,
        valid_from=valid_from,
        price_list_rate=price_list_rate,
        for_update=for_update,
        limit=MAX_ITEM_PRICE_SCOPE_CANDIDATES + 1,
    )
    candidates = cast("list[ItemPriceRecord]", query.run(as_dict=True))
    if len(candidates) > MAX_ITEM_PRICE_SCOPE_CANDIDATES:
        frappe.throw(
            _(
                "Too many Item Prices match this scope. Refine the item, price "
                "list, party, batch, or date before continuing."
            )
        )

    return _select_applicable_item_price(candidates, quantity)


def _select_applicable_item_price(
    candidates: list[ItemPriceRecord],
    quantity: float | int | None,
) -> ItemPriceRecord | None:
    """Choose a deterministic generic or quantity-compatible packing price."""
    requested_quantity = flt(quantity)
    ordered_candidates = sorted(candidates, key=_item_price_priority)
    for candidate in ordered_candidates:
        packing_unit = cint(candidate.get("packing_unit") or 0)
        if packing_unit <= 0:
            return candidate
        if requested_quantity and requested_quantity % packing_unit == 0:
            return candidate

    return None


def _lock_item_price_scope(request: RateMismatchCreateRequest) -> None:
    """Serialise app-level creates and updates for one Item Price item scope."""
    item = frappe.qb.DocType("Item")
    (
        frappe.qb.from_(item)
        .select(item.name)
        .where(item.name == request.item_code)
        .for_update()
        .run()
    )


def _normalise_scope_value(value: object | None) -> str:
    """Use one representation for nullable Link/Data Item Price scope fields."""
    return str(value).strip() if value else ""


def _duplicate_valid_from_message(
    *,
    item_code: str,
    item_name: str | None = None,
    valid_from: date,
    customer: str | None = None,
    supplier: str | None = None,
) -> str:
    """Build the validation message for same-date duplicate Item Prices."""
    party_label = _("Customer") if customer else _("Supplier")
    party_value = customer or supplier or _("the selected party")
    party_name = _get_party_display_name(customer=customer, supplier=supplier)
    party_display = _format_id_and_name(party_value, party_name)
    item_display = _format_id_and_name(item_code, item_name)

    return "".join(
        [
            _(
                "<p>An Item Price record already exists with the same Valid From date.</p>"
            ),
            "<ul>",
            _("<li><strong>Item:</strong> {0}</li>").format(item_display),
            _("<li><strong>{0}:</strong> {1}</li>").format(party_label, party_display),
            _("<li><strong>Valid From:</strong> {0}</li>").format(
                escape_html(formatdate(valid_from))
            ),
            "</ul>",
        ]
    )


def _get_party_display_name(
    *,
    customer: str | None = None,
    supplier: str | None = None,
) -> str | None:
    """Return the party display name for duplicate validation messages."""
    if customer:
        return frappe.db.get_value("Customer", customer, "customer_name")

    if supplier:
        return frappe.db.get_value("Supplier", supplier, "supplier_name")

    return None


def _format_id_and_name(identifier: str, display_name: str | None = None) -> str:
    """Format an ID and display name for user-facing validation details."""
    if display_name and display_name != identifier:
        return _("{0}: {1}").format(escape_html(identifier), escape_html(display_name))

    return escape_html(identifier)


def _throw_duplicate_valid_from_error(
    *,
    item_code: str,
    item_name: str | None = None,
    valid_from: date,
    customer: str | None = None,
    supplier: str | None = None,
) -> None:
    """Throw the same-date duplicate validation with an error title."""
    frappe.throw(
        _duplicate_valid_from_message(
            item_code=item_code,
            item_name=item_name,
            valid_from=valid_from,
            customer=customer,
            supplier=supplier,
        ),
        title=_("Error"),
    )


def _validate_rate_mismatch_behaviour(doc: Document) -> None:
    """Reject conflicting same-date Item Price behaviours."""
    rows = getattr(doc, SETTINGS_TABLE_FIELD, None) or []
    for row in rows:
        update_item_price = _get_row_value(row, "update_item_price")
        throw_exception = _get_row_value(row, "throw_exception")

        if cint(update_item_price) and cint(throw_exception):
            frappe.throw(
                _(
                    "Select either Update Item Price or Block Same-Date Duplicate "
                    "for {0}, not both."
                ).format(_get_row_value(row, "doctype_name"))
            )


def _get_row_value(
    row: object,
    fieldname: str,
    default: RowValue = None,
) -> RowValue:
    """Return a child-row value from either a Document-like row or a dict."""
    if isinstance(row, dict):
        return cast("RowValue", row.get(fieldname, default))

    return cast("RowValue", getattr(row, fieldname, default))


def _serialise_doctype_rows(doc: Document) -> list[tuple[str, int, int, int]]:
    """Serialise doctype rows for change detection during migrate."""
    rows = getattr(doc, SETTINGS_TABLE_FIELD, None) or []
    return [
        (
            str(_get_row_value(row, "doctype_name")),
            cint(_get_row_value(row, "enabled", 0)),
            cint(_get_row_value(row, "update_item_price", 0)),
            cint(_get_row_value(row, "throw_exception", 0)),
        )
        for row in rows
        if _get_row_value(row, "doctype_name")
    ]


def _item_price_priority(candidate: ItemPriceRecord) -> tuple[int, str]:
    return -cint(candidate.get("packing_unit") or 0), candidate.get("name") or ""
