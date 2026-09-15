const assert = require("node:assert/strict");
const test = require("node:test");
const { createHarness } = require("./helpers/reporting_currency_harness.cjs");

function startSync() {
  const state = createHarness();
  state.request.callback({
    message: { progress_event: "rc_gle_sync_abcdef123456", done_event: "done" }
  });
  return state;
}

function tick(state) {
  const [id, callback] = [...state.timers.entries()][0];
  state.timers.delete(id);
  callback();
}

for (const queueState of ["missing", "finished"]) {
  test(`${queueState} status waits for the authoritative completion event`, () => {
    const state = startSync();
    state.request.callback({ message: { state: queueState } });
    assert.ok(!state.trace.includes("message:red"));
    assert.ok(state.handlers.has("done"));
    state.handlers.get("done")({
      status: "success",
      inserted: 2673,
      duration_seconds: 1.69
    });
    assert.equal(state.trace.filter((event) => event === "alert:green").length, 1);
    assert.equal(state.handlers.size, 0);
    assert.equal(state.timers.size, 0);
  });
}

test("a missing result can be recovered by the next poll without a second sync", () => {
  const state = startSync();
  state.request.callback({ message: { state: "missing" } });
  tick(state);
  assert.match(state.request.method, /get_reporting_currency_sync_status$/);
  state.request.callback({
    message: { state: "complete", result: { status: "partial_success" } }
  });
  assert.ok(!state.trace.includes("message:red"));
  assert.ok(state.trace.includes("message:orange"));
  assert.equal(state.timers.size, 0);
});

test("persistent missing results stop after bounded retries without claiming success", () => {
  const state = startSync();
  for (let attempt = 0; attempt < 5; attempt += 1) {
    state.request.callback({ message: { state: "missing" } });
    assert.ok(!state.trace.includes("message:red"));
    tick(state);
    assert.match(state.request.method, /get_reporting_currency_sync_status$/);
  }
  state.request.callback({ message: { state: "missing" } });
  assert.equal(state.trace.filter((event) => event === "message:red").length, 1);
  assert.ok(!state.trace.includes("alert:green"));
  assert.equal(state.handlers.size, 0);
  assert.equal(state.timers.size, 0);
});

test("completion during a retried status request ignores its late missing response", () => {
  const state = startSync();
  state.request.callback({ message: { state: "missing" } });
  tick(state);
  const pending = state.request;
  state.handlers.get("done")({ status: "success" });
  pending.callback({ message: { state: "missing" } });
  assert.ok(!state.trace.includes("message:red"));
  assert.equal(state.trace.filter((event) => event === "alert:green").length, 1);
  assert.equal(state.timers.size, 0);
});
