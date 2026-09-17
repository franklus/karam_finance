const assert = require("node:assert/strict");
const test = require("node:test");
const { createHarness } = require("./helpers/reporting_currency_harness.cjs");

function changeCurrency() {
  const state = createHarness(false);
  state.events.refresh(state.frm);
  state.frm.doc.reporting_currency = "EUR";
  state.events.before_save(state.frm);
  return state;
}

test("direct currency edit requires one confirmation on save", () => {
  const state = changeCurrency();
  assert.equal(state.buttons.has("Change Reporting Currency"), false);
  assert.equal(state.readOnly, undefined);
  assert.equal(state.frappe.validated, false);
  assert.match(state.confirm.message, /USD.*EUR/);
  assert.match(state.confirm.message, /GL.*DOE/);
  assert.match(state.confirm.message, /other settings separately/);
  assert.equal(state.request, null);
  state.confirm.cancel();
  assert.equal(state.request, null);
  assert.equal(state.frm.doc.reporting_currency, "EUR");
});

test("confirmed currency change queues the requested currency and saved revision", () => {
  const state = changeCurrency();
  state.confirm.accept();
  assert.match(state.request.method, /enqueue_currency_change$/);
  assert.deepEqual(JSON.parse(JSON.stringify(state.request.args)), {
    requested_currency: "EUR",
    expected_modified: "2026-09-10 10:00:00",
    confirmed: true
  });
  state.request.callback({
    message: { progress_event: "progress", done_event: "done" }
  });
  state.handlers.get("done")({ status: "success", reporting_currency: "EUR" });
  assert.equal(state.handlers.size, 0);
  assert.ok(state.trace.includes("reload"));
});

test("an initial currency or a currency without ledger entries saves normally", async () => {
  const state = createHarness(false);
  state.frm.doc.__onload.has_reporting_entries = false;
  state.frm.doc.reporting_currency = "EUR";
  assert.equal(await state.events.before_save(state.frm), undefined);
  assert.equal(state.confirm, undefined);
  assert.notEqual(state.frappe.validated, false);
  assert.equal(state.request, null);
});

test("currency change uses DOE rates edited directly in the child table", () => {
  const state = createHarness(false);
  state.frm.doc.reporting_currency = "GBP";
  state.frm.doc.rc_parameters = [
    { name: "DOE-A", doe_posting_date: "2026-12-31", exchange_rate: 0.25 },
    { name: "DOE-B", doe_posting_date: "2026-12-31", exchange_rate: 0.2 }
  ];
  state.events.before_save(state.frm);
  assert.match(state.confirm.message, /rates currently entered/);
  state.confirm.accept();
  assert.deepEqual(JSON.parse(JSON.stringify(state.request.args.doe_rates)), {
    "DOE-A": 0.25,
    "DOE-B": 0.2
  });
  assert.deepEqual(
    state.frm.doc.rc_parameters.map((row) => row.exchange_rate),
    [0.25, 0.2]
  );
});

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

test("sync recovers completion sent before the enqueue response arrives", () => {
  const state = createHarness();
  // A worker can finish while the browser is still waiting for these event names.
  assert.equal(state.handlers.size, 0);
  state.request.callback({
    message: {
      progress_event: "rc_gle_sync_abcdef123456",
      done_event: "rc_gle_sync_abcdef123456_done"
    }
  });
  assert.match(state.request.method, /get_reporting_currency_sync_status$/);
  state.request.callback({
    message: { state: "complete", result: { status: "success", inserted: 2 } }
  });
  assert.ok(state.trace.includes("reload"));
  assert.equal(state.handlers.size, 0);
  assert.equal(state.timers.size, 0);
});

test("late poll responses cannot complete a realtime-finished sync twice", () => {
  const state = createHarness();
  state.request.callback({
    message: {
      progress_event: "rc_gle_sync_abcdef123456",
      done_event: "rc_gle_sync_abcdef123456_done"
    }
  });
  const poll = state.request;
  assert.match(poll.method, /get_reporting_currency_sync_status$/);
  state.handlers.get("rc_gle_sync_abcdef123456_done")({ status: "partial_success" });
  poll.callback({ message: { state: "complete", result: { status: "success" } } });
  assert.equal(state.trace.filter((event) => event === "reload").length, 1);
  assert.equal(state.timers.size, 0);
});

test("a queued job is checked again without submitting another sync", () => {
  const state = createHarness();
  state.request.callback({
    message: { progress_event: "rc_gle_sync_abcdef123456", done_event: "done" }
  });
  state.request.callback({ message: { state: "queued" } });
  assert.equal(state.timers.size, 1);
  const [id, tick] = [...state.timers.entries()][0];
  state.timers.delete(id);
  tick();
  assert.match(state.request.method, /get_reporting_currency_sync_status$/);
  state.request.callback({
    message: { state: "complete", result: { status: "partial_success" } }
  });
  assert.ok(state.trace.includes("message:orange"));
  assert.equal(state.timers.size, 0);
});

test("lost failure events are recovered without showing success", () => {
  const state = createHarness();
  state.request.callback({
    message: { progress_event: "rc_gle_sync_abcdef123456", done_event: "done" }
  });
  state.request.callback({
    message: { state: "complete", result: { status: "error" } }
  });
  assert.ok(state.trace.includes("message:red"));
  assert.ok(!state.trace.includes("reload"));
  assert.equal(state.handlers.size, 0);
});

test("repeated connection failures stop monitoring without resubmitting", () => {
  const state = createHarness();
  state.request.callback({
    message: { progress_event: "rc_gle_sync_abcdef123456", done_event: "done" }
  });
  for (let attempt = 0; attempt < 3; attempt += 1) {
    assert.match(state.request.method, /get_reporting_currency_sync_status$/);
    state.request.error();
    if (attempt < 2) {
      const [id, tick] = [...state.timers.entries()][0];
      state.timers.delete(id);
      tick();
    }
  }
  assert.ok(state.trace.includes("message:red"));
  assert.ok(!state.trace.includes("reload"));
  assert.equal(state.handlers.size, 0);
  assert.equal(state.timers.size, 0);
});

test("a failed queue job is not mistaken for successful completion", () => {
  const state = createHarness();
  state.request.callback({
    message: { progress_event: "rc_gle_sync_abcdef123456", done_event: "done" }
  });
  state.request.callback({ message: { state: "failed" } });
  assert.ok(state.trace.includes("message:red"));
  assert.ok(!state.trace.includes("reload"));
  assert.equal(state.timers.size, 0);
});
