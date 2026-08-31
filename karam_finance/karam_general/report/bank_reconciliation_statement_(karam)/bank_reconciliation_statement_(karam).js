// Copyright (c) 2024, Noospheric and contributors
// For license information, please see license.txt

frappe.query_reports["Bank Reconciliation Statement (Karam)"] = {
  filters: [
    {
      fieldname: "company",
      label: __("Company"),
      fieldtype: "Link",
      options: "Company",
      reqd: 1,
      default: frappe.defaults.get_user_default("Company")
    },
    {
      fieldname: "account",
      label: __("Bank Account"),
      fieldtype: "Link",
      options: "Account",
      default: frappe.defaults.get_user_default("Company")
        ? (locals[":Company"]?.[frappe.defaults.get_user_default("Company")]
            ?.default_bank_account ?? "")
        : "",
      reqd: 1,
      get_query() {
        const company = frappe.query_report.get_filter_value("company");
        return {
          query: "erpnext.controllers.queries.get_account_list",
          filters: [
            ["Account", "account_type", "in", "Bank, Cash"],
            ["Account", "is_group", "=", 0],
            ["Account", "disabled", "=", 0],
            ["Account", "company", "=", company]
          ]
        };
      }
    },
    {
      fieldname: "report_date",
      label: __("Date"),
      fieldtype: "Date",
      default: frappe.datetime.get_today(),
      reqd: 1
    },
    {
      fieldname: "include_pos_transactions",
      label: __("Include POS Transactions"),
      fieldtype: "Check"
    }
  ],
  get_datatable_options(options) {
    return (
      window.karamReportTableUX?.applyCurrentReportColumnWidths(options) || options
    );
  },
  after_datatable_render() {
    window.karamReportTableUX?.refreshCurrentReportColumnWidths(frappe.query_report);
  },
  formatter(value, row, column, _data, default_formatter) {
    if (
      column.fieldname === "payment_entry" &&
      value === __("Cheques and Deposits incorrectly cleared")
    ) {
      column.link_onclick =
        "frappe.query_reports['Bank Reconciliation Statement (Karam)'].open_utility_report()";
    } else {
      delete column.link_onclick;
    }
    const formatted = default_formatter(value, row, column, _data);
    return alignCurrencyWithSharedHelper(
      "Bank Reconciliation Statement (Karam)",
      value,
      column,
      formatted
    );
  },
  open_utility_report() {
    frappe.route_options = {
      company: frappe.query_report.get_filter_value("company"),
      account: frappe.query_report.get_filter_value("account"),
      report_date: frappe.query_report.get_filter_value("report_date")
    };
    frappe.open_in_new_tab = true;
    frappe.set_route("query-report", "Cheques and Deposits Incorrectly cleared");
  }
};
