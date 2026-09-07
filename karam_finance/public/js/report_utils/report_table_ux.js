/* global module, require */

"use strict";

{
  const {
    asText,
    calculateColumnWidths,
    calculateSerialNumberWidth,
    applyRenderedColumnWidths
  } =
    typeof module !== "undefined" && module.exports
      ? require("./report_column_widths.js")
      : globalThis.karamReportColumnWidths;

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
    updateSerialNumberStyles(datatable.style);
  }

  function applySerialNumberColumnWidth(datatable, rowCount) {
    const column = datatable?.datamanager?.getColumnById?.("_rowIndex");
    if (!column) {
      return datatable;
    }

    const width = calculateSerialNumberWidth(rowCount);
    const style = datatable.style;
    if (style) {
      // Tree expansion and filtering call setDimensions again after rendering.
      style.getRowIndexColumnWidth = () => width;
    }
    if (column.width !== width) {
      setSerialNumberColumnWidth(datatable, column, width);
    }
    return datatable;
  }

  function applyCurrentReportColumnWidths(options, config = {}) {
    const report = currentReport();
    const rows = reportRows(report, options);
    const visibleRows = report?._karamReportTableUXPreRendering
      ? rows
      : filterReportRows(rows, config);
    const visibleOptions = visibleReportOptions(options, visibleRows, config);
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

    const sourceRows = reportRows(report, currentReport());
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

  function updateSerialNumberStyles(style) {
    style?.setStickyColumnStyle?.();
    style?.setBodyStyle?.();
  }

  function currentReport() {
    return globalThis.frappe?.query_report;
  }

  function reportRows(report, options) {
    return report?.data || options?.data || [];
  }

  function visibleReportOptions(options, visibleRows, config) {
    return config.excludedRowFlags?.length || config.excludedTrailingRows
      ? { ...options, data: visibleRows, showTotalRow: false }
      : options;
  }

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
