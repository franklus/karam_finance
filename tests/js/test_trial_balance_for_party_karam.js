const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const tableUX = require("../../karam_finance/public/js/report_utils/report_table_ux.js");

test("Party TB retains its calculated total in the rendered table", () => {
  let preRenderConfig;
  const context = vm.createContext({
    __: (value) => value,
    frappe: {
      query_reports: {},
      defaults: { get_user_default: () => "Company" },
      datetime: { get_today: () => "2026-01-01" }
    },
    erpnext: {
      utils: { get_fiscal_year: () => ["2026", "2026-01-01", "2026-12-31"] }
    },
    window: {
      karamReportTableUX: {
        installPreRenderColumnWidths: (_report, config) => {
          preRenderConfig = config;
        },
        applyCurrentReportColumnWidths: tableUX.applyCurrentReportColumnWidths
      }
    }
  });
  vm.runInContext(
    fs.readFileSync(
      path.resolve(
        __dirname,
        "../../karam_finance/karam_general/report/trial_balance_for_party_(karam)/trial_balance_for_party_(karam).js"
      ),
      "utf8"
    ),
    context
  );
  const report = context.frappe.query_reports["Trial Balance for Party (Karam)"];
  report.onload({});
  assert.equal(preRenderConfig, undefined);
  const total = { party: "'Totals'", bold: 1, debit: 300 };
  const rows = [{ party: "Customer A", debit: 300 }, {}, total];
  const result = report.get_datatable_options({ data: rows, columns: [] });
  assert.equal(result.data.length, 3);
  assert.equal(result.data.at(-1), total);
});
