window.setupTrialBalanceFilters = async function setupTrialBalanceFilters(
  report,
  options = { excludedRowFlags: ["is_spacer"] }
) {
  await frappe.require("/assets/karam_finance/css/general_ledger_karam.css");
  window.karamReportTableUX?.installPreRenderColumnWidths(report, {
    excludedRowFlags: options.excludedRowFlags
  });
  const area = report.check_filter_area;
  if (!area?.length) {
    return;
  }
  const groups = [
    ["Calculation", ["show_net_values", "show_unclosed_fy_pl_balances"]],
    [
      "Inclusions",
      [
        "with_period_closing_entry_for_opening",
        "with_period_closing_entry_for_current_period",
        "include_default_book_entries"
      ]
    ],
    ["Display", ["show_group_accounts", "show_zero_values"]],
    ["Reconciliation", ["exclude_reporting_doe", "exclude_manual_entries"]]
  ];
  const filters = Object.fromEntries(
    report.filters
      .filter((filter) => filter.df.fieldtype === "Check")
      .map((filter) => [filter.df.fieldname, filter])
  );
  // Preserve the existing controls, values and change handlers when moving them.
  Object.values(filters).forEach((filter) => {
    $(filter.wrapper).detach();
  });
  area.empty().addClass("karam-general-ledger-check-filters");
  appendTrialBalanceFilterGroups(area, groups, filters);
};

function appendTrialBalanceFilterGroups(area, groups, filters) {
  for (const [label, fields] of groups) {
    if (!fields.some((field) => filters[field])) {
      continue;
    }
    const group = $("<div>", {
      class: "karam-gl-filter-group",
      "aria-label": __(label)
    });
    $("<div>", { class: "karam-gl-filter-group__heading" })
      .text(__(label))
      .appendTo(group);
    for (const field of fields) {
      if (filters[field]) {
        group.append(filters[field].wrapper);
      }
    }
    area.append(group);
  }
}
