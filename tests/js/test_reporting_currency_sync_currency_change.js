const assert = require("node:assert/strict");
const test = require("node:test");
const { createHarness } = require("./helpers/reporting_currency_harness.cjs");

test("currency-change jobs recover a terminal result after realtime is missed", () => {
  const state = createHarness();
  state.request.callback({
    message: {
      progress_event: "rc_currency_change_abcdef1234567890",
      done_event: "rc_currency_change_abcdef1234567890_done"
    }
  });
  assert.match(state.request.method, /get_reporting_currency_sync_status$/);
  state.request.callback({
    message: {
      state: "complete",
      result: { status: "success", reporting_currency: "EUR" }
    }
  });
  assert.ok(state.trace.includes("reload"));
  assert.equal(state.handlers.size, 0);
  assert.equal(state.timers.size, 0);
});

test("currency-change failures and lost connections stop without resubmitting work", () => {
  const state = createHarness();
  state.request.callback({
    message: {
      progress_event: "rc_currency_change_abcdef1234567890",
      done_event: "rc_currency_change_abcdef1234567890_done"
    }
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

test("currency-change realtime completion wins over a late status response", () => {
  const state = createHarness();
  state.request.callback({
    message: {
      progress_event: "rc_currency_change_abcdef1234567890",
      done_event: "rc_currency_change_abcdef1234567890_done"
    }
  });
  const poll = state.request;
  state.handlers.get("rc_currency_change_abcdef1234567890_done")({
    status: "success",
    reporting_currency: "EUR"
  });
  poll.callback({ message: { state: "complete", result: { status: "success" } } });
  assert.equal(state.trace.filter((event) => event === "reload").length, 1);
  assert.equal(state.timers.size, 0);
});
