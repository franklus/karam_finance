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

    const context = getTextMeasureContext();
    if (context?.measureText) {
      context.font = "14px Inter, sans-serif";
      return context.measureText(text).width;
    }

    return text.length * characterWidth;
  }

  function getCachedTextWidth(value, characterWidth, measureText, cache) {
    const text = asText(value);
    if (!text) {
      return 0;
    }
    const key = `${characterWidth}\u0000${text}`;
    if (!cache.has(key)) {
      cache.set(key, measureText(text, characterWidth));
    }
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
      const longestValueWidth = measureColumnValues(
        rows,
        column.id || fieldname,
        displayValue,
        measure,
        textLimit
      );

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
    const headerContent = getHeaderContent(datatable, column.colIndex);
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

  function getTextMeasureContext() {
    if (
      !textMeasureContext &&
      typeof document !== "undefined" &&
      document.createElement
    ) {
      const canvas = document.createElement("canvas");
      textMeasureContext = canvas.getContext?.("2d") || null;
    }
    return textMeasureContext;
  }

  function measureColumnValues(rows, fieldname, displayValue, measure, textLimit) {
    let longestValueWidth = 0;

    for (const row of rows || []) {
      longestValueWidth = Math.max(
        longestValueWidth,
        measure(displayValue(row?.[fieldname], row))
      );
      if (longestValueWidth >= textLimit) {
        longestValueWidth = textLimit;
        break;
      }
    }

    return longestValueWidth;
  }

  function getHeaderContent(datatable, columnIndex) {
    const headerCell = datatable?.getColumnHeaderElement?.(columnIndex);
    return headerCell?.querySelector?.(`.dt-cell__content--header-${columnIndex}`);
  }

  const api = {
    asText,
    calculateColumnWidths,
    calculateSerialNumberWidth,
    applyRenderedColumnWidths
  };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  globalThis.karamReportColumnWidths = api;
}
