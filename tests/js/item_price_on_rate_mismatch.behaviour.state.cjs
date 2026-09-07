function messageDialog() {
  return { custom_onhide: null, $wrapper: { is: () => true } };
}

function createHarnessState(harnessOptions) {
  return {
    handlers: {},
    listeners: {},
    modelHandlers: {},
    timers: [],
    calls: [],
    confirms: [],
    alerts: [],
    messages: [],
    setValues: [],
    nextTimerId: 1,
    msgDialog: harnessOptions.withMessageDialog === false ? null : messageDialog(),
    confirmReject: null,
    timerInstalledDialog: false,
    callImplementation: () =>
      Promise.resolve({ message: { enabled: true, valid_from: "2026-04-30" } }),
    cdn: "ROW-1",
    childDoctype: "Purchase Order Item",
    row: {
      name: "ROW-1",
      parent: "PUR-ORD-2026-00036",
      parenttype: "Purchase Order",
      item_code: "ITM02221",
      rate: 7.1,
      price_list_rate: 6.56,
      stock_uom: "Bag (50Kg)",
      conversion_factor: 1,
      qty: 12,
      batch_no: "BATCH-001"
    }
  };
}

module.exports = { messageDialog, createHarnessState };
