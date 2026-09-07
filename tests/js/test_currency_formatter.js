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
  assert.match(html, /gap:20px;/);
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

test("alignCurrencyCell blanks unavailable currency values", () => {
  const formatted = "<span>L.L 0.00</span>";
  const out = formatter.alignCurrencyCell({
    value: "",
    column: { fieldtype: "Currency" },
    formatted
  });
  assert.equal(out, "");
});

test("exports runtime metadata fields", () => {
  assert.equal(typeof formatter.__version__, "string");
  assert.match(formatter.__version__, /^\d{4}\.\d{2}\.\d{2}$/);
  assert.equal(typeof formatter.__loaded_at__, "string");
  assert.ok(Number.isFinite(Date.parse(formatter.__loaded_at__)));
});

test("currency helper distinguishes null amounts from genuine zero", () => {
  const column = { fieldtype: "Currency" };
  assert.equal(
    formatter.alignCurrencyCell({ value: null, column, formatted: "$ 0.00" }),
    ""
  );
  assert.match(
    formatter.alignCurrencyCell({ value: 0, column, formatted: "$ 0.00" }),
    /0.00/
  );
  assert.equal(
    globalThis.alignCurrencyWithSharedHelper("Test", undefined, column, "$ 0.00"),
    ""
  );
});

test("negative numeric values are tinted without changing their formatting", () => {
  for (const fieldtype of ["Currency", "Float", "Int", "Percent"]) {
    const result = globalThis.alignCurrencyWithSharedHelper(
      "General Ledger (Karam)",
      -1250,
      { fieldtype },
      "$ -1,250.00"
    );
    assert.match(result, /class="karam-negative-value"/);
    assert.match(result, /-1,250.00/);
    if (fieldtype === "Currency") {
      assert.match(result, /gap:20px/);
    }
  }
});

test("credit amounts, zero, blanks and non-numeric fields remain neutral", () => {
  for (const value of [1250, 0, -0, null, undefined, ""]) {
    assert.equal(
      formatter.tintNegativeValue(value, { fieldtype: "Currency" }, "1,250.00"),
      "1,250.00"
    );
  }
  assert.equal(
    formatter.tintNegativeValue(-0.0001, { fieldtype: "Currency" }, "L.L -0.00"),
    "L.L -0.00"
  );
  assert.equal(
    formatter.tintNegativeValue(-1250, { fieldtype: "Data" }, "-1250"),
    "-1250"
  );
  assert.match(
    formatter.tintNegativeValue(-1250, { fieldtype: "Currency" }, "L.L (1,250.00)"),
    /karam-negative-value/
  );
});
