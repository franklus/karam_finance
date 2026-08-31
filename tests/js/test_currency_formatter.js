const assert = require("node:assert/strict");
const test = require("node:test");

const formatter = require("../../karam_finance/public/js/report_utils/currency_formatter.bundle.js");

test("splitSymbolAndNumber extracts symbol and numeric value", () => {
  const parts = formatter.splitSymbolAndNumber("L.L 1,250.00");
  assert.deepEqual(parts, { symbol: "L.L", number: "1,250.00" });
});

test("formatCurrencyHtml returns flex wrapper for currency values", () => {
  const html = formatter.formatCurrencyHtml("<span>$ 150.00</span>");
  assert.match(html, /display:flex/);
  assert.match(html, /<span>\$<\/span>/);
  assert.match(html, /<span>150.00<\/span>/);
});

test("formatCurrencyHtml supports parenthesised negatives", () => {
  const html = formatter.formatCurrencyHtml("<span>L.L (1,250.00)</span>");
  assert.match(html, /<span>L\.L<\/span>/);
  assert.match(html, /<span>\(1,250\.00\)<\/span>/);
});

test("formatCurrencyHtml preserves blanks", () => {
  const html = formatter.formatCurrencyHtml("");
  assert.equal(html, "");
});

test("alignCurrencyCell keeps non-currency columns unchanged", () => {
  const formatted = "<span>ABC</span>";
  const out = formatter.alignCurrencyCell({
    value: "ABC",
    column: { fieldtype: "Data" },
    formatted
  });
  assert.equal(out, formatted);
});

test("alignCurrencyCell keeps empty currency values unchanged", () => {
  const formatted = "<span>L.L 0.00</span>";
  const out = formatter.alignCurrencyCell({
    value: "",
    column: { fieldtype: "Currency" },
    formatted
  });
  assert.equal(out, formatted);
});

test("exports runtime metadata fields", () => {
  assert.equal(typeof formatter.__version__, "string");
  assert.match(formatter.__version__, /^\d{4}\.\d{2}\.\d{2}$/);
  assert.equal(typeof formatter.__loaded_at__, "string");
  assert.ok(Number.isFinite(Date.parse(formatter.__loaded_at__)));
});
