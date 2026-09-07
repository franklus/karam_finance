const assert = require("node:assert/strict");
const test = require("node:test");

const tableUX = require("../../karam_finance/public/js/report_utils/report_table_ux.js");

test("shared report widths preserve options and derive widths from current data", () => {
  const options = {
    columns: [{ fieldname: "account", fieldtype: "Link", width: 900 }],
    serialNoColumn: true
  };
  const resized = tableUX.applyDynamicColumnWidths(options, [
    { account: "A substantially longer account value" }
  ]);

  assert.equal(resized.serialNoColumn, true);
  assert.ok(resized.columns[0].width < options.columns[0].width);
  assert.equal(options.columns[0].width, 900);
});

test("headers are included in width calculation", () => {
  const [column] = tableUX.calculateColumnWidths(
    [
      {
        fieldname: "against",
        fieldtype: "Data",
        label: "A header longer than the row value"
      }
    ],
    [{ against: "Cash" }]
  );

  assert.equal(column.width, 320);
});

test("tree indentation is included for the first report column", () => {
  const [column] = tableUX.calculateColumnWidths(
    [{ fieldname: "account", fieldtype: "Link", label: "Account" }],
    [{ account: "Cash", indent: 3 }]
  );

  assert.ok(column.width >= 112);
});

test("all columns use the same hard width cap", () => {
  const [column] = tableUX.calculateColumnWidths(
    [{ fieldname: "against", fieldtype: "Data", label: "Against Account" }],
    [{ against: "x".repeat(2000) }]
  );

  assert.equal(column.width, 640);
});

test("serial number width includes DataTable cell chrome", () => {
  assert.equal(tableUX.calculateSerialNumberWidth(999), 52);
  assert.equal(tableUX.calculateSerialNumberWidth(1000), 60);
  assert.equal(tableUX.calculateSerialNumberWidth(10000), 68);
});

test("serial number width is applied to the DataTable synthetic column", () => {
  const serialColumn = { id: "_rowIndex", colIndex: 0, width: 999 };
  let bodyWidth;
  const datatable = {
    datamanager: { getColumnById: () => serialColumn },
    columnmanager: {
      setColumnHeaderWidth() {},
      setColumnWidth(_colIndex, width) {
        bodyWidth = width;
      }
    },
    style: { setStickyColumnStyle() {}, setBodyStyle() {} }
  };
  tableUX.applySerialNumberColumnWidth(datatable, 10000);

  assert.equal(serialColumn.width, 68);
  assert.equal(bodyWidth, 68);
});

test("widths scan the complete result rather than a 250-row viewport", () => {
  let currentColumns = [{ fieldname: "account", fieldtype: "Link", width: 30 }];
  let refreshedRows = [];
  const datatable = {
    options: { columns: currentColumns },
    datamanager: {
      getColumns() {
        return currentColumns;
      }
    },
    refresh(rows, columns) {
      refreshedRows = rows;
      currentColumns = columns;
      this.options.columns = columns;
    }
  };
  const rows = Array.from({ length: 251 }, () => ({ account: "Cash" }));
  rows[250] = { account: "x".repeat(100) };

  tableUX.refreshCurrentReportColumnWidths({ data: rows, datatable });

  assert.equal(refreshedRows.length, 251);
  assert.equal(currentColumns[0].width, 640);
});

test("rendered header and cell content can raise the measured width", () => {
  let currentColumns = [{ fieldname: "account", fieldtype: "Link", width: 999 }];
  const datatable = {
    options: { columns: currentColumns },
    bodyScrollable: { querySelectorAll: () => [{ scrollWidth: 450 }] },
    datamanager: {
      getColumns() {
        return currentColumns;
      }
    },
    getColumnHeaderElement() {
      return {
        querySelector() {
          return { scrollWidth: 420 };
        }
      };
    },
    refresh(_rows, columns) {
      currentColumns = columns;
      this.options.columns = columns;
    }
  };

  tableUX.refreshCurrentReportColumnWidths({ data: [{ account: "Cash" }], datatable });

  assert.equal(currentColumns[0].width, 450);
});

test("refresh reapplies current-result widths after a manual resize", () => {
  let currentColumns = [{ fieldname: "account", fieldtype: "Link", width: 999 }];
  let currentRows = [];
  const datatable = {
    options: { columns: currentColumns },
    datamanager: {
      getColumns() {
        return currentColumns;
      }
    },
    refresh(rows, columns) {
      currentRows = rows;
      currentColumns = columns;
      this.options.columns = columns;
    }
  };
  const firstRows = [{ account: "Cash" }];
  const report = { data: firstRows, datatable };

  tableUX.refreshCurrentReportColumnWidths(report);
  assert.deepEqual(currentRows, firstRows);
  assert.equal(currentColumns[0].width, 104);

  currentColumns[0].width = 999;
  const refreshedRows = [
    { account: "A substantially longer account value" },
    { account: "Total", is_total: true }
  ];
  report.data = refreshedRows;
  tableUX.refreshCurrentReportColumnWidths(report, {
    excludedRowFlags: ["is_total"]
  });

  assert.deepEqual(currentRows, [refreshedRows[0]]);
  assert.ok(currentColumns[0].width > 96);
  assert.ok(currentColumns[0].width < 999);
});

test("non-GL reports can exclude footer rows without changing report data", () => {
  const originalFrappe = globalThis.frappe;
  const rows = [
    { account: "Cash" },
    { account: "", is_spacer: true },
    { account: "Total", is_total: true }
  ];
  globalThis.frappe = { query_report: { data: rows } };

  try {
    const resized = tableUX.applyCurrentReportColumnWidths(
      { columns: [{ fieldname: "account" }], data: rows },
      { excludedRowFlags: ["is_spacer", "is_total"] }
    );
    assert.deepEqual(resized.data, [{ account: "Cash" }]);
    assert.equal(resized.showTotalRow, false);
    assert.equal(globalThis.frappe.query_report.data, rows);
  } finally {
    globalThis.frappe = originalFrappe;
  }
});

test("tree controls are removed without disabling the tree report", () => {
  let removeCalls = 0;
  const report = {
    $tree_footer: {
      remove() {
        removeCalls += 1;
      }
    }
  };

  tableUX.removeTreeFooter(report);

  assert.equal(removeCalls, 1);
});
test("non-GL reports can exclude deterministic trailing total rows", () => {
  const originalFrappe = globalThis.frappe;
  globalThis.frappe = {
    query_report: {
      data: [{ party: "Customer" }, { party: "" }, { party: "Totals" }]
    }
  };

  try {
    const resized = tableUX.applyCurrentReportColumnWidths(
      { columns: [{ fieldname: "party" }] },
      { excludedTrailingRows: 2 }
    );
    assert.deepEqual(resized.data, [{ party: "Customer" }]);
    assert.equal(resized.showTotalRow, false);
  } finally {
    globalThis.frappe = originalFrappe;
  }
});

test("native serial width survives DataTable dimension resets", () => {
  const serialColumn = { id: "_rowIndex", colIndex: 0, width: 30 };
  const datatable = {
    datamanager: { getColumnById: () => serialColumn },
    style: { getRowIndexColumnWidth: () => 30 }
  };
  tableUX.applySerialNumberColumnWidth(datatable, 168);
  // DataTable's setupColumnWidth runs again on deferred tree row redraws.
  serialColumn.width = datatable.style.getRowIndexColumnWidth();
  assert.equal(serialColumn.width, 52);
  tableUX.applySerialNumberColumnWidth(datatable, 1000);
  serialColumn.width = datatable.style.getRowIndexColumnWidth();
  assert.equal(serialColumn.width, 60);
});

test("currency widths measure formatted amounts and cache by row currency", () => {
  const originalFrappe = globalThis.frappe;
  const calls = [];
  globalThis.frappe = {
    meta: { get_field_currency: (column, row) => row[column.options] },
    form: {
      formatters: {
        Currency(value, column, options, row) {
          calls.push([value, column.precision, options.only_value, row.currency]);
          return `${row.currency} ${Number(value).toLocaleString("en-US", {
            minimumFractionDigits: column.precision,
            maximumFractionDigits: column.precision
          })}`;
        }
      }
    }
  };
  try {
    const rows = [
      { debit: 1830000000, currency: "L.L" },
      { debit: 1830000000, currency: "L.L" },
      { debit: 1830000000, currency: "USD" },
      { debit: null, currency: "USD" }
    ];
    const columns = [
      { fieldname: "debit", fieldtype: "Currency", options: "currency", precision: 2 }
    ];
    const [column] = tableUX.calculateColumnWidths(columns, rows, {
      measureText: (value) => String(value).length * 8
    });
    assert.equal(column.width, "L.L 1,830,000,000.00".length * 8 + 32 + 20);
    assert.equal(calls.length, 2);
    assert.deepEqual(calls[0], [1830000000, 2, true, "L.L"]);
    assert.equal(rows[0].debit, 1830000000);
  } finally {
    globalThis.frappe = originalFrappe;
  }
});
