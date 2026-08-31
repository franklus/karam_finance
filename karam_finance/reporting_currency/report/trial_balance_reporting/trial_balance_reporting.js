// Copyright (c) 2026, Noospheric
// For license information, please see license.txt

frappe.query_reports["Trial Balance (Reporting)"] = {
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
      fieldname: "fiscal_year",
      label: __("Fiscal Year"),
      fieldtype: "Link",
      options: "Fiscal Year",
      default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today()),
      reqd: 1,
      on_change(query_report) {
        const { fiscal_year } = query_report.get_values();
        if (!fiscal_year) {
          return;
        }
        frappe.model.with_doc("Fiscal Year", fiscal_year, () => {
          const fy = frappe.model.get_doc("Fiscal Year", fiscal_year);
          frappe.query_report.set_filter_value({
            from_date: fy.year_start_date,
            to_date: fy.year_end_date
          });
        });
      }
    },
    {
      fieldname: "from_date",
      label: __("From Date"),
      fieldtype: "Date",
      default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today(), true)[1]
    },
    {
      fieldname: "to_date",
      label: __("To Date"),
      fieldtype: "Date",
      default: erpnext.utils.get_fiscal_year(frappe.datetime.get_today(), true)[2]
    },
    {
      fieldname: "cost_center",
      label: __("Cost Center"),
      fieldtype: "MultiSelectList",
      get_data(txt) {
        return frappe.db.get_link_options("Cost Center", txt, {
          company: frappe.query_report.get_filter_value("company")
        });
      },
      options: "Cost Center"
    },
    {
      fieldname: "project",
      label: __("Project"),
      fieldtype: "MultiSelectList",
      get_data(txt) {
        return frappe.db.get_link_options("Project", txt, {
          company: frappe.query_report.get_filter_value("company")
        });
      },
      options: "Project"
    },
    {
      fieldname: "finance_book",
      label: __("Finance Book"),
      fieldtype: "Link",
      options: "Finance Book"
    },
    {
      fieldname: "with_period_closing_entry_for_opening",
      label: __("With Period Closing Entry For Opening Balances"),
      fieldtype: "Check",
      default: 1
    },
    {
      fieldname: "with_period_closing_entry_for_current_period",
      label: __("Period Closing Entry For Current Period"),
      fieldtype: "Check",
      default: 1
    },
    {
      fieldname: "include_default_book_entries",
      label: __("Include Default FB Entries"),
      fieldtype: "Check",
      default: 1
    }
  ],
  get_datatable_options(options) {
    return (
      window.karamReportTableUX?.applyCurrentReportColumnWidths(options, {
        excludedTrailingRows: 2
      }) || options
    );
  },
  after_datatable_render() {
    window.karamReportTableUX?.refreshCurrentReportColumnWidths(frappe.query_report, {
      excludedTrailingRows: 2
    });
  },
  after_refresh(report) {
    window.karamReportTableUX?.removeTreeFooter(report);
  },
  formatter(value, row, column, data, default_formatter) {
    const formatted = erpnext.financial_statements.formatter(
      value,
      row,
      column,
      data,
      default_formatter
    );
    return alignCurrencyWithSharedHelper(
      "Trial Balance (Reporting)",
      value,
      column,
      formatted
    );
  },
  tree: true,
  name_field: "account",
  parent_field: "parent_account",
  initial_depth: 3
};

// Add accounting dimensions (Letter of Credit, Auxiliary, etc.) dynamically
erpnext.utils.add_dimensions("Trial Balance (Reporting)", 6);
