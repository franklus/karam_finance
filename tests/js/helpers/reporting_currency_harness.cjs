const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.join(
    __dirname,
    "../../../karam_finance/reporting_currency/doctype/reporting_currency_settings/reporting_currency_settings.js"
  ),
  "utf8"
);

function realtimeFor(state) {
  return {
    on(event, handler) {
      state.handlers.set(event, handler);
    },
    off(event, handler) {
      assert.equal(state.handlers.get(event), handler);
      state.handlers.delete(event);
      state.trace.push(`off:${event}`);
    }
  };
}

function formFor(state, button) {
  return {
    doc: {
      reporting_currency: "USD",
      modified: "2026-09-10 10:00:00",
      __onload: {
        has_reporting_entries: true,
        saved_reporting_currency: "USD"
      }
    },
    reload_doc: () => state.trace.push("reload"),
    add_custom_button(label, handler) {
      state.buttons.set(label, handler);
      return button;
    }
  };
}

function loadAsset(state, asset, ready) {
  if (asset === "/assets/karam_finance/js/reporting_currency_sync_status.js") {
    const pollingSource = fs.readFileSync(
      path.join(
        __dirname,
        "../../../karam_finance/public/js/reporting_currency_sync_status.js"
      ),
      "utf8"
    );
    vm.runInContext(pollingSource, state.context);
  }
  ready();
}

function createHarness(startImmediately = true) {
  const state = { handlers: new Map(), buttons: new Map(), trace: [], request: null };
  const frappe = {
    utils: {
      escape_html: (value) =>
        String(value).replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    },
    ui: {
      form: {
        on(_doctype, events) {
          state.events = events;
        }
      }
    },
    prompt(fields, submit) {
      state.promptFields = fields;
      state.prompt = submit;
    },
    confirm(message, accept, cancel) {
      state.confirm = { message, accept, cancel };
    },
    require(asset, ready) {
      loadAsset(state, asset, ready);
    },
    realtime: realtimeFor(state),
    call(request) {
      state.request = request;
    },
    show_progress(_title, current, total) {
      state.trace.push(`progress:${current}/${total}`);
    },
    hide_progress() {
      state.trace.push("hide");
    },
    msgprint(message) {
      state.trace.push(`message:${message.indicator}`);
    },
    show_alert(message) {
      state.trace.push(`alert:${message.indicator}`);
    }
  };
  state.frappe = frappe;
  state.timers = new Map();
  state.context = createRuntimeContext(frappe, state);
  vm.runInContext(source, state.context);
  const button = {
    addClass() {},
    prop(name, value) {
      assert.equal(name, "disabled");
      state.trace.push(`button:${value}`);
    }
  };
  state.frm = formFor(state, button);
  if (startImmediately) {
    state.context.queue_reporting_currency_sync(state.frm, button);
  }
  return state;
}

function createRuntimeContext(frappe, state) {
  let timerId = 0;
  return vm.createContext({
    frappe,
    window: {},
    URLSearchParams,
    setTimeout(callback) {
      timerId += 1;
      state.timers.set(timerId, callback);
      return timerId;
    },
    clearTimeout(id) {
      state.timers.delete(id);
    },
    __(message, values = []) {
      return message.replace(/\{(\d+)\}/g, (_match, index) => String(values[index]));
    }
  });
}

module.exports = { createHarness };
