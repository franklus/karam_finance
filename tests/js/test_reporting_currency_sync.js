const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(
  path.join(
    __dirname,
    "../../karam_finance/reporting_currency/doctype/reporting_currency_settings/reporting_currency_settings.js"
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

function createHarness() {
  const state = { handlers: new Map(), trace: [], request: null };
  const frappe = {
    ui: { form: { on() {} } },
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
  const context = vm.createContext({ frappe, window: {}, URLSearchParams, __: String });
  vm.runInContext(source, context);
  const button = {
    prop(name, value) {
      assert.equal(name, "disabled");
      state.trace.push(`button:${value}`);
    }
  };
  context.queue_reporting_currency_sync(
    { reload_doc: () => state.trace.push("reload") },
    button
  );
  return state;
}

function startSync() {
  const state = createHarness();
  state.request.callback({
    message: { progress_event: "progress", done_event: "done" }
  });
  state.trace.length = 0;
  return state;
}

test("sync completion removes subscriptions before notifying and reloading", () => {
  const state = startSync();
  state.handlers.get("done")({ status: "success", inserted: 2 });
  assert.deepEqual(state.trace, [
    "off:progress",
    "off:done",
    "hide",
    "alert:green",
    "reload",
    "button:false"
  ]);
  assert.equal(state.handlers.size, 0);
});

test("partial sync success reloads after the DOE warning", () => {
  const state = startSync();
  state.handlers.get("done")({ status: "partial_success", inserted: 2 });
  assert.deepEqual(state.trace, [
    "off:progress",
    "off:done",
    "hide",
    "message:orange",
    "alert:orange",
    "reload",
    "button:false"
  ]);
});

test("sync failure restores the button without reloading", () => {
  const state = startSync();
  state.handlers.get("done")({ status: "error" });
  assert.deepEqual(state.trace, [
    "off:progress",
    "off:done",
    "hide",
    "message:red",
    "button:false"
  ]);
});

test("invalid queue responses and request failures restore the button", () => {
  const invalid = createHarness();
  invalid.request.callback({ message: {} });
  assert.deepEqual(invalid.trace, ["button:true", "message:red", "button:false"]);
  assert.equal(invalid.handlers.size, 0);
  const failed = createHarness();
  failed.request.error();
  assert.deepEqual(failed.trace, ["button:true", "button:false"]);
});

test("sync progress cannot exceed its reported total", () => {
  const state = startSync();
  state.handlers.get("progress")({ current: 12, total: 10 });
  assert.deepEqual(state.trace, ["progress:10/10"]);
});
