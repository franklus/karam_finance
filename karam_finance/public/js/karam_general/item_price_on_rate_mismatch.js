{
  const {
    PRICING_ADJUSTMENT_FIELDS,
    SUPPORTED_CHILD_DOCTYPES,
    SUPPORTED_PARENT_DOCTYPES,
    clearPricingAdjustment,
    consumePriceListRateChange,
    createItemPriceFromMismatch,
    getChildDoctype,
    getEventDetails,
    getFormForChildRow,
    getItemPriceMismatchContext,
    getPromptKey,
    getPromptMessage,
    getSelectedPriceList,
    isDuplicateValidFromError,
    markPricingAdjustment,
    pendingPromptStateByRow,
    previousPriceListRateByRow,
    promptStateByRow,
    rememberActiveForm,
    rememberUserPricingField,
    resetRowPricing,
    revertRowPricing,
    runAfterMessageDialogDismissal,
    schedulePromptForRateMismatch,
    shouldPrompt,
    showItemPriceSuccessAlert,
    userPricingFieldByRow
  } = window.karamItemPriceMismatch;

  const clearPromptState = (row) => {
    previousPriceListRateByRow.delete(row.name);
    promptStateByRow.delete(row.name);
  };
  const clearPendingPromptState = (row) => {
    promptStateByRow.delete(row.name);
    pendingPromptStateByRow.delete(row.name);
  };
  const showRevertAlert = () =>
    frappe.show_alert({
      message: __("Rate reverted to the existing Item Price value."),
      indicator: "orange"
    });

  function promptToCreateItemPrice(frm, cdt, cdn, context) {
    const row = locals[cdt][cdn];
    const priceList = getSelectedPriceList(frm);
    const confirmedRate = flt(row.price_list_rate);
    const originalRate = previousPriceListRateByRow.get(row.name) ?? confirmedRate;
    const promptKey = getPromptKey(frm, row, priceList);
    if (promptStateByRow.get(row.name) === promptKey) return;
    promptStateByRow.set(row.name, promptKey);
    frappe.confirm(
      getPromptMessage(frm, row, priceList, context, confirmedRate),
      async () => {
        try {
          const result = await createItemPriceFromMismatch(frm, cdt, cdn);
          await resetRowPricing(frm, cdt, cdn, confirmedRate);
          await frm.save();
          showItemPriceSuccessAlert(frm, result, confirmedRate);
          clearPromptState(row);
        } catch (error) {
          clearPromptState(row);
          throw error;
        }
      },
      async () => {
        try {
          await revertRowPricing(frm, cdt, cdn, originalRate);
          showRevertAlert();
          clearPromptState(row);
        } catch (error) {
          clearPromptState(row);
          throw error;
        }
      }
    );
  }

  function handlePromptError(error, frm, cdt, cdn, row) {
    clearPendingPromptState(row);
    if (isDuplicateValidFromError(error)) {
      const originalRate =
        previousPriceListRateByRow.get(row.name) ?? row.price_list_rate;
      runAfterMessageDialogDismissal(async () => {
        await revertRowPricing(frm, cdt, cdn, originalRate);
        showRevertAlert();
        clearPromptState(row);
      });
    } else if (!error || !error.exc_type) {
      frappe.show_alert({
        message: __("Unable to prepare the Item Price prompt right now."),
        indicator: "orange"
      });
    }
  }

  async function maybePromptForRateMismatch(frm, cdt, cdn) {
    const row = locals[cdt][cdn];
    if (!shouldPrompt(frm, row)) {
      if (row?.name) clearPendingPromptState(row);
      return;
    }
    const promptKey = getPromptKey(frm, row, getSelectedPriceList(frm));
    if (
      promptStateByRow.get(row.name) === promptKey ||
      pendingPromptStateByRow.get(row.name) === promptKey
    )
      return;
    pendingPromptStateByRow.set(row.name, promptKey);
    try {
      const context = await getItemPriceMismatchContext(
        frm,
        row,
        getSelectedPriceList(frm)
      );
      pendingPromptStateByRow.delete(row.name);
      if (context.enabled && context.valid_from) {
        promptToCreateItemPrice(frm, cdt, cdn, context);
      }
    } catch (error) {
      handlePromptError(error, frm, cdt, cdn, row);
    }
  }

  function handlePriceListRateChange(browserEvent) {
    const { fieldObject, fieldname, cdn } = getEventDetails(browserEvent);
    if (fieldname !== "price_list_rate") return;
    const cdt = fieldObject?.doctype || (cdn ? getChildDoctype(cdn) : null);
    const frm =
      fieldObject?.frm || (cdt ? getFormForChildRow(locals[cdt]?.[cdn]) : null);
    clearPricingAdjustment(cdn);
    schedulePromptForRateMismatch(frm, cdt, cdn);
  }

  function handlePriceListRateModelChange(cdt, row) {
    if (!row?.name) return;
    const changed = consumePriceListRateChange(row);
    if (userPricingFieldByRow.get(row.name) !== "price_list_rate") return;
    clearPricingAdjustment(row.name);
    schedulePromptForRateMismatch(getFormForChildRow(row), cdt, row.name, {
      priceListRateChanged: changed
    });
  }

  function registerChildHandlers(doctype) {
    frappe.model.on(doctype, "price_list_rate", (_fieldname, _value, row) =>
      handlePriceListRateModelChange(doctype, row)
    );
    PRICING_ADJUSTMENT_FIELDS.forEach((fieldname) =>
      frappe.model.on(doctype, fieldname, (_fieldname, _value, row) =>
        markPricingAdjustment(row?.name)
      )
    );
    frappe.ui.form.on(doctype, {
      price_list_rate(frm, cdt, cdn) {
        const row = locals[cdt]?.[cdn];
        const changed = consumePriceListRateChange(row);
        rememberActiveForm(frm);
        if (userPricingFieldByRow.get(cdn) !== "price_list_rate") return;
        clearPricingAdjustment(cdn);
        schedulePromptForRateMismatch(frm, cdt, cdn, { priceListRateChanged: changed });
      }
    });
  }

  function initItemPriceOnRateMismatch() {
    SUPPORTED_CHILD_DOCTYPES.forEach(registerChildHandlers);
    SUPPORTED_PARENT_DOCTYPES.forEach((doctype) =>
      frappe.ui.form.on(doctype, {
        refresh: rememberActiveForm,
        onload_post_render: rememberActiveForm
      })
    );
    document.addEventListener("change", handlePriceListRateChange, true);
    document.addEventListener("input", rememberUserPricingField, true);
    document.addEventListener("change", rememberUserPricingField, true);
  }

  Object.assign(window.karamItemPriceMismatch, { maybePromptForRateMismatch });
  initItemPriceOnRateMismatch();
}
