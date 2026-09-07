const assert = require("node:assert/strict");
const test = require("node:test");

let handlers;
global.frappe = {
  ui: {
    form: {
      on(_doctype, config) {
        handlers = config;
      }
    }
  },
  db: {
    get_single_value(doctype, field) {
      assert.equal(doctype, "Reporting Currency Settings");
      assert.equal(field, "reporting_currency");
      return Promise.resolve("USD");
    }
  }
};
require("../../karam_finance/reporting_currency/doctype/reporting_currency_gle/reporting_currency_gle.js");

test("new RCGLE form fills its currency from settings", async () => {
  const doc = { reporting_currency: "LBP" };
  await handlers.onload({
    doc,
    is_new: () => true,
    set_value(field, value) {
      doc[field] = value;
      return Promise.resolve();
    }
  });
  assert.equal(doc.reporting_currency, "USD");
});

test("saved, linked and DOE entries are not relabelled on load", async () => {
  await Promise.all(
    [
      [false, {}],
      [true, { gl_entry: "GLE-1" }],
      [true, { reporting_doe: 1 }]
    ].map(([isNew, fields]) =>
      handlers.onload({
        doc: { reporting_currency: "EUR", ...fields },
        is_new: () => isNew,
        set_value() {
          assert.fail("Existing denomination must be checked by server validation");
        }
      })
    )
  );
});
