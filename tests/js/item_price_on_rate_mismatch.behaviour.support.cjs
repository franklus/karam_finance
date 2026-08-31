const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SCRIPT_PATHS = [
  "item_price_on_rate_mismatch_state.js",
  "item_price_on_rate_mismatch_prompt.js",
  "item_price_on_rate_mismatch.js"
].map((filename) =>
  path.resolve(__dirname, "../../karam_finance/public/js/karam_general", filename)
);

function createClosestTarget(fieldname, cdn) {
  return {
    closest(selector) {
      if (selector === "[data-fieldname]") return { dataset: { fieldname } };
      if (selector === ".grid-row[data-name]") return { dataset: { name: cdn } };
      return null;
    }
  };
}

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

function createHarnessContext(harnessOptions, state) {
  const runtime = state;
  const { cdn, childDoctype, row } = state;
  const context = {
    Object,
    Promise,
    console,
    locals: { [childDoctype]: { [cdn]: row } },
    window: {
      setTimeout(callback, delay) {
        const id = runtime.nextTimerId;
        runtime.nextTimerId += 1;
        runtime.timers.push({
          id,
          callback() {
            if (
              harnessOptions.installMessageDialogOnTimer &&
              !runtime.timerInstalledDialog
            ) {
              runtime.timerInstalledDialog = true;
              runtime.msgDialog = messageDialog();
            }
            callback();
          },
          delay
        });
        return id;
      },
      clearTimeout(timerId) {
        const timer = runtime.timers.find(({ id }) => id === timerId);
        if (timer) timer.callback = null;
      }
    },
    document: {
      addEventListener(name, callback) {
        (runtime.listeners[name] ||= []).push(callback);
      }
    },
    frappe: {
      call(callOptions) {
        runtime.calls.push(callOptions);
        return runtime.callImplementation(callOptions);
      },
      confirm(message, _accept, reject) {
        runtime.confirms.push(message);
        runtime.confirmReject = reject;
      },
      model: {
        on(doctype, fieldname, handler) {
          (runtime.modelHandlers[doctype] ||= {})[fieldname] = handler;
        },
        set_value(doctype, docname, fieldname, value) {
          runtime.setValues.push({ doctype, docname, fieldname, value });
          context.locals[doctype][docname][fieldname] = value;
          return Promise.resolve();
        }
      },
      show_alert(alert) {
        runtime.alerts.push(alert);
      },
      msgprint(message) {
        runtime.messages.push(message);
      },
      get msg_dialog() {
        return runtime.msgDialog;
      },
      set msg_dialog(value) {
        runtime.msgDialog = value;
      },
      ui: {
        form: {
          on(doctype, formHandlers) {
            runtime.handlers[doctype] = formHandlers;
          }
        }
      },
      utils: { escape_html: String }
    },
    __(message) {
      return message;
    },
    flt(value) {
      return Number.parseFloat(value) || 0;
    },
    format_currency(value, currency) {
      return `${currency} ${Number.parseFloat(value).toFixed(4)}`;
    }
  };
  return context;
}

function createHarnessForm(harnessOptions, row) {
  return {
    doctype: "Purchase Order",
    docname: "PUR-ORD-2026-00036",
    doc: {
      name: "PUR-ORD-2026-00036",
      buying_price_list: "Standard Buying",
      currency: "USD",
      supplier: "SUP0085",
      supplier_name: "Sodamco SAL",
      transaction_date: "2026-04-30",
      items: harnessOptions.seedInitialPriceListRate === false ? [] : [row]
    },
    refresh_field() {},
    script_manager: { trigger() {} },
    cscript: {},
    save() {
      return Promise.resolve();
    }
  };
}

function createHarness(harnessOptions = {}) {
  const state = createHarnessState(harnessOptions);
  const context = createHarnessContext(harnessOptions, state);
  vm.createContext(context);
  SCRIPT_PATHS.forEach((scriptPath) =>
    vm.runInContext(fs.readFileSync(scriptPath, "utf8"), context)
  );
  const currentForm = createHarnessForm(harnessOptions, state.row);
  state.handlers["Purchase Order"].refresh(currentForm);

  function dispatch(name, fieldname) {
    state.listeners[name]?.forEach((callback) =>
      callback({ target: createClosestTarget(fieldname, state.cdn) })
    );
  }

  function dispatchModelChange(fieldname) {
    state.modelHandlers[state.childDoctype]?.[fieldname]?.(
      fieldname,
      state.row[fieldname],
      state.row
    );
  }

  function flushTimerBatch() {
    if (!state.timers.length) return Promise.resolve();
    const batch = state.timers.splice(0);
    batch.forEach(({ callback }) => callback?.());
    return Promise.resolve().then(flushTimerBatch);
  }

  function settleMicrotasks(count) {
    return count ? Promise.resolve().then(() => settleMicrotasks(count - 1)) : null;
  }

  async function flushTimers() {
    await flushTimerBatch();
    await settleMicrotasks(12);
  }

  return {
    alerts: state.alerts,
    calls: state.calls,
    cdn: state.cdn,
    childDoctype: state.childDoctype,
    confirms: state.confirms,
    dismissMessageDialog: async () => {
      await context.frappe.msg_dialog?.custom_onhide?.();
      await flushTimers();
    },
    rejectPrompt: async () => {
      await state.confirmReject?.();
      await flushTimers();
    },
    dispatchPricingFieldInput: (fieldname) => dispatch("input", fieldname),
    dispatchPricingFieldChange: (fieldname) => dispatch("change", fieldname),
    dispatchPriceListRateChange: () => dispatch("change", "price_list_rate"),
    dispatchPriceListRateModelChange: () => dispatchModelChange("price_list_rate"),
    dispatchModelChange,
    flushTimers,
    form: () => currentForm,
    handlers: state.handlers[state.childDoctype],
    messages: state.messages,
    row: state.row,
    setValues: state.setValues,
    setCallImplementation(implementation) {
      state.callImplementation = implementation;
    }
  };
}

module.exports = { createHarness };
