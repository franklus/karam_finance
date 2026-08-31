{
  const { getDocumentScope, getSelectedPriceList, suppressedRows } =
    window.karamItemPriceMismatch;

  async function updateRowFields(cdt, cdn, fieldValues) {
    suppressedRows.add(cdn);
    try {
      const updates = Object.entries(fieldValues).filter(
        ([fieldname]) => locals[cdt][cdn] && Object.hasOwn(locals[cdt][cdn], fieldname)
      );
      await updates.reduce(
        (promise, [fieldname, value]) =>
          promise.then(() => frappe.model.set_value(cdt, cdn, fieldname, value)),
        Promise.resolve()
      );
    } finally {
      window.setTimeout(() => suppressedRows.delete(cdn), 0);
    }
  }

  async function applyRowPricing(frm, cdt, cdn, rate) {
    const targetRate = flt(rate);
    await updateRowFields(cdt, cdn, {
      price_list_rate: targetRate,
      rate: targetRate,
      discount_percentage: 0,
      discount_amount: 0,
      margin_type: "",
      margin_rate_or_amount: 0,
      rate_with_margin: 0,
      base_rate_with_margin: 0
    });
    frm.refresh_field("items");
    frm.script_manager.trigger("price_list_rate", cdt, cdn);
    if (typeof frm.cscript?.calculate_taxes_and_totals === "function") {
      frm.cscript.calculate_taxes_and_totals();
    }
  }

  function resetRowPricing(frm, cdt, cdn, rate) {
    return applyRowPricing(frm, cdt, cdn, rate);
  }

  function revertRowPricing(frm, cdt, cdn, rate) {
    return applyRowPricing(frm, cdt, cdn, rate);
  }

  const getErrorText = (error) =>
    Object.values({
      message: error?.message,
      exception: error?.exception,
      exc: error?.exc,
      serverMessages: error?._server_messages,
      responseMessages: error?.responseJSON?._server_messages,
      responseException: error?.responseJSON?.exception,
      responseExc: error?.responseJSON?.exc,
      responseText: error?.responseText
    })
      .filter(Boolean)
      .join(" ");

  const isDuplicateValidFromError = (error) =>
    getErrorText(error).includes("with the same Valid From date");

  function runAfterMessageDialogDismissal(callback, attempt = 0) {
    const dialog = frappe.msg_dialog;
    if (!dialog && attempt < 5)
      return window.setTimeout(
        () => runAfterMessageDialogDismissal(callback, attempt + 1),
        0
      );
    if (!dialog || dialog.$wrapper?.is?.(":visible") === false)
      return window.setTimeout(callback, 0);
    const previousOnhide = dialog.custom_onhide;
    dialog.custom_onhide = () => {
      if (typeof previousOnhide === "function") previousOnhide();
      dialog.custom_onhide = previousOnhide;
      callback();
    };
  }

  function getItemPriceRequest(frm, row, priceList) {
    return {
      item_code: row.item_code,
      price_list: priceList,
      currency: frm.doc.currency,
      stock_uom: row.stock_uom || row.uom,
      batch_no: row.batch_no || null,
      qty: row.qty || 0,
      doctype: frm.doctype,
      ...getDocumentScope(frm)
    };
  }

  const CONTEXT_METHOD =
    "karam_finance.karam_general.utils.item_price_on_rate_mismatch.get_item_price_mismatch_context_api";
  const CREATE_METHOD =
    "karam_finance.karam_general.utils.item_price_on_rate_mismatch.create_item_price_for_rate_mismatch";
  const callItemPriceEndpoint = async (method, args) =>
    (await frappe.call({ method, args })).message || {};

  const getItemPriceMismatchContext = (frm, row, priceList) =>
    callItemPriceEndpoint(CONTEXT_METHOD, {
      ...getItemPriceRequest(frm, row, priceList),
      price_list_rate: row.price_list_rate
    });

  function createItemPriceFromMismatch(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    const args = {
      ...getItemPriceRequest(frm, row, getSelectedPriceList(frm)),
      conversion_factor: row.conversion_factor || 1,
      price_list_rate: row.price_list_rate,
      rate: row.rate
    };
    return callItemPriceEndpoint(CREATE_METHOD, args);
  }

  function showItemPriceSuccessAlert(frm, result, rate) {
    const action = result.created
      ? "created"
      : result.updated
        ? "updated"
        : result.reused
          ? "used"
          : null;
    if (!action) return;
    const messages = {
      created: __("Item Price created for rate {0}."),
      updated: __("Item Price updated for rate {0}."),
      used: __("Existing Item Price used for rate {0}.")
    };
    frappe.show_alert({
      message: __(messages[action], [format_currency(rate, frm.doc.currency)]),
      indicator: "green"
    });
  }

  function getPartyDetails(frm) {
    const party = frm.doc.supplier
      ? ["Supplier", frm.doc.supplier, frm.doc.supplier_name]
      : frm.doc.customer
        ? ["Customer", frm.doc.customer, frm.doc.customer_name]
        : ["Party"];
    return {
      label: __(party[0]),
      value: party.slice(1).filter(Boolean).join(": ") || __("Not set")
    };
  }

  function getPromptDetails(frm, row, priceList, rateLine, validFrom) {
    const party = getPartyDetails(frm);
    const details = [
      [__("Price List"), priceList],
      [__("Rate"), rateLine],
      [__("UOM"), row.stock_uom || row.uom || __("Unknown UOM")],
      [__("Batch No"), row.batch_no || __("Global")],
      [__("Quantity"), row.qty || 0],
      [party.label, party.value],
      [__("Valid From"), validFrom || ""]
    ];
    const rows = details.map(
      ([label, value]) =>
        `<li><strong>${label}:</strong> ${frappe.utils.escape_html(value)}</li>`
    );
    return `<ul>${rows.join("")}</ul>`;
  }

  function getItemPriceLink(itemPriceName) {
    const href = `/app/item-price/${encodeURIComponent(itemPriceName)}`;
    return [
      `<a href="${href}" target="_blank">`,
      frappe.utils.escape_html(itemPriceName),
      "</a>"
    ].join("");
  }

  function buildPromptAction(color, action, description, itemCode, details) {
    return [
      `<p><strong style="color: var(--${color}-600);">${__(action)}</strong>`,
      description,
      `${itemCode}</p>${details}`
    ].join(" ");
  }

  function getPromptMessage(frm, row, priceList, context, confirmedRate) {
    const itemCode = frappe.utils.escape_html(row.item_code);
    const formattedRate = format_currency(confirmedRate, frm.doc.currency);
    const details = (rateLine) =>
      getPromptDetails(frm, row, priceList, rateLine, context.valid_from);
    if (context.item_price_name && context.update_item_price) {
      if (flt(context.item_price_rate) === confirmedRate) {
        return buildPromptAction(
          "blue",
          "Use",
          __("the existing Item Price for Item"),
          itemCode,
          details(formattedRate)
        );
      }
      const previousRate = format_currency(context.item_price_rate, frm.doc.currency);
      const link = getItemPriceLink(context.item_price_name);
      return buildPromptAction(
        "blue",
        "Update",
        `${link} ${__("Item Price for Item")}`,
        itemCode,
        details(`${previousRate} &rarr; <strong>${formattedRate}</strong>`)
      );
    }
    return buildPromptAction(
      "green",
      "Create",
      __("a new Item Price for Item"),
      itemCode,
      details(formattedRate)
    );
  }

  Object.assign(window.karamItemPriceMismatch, {
    createItemPriceFromMismatch,
    getItemPriceMismatchContext,
    getPromptMessage,
    isDuplicateValidFromError,
    resetRowPricing,
    revertRowPricing,
    runAfterMessageDialogDismissal,
    showItemPriceSuccessAlert
  });
}
