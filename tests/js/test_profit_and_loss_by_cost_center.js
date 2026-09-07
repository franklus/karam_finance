const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const reportPath = path.resolve(
  __dirname,
  "../../karam_finance/karam_general/report/profit_and_loss_statement_by_cost_center_(karam)/profit_and_loss_statement_by_cost_center_(karam).js"
);

function loadCostCenterReport() {
  const context = vm.createContext({
    frappe: {
      query_reports: {},
      query_report: { get_filter_value: () => "Report" }
    },
    erpnext: {
      financial_statements: { filters: [] },
      utils: { add_dimensions() {} }
    },
    $: { extend: Object.assign },
    __: (value) => value,
    window: { alignCurrencyWithSharedHelper: (_report, _value, _column, html) => html }
  });
  vm.runInContext(fs.readFileSync(reportPath, "utf8"), context);
  return context;
}

test("cost-centre formatter enables tree controls without mutating shared columns", () => {
  const context = loadCostCenterReport();
  const column = Object.freeze({ fieldname: "cost_center", colIndex: 1 });
  let receivedColumn;
  const output = context.costCenterFormatter(
    "Branch",
    0,
    column,
    { cost_center: "Branch" },
    (value, _row, displayColumn) => {
      receivedColumn = displayColumn;
      return value;
    }
  );
  assert.equal(output, "Branch");
  assert.equal(receivedColumn.is_tree, true);
  assert.equal(column.is_tree, undefined);
});

test("cost-centre analysis views preserve non-metric columns and undefined percentages", () => {
  const context = loadCostCenterReport();
  assert.equal(context.formatSelectedViewMetric("Growth", { colIndex: 2 }, null), null);
  assert.equal(context.formatSelectedViewMetric("Growth", { colIndex: 3 }, null), "NA");
  assert.equal(context.formatSelectedViewMetric("Margin", { colIndex: 1 }, null), null);
  assert.equal(context.formatSelectedViewMetric("Margin", { colIndex: 2 }, null), "NA");
  assert.equal(context.formatSelectedViewMetric("Report", { colIndex: 3 }, null), null);
});
