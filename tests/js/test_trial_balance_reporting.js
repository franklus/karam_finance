const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadReport() {
  const seen = [];
  const context = vm.createContext({
    __: (value) => value,
    frappe: {
      query_reports: {},
      defaults: { get_user_default: () => "Company" },
      datetime: { get_today: () => "2026-01-01" }
    },
    erpnext: {
      utils: { get_fiscal_year: () => ["2026", "2026-01-01", "2026-12-31"] },
      financial_statements: {
        formatter: (value) => {
          seen.push(value);
          return value;
        }
      }
    },
    window: {
      alignCurrencyWithSharedHelper: (_name, _value, _column, formatted) => formatted
    }
  });
  vm.runInContext(
    fs.readFileSync(
      path.resolve(
        __dirname,
        "../../karam_finance/reporting_currency/report/trial_balance_(reporting_currency)/trial_balance_(reporting_currency).js"
      ),
      "utf8"
    ),
    context
  );
  return {
    report: context.frappe.query_reports["Trial Balance (Reporting Currency)"],
    seen,
    context
  };
}

test("Reporting TB renders its total and exact display amount", () => {
  const { report, seen } = loadReport();
  const data = { is_total: true, debit: 1.005, _display_amounts: { debit: "1.01" } };
  assert.equal(
    report.formatter(
      data.debit,
      0,
      { fieldname: "debit", fieldtype: "Currency" },
      data
    ),
    "1.01"
  );
  assert.equal(data.debit, 1.005);
  assert.equal(seen.length, 1);
});

test("Reporting TB keeps total rows when sizing and rendering", () => {
  const { report, context } = loadReport();
  let config;
  context.window.karamReportTableUX = {
    applyCurrentReportColumnWidths: (options, value) => {
      config = value;
      return options;
    }
  };
  report.get_datatable_options({});
  assert.deepEqual(Array.from(config.excludedRowFlags), ["is_spacer"]);
});

test("Reporting TB account links open Reporting GL with exclusions", () => {
  const { report, context } = loadReport();
  const filters = {
    company: "Example",
    from_date: "2026-01-01",
    to_date: "2026-12-31",
    exclude_reporting_doe: 1,
    exclude_manual_entries: 0
  };
  context.frappe.query_report = { get_filter_values: () => filters };
  let destination;
  context.frappe.set_route = (...args) => {
    destination = args;
  };
  report.open_reporting_ledger({ account: "Cash" });
  assert.deepEqual(destination, ["query-report", "General Ledger (Reporting Currency)"]);
  assert.equal(context.frappe.route_options.exclude_reporting_doe, 1);
  assert.equal(context.frappe.route_options.account[0], "Cash");
});

test("Reporting TB total label is not an account link", () => {
  const { report } = loadReport();
  const column = { fieldname: "account", fieldtype: "Link" };
  let renderedColumn;
  const formatted = report.formatter(
    "'Total'",
    0,
    column,
    { is_total: true },
    (value, _row, col) => {
      renderedColumn = col;
      return value;
    }
  );
  assert.equal(formatted, "<strong>Total</strong>");
  assert.equal(renderedColumn.fieldtype, "Data");
  assert.equal(column.fieldtype, "Link");
});

test("Reporting TB bolds nested group labels but leaves leaf labels normal", () => {
  const { report } = loadReport();
  const column = { fieldname: "account", fieldtype: "Link" };
  const formatter = (value) => value;
  const group = {
    account: "Banks",
    account_name: "Banks",
    parent_account: "Assets",
    is_group: 1
  };
  const leaf = {
    account: "Bank A",
    account_name: "Bank A",
    parent_account: "Banks",
    is_group: 0
  };
  assert.equal(
    report.formatter("Banks", 0, column, group, formatter),
    "<strong>Banks</strong>"
  );
  assert.equal(report.formatter("Bank A", 1, column, leaf, formatter), "Bank A");
});

test("Reporting TB keeps baseline accounting controls and useful RC exclusions", () => {
  const { report } = loadReport();
  const filters = new Map(report.filters.map((filter) => [filter.fieldname, filter]));
  assert.equal(filters.get("fiscal_year").reqd, 1);
  for (const name of [
    "with_period_closing_entry_for_opening",
    "with_period_closing_entry_for_current_period",
    "include_default_book_entries",
    "show_net_values",
    "show_group_accounts"
  ]) {
    assert.equal(filters.get(name).default, 1);
  }
  assert.equal(filters.has("ignore_fiscal_year"), false);
  assert.equal(filters.has("show_source_currency_columns"), false);
  assert.equal(filters.has("show_company_currency_columns"), false);
  assert.equal(filters.get("exclude_reporting_doe").default, 0);
  assert.equal(filters.get("exclude_manual_entries").default, 0);
});
