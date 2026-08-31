// Profit and Loss Statement by Cost Center
// Duplicates ERPNext P&L filters and UI; results respect Cost Center filter(s).

function getSelectedView() {
  return frappe.query_report.get_filter_value("selected_view") || "Report";
}

function formatSelectedViewMetric(selectedView, column, rawValue) {
  if (!column) return null;
  const threshold =
    selectedView === "Growth" ? 3 : selectedView === "Margin" ? 2 : null;
  if (threshold === null || column.colIndex < threshold) return null;
  if (rawValue == null) return "NA";
  const prefix = selectedView === "Growth" && rawValue >= 0 ? "+" : "";
  const output = $(`<span>${prefix}${rawValue}%</span>`).addClass(
    rawValue < 0 ? "text-danger" : "text-success"
  );
  return output.wrap("<p></p>").parent().html();
}

frappe.query_reports["Profit and Loss Statement by Cost Center"] = $.extend(
  {},
  erpnext.financial_statements
);
erpnext.utils.add_dimensions("Profit and Loss Statement by Cost Center", 10);

// Reuse P&L view options and flags
frappe.query_reports["Profit and Loss Statement by Cost Center"].filters.push({
  fieldname: "selected_view",
  label: __("Select View"),
  fieldtype: "Select",
  options: [
    { value: "Report", label: __("Report View") },
    { value: "Growth", label: __("Growth View") },
    { value: "Margin", label: __("Margin View") }
  ],
  default: "Report",
  reqd: 1
});

// Override tree configuration to use Cost Center hierarchy
frappe.query_reports["Profit and Loss Statement by Cost Center"].tree = true;
frappe.query_reports["Profit and Loss Statement by Cost Center"].name_field =
  "cost_center";
frappe.query_reports["Profit and Loss Statement by Cost Center"].parent_field =
  "parent_cost_center";
frappe.query_reports["Profit and Loss Statement by Cost Center"].initial_depth = 3;

// Formatter: bold group rows and suppress non-data payload rows
function isFooterDataRow(data) {
  return Boolean(data?.is_spacer || data?.is_footer || data?.is_footer_payload);
}

function isBoldRow(data) {
  return Boolean(data?.is_group || data?.bold);
}

function formatterExtraStyles(data, column) {
  const styles = [];
  if (isBoldRow(data)) styles.push("font-weight:bold;");
  if (data?.warn_if_negative && data[column.fieldname] < 0) {
    styles.push("color:var(--red-500);");
  }
  return styles.join("");
}

function applyBoldFormatterOutput(value, data) {
  if (!isBoldRow(data)) return value;
  return $(`<span>${value}</span>`)
    .css("font-weight", "bold")
    .wrap("<p></p>")
    .parent()
    .html();
}

function costCenterFormatter(value, row, column, data, default_formatter) {
  if (isFooterDataRow(data)) return "";
  if (data?.cost_center && column.fieldname === "cost_center") {
    column.is_tree = true;
  }

  const selectedViewOutput = formatSelectedViewMetric(
    getSelectedView(),
    column,
    data?.[column.fieldname] ?? value
  );
  if (selectedViewOutput != null) return selectedViewOutput;

  const formatted = default_formatter(value, row, column, data);
  const aligned = alignCurrencyWithSharedHelper(
    "Profit and Loss Statement by Cost Center",
    value,
    column,
    formatted,
    formatterExtraStyles(data, column)
  );
  return applyBoldFormatterOutput(aligned, data);
}

function getPlccDatatableOptions(options) {
  const resized =
    window.karamReportTableUX?.applyCurrentReportColumnWidths(options, {
      excludedRowFlags: ["is_spacer", "is_footer", "is_footer_payload"]
    }) || options;
  return { ...resized, showTotalRow: false, serialNoColumn: true };
}

frappe.query_reports["Profit and Loss Statement by Cost Center"].formatter =
  costCenterFormatter;
frappe.query_reports["Profit and Loss Statement by Cost Center"].get_datatable_options =
  getPlccDatatableOptions;
frappe.query_reports[
  "Profit and Loss Statement by Cost Center"
].after_datatable_render = () =>
  window.karamReportTableUX?.refreshCurrentReportColumnWidths(frappe.query_report, {
    excludedRowFlags: ["is_spacer", "is_footer", "is_footer_payload"]
  });
frappe.query_reports["Profit and Loss Statement by Cost Center"].after_refresh = (
  report
) => window.karamReportTableUX?.removeTreeFooter(report);

frappe.query_reports["Profit and Loss Statement by Cost Center"].filters.push({
  fieldname: "accumulated_values",
  label: __("Accumulated Values"),
  fieldtype: "Check",
  default: 1
});

frappe.query_reports["Profit and Loss Statement by Cost Center"].filters.push({
  fieldname: "include_default_book_entries",
  label: __("Include Default FB Entries"),
  fieldtype: "Check",
  default: 1
});

frappe.query_reports["Profit and Loss Statement by Cost Center"].filters.push({
  fieldname: "show_zero_values",
  label: __("Show zero values"),
  fieldtype: "Check",
  default: 0
});
