// Trial Balance (Karam): ERPNext Trial Balance with Account Currency columns

function trialBalanceKaramFormatter(value, row, column, data, default_formatter) {
  // Return empty string for spacer and total rows (total shown in sticky footer)
  if (data && (data.is_spacer || data.is_total)) {
    return "";
  }
  const formatted = erpnext.financial_statements.formatter(
    value,
    row,
    column,
    data,
    default_formatter
  );
  let extra = "";
  if (data && !data.parent_account) extra += "font-weight:bold;";
  if (data && data.warn_if_negative && data[column.fieldname] < 0) {
    extra += "color:var(--red-500);";
  }
  return alignCurrencyWithSharedHelper(
    "Trial Balance (Karam)",
    value,
    column,
    formatted,
    extra
  );
}

function setDateRangeModeRequirements(query_report) {
  const ignoreFiscalYear = query_report.get_filter_value("ignore_fiscal_year");
  const requiredFilters = {
    fiscal_year: !ignoreFiscalYear,
    from_date: ignoreFiscalYear,
    to_date: ignoreFiscalYear
  };

  Object.entries(requiredFilters).forEach(([fieldname, isRequired]) => {
    const filter = query_report.get_filter(fieldname);
    filter.df.reqd = isRequired ? 1 : 0;
    filter.refresh();
  });
}

frappe.query_reports["Trial Balance (Karam)"] = {
  onload(query_report) {
    setDateRangeModeRequirements(query_report);
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
        const { fiscal_year, ignore_fiscal_year } = query_report.get_values();
        if (ignore_fiscal_year) {
          return;
        }
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
      fieldname: "ignore_fiscal_year",
      label: __("Ignore Fiscal Year"),
      fieldtype: "Check",
      default: 0,
      on_change(query_report) {
        setDateRangeModeRequirements(query_report);
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
      fieldname: "presentation_currency",
      label: __("Currency"),
      fieldtype: "Select",
      options: erpnext.get_presentation_currency_list()
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
      fieldname: "show_zero_values",
      label: __("Show zero values"),
      fieldtype: "Check"
    },
    {
      fieldname: "show_unclosed_fy_pl_balances",
      label: __("Show unclosed fiscal year's P&L balances"),
      fieldtype: "Check"
    },
    {
      fieldname: "include_default_book_entries",
      label: __("Include Default FB Entries"),
      fieldtype: "Check",
      default: 1
    },
    {
      fieldname: "show_net_values",
      label: __("Show net values in opening and closing columns"),
      fieldtype: "Check",
      default: 1
    },
    {
      fieldname: "show_group_accounts",
      label: __("Show Group Accounts"),
      fieldtype: "Check",
      default: 1
    }
  ],
  formatter: trialBalanceKaramFormatter,
  get_datatable_options(options) {
    return (
      window.karamReportTableUX?.applyCurrentReportColumnWidths(options, {
        excludedRowFlags: ["is_spacer", "is_total"]
      }) || options
    );
  },
  after_datatable_render() {
    window.karamReportTableUX?.refreshCurrentReportColumnWidths(frappe.query_report, {
      excludedRowFlags: ["is_spacer", "is_total"]
    });
  },
  after_refresh(report) {
    window.karamReportTableUX?.removeTreeFooter(report);
  },
  tree: true,
  name_field: "account",
  parent_field: "parent_account",
  initial_depth: 3
};

erpnext.utils.add_dimensions("Trial Balance (Karam)", 6);
