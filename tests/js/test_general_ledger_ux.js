const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const ux = require("../../karam_finance/public/js/report_utils/general_ledger_ux.js");
const tableUX = require("../../karam_finance/public/js/report_utils/report_table_ux.js");

test("GL sizes columns before DataTable renders", () => {
  const reportPath = path.resolve(
    __dirname,
    "../../karam_finance/karam_general/report/general_ledger_(karam)/general_ledger_(karam).js"
  );
  const reportSource = fs.readFileSync(reportPath, "utf8");

  assert.equal(reportSource.includes("show_amount_in_company_currency"), false);
  assert.equal(reportSource.includes("getKaramReportTableUX()"), true);
  assert.equal(
    reportSource.includes("getKaramGeneralLedgerUX().getVisibleColumns"),
    false
  );
  assert.equal(reportSource.includes("serialNoColumn = false"), true);
  assert.equal(reportSource.includes("applyKaramGeneralLedgerSerialNumbers"), false);
  assert.equal(
    reportSource.includes("installKaramGeneralLedgerPreRender(report)"),
    true
  );
  assert.equal(reportSource.includes("applyRenderedColumnWidths"), false);
  assert.equal(reportSource.includes("syncKaramGeneralLedgerDatatable"), false);
});

test("GL hides only the two leading values in DataTable's total row", () => {
  const totalColumn = (id) => ({ id, isHeader: true });

  assert.equal(ux.isLeadingTotalColumn(totalColumn(ux.SERIAL_NUMBER_FIELD)), true);
  assert.equal(ux.isLeadingTotalColumn(totalColumn("posting_date")), true);
  assert.equal(ux.isLeadingTotalColumn(totalColumn("account")), false);
  assert.equal(
    ux.isLeadingTotalColumn(totalColumn("posting_date"), {
      posting_date: "2026-03-31"
    }),
    false
  );
});

test("column widths follow the longest value in the current result", () => {
  const columns = [
    {
      fieldname: "account",
      fieldtype: "Link",
      label: "A deliberately much longer header than the data",
      width: 900
    },
    { fieldname: "remarks", fieldtype: "Data", label: "Remarks", width: 400 }
  ];
  const rows = [
    {
      account: "1000 - Cash",
      remarks: "x".repeat(2000)
    }
  ];

  const [account, remarks] = tableUX.calculateColumnWidths(columns, rows);
  assert.equal(account.width, 424);
  assert.equal(remarks.width, 640);
  assert.equal(columns[0].width, 900);
});

test("column width measurement caches repeats and stops at the cap", () => {
  let measurementCount = 0;
  const rows = Array.from({ length: 10_000 }, () => ({
    account: "1000 - Cash",
    remarks: "x".repeat(2000)
  }));

  const [account, remarks] = tableUX.calculateColumnWidths(
    [
      { fieldname: "account", fieldtype: "Link", label: "Account" },
      { fieldname: "remarks", fieldtype: "Data", label: "Remarks" }
    ],
    rows,
    {
      measureText(text, characterWidth) {
        measurementCount += 1;
        return text.length * characterWidth;
      }
    }
  );

  assert.equal(account.width, 120);
  assert.equal(remarks.width, 640);
  assert.equal(measurementCount, 4);
});

test("column widths are recalculated for each report result", () => {
  const columns = [{ fieldname: "account", fieldtype: "Link", width: 500 }];
  const shortView = tableUX.calculateColumnWidths(columns, [{ account: "Cash" }]);
  const longView = tableUX.calculateColumnWidths(columns, [
    { account: "A substantially longer account value for this view" }
  ]);

  assert.equal(shortView[0].width, 104);
  assert.ok(longView[0].width > shortView[0].width);
});

test("pathological structured values cannot expand the whole report", () => {
  const [against] = tableUX.calculateColumnWidths(
    [{ fieldname: "against" }],
    [{ against: "x".repeat(977) }]
  );

  assert.equal(against.width, 640);
});

test("visible-column selection excludes hidden report columns", () => {
  const columns = tableUX.getVisibleColumns([
    { fieldname: "gl_entry", hidden: 1 },
    { fieldname: "posting_date", hidden: 0 },
    { fieldname: "account" }
  ]);

  assert.deepEqual(
    columns.map((column) => column.fieldname),
    ["posting_date", "account"]
  );
});

test("pagination keeps footer rows out of the paged body and reports an accessible range", () => {
  const body = Array.from({ length: 5014 }, (_value, index) => ({
    name: `GLE-${index}`
  }));
  const rows = [
    ...body,
    { account: "'Total'", row_type: "report_total" },
    { account: "'Closing (Opening + Total)'", row_type: "closing" }
  ];
  const state = ux.createPaginationState();

  let page = ux.paginateRows(rows, state);
  assert.equal(page.rows.length, 250);
  assert.equal(page.footerRows.length, 2);
  assert.equal(ux.formatRange(page.range), "1–250 of 5,014");

  state.setPage(99);
  page = ux.paginateRows(rows, state);
  assert.equal(state.page, 21);
  assert.equal(page.rows.length, 14);
  assert.equal(ux.formatRange(page.range), "5,001–5,014 of 5,014");

  state.setPageSize(1000);
  page = ux.paginateRows(rows, state);
  assert.equal(state.page, 1);
  assert.equal(page.rows.length, 1000);
  assert.equal(ux.formatRange(page.range), "1–1,000 of 5,014");
});

test("pagination preserves group closing rows and only removes the trailing footer", () => {
  const rows = [
    { account: "'Opening'", row_type: "opening" },
    { account: "'Total for Cash'", row_type: "group_total" },
    { account: "'Closing for Cash'", row_type: "closing" },
    { is_separator: 1, row_type: "separator" },
    { account: "'Total'", row_type: "report_total" },
    { account: "'Closing (Opening + Total)'", row_type: "closing" }
  ];

  const split = ux.splitFooterRows(rows);
  assert.equal(split.bodyRows.length, 3);
  assert.equal(split.bodyRows[2].account, "'Closing for Cash'");
  assert.equal(split.footerRows.length, 3);
  assert.equal(split.footerRows[0].row_type, "separator");
  assert.equal(split.footerRows[1].row_type, "report_total");
  assert.equal(split.footerRows[2].row_type, "closing");
});

test("pagination handles an empty report and clamps invalid page sizes", () => {
  const state = ux.createPaginationState(42);
  const page = ux.paginateRows([], state);

  assert.equal(state.pageSize, 250);
  assert.deepEqual(page.range, { start: 0, end: 0, total: 0 });
  assert.equal(ux.formatRange(page.range), "0 of 0");
  assert.deepEqual(page.rows, []);
  assert.deepEqual(page.footerRows, []);
});

test("pagination clamps the current page when a refreshed result shrinks", () => {
  const state = ux.createPaginationState();
  state.setTotal(5014).setPage(21);
  state.setTotal(20);

  assert.equal(state.page, 1);
  assert.deepEqual(state.getRange(), { start: 1, end: 20, total: 20 });
});

test("pagination materialises stable absolute serial numbers before render", () => {
  const sourceRows = Array.from({ length: 4980 }, (_value, index) => ({ index }));
  const state = ux.createPaginationState();
  state.setTotal(sourceRows.length).setPage(2);

  let rows = ux.addAbsoluteSerialNumbers(state.getRows(sourceRows), state.getOffset());
  assert.equal(rows[0][ux.SERIAL_NUMBER_FIELD], 251);
  assert.equal(rows.at(-1)[ux.SERIAL_NUMBER_FIELD], 500);
  assert.equal(sourceRows[250][ux.SERIAL_NUMBER_FIELD], undefined);

  state.setPageSize(500).setPage(3);
  rows = ux.addAbsoluteSerialNumbers(state.getRows(sourceRows), state.getOffset());
  assert.equal(rows[0][ux.SERIAL_NUMBER_FIELD], 1001);
  assert.equal(rows.at(-1)[ux.SERIAL_NUMBER_FIELD], 1500);
  assert.deepEqual(state.getRange(), { start: 1001, end: 1500, total: 4980 });
});

test("Show All restores the complete unpaged result", () => {
  const rows = Array.from({ length: 4980 }, (_value, index) => ({ index }));
  const state = ux.createPaginationState();
  state.setTotal(rows.length).setPage(10);
  state.setPageSize(ux.SHOW_ALL_PAGE_SIZE);

  assert.equal(state.page, 1);
  assert.equal(state.getTotalPages(), 1);
  assert.equal(state.getOffset(), 0);
  assert.equal(state.getRows(rows).length, 4980);
  assert.deepEqual(state.getRange(), { start: 1, end: 4980, total: 4980 });
});
