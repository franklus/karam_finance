frappe.query_reports["Asset Depreciation Ledger Summary (Karam)"] = {
  onload(report) {
    window.karamReportTableUX?.installPreRenderColumnWidths(report);
    const fallbackOptions = [
      "",
      "Partially Depreciated",
      "Fully Depreciated",
      "Scrapped",
      "Sold",
      "Disposed"
    ];

    frappe.model.with_doctype("Asset", () => {
      const statusField = frappe
        .get_meta("Asset")
        ?.fields?.find((field) => field.fieldname === "status");
      const options = statusField?.options
        ? ["", ...statusField.options.split("\n")]
        : fallbackOptions;
      const statusFilter = report.get_filter("status");
      if (statusFilter) {
        statusFilter.df.options = options.join("\n");
        statusFilter.refresh();
      }
    });
  },
  formatter(value, row, column, data, default_formatter) {
    const formatted = default_formatter(value, row, column, data);
    return window.alignCurrencyWithSharedHelper(
      "Asset Depreciation Ledger Summary (Karam)",
      value,
      column,
      formatted
    );
  },
  get_datatable_options(options) {
    return (
      window.karamReportTableUX?.applyCurrentReportColumnWidths(options) || options
    );
  },
  filters: [
    {
      fieldname: "company",
      label: __("Company"),
      fieldtype: "Link",
      options: "Company",
      default: frappe.defaults.get_user_default("Company"),
      reqd: 1
    },
    {
      fieldname: "from_date",
      label: __("From Date"),
      fieldtype: "Date",
      default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
      reqd: 1
    },
    {
      fieldname: "to_date",
      label: __("To Date"),
      fieldtype: "Date",
      default: frappe.datetime.get_today(),
      reqd: 1
    },
    {
      fieldname: "asset",
      label: __("Asset"),
      fieldtype: "Link",
      options: "Asset"
    },
    {
      fieldname: "asset_category",
      label: __("Asset Category"),
      fieldtype: "Link",
      options: "Asset Category"
    },
    {
      fieldname: "status",
      label: __("Status"),
      fieldtype: "Select",
      options: ""
    },
    {
      fieldname: "finance_book",
      label: __("Finance Book"),
      fieldtype: "Link",
      options: "Finance Book"
    },
    {
      fieldname: "include_default_book_assets",
      label: __("Include Default FB Assets"),
      fieldtype: "Check",
      default: 1
    }
  ]
};
