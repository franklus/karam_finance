/* global module */

"use strict";

{
  const DEFAULT_PAGE_SIZE = 250;
  const SHOW_ALL_PAGE_SIZE = 0;
  const PAGE_SIZES = Object.freeze([SHOW_ALL_PAGE_SIZE, 250, 500, 1000]);
  const FOOTER_ROW_TYPES = Object.freeze(new Set(["report_total", "closing"]));
  const SERIAL_NUMBER_FIELD = "_karamSerialNumber";

  function asText(value) {
    if (value === null || value === undefined) return "";
    if (Array.isArray(value)) return value.join(", ");
    if (typeof value === "object") return asText(value.name ?? value.value ?? "");
    return String(value);
  }

  function escapeHtml(value) {
    const entities = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "'": "&#39;",
      '"': "&quot;"
    };
    return asText(value).replace(/[&<>'"]/g, (character) => entities[character]);
  }

  function isFooterRow(row) {
    return Boolean(row && FOOTER_ROW_TYPES.has(row.row_type));
  }

  function splitFooterRows(rows) {
    const allRows = rows || [];
    const footerRows = [];
    let footerIndex = allRows.length - 1;

    while (footerIndex >= 0 && isFooterRow(allRows[footerIndex])) {
      footerRows.unshift(allRows[footerIndex]);
      footerIndex -= 1;
    }
    if (
      footerRows.length &&
      (allRows[footerIndex]?.is_separator ||
        allRows[footerIndex]?.row_type === "separator")
    ) {
      footerRows.unshift(allRows[footerIndex]);
      footerIndex -= 1;
    }

    return {
      bodyRows: allRows.slice(0, footerIndex + 1),
      footerRows
    };
  }

  function clampPage(page, totalItems, pageSize) {
    const totalPages =
      pageSize === SHOW_ALL_PAGE_SIZE
        ? 1
        : Math.max(1, Math.ceil(totalItems / pageSize));
    return Math.min(Math.max(1, Number(page) || 1), totalPages);
  }

  function createPaginationState(pageSize = DEFAULT_PAGE_SIZE) {
    const state = {
      page: 1,
      pageSize: PAGE_SIZES.includes(pageSize) ? pageSize : DEFAULT_PAGE_SIZE,
      totalItems: 0,
      setTotal(totalItems) {
        this.totalItems = Math.max(0, Number(totalItems) || 0);
        this.page = clampPage(this.page, this.totalItems, this.pageSize);
        return this;
      },
      setPage(page) {
        this.page = clampPage(page, this.totalItems, this.pageSize);
        return this;
      },
      setPageSize(nextPageSize) {
        this.pageSize = PAGE_SIZES.includes(nextPageSize)
          ? nextPageSize
          : DEFAULT_PAGE_SIZE;
        this.page = 1;
        return this;
      },
      reset(totalItems = 0) {
        this.page = 1;
        return this.setTotal(totalItems);
      },
      getTotalPages() {
        if (this.pageSize === SHOW_ALL_PAGE_SIZE) return 1;
        return Math.max(1, Math.ceil(this.totalItems / this.pageSize));
      },
      getOffset() {
        if (this.pageSize === SHOW_ALL_PAGE_SIZE) return 0;
        return (this.page - 1) * this.pageSize;
      },
      getRange() {
        if (!this.totalItems) return { start: 0, end: 0, total: 0 };
        if (this.pageSize === SHOW_ALL_PAGE_SIZE) {
          return { start: 1, end: this.totalItems, total: this.totalItems };
        }
        const start = this.getOffset();
        return {
          start: start + 1,
          end: Math.min(start + this.pageSize, this.totalItems),
          total: this.totalItems
        };
      },
      getRows(rows) {
        if (this.pageSize === SHOW_ALL_PAGE_SIZE) return (rows || []).slice();
        const start = this.getOffset();
        return (rows || []).slice(start, start + this.pageSize);
      }
    };

    return state;
  }

  function isLeadingTotalColumn(column, data) {
    return Boolean(
      !data &&
      column?.isHeader &&
      [SERIAL_NUMBER_FIELD, "posting_date"].includes(column.id)
    );
  }

  function addAbsoluteSerialNumbers(rows, offset = 0) {
    const firstIndex = Math.max(0, Number(offset) || 0);
    return (rows || []).map((row, index) => ({
      ...row,
      [SERIAL_NUMBER_FIELD]: firstIndex + index + 1
    }));
  }

  function paginateRows(rows, state) {
    const { bodyRows, footerRows } = splitFooterRows(rows);
    state.setTotal(bodyRows.length);

    return {
      rows: state.getRows(bodyRows),
      bodyRows,
      footerRows,
      range: state.getRange(),
      totalPages: state.getTotalPages()
    };
  }

  function formatRange(range) {
    if (!range.total) return "0 of 0";
    const start = range.start.toLocaleString();
    const end = range.end.toLocaleString();
    const total = range.total.toLocaleString();
    return `${start}–${end} of ${total}`;
  }

  const api = {
    DEFAULT_PAGE_SIZE,
    PAGE_SIZES,
    SHOW_ALL_PAGE_SIZE,
    SERIAL_NUMBER_FIELD,
    addAbsoluteSerialNumbers,
    createPaginationState,
    escapeHtml,
    formatRange,
    isFooterRow,
    isLeadingTotalColumn,
    paginateRows,
    splitFooterRows
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }

  if (typeof globalThis === "object") {
    globalThis.karamGeneralLedgerUX = api;
  }
}
