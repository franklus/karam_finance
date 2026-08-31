// General Ledger (Reporting): GL report using Reporting Currency GLE

frappe.query_reports["General Ledger (Reporting)"] = {
  get_datatable_options(options) {
    return (
      window.karamReportTableUX?.applyCurrentReportColumnWidths(options, {
        excludedRowFlags: ["is_spacer", "is_report_footer"]
      }) || options
    );
  },
  after_datatable_render() {
    window.karamReportTableUX?.refreshCurrentReportColumnWidths(frappe.query_report, {
      excludedRowFlags: ["is_spacer", "is_report_footer"]
    });
  },
  formatter(value, row, column, data, default_formatter) {
    if (data && (data.is_separator || data.row_type === "separator")) {
      return "";
    }
    const formatted = default_formatter(value, row, column, data);
    return alignCurrencyWithSharedHelper(
      "General Ledger (Reporting)",
      value,
      column,
      formatted
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
      reqd: 1,
      width: "60px"
    },
    {
      fieldname: "to_date",
      label: __("To Date"),
      fieldtype: "Date",
      default: frappe.datetime.get_today(),
      reqd: 1,
      width: "60px"
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
      fieldname: "voucher_no",
      label: __("Voucher No"),
      fieldtype: "Data",
      on_change() {
        frappe.query_report.set_filter_value(
          "categorize_by",
          "Categorise by Voucher (Consolidated)"
        );
      }
    },
    {
      fieldname: "against_voucher_no",
      label: __("Against Voucher No"),
      fieldtype: "Data"
    },
    { fieldtype: "Break" },
    {
      fieldname: "party_type",
      label: __("Party Type"),
      fieldtype: "Autocomplete",
      options: Object.keys(frappe.boot.party_account_types),
      on_change() {
        frappe.query_report.set_filter_value("party", []);
      }
    },
    {
      fieldname: "party",
      label: __("Party"),
      fieldtype: "MultiSelectList",
      get_data(txt) {
        if (!frappe.query_report.filters) return undefined;
        const party_type = frappe.query_report.get_filter_value("party_type");
        if (!party_type) return undefined;
        return frappe.db.get_link_options(party_type, txt);
      },
      on_change() {
        const party_type = frappe.query_report.get_filter_value("party_type");
        const parties = frappe.query_report.get_filter_value("party");
        if (!party_type || parties.length === 0 || parties.length > 1) {
          frappe.query_report.set_filter_value("party_name", "");
          frappe.query_report.set_filter_value("tax_id", "");
        } else {
          const party = parties[0];
          const fieldname = erpnext.utils.get_party_name(party_type) || "name";
          frappe.db.get_value(party_type, party, fieldname, (value) => {
            frappe.query_report.set_filter_value("party_name", value[fieldname]);
          });
          if (party_type === "Customer" || party_type === "Supplier") {
            frappe.db.get_value(party_type, party, "tax_id", (value) => {
              frappe.query_report.set_filter_value("tax_id", value.tax_id);
            });
          }
        }
      }
    },
    { fieldname: "party_name", label: __("Party Name"), fieldtype: "Data", hidden: 1 },
    {
      fieldname: "categorize_by",
      label: __("Categorise by"),
      fieldtype: "Select",
      options: [
        "",
        { label: __("Categorise by Voucher"), value: "Categorise by Voucher" },
        {
          label: __("Categorise by Voucher (Consolidated)"),
          value: "Categorise by Voucher (Consolidated)"
        },
        { label: __("Categorise by Account"), value: "Categorise by Account" },
        { label: __("Categorise by Party"), value: "Categorise by Party" }
      ],
      default: "Categorise by Voucher (Consolidated)"
    },
    { fieldname: "tax_id", label: __("Tax Id"), fieldtype: "Data", hidden: 1 },
    {
      fieldname: "cost_center",
      label: __("Cost Center"),
      fieldtype: "MultiSelectList",
      options: "Cost Center",
      get_data(txt) {
        return frappe.db.get_link_options("Cost Center", txt, {
          company: frappe.query_report.get_filter_value("company")
        });
      }
    },
    {
      fieldname: "project",
      label: __("Project"),
      fieldtype: "MultiSelectList",
      options: "Project",
      get_data(txt) {
        return frappe.db.get_link_options("Project", txt, {
          company: frappe.query_report.get_filter_value("company")
        });
      }
    },
    {
      fieldname: "show_opening_entries",
      label: __("Show Opening Entries"),
      fieldtype: "Check"
    },
    {
      fieldname: "show_cancelled_entries",
      label: __("Show Cancelled Entries"),
      fieldtype: "Check"
    },
    {
      fieldname: "show_net_values_in_party_account",
      label: __("Show Net Values in Party Account"),
      fieldtype: "Check"
    },
    { fieldname: "show_remarks", label: __("Show Remarks"), fieldtype: "Check" },
    {
      fieldname: "ignore_err",
      label: __("Ignore Exchange Rate Revaluation and Gain / Loss Journals"),
      fieldtype: "Check"
    },
    {
      fieldname: "ignore_cr_dr_notes",
      label: __("Ignore System Generated Credit / Debit Notes"),
      fieldtype: "Check"
    },
    {
      fieldname: "reporting_doe",
      label: __("Reporting DOE"),
      fieldtype: "Check",
      default: 0
    },
    {
      fieldname: "manual_entry",
      label: __("Manual Entry"),
      fieldtype: "Check",
      default: 0
    }
  ]
};
