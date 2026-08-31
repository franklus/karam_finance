// Trial Balance for Party (Reporting): mirrors ERPNext Trial Balance for Party
// but reads from Reporting Currency GLE and uses Reporting Currency Settings.

frappe.query_reports["Trial Balance for Party (Reporting)"] = {
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
      fieldname: "party_type",
      label: __("Party Type"),
      fieldtype: "Link",
      options: "Party Type",
      default: "Customer",
      reqd: 1
    },
    {
      fieldname: "party",
      label: __("Party"),
      fieldtype: "Dynamic Link",
      get_options() {
        const party_type = frappe.query_report.get_filter_value("party_type");
        const party = frappe.query_report.get_filter_value("party");
        if (party && !party_type) {
          frappe.throw(__("Please select Party Type first"));
        }
        return party_type;
      }
    },
    {
      fieldname: "account",
      label: __("Account"),
      fieldtype: "MultiSelectList",
      options: "Account",
      get_data(txt) {
        return frappe.db.get_link_options("Account", txt, {
          company: frappe.query_report.get_filter_value("company")
        });
      }
    },
    {
      fieldname: "show_zero_values",
      label: __("Show zero values"),
      fieldtype: "Check"
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
  formatter(value, row, column, data, default_formatter) {
    let formatted = default_formatter(value, row, column, data);
    formatted = alignCurrencyWithSharedHelper(
      "Trial Balance for Party (Reporting)",
      value,
      column,
      formatted
    );
    if (data && data.bold) {
      formatted = $(`<span>${formatted}</span>`)
        .css("font-weight", "bold")
        .wrap("<p></p>")
        .parent()
        .html();
    }
    return formatted;
  }
};
