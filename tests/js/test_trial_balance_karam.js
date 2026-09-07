const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadReport() {
  const context = vm.createContext({
    __: (text) => text,
    frappe: {
      query_reports: {},
      defaults: { get_user_default: () => "Company" },
      datetime: { get_today: () => "2026-01-01" }
    },
    erpnext: {
      get_presentation_currency_list: () => ["USD"],
      utils: {
        get_fiscal_year: () => ["2026", "2026-01-01", "2026-12-31"],
        add_dimensions: () => {}
      },
      financial_statements: { formatter: (value) => String(value) }
    },
    window: {
      alignCurrencyWithSharedHelper: (_name, _value, _column, formatted, style) =>
        `<span style="${style}">${formatted}</span>`
    }
  });
  vm.runInContext(
    fs.readFileSync(
      path.resolve(
        __dirname,
        "../../karam_finance/karam_general/report/trial_balance_(karam)/trial_balance_(karam).js"
      ),
      "utf8"
    ),
    context
  );
  return { report: context.frappe.query_reports["Trial Balance (Karam)"], context };
}

test("Karam TB renders calculated total without making it an account link", () => {
  const { report } = loadReport();
  const data = { is_total: true, parent_account: null };
  const column = { fieldname: "account", fieldtype: "Link" };
  const formatted = report.formatter("'Total'", 0, column, data, (value, _row, col) => {
    assert.equal(col.fieldtype, "Data");
    assert.equal(col.link_onclick, null);
    return value;
  });
  assert.equal(formatted, "<strong>Total</strong>");
  assert.equal(column.fieldtype, "Link");
  assert.match(
    report.formatter("123.45", 0, { fieldname: "debit", fieldtype: "Currency" }, data),
    /font-weight:bold;.*123\.45/
  );
  assert.equal(report.formatter(10, 0, column, { is_spacer: true }), "");
});

test("Karam TB sizing includes totals", () => {
  const { report, context } = loadReport();
  context.window.karamReportTableUX = {
    applyCurrentReportColumnWidths: (options, config) => {
      assert.deepEqual(Array.from(config.excludedRowFlags), ["is_spacer"]);
      return options;
    }
  };
  report.get_datatable_options({});
});
