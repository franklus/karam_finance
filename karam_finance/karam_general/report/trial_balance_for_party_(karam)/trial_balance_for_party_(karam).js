// Trial Balance for Party (Karam): ERPNext Trial Balance for Party with Account Currency columns

frappe.query_reports["Trial Balance for Party (Karam)"] = {
  onload(report) {
    window.karamReportTableUX?.installPreRenderColumnWidths(report);
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
    },
    {
      fieldname: "exclude_zero_balance_parties",
      label: __("Exclude Zero Balance Parties"),
      fieldtype: "Check",
      default: 1
    }
  ],
  get_datatable_options(options) {
    return (
      window.karamReportTableUX?.applyCurrentReportColumnWidths(options) || options
    );
  },
  formatter(value, row, column, data, default_formatter) {
    let formatted = default_formatter(value, row, column, data);
    formatted = window.alignCurrencyWithSharedHelper(
      "Trial Balance for Party (Karam)",
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
