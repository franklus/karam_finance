const assert = require("node:assert/strict");
const test = require("node:test");
const { loadForm, makeForm } = require("./letter_reconciliation.support.cjs");

function setup() {
  const env = loadForm(
    "Letter Reconciliation Settings",
    "letter_reconciliation_settings",
    "rebuild_messages"
  );
  return {
    ...env,
    frm: makeForm({ rebuild_company: "ACME", rebuild_whole_history: 1 })
  };
}

for (const status of ["success", "partial_success", "error", "fatal_error"]) {
  test(`rebuild ${status} removes subscriptions and ignores late progress`, () => {
    const { handlers, frappe, events, frm } = setup();
    handlers.run_historical_rebuild(frm);
    const request = frappe.call.calls[0][0];
    assert.ok(request.method.endsWith(".enqueue_historical_gl_rebuild"));
    request.callback({
      message: { progress_event: "progress-1", done_event: "done-1" }
    });
    const progress = events.get("progress-1");
    progress({ current: 120, total: 100, message: "Working" });
    assert.equal(frappe.show_progress.calls.at(-1)[1], 100);
    events.get("done-1")({ status, rebuilt_count: 2, failed_count: 1 });
    assert.equal(events.size, 0);
    assert.equal(frm.reload_doc.calls.length, 1);
    assert.equal(frappe.hide_progress.calls.length, 1);
    const previous = frappe.show_progress.calls.length;
    progress({ current: 1 });
    assert.equal(frappe.show_progress.calls.length, previous);
    const expected = status === "partial_success" ? "orange" : "red";
    if (status === "success") {
      assert.equal(frappe.show_alert.calls[0][0].indicator, "green");
    } else {
      assert.equal(frappe.msgprint.calls[0][0].indicator, expected);
    }
  });
}

test("missing progress information creates no subscriptions", () => {
  const { handlers, frappe, events, frm } = setup();
  handlers.run_historical_rebuild(frm);
  frappe.call.calls[0][0].callback({ message: {} });
  assert.equal(events.size, 0);
  assert.equal(frappe.show_progress.calls.length, 0);
  assert.equal(frappe.msgprint.calls[0][0].indicator, "red");
});

test("preview renders scope, classification and escaped reasons and accounts", () => {
  const { handlers, frappe, frm } = setup();
  handlers.preview_historical_rebuild(frm);
  const request = frappe.call.calls[0][0];
  assert.ok(request.method.endsWith(".preview_historical_gl_rebuild"));
  request.callback({
    message: {
      filters: { company: "<ACME>", whole_history: 1 },
      total_submitted_vouchers: 5,
      eligible: { count: 2 },
      already_correct: { count: 1 },
      blocked: {
        count: 2,
        groups: [{ reason: "<closed>", voucher_count: 2, accounts: ["<Cash>"] }]
      }
    }
  });
  const result = frappe.msgprint.calls[0][0];
  assert.equal(result.indicator, "orange");
  assert.ok(result.message.includes("Entire company history"));
  assert.ok(result.message.includes("&lt;ACME>"));
  assert.ok(result.message.includes("&lt;closed>"));
  assert.ok(result.message.includes("&lt;Cash>"));
  assert.equal(frm.reload_doc.calls.length, 1);
});

test("whole-history toggle preserves required date field behaviour", () => {
  const { handlers, frm } = setup();
  handlers.refresh(frm);
  assert.deepEqual(frm.set_df_property.calls[0], [
    "rebuild_from_posting_date",
    "reqd",
    0
  ]);
  frm.doc.rebuild_whole_history = 0;
  handlers.rebuild_whole_history(frm);
  assert.deepEqual(frm.toggle_display.calls.at(-1), ["rebuild_to_posting_date", true]);
  assert.deepEqual(frm.set_df_property.calls.at(-1), [
    "rebuild_to_posting_date",
    "reqd",
    1
  ]);
});
