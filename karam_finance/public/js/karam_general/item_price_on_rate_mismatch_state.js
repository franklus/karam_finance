{
  const SUPPORTED_PARENT_DOCTYPES = new Set([
    "Quotation",
    "Sales Order",
    "Delivery Note",
    "Sales Invoice",
    "POS Invoice",
    "Purchase Order",
    "Purchase Receipt",
    "Purchase Invoice"
  ]);
  const SUPPORTED_CHILD_DOCTYPES = [...SUPPORTED_PARENT_DOCTYPES].map(
    (doctype) => `${doctype} Item`
  );
  const promptStateByRow = new Map();
  const pendingPromptStateByRow = new Map();
  const activeFormsByDocument = new Map();
  const suppressedRows = new Set();
  const pricingAdjustmentRows = new Set();
  const pricingAdjustmentTimersByRow = new Map();
  const userPricingFieldByRow = new Map();
  const userPricingFieldTimersByRow = new Map();
  const priceListRateByRow = new Map();
  const previousPriceListRateByRow = new Map();
  const PRICING_ADJUSTMENT_FIELDS = ["discount_percentage", "discount_amount"];
  const USER_PRICING_FIELDS = new Set([
    "price_list_rate",
    ...PRICING_ADJUSTMENT_FIELDS
  ]);

  const getPromptKey = (frm, row, priceList) =>
    [
      frm.doctype,
      row.name,
      row.item_code,
      priceList,
      flt(row.rate),
      flt(row.price_list_rate)
    ].join("::");

  const getSelectedPriceList = (frm) =>
    frm.doc.selling_price_list || frm.doc.buying_price_list || "";

  const getDocumentScope = (frm) => ({
    transaction_date: frm.doc.transaction_date || null,
    posting_date: frm.doc.posting_date || null,
    customer: frm.doc.customer || null,
    supplier: frm.doc.supplier || null
  });

  const getFormKey = (doctype, docname) => `${doctype || ""}::${docname || ""}`;

  function rememberActiveForm(frm) {
    if (!frm?.doctype || !SUPPORTED_PARENT_DOCTYPES.has(frm.doctype)) return;
    activeFormsByDocument.set(
      getFormKey(frm.doctype, frm.docname || frm.doc?.name),
      frm
    );
    (frm.doc?.items || []).forEach(
      (row) => row?.name && priceListRateByRow.set(row.name, flt(row.price_list_rate))
    );
  }

  const getChildDoctype = (cdn) =>
    SUPPORTED_CHILD_DOCTYPES.find((doctype) => locals[doctype]?.[cdn]);

  const getFormForChildRow = (row) =>
    row
      ? activeFormsByDocument.get(getFormKey(row.parenttype, row.parent)) || null
      : null;

  const shouldPrompt = (frm, row) =>
    Boolean(
      frm &&
      row &&
      SUPPORTED_PARENT_DOCTYPES.has(frm.doctype) &&
      row.item_code &&
      flt(row.price_list_rate) &&
      getSelectedPriceList(frm)
    );

  function schedulePromptForRateMismatch(
    frm,
    cdt,
    cdn,
    { priceListRateChanged = false } = {}
  ) {
    if (!frm || !cdt || !cdn || suppressedRows.has(cdn)) return;
    if (pricingAdjustmentRows.has(cdn) && !priceListRateChanged) return;
    window.setTimeout(
      () => window.karamItemPriceMismatch.maybePromptForRateMismatch(frm, cdt, cdn),
      0
    );
  }

  const getFieldObject = (browserEvent) =>
    browserEvent.target?.closest?.(".frappe-control[data-fieldname]")?.fieldobj;
  const getGridRowName = (browserEvent) =>
    browserEvent.target?.closest?.(".grid-row[data-name]")?.dataset?.name;

  function getEventDetails(browserEvent) {
    const fieldObject = getFieldObject(browserEvent);
    const fieldname =
      fieldObject?.df?.fieldname ||
      browserEvent.target?.closest?.("[data-fieldname]")?.dataset?.fieldname;
    return {
      fieldObject,
      fieldname,
      cdn: fieldObject?.docname || getGridRowName(browserEvent)
    };
  }

  function clearState(set, timers, cdn) {
    const timer = timers.get(cdn);
    if (timer) window.clearTimeout(timer);
    timers.delete(cdn);
    set.delete(cdn);
  }

  const clearPricingAdjustment = (cdn) =>
    clearState(pricingAdjustmentRows, pricingAdjustmentTimersByRow, cdn);

  const clearUserPricingField = (cdn) =>
    clearState(userPricingFieldByRow, userPricingFieldTimersByRow, cdn);

  function markPricingAdjustment(cdn) {
    if (!cdn) return;
    clearPricingAdjustment(cdn);
    pricingAdjustmentRows.add(cdn);
    pricingAdjustmentTimersByRow.set(
      cdn,
      window.setTimeout(() => clearPricingAdjustment(cdn), 500)
    );
  }

  function consumePriceListRateChange(row) {
    if (!row?.name) return false;
    const currentRate = flt(row.price_list_rate);
    const previousRate = priceListRateByRow.get(row.name);
    priceListRateByRow.set(row.name, currentRate);
    const changed = previousRate !== undefined && previousRate !== currentRate;
    if (changed) previousPriceListRateByRow.set(row.name, previousRate);
    return changed;
  }

  function rememberUserPricingField(browserEvent) {
    const { fieldname, cdn } = getEventDetails(browserEvent);
    if (!USER_PRICING_FIELDS.has(fieldname) || !cdn) return;
    clearUserPricingField(cdn);
    userPricingFieldByRow.set(cdn, fieldname);
    userPricingFieldTimersByRow.set(
      cdn,
      window.setTimeout(() => clearUserPricingField(cdn), 1000)
    );
    if (fieldname === "price_list_rate") clearPricingAdjustment(cdn);
    if (PRICING_ADJUSTMENT_FIELDS.includes(fieldname)) markPricingAdjustment(cdn);
  }

  Object.assign((window.karamItemPriceMismatch ||= {}), {
    PRICING_ADJUSTMENT_FIELDS,
    SUPPORTED_CHILD_DOCTYPES,
    SUPPORTED_PARENT_DOCTYPES,
    clearPricingAdjustment,
    consumePriceListRateChange,
    getChildDoctype,
    getDocumentScope,
    getEventDetails,
    getFormForChildRow,
    getPromptKey,
    getSelectedPriceList,
    markPricingAdjustment,
    pendingPromptStateByRow,
    previousPriceListRateByRow,
    promptStateByRow,
    rememberActiveForm,
    rememberUserPricingField,
    schedulePromptForRateMismatch,
    shouldPrompt,
    suppressedRows,
    userPricingFieldByRow
  });
}
