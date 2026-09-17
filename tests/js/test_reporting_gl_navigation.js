const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

function loadRenderers() {
  const context = vm.createContext({ window: {} });
  const reports = [
    ["karam_general", "karam", "Karam", "Karam"],
    ["reporting_currency", "reporting_currency", "Reporting", "Reporting Currency"]
  ];
  for (const [module, suffix, label, reportLabel] of reports) {
    const file = path.resolve(
      __dirname,
      "../../karam_finance",
      module,
      `report/general_ledger_(${suffix})/general_ledger_(${suffix}).js`
    );
    const source = fs
      .readFileSync(file, "utf8")
      .split(`frappe.query_reports["General Ledger (${reportLabel})"] =`)[0];
    vm.runInContext(source, context);
    context[`get${label}GeneralLedgerContext`] = () => ({
      dataReference: null,
      pagination: { reset() {} }
    });
    context[`get${label}ReportTableUX`] = () => ({
      getVisibleColumns: (columns) => columns
    });
    context[`get${label}GeneralLedgerDatatableView`] = (report) => ({
      columns: report.columns,
      rows: [{ source: label }]
    });
  }
  return context;
}

test("GL variants keep their renderers separate on a reused QueryReport", () => {
  const context = loadRenderers();
  const originalRows = [{ source: "original" }];
  const report = {
    data: originalRows,
    columns: [],
    render_datatable() {
      return this.data[0].source;
    }
  };
  context.installKaramGeneralLedgerPreRender(report);
  context.installReportingGeneralLedgerPreRender(report);
  for (const name of ["Karam", "Reporting", "Karam"]) {
    report.report_name = `General Ledger (${name === "Reporting" ? "Reporting Currency" : name})`;
    assert.equal(report.render_datatable(), name);
    assert.equal(report.data, originalRows);
  }
  report.report_name = "Trial Balance (Reporting Currency)";
  assert.equal(report.render_datatable(), "original");
});

test("Reporting GL preserves exact display values and leaves source values intact", () => {
  const context = loadRenderers();
  const row = {
    debit: 51309440814079.55,
    _display_amounts: { debit: "51309440814079.54" }
  };
  assert.equal(
    context.getReportingDisplayAmount(
      row.debit,
      { fieldtype: "Currency", fieldname: "debit" },
      row
    ),
    "51309440814079.54"
  );
  assert.equal(row.debit, 51309440814079.55);
  assert.equal(
    context.getReportingDisplayAmount(
      7,
      { fieldtype: "Float", fieldname: "rate" },
      row
    ),
    7
  );
});

test("Reporting GL exposes optional column controls and preserves their handlers", () => {
  const context = loadRenderers();
  const groups = [];
  const controls = ["show_remarks", "show_source_currency_columns"].map(
    (fieldname) => ({
      df: { fieldtype: "Check", fieldname },
      wrapper: {
        handlers: true,
        detached: false,
        detach() {
          this.detached = true;
        }
      }
    })
  );
  context.__ = (value) => value;
  context.$ = (element) =>
    element === "<div>"
      ? {
          children: [],
          append(child) {
            this.children.push(child);
          },
          text() {
            return this;
          }
        }
      : element;
  const area = {
    length: 1,
    empty() {
      for (const control of controls) {
        if (!control.wrapper.detached) {
          control.wrapper.handlers = false;
        }
      }
      groups.length = 0;
    },
    append(group) {
      groups.push(group);
    }
  };
  context.setupReportingGeneralLedgerFilterGroups({
    check_filter_area: area,
    filters: controls
  });
  for (const { wrapper } of controls) {
    assert.ok(groups.some((group) => group.children.includes(wrapper)));
    assert.equal(wrapper.handlers, true);
  }
});

test("Reporting GL print and export hooks are scoped to its own report", () => {
  const context = loadRenderers();
  context.getReportingGeneralLedgerUX = () => ({
    splitFooterRows: (rows) => ({
      footerRows: rows.filter((row) => row.row_type === "closing")
    }),
    isFooterRow: (row) => row.row_type === "closing"
  });
  const rows = [
    {
      gl_entry: "RC-1",
      reporting_doe: 1,
      voucher_no: "DOE-1",
      debit: 1.005,
      _display_amounts: { debit: "1.01" }
    },
    { row_type: "closing", debit: 1.005, _display_amounts: { debit: "1.01" } }
  ];
  const report = {
    report_name: "General Ledger (Reporting Currency)",
    data: rows,
    get_data_for_print: () => "original print",
    get_validated_visible_indexes: () => "original indexes"
  };
  context.installReportingGeneralLedgerFullDataActions(report);
  assert.equal(report.get_validated_visible_indexes().length, 2);
  const printed = report.get_data_for_print();
  assert.equal(printed[0].debit, "1.01");
  assert.equal(printed[0].voucher_no, "");
  assert.equal(printed[1].is_total_row, true);
  assert.equal(rows[0].voucher_no, "DOE-1");
  report.report_name = "Trial Balance (Reporting Currency)";
  assert.equal(report.get_data_for_print(), "original print");
  assert.equal(report.get_validated_visible_indexes(), "original indexes");
});
