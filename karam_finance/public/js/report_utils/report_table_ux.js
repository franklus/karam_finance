/* global module */

"use strict";

{
  const COLUMN_WIDTHS = Object.freeze({
    Currency: { minimum: 96, characterWidth: 8.5 },
    Date: { minimum: 96, characterWidth: 8 },
    Link: { minimum: 96, characterWidth: 8 },
    "Dynamic Link": { minimum: 110, characterWidth: 8 },
    Data: { minimum: 72, characterWidth: 8 },
    Select: { minimum: 96, characterWidth: 8 },
    default: { minimum: 72, characterWidth: 8 }
  });

  const MAXIMUM_COLUMN_WIDTH = 640,
    HEADER_PADDING = 48,
    DATATABLE_CONTENT_INSET = 20,
    CELL_PADDING = 12,
    CURRENCY_SYMBOL_GAP = 20;
  const SERIAL_PADDING = 28,
    MINIMUM_SERIAL_WIDTH = 30,
    TREE_INDENT_WIDTH = 20,
    TREE_TOGGLE_WIDTH = 16;
  let textMeasureContext;

  function asText(value) {
    if (value === null || value === undefined) {
      return "";
    }
    if (Array.isArray(value)) {
      return value.join(", ");
    }
    if (typeof value === "object") {
      return asText(value.name ?? value.value ?? "");
    }
    return String(value);
  }

  const getColumnFieldname = (column) => column?.fieldname || column?.id || "";
  const getColumnHeader = (column) =>
    asText(column?.name ?? column?.label ?? getColumnFieldname(column));

  function estimateTextWidth(value, characterWidth) {
    const text = asText(value);
    if (!text) {
      return 0;
    }

    if (
      !textMeasureContext &&
      typeof document !== "undefined" &&
      document.createElement
    ) {
      const canvas = document.createElement("canvas");
      textMeasureContext = canvas.getContext?.("2d") || null;
    }
    if (textMeasureContext?.measureText) {
      textMeasureContext.font = "14px Inter, sans-serif";
      return textMeasureContext.measureText(text).width;
    }

    return text.length * characterWidth;
  }

  function getCachedTextWidth(value, characterWidth, measureText, cache) {
    const text = asText(value);
    if (!text) return 0;
    const key = `${characterWidth}\u0000${text}`;
    if (!cache.has(key)) cache.set(key, measureText(text, characterWidth));
    return cache.get(key);
  }

  function getTreeIndentWidth(columnIndex, rows) {
    if (columnIndex !== 0) {
      return 0;
    }

    const maximumIndent = (rows || []).reduce((maximum, row) => {
      const indent = Number(row?.indent);
      return Number.isFinite(indent) ? Math.max(maximum, indent) : maximum;
    }, 0);
    return maximumIndent > 0
      ? maximumIndent * TREE_INDENT_WIDTH + TREE_TOGGLE_WIDTH
      : 0;
  }

  function createDisplayValueFormatter(column) {
    const formatter = globalThis.frappe?.form?.formatters?.Currency;
    if (column.fieldtype !== "Currency" || !formatter) {
      return (value) => value;
    }
    const formattedValues = new Map();
    return (value, row) => {
      if (value === null || value === undefined || value === "") {
        return "";
      }
      const currency = globalThis.frappe.meta.get_field_currency(column, row);
      const key = JSON.stringify([currency, value]);
      if (!formattedValues.has(key)) {
        formattedValues.set(key, formatter(value, column, { only_value: true }, row));
      }
      return formattedValues.get(key);
    };
  }

  function calculateColumnWidths(columns, rows, options = {}) {
    const maximum = options.maximumColumnWidth ?? MAXIMUM_COLUMN_WIDTH;
    const measureText = options.measureText || estimateTextWidth;
    const cache = new Map();

    return (columns || []).map((column, columnIndex) => {
      const config = COLUMN_WIDTHS[column.fieldtype] || COLUMN_WIDTHS.default;
      const fieldname = getColumnFieldname(column);
      const displayValue = createDisplayValueFormatter(column);
      const measure = (value) =>
        getCachedTextWidth(value, config.characterWidth, measureText, cache);
      const contentPadding =
        DATATABLE_CONTENT_INSET +
        CELL_PADDING +
        (column.fieldtype === "Currency" ? CURRENCY_SYMBOL_GAP : 0) +
        getTreeIndentWidth(columnIndex, rows);
      const textLimit = Math.max(0, maximum - contentPadding);
      let longestValueWidth = 0;

      for (const row of rows || []) {
        longestValueWidth = Math.max(
          longestValueWidth,
          measure(displayValue(row?.[column.id || fieldname], row))
        );
        if (longestValueWidth >= textLimit) {
          longestValueWidth = textLimit;
          break;
        }
      }

      const contentWidth = Math.ceil(longestValueWidth + contentPadding);
      const headerWidth = Math.ceil(measure(getColumnHeader(column)) + HEADER_PADDING);

      return {
        ...column,
        width: Math.max(config.minimum, headerWidth, Math.min(contentWidth, maximum))
      };
    });
  }

  function calculateSerialNumberWidth(rowCount) {
    const maximumSerialNumber = Math.max(1, Number(rowCount) || 0);
    return Math.max(
      MINIMUM_SERIAL_WIDTH,
      Math.ceil(estimateTextWidth(maximumSerialNumber, 8) + SERIAL_PADDING)
    );
  }

  function getRenderedHeaderWidth(datatable, column) {
    if (!column) {
      return 0;
    }
    const headerCell = datatable?.getColumnHeaderElement?.(column.colIndex);
    const headerContent = headerCell?.querySelector?.(
      `.dt-cell__content--header-${column.colIndex}`
    );
    return headerContent?.scrollWidth ? Math.ceil(headerContent.scrollWidth) : 0;
  }

  function applyRenderedColumnWidths(datatable, columns, options = {}) {
    const maximum = options.maximumColumnWidth ?? MAXIMUM_COLUMN_WIDTH;
    const currentColumns = datatable?.datamanager?.getColumns(true) || [];
    return columns.map((column, index) => {
      const currentColumn = currentColumns[index];
      const renderedHeaderWidth = getRenderedHeaderWidth(datatable, currentColumn);
      const cells = datatable?.bodyScrollable?.querySelectorAll?.(
        `.dt-cell__content--col-${currentColumn?.colIndex}`
      );
      const renderedBodyWidth = Math.min(
        Array.from(cells || []).reduce(
          (bodyWidth, cell) => Math.max(bodyWidth, Number(cell.scrollWidth) || 0),
          0
        ),
        maximum
      );
      const renderedWidth = Math.max(renderedHeaderWidth, renderedBodyWidth);
      return renderedWidth > column.width
        ? { ...column, width: renderedWidth }
        : column;
    });
  }

  function applyDynamicColumnWidths(options, rows, widthOptions = {}) {
    if (!options || !Array.isArray(options.columns)) {
      return options;
    }
    return {
      ...options,
      columns: calculateColumnWidths(options.columns, rows, widthOptions)
    };
  }

  function filterReportRows(rows, config) {
    const excludedRowFlags = config.excludedRowFlags || [];
    let visibleRows = excludedRowFlags.length
      ? rows.filter((row) => !excludedRowFlags.some((flag) => row?.[flag]))
      : rows;
    if (config.excludedTrailingRows) {
      visibleRows = visibleRows.slice(0, -config.excludedTrailingRows);
    }
    return visibleRows;
  }

  function getReportColumns(datatable) {
    return datatable.options?.columns || datatable.datamanager?.getColumns(true) || [];
  }

  function haveColumnWidthsChanged(currentColumns, resizedColumns) {
    return resizedColumns.some(
      (column, index) => currentColumns[index]?.width !== column.width
    );
  }

  function setSerialNumberColumnWidth(datatable, column, width) {
    Object.assign(column, { width });
    datatable.columnmanager?.setColumnHeaderWidth?.(column.colIndex);
    datatable.columnmanager?.setColumnWidth?.(column.colIndex, width);
    datatable.style?.setStickyColumnStyle?.();
    datatable.style?.setBodyStyle?.();
  }

  function applySerialNumberColumnWidth(datatable, rowCount) {
    const column = datatable?.datamanager?.getColumnById?.("_rowIndex");
    if (!column) return datatable;

    const width = calculateSerialNumberWidth(rowCount);
    const style = datatable.style;
    if (style) {
      // Tree expansion and filtering call setDimensions again after rendering.
      style.getRowIndexColumnWidth = () => width;
    }
    if (column.width !== width) setSerialNumberColumnWidth(datatable, column, width);
    return datatable;
  }

  function applyCurrentReportColumnWidths(options, config = {}) {
    const report = globalThis.frappe?.query_report;
    const rows = report?.data || options?.data || [];
    const visibleRows = report?._karamReportTableUXPreRendering
      ? rows
      : filterReportRows(rows, config);
    const visibleOptions =
      config.excludedRowFlags?.length || config.excludedTrailingRows
        ? { ...options, data: visibleRows, showTotalRow: false }
        : options;
    return applyDynamicColumnWidths(
      visibleOptions,
      visibleRows,
      config.widthOptions || {}
    );
  }

  function installPreRenderColumnWidths(report, config = {}) {
    const target = report;
    if (!target) {
      return target;
    }
    target._karamReportTableUXConfigs ||= new Map();
    target._karamReportTableUXConfigs.set(target.report_name, config);
    if (target._karamReportTableUXPreRenderInstalled) {
      return target;
    }

    const originalRenderDatatable = target.render_datatable;
    if (typeof originalRenderDatatable !== "function") {
      return target;
    }

    target.render_datatable = function renderKaramReportDatatable(...args) {
      const activeConfig = this._karamReportTableUXConfigs.get(this.report_name);
      if (!activeConfig) {
        return originalRenderDatatable.apply(this, args);
      }
      const sourceRows = this.data;
      const sourceColumns = this.columns;
      if (!Array.isArray(sourceRows) || !Array.isArray(sourceColumns)) {
        return originalRenderDatatable.apply(this, args);
      }

      const visibleRows = filterReportRows(sourceRows, activeConfig);
      const hadPreRenderingFlag = Object.hasOwn(
        this,
        "_karamReportTableUXPreRendering"
      );
      const previousPreRenderingFlag = this._karamReportTableUXPreRendering;
      this.data = visibleRows;
      this.columns = calculateColumnWidths(
        sourceColumns,
        visibleRows,
        activeConfig.widthOptions || {}
      );
      this._karamReportTableUXPreRendering = true;
      try {
        const result = originalRenderDatatable.apply(this, args);
        // DataTable creates its native serial column only during rendering.
        applySerialNumberColumnWidth(this.datatable, visibleRows.length);
        return result;
      } finally {
        this.data = sourceRows;
        this.columns = sourceColumns;
        if (hadPreRenderingFlag) {
          this._karamReportTableUXPreRendering = previousPreRenderingFlag;
        } else {
          delete this._karamReportTableUXPreRendering;
        }
      }
    };
    target._karamReportTableUXPreRenderInstalled = true;
    return target;
  }

  function refreshDatatable(datatable, rows, columns, sourceRows) {
    const currentColumns = datatable.datamanager?.getColumns(true) || [];
    const sourceChanged = datatable._karamReportTableUXSourceRows !== sourceRows;
    if (sourceChanged || haveColumnWidthsChanged(currentColumns, columns)) {
      datatable.refresh(rows, columns);
    }
  }

  function refreshCurrentReportColumnWidths(report, config = {}) {
    // Frappe skips get_datatable_options when it reuses an existing DataTable.
    const datatable = report?.datatable;
    if (!datatable) {
      return datatable;
    }

    const sourceRows = report.data || globalThis.frappe?.query_report?.data || [];
    const visibleRows = filterReportRows(sourceRows, config);
    const sourceColumns = getReportColumns(datatable);
    let resizedColumns = calculateColumnWidths(
      sourceColumns,
      visibleRows,
      config.widthOptions || {}
    );
    refreshDatatable(datatable, visibleRows, resizedColumns, sourceRows);
    datatable._karamReportTableUXSourceRows = sourceRows;
    resizedColumns = applyRenderedColumnWidths(
      datatable,
      resizedColumns,
      config.widthOptions || {}
    );
    refreshDatatable(datatable, visibleRows, resizedColumns, sourceRows);
    applySerialNumberColumnWidth(datatable, visibleRows.length);
    return datatable;
  }

  const removeTreeFooter = (report) => report?.$tree_footer?.remove();
  const getVisibleColumns = (columns) =>
    (columns || []).filter((column) => !column.hidden);

  const api = {
    applyCurrentReportColumnWidths,
    applyDynamicColumnWidths,
    asText,
    calculateColumnWidths,
    calculateSerialNumberWidth,
    getVisibleColumns,
    installPreRenderColumnWidths,
    applySerialNumberColumnWidth,
    applyRenderedColumnWidths,
    refreshCurrentReportColumnWidths,
    removeTreeFooter
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }

  if (typeof globalThis === "object") {
    globalThis.karamReportTableUX = api;
  }
}
