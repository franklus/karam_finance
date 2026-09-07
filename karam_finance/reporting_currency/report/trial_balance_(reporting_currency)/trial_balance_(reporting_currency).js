// Trial Balance (Reporting Currency): ERPNext Trial Balance with Account Currency columns

function trialBalanceReportingFormatter(value, row, column, data, default_formatter) {
  // Spacer rows have no content; the total remains visible in the table.
  if (data?.is_spacer) {
    return "";
  }
  const displayValue = getReportingTrialBalanceDisplayValue(value, column, data);
  const formatted = formatReportingTrialBalanceCell(
    displayValue,
    row,
    column,
    data,
    default_formatter
  );
  const extra = getReportingTrialBalanceStyle(data);
  const aligned = window.alignCurrencyWithSharedHelper(
    "Trial Balance (Reporting Currency)",
    displayValue,
    column,
    formatted,
    extra
  );
  return extra && column.fieldtype !== "Currency"
    ? `<strong>${aligned}</strong>`
    : aligned;
}

frappe.query_reports["Trial Balance (Reporting Currency)"] = {
  separate_check_filters: true,
  onload(query_report) {
    return frappe
      .require(
        "/assets/karam_finance/js/report_utils/trial_balance_reporting_filters.js"
      )
      .then(() => window.setupTrialBalanceFilters(query_report));
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
      label: __("Reporting Currency"),
      fieldtype: "Data",
      read_only: 1
    },
    {
      fieldname: "exclude_reporting_doe",
      label: __("Exclude Reporting DOE"),
      fieldtype: "Check",
      default: 0
    },
    {
      fieldname: "exclude_manual_entries",
      label: __("Exclude Manual Entries"),
      fieldtype: "Check",
      default: 0
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
  open_reporting_ledger(data) {
    const filters = frappe.query_report.get_filter_values();
    frappe.route_options = {
      company: filters.company,
      account: [data.account],
      finance_book: filters.finance_book,
      from_date: filters.from_date,
      to_date: filters.to_date,
      categorize_by: "Categorise by Account",
      exclude_reporting_doe: filters.exclude_reporting_doe,
      exclude_manual_entries: filters.exclude_manual_entries,
      include_default_book_entries: filters.include_default_book_entries
    };
    frappe.set_route("query-report", "General Ledger (Reporting Currency)");
  },
  formatter: trialBalanceReportingFormatter,
  get_datatable_options(options) {
    return (
      window.karamReportTableUX?.applyCurrentReportColumnWidths(options, {
        excludedRowFlags: ["is_spacer"]
      }) || options
    );
  },
  after_refresh(report) {
    window.karamReportTableUX?.removeTreeFooter(report);
    const currency = report.data?.[0]?.currency;
    if (currency) {
      report.get_filter("presentation_currency").set_input(currency);
    }
  },
  tree: true,
  name_field: "account",
  parent_field: "parent_account",
  initial_depth: 3
};

function getReportingTrialBalanceDisplayValue(value, column, data) {
  return data?._display_amounts?.[column.fieldname] ?? value;
}

function getReportingTrialBalanceStyle(data) {
  const bold = data && (data.is_group || data.is_group_account || data.is_total);
  return bold ? "font-weight:bold;" : "";
}

function formatReportingTrialBalanceCell(value, row, column, data, defaultFormatter) {
  if (column.fieldname !== "account") {
    const args = [value, row, column, data, defaultFormatter];
    return erpnext.financial_statements.formatter(...args);
  }
  const accountColumn = { ...column, is_tree: true };
  if (data?.is_total) {
    accountColumn.fieldtype = "Data";
    accountColumn.link_onclick = null;
    return defaultFormatter(__("Total"), row, accountColumn, data);
  }
  const handler =
    'frappe.query_reports["Trial Balance (Reporting Currency)"].open_reporting_ledger';
  accountColumn.link_onclick = `${handler}(${JSON.stringify(data)})`;
  return defaultFormatter(data?.account_name || value, row, accountColumn, data);
}
