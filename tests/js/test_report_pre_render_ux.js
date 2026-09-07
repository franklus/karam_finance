const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const tableUX = require("../../karam_finance/public/js/report_utils/report_table_ux.js");

const SELECTED_REPORT_SCRIPTS = [
  "karam_general/report/asset_depreciation_ledger_summary_(karam)/asset_depreciation_ledger_summary_(karam).js",
  "karam_general/report/bank_reconciliation_statement_(karam)/bank_reconciliation_statement_(karam).js",
  "karam_general/report/profit_and_loss_statement_by_cost_center_(karam)/profit_and_loss_statement_by_cost_center_(karam).js",
  "karam_general/report/trial_balance_(karam)/trial_balance_(karam).js",
  "karam_general/report/trial_balance_for_party_(karam)/trial_balance_for_party_(karam).js",
  "reporting_currency/report/trial_balance_for_party_(reporting_currency)/trial_balance_for_party_(reporting_currency).js",
  "reporting_currency/report/trial_balance_(reporting_currency)/trial_balance_(reporting_currency).js"
];

const COMPLETED_GENERAL_LEDGER_REPORTING =
  "reporting_currency/report/general_ledger_(reporting_currency)/general_ledger_(reporting_currency).js";
const COMPLETED_GENERAL_LEDGER_KARAM =
  "karam_general/report/general_ledger_(karam)/general_ledger_(karam).js";

const TREE_REPORT_SCRIPTS = [
  "karam_general/report/profit_and_loss_statement_by_cost_center_(karam)/profit_and_loss_statement_by_cost_center_(karam).js",
  "karam_general/report/trial_balance_(karam)/trial_balance_(karam).js",
  "reporting_currency/report/trial_balance_(reporting_currency)/trial_balance_(reporting_currency).js"
];

function readReportSource(relativePath) {
  return fs.readFileSync(
    path.resolve(__dirname, "../../karam_finance", relativePath),
    "utf8"
  );
}

function findReportScripts(relativeDirectory) {
  const directory = path.resolve(__dirname, "../../karam_finance", relativeDirectory);
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const relativePath = path.join(relativeDirectory, entry.name);
    if (entry.isDirectory()) {
      return findReportScripts(relativePath);
    }
    return entry.isFile() && entry.name.endsWith(".js") ? [relativePath] : [];
  });
}

test("selected reports cover the complete custom report inventory", () => {
  const reportScripts = [
    ...findReportScripts("karam_general/report"),
    ...findReportScripts("reporting_currency/report")
  ].sort();
  const expectedScripts = [
    ...SELECTED_REPORT_SCRIPTS,
    COMPLETED_GENERAL_LEDGER_KARAM,
    COMPLETED_GENERAL_LEDGER_REPORTING
  ].sort();

  assert.deepEqual(reportScripts, expectedScripts);
});

test("pre-render widths filter display-only rows and restore report state", () => {
  const sourceRows = [
    { account: "Cash" },
    { account: "x".repeat(200), is_total: true }
  ];
  const sourceColumns = [{ fieldname: "account", fieldtype: "Link", width: 900 }];
  let renderedRows;
  let renderedColumns;
  const report = {
    data: sourceRows,
    columns: sourceColumns,
    render_datatable() {
      renderedRows = this.data;
      renderedColumns = this.columns;
      return "rendered";
    }
  };

  tableUX.installPreRenderColumnWidths(report, {
    excludedRowFlags: ["is_total"]
  });
  const installedRenderer = report.render_datatable;
  tableUX.installPreRenderColumnWidths(report, {
    excludedRowFlags: ["is_total"]
  });

  assert.equal(report.render_datatable, installedRenderer);
  assert.equal(report.render_datatable(), "rendered");
  assert.deepEqual(renderedRows, [sourceRows[0]]);
  assert.equal(renderedColumns[0].width, 104);
  assert.equal(report.data, sourceRows);
  assert.equal(report.columns, sourceColumns);
});

test("pre-render widths exclude trailing rows exactly once", () => {
  const originalFrappe = globalThis.frappe;
  const sourceRows = [
    { party: "Customer A" },
    { party: "Customer B" },
    { party: "" },
    { party: "Totals" }
  ];
  let renderedRows;
  const report = {
    data: sourceRows,
    columns: [{ fieldname: "party", fieldtype: "Link" }],
    render_datatable() {
      const options = tableUX.applyCurrentReportColumnWidths(
        { columns: this.columns, data: this.data },
        { excludedTrailingRows: 2 }
      );
      renderedRows = options.data;
    }
  };
  globalThis.frappe = { query_report: report };

  try {
    tableUX.installPreRenderColumnWidths(report, { excludedTrailingRows: 2 });
    report.render_datatable();
    assert.deepEqual(renderedRows, sourceRows.slice(0, 2));
    assert.equal(report.data, sourceRows);
  } finally {
    globalThis.frappe = originalFrappe;
  }
});

test("selected custom reports install pre-render dynamic widths", () => {
  for (const relativePath of SELECTED_REPORT_SCRIPTS) {
    const source = readReportSource(relativePath);
    assert.match(source, /applyCurrentReportColumnWidths\(options/, relativePath);
    const setupSource = source.includes("trial_balance_reporting_filters.js")
      ? readReportSource("public/js/report_utils/trial_balance_reporting_filters.js")
      : source;
    assert.match(setupSource, /installPreRenderColumnWidths\(/, relativePath);
    assert.doesNotMatch(source, /refreshCurrentReportColumnWidths/, relativePath);
    assert.doesNotMatch(source, /karam-gl-pagination|data-karam-gl-page/, relativePath);
  }
  for (const relativePath of TREE_REPORT_SCRIPTS) {
    assert.match(
      readReportSource(relativePath),
      /removeTreeFooter\(report\)/,
      relativePath
    );
  }
});

test("General Ledger Reporting uses its own Karam-style pre-render wrapper", () => {
  const source = readReportSource(COMPLETED_GENERAL_LEDGER_REPORTING);
  assert.match(source, /installReportingGeneralLedgerPreRender/);
  assert.match(source, /_reportingGeneralLedgerPreRenderInstalled/);
  assert.doesNotMatch(source, /_karamGeneralLedgerPreRenderInstalled/);
});

test("GL renderer leaves other reports' rows and columns untouched", () => {
  const vm = require("node:vm");
  const context = {
    frappe: {
      query_reports: {},
      defaults: { get_user_default: () => "Company" },
      datetime: { add_months: () => "2026-01-01", get_today: () => "2026-09-06" }
    },
    __: (value) => value,
    erpnext: { utils: { add_dimensions() {} } }
  };
  vm.createContext(context);
  const rendererSource = readReportSource(COMPLETED_GENERAL_LEDGER_KARAM).split(
    'frappe.query_reports["General Ledger (Karam)"] ='
  )[0];
  vm.runInContext(rendererSource, context);
  const rows = [{ account: "Cash", indent: 0 }];
  const columns = [{ fieldname: "account" }];
  const removed = [];
  const report = {
    report_name: "Trial Balance (Karam)",
    data: rows,
    columns,
    $report: {
      next: (selector) => ({ remove: () => removed.push(selector) }),
      find: (selector) => ({ remove: () => removed.push(selector) })
    },
    render_datatable() {
      assert.equal(this.data, rows);
      assert.equal(this.columns, columns);
      return "TB rendered";
    }
  };
  context.installKaramGeneralLedgerPreRender(report);
  assert.equal(report.render_datatable(), "TB rendered");
  assert.deepEqual(removed, [".karam-gl-pagination", ".karam-gl-summary-row"]);
});

test("native serial width follows the full result on creation and refresh", () => {
  const serial = { id: "_rowIndex", colIndex: 0, width: 30 };
  const widths = [];
  const report = {
    report_name: "Trial Balance (Karam)",
    columns: [{ fieldname: "account" }],
    data: Array.from({ length: 168 }, () => ({ account: "Cash" })),
    render_datatable() {
      serial.width = 30;
      this.datatable = {
        datamanager: { getColumnById: () => serial },
        columnmanager: { setColumnWidth: (_index, width) => widths.push(width) }
      };
    }
  };
  tableUX.installPreRenderColumnWidths(report);
  report.render_datatable();
  assert.equal(serial.width, tableUX.calculateSerialNumberWidth(168));
  report.data = Array.from({ length: 1000 }, () => ({ account: "Cash" }));
  report.render_datatable();
  assert.deepEqual(widths, [52, 60]);
});

test("width settings stay with their report when navigating a shared instance", () => {
  const rows = [{ account: "Cash" }, { account: "Total", is_total: true }];
  let rendered;
  const report = {
    report_name: "Trial Balance (Karam)",
    data: rows,
    columns: [{ fieldname: "account" }],
    render_datatable() {
      rendered = this.data;
    }
  };
  tableUX.installPreRenderColumnWidths(report, { excludedRowFlags: ["is_total"] });
  report.render_datatable();
  assert.equal(rendered.length, 1);
  report.report_name = "Bank Reconciliation Statement (Karam)";
  tableUX.installPreRenderColumnWidths(report);
  report.render_datatable();
  assert.equal(rendered.length, 2);
  report.report_name = "Trial Balance (Karam)";
  report.render_datatable();
  assert.equal(rendered.length, 1);
  report.report_name = "General Ledger (Karam)";
  report.render_datatable();
  assert.equal(rendered, rows);
});
