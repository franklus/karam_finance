const assert = require("node:assert/strict");
const test = require("node:test");
const { loadForm, makeForm } = require("./letter_reconciliation.support.cjs");

function setup() {
  const env = loadForm("Letter Reconciliation", "letter_reconciliation", "selection");
  const frm = makeForm({
    account: "Cash",
    company: "ACME",
    jv_debit: [],
    jv_credit: []
  });
  frm.selected_credit_items = new Set([
    { jv_row_name: "C", credit: 10, posting_date: "2025-12-31" }
  ]);
  frm.selected_debit_items = new Set([
    { jv_row_name: "D", debit: 10, posting_date: "2026-01-01" }
  ]);
  return { ...env, frm };
}

test("assignment retains request rows, year and account; completion clears selections", () => {
  const { handlers, frappe, frm } = setup();
  handlers.assign_letter(frm);
  const request = frappe.call.calls[0][0];
  assert.ok(request.method.endsWith(".set_letter"));
  assert.equal(request.args.latest_year, 2026);
  assert.equal(request.args.account, "Cash");
  assert.equal(request.args.cr_items[0].jv_row_name, "C");
  assert.equal(request.args.dt_items[0].jv_row_name, "D");
  request.callback({ message: { last_letter: "A", next_letter: "B" } });
  assert.equal(frm.doc.last_letter, "A");
  assert.equal(frm.doc.next_letter, "B");
  assert.equal(frm.selected_credit_items.size, 0);
  assert.deepEqual(frm.trigger.calls, [["account"]]);
  assert.equal(frm.doc.difference, 0);
});

for (const action of ["assign_letter", "remove_letter"]) {
  test(`${action} failures preserve selections and render an error`, () => {
    const { handlers, frappe, frm } = setup();
    handlers[action](frm);
    const request = frappe.call.calls[0][0];
    request.callback({ exc: "denied", message: {} });
    request.error();
    assert.equal(frm.selected_credit_items.size, 1);
    assert.equal(frm.reload_doc.calls.length, 0);
    assert.equal(frappe.msgprint.calls[0][0].indicator, "red");
    frm.selected_debit_items.clear();
    assert.throws(() => handlers[action](frm), /at least one/);
  });
}

test("entry fetch preserves filters and account currency amounts and renders failures", () => {
  const { handlers, frappe, frm } = setup();
  Object.assign(frm.doc, {
    start_date: "2026-01-01",
    end_date: "2026-09-01",
    party_type: "Customer",
    party: "C1",
    show_letter: "Assigned"
  });
  handlers.account(frm);
  const request = frm.call.calls[0][0];
  assert.deepEqual(JSON.parse(JSON.stringify(request.args)), {
    account: "Cash",
    start_date: "2026-01-01",
    end_date: "2026-09-01",
    party_type: "Customer",
    party: "C1",
    show_letter: "Assigned"
  });
  request.callback({
    message: {
      cr: [{ jv_row_name: "C", credit: 30, credit_in_account_currency: 20 }],
      dr: [{ jv_row_name: "D", debit_in_account_currency: 20 }]
    }
  });
  assert.equal(frm.doc.jv_credit[0].credit, 20);
  assert.equal(frm.doc.jv_debit[0].debit, 20);
  request.callback({ message: { error: "denied" } });
  request.error({ message: "offline" });
  assert.equal(frappe.msgprint.calls.length, 2);
  assert.equal(frm.doc.jv_credit.length, 1);
});

function wrapper() {
  const bindings = new Map([["change.other", () => {}]]);
  return {
    bindings,
    off(name) {
      bindings.delete(name);
      return this;
    },
    on(name, _selector, fn) {
      bindings.set(name, fn);
      return this;
    }
  };
}

test("repeated initialisation binds selection once and preserves unrelated listeners", () => {
  const { context, frm, allHandlers } = setup();
  const debitWrapper = wrapper();
  frm.fields_dict = {
    jv_debit: { grid: { wrapper: debitWrapper } },
    jv_credit: { grid: { wrapper: wrapper() } }
  };
  frm.doc.jv_debit = [{ debit: "12.50", posting_date: "2026-01-01" }];
  const selection = context.frappe.karam_letter_selection;
  selection.clear_selections(frm);
  selection.init_row_selection(frm);
  selection.init_row_selection(frm);
  assert.equal(debitWrapper.bindings.size, 2);
  const select = debitWrapper.bindings.get("change.karamLetterSelection");
  select({ target: { closest: () => ({ data: () => 1 }), is: () => true } });
  assert.equal(frm.doc.debit_sum, 12.5);
  assert.equal(frm.doc.difference, 12.5);
  select({ target: { closest: () => ({ data: () => 1 }), is: () => false } });
  assert.equal(frm.doc.debit_sum, 0);
  allHandlers["Journal Entry Debit"].debit(frm);
  assert.equal(frm.selected_debit_items.size, 0);
});
