// General Ledger (Karam): ERPNext GL with Karam filters/columns
/* eslint-disable max-lines */

function getKaramGeneralLedgerUX() {
  return window.karamGeneralLedgerUX;
}

function getKaramReportTableUX() {
  return window.karamReportTableUX;
}

function getActiveKaramGeneralLedgerReport() {
  return frappe._karamGeneralLedgerReport || frappe.query_report;
}

function getKaramGeneralLedgerContext(report) {
  const ux = getKaramGeneralLedgerUX();
  const target = report;
  if (
    !target._karamGeneralLedgerContext ||
    target._karamGeneralLedgerContext.ux !== ux
  ) {
    target._karamGeneralLedgerContext = {
      dataReference: null,
      footerRows: [],
      pagination: ux.createPaginationState(),
      ux
    };
  }
  return target._karamGeneralLedgerContext;
}

function setupKaramGeneralLedgerFilterGroups(report) {
  const area = report.check_filter_area;
  if (!area?.length) {
    return;
  }

  const groups = [
    [
      "Calculation",
      [
        "include_dimensions",
        "disable_opening_balance_calculation",
        "show_net_values_in_party_account"
      ]
    ],
    [
      "Inclusions",
      ["show_opening_entries", "include_default_book_entries", "show_cancelled_entries"]
    ],
    ["Additional columns", ["add_values_in_transaction_currency", "show_remarks"]],
    ["Exclusions", ["ignore_err", "ignore_cr_dr_notes"]]
  ];
  const filterMap = Object.fromEntries(
    report.filters
      .filter((filter) => filter.df.fieldtype === "Check")
      .map((filter) => [filter.df.fieldname, filter])
  );

  area.empty();
  groups.forEach(([label, fieldnames]) => {
    const group = $("<div>", {
      class: "karam-gl-filter-group",
      "aria-label": __(label)
    });
    group.append(
      $("<div>", { class: "karam-gl-filter-group__heading" }).text(__(label))
    );
    fieldnames.forEach((fieldname) => {
      if (filterMap[fieldname]) {
        group.append(filterMap[fieldname].wrapper);
      }
    });
    area.append(group);
  });
}

function formatKaramGeneralLedgerFooterCell(column, row) {
  const ux = getKaramGeneralLedgerUX();
  if (
    !column ||
    !row ||
    ["_rowIndex", "_checkbox", ux.SERIAL_NUMBER_FIELD].includes(column.id)
  ) {
    return "";
  }

  return formatKaramGeneralLedgerFooterValue(column, row);
}

function formatKaramGeneralLedgerFooterValue(column, row) {
  const ux = getKaramGeneralLedgerUX();
  let value = row[column.id];
  if (column.id === "account" && typeof value === "string") {
    value = value.replace(/^['"]|['"]$/g, "");
  }
  if ([null, undefined, ""].includes(value)) {
    return "";
  }
  if (column.id === "account") {
    return ux.escapeHtml(value);
  }
  return column.format ? column.format(value, null, column, row) : ux.escapeHtml(value);
}

function getKaramGeneralLedgerDatatableView(report, columns) {
  const ux = getKaramGeneralLedgerUX();
  const tableUX = getKaramReportTableUX();
  const context = getKaramGeneralLedgerContext(report);
  const split = ux.splitFooterRows(report.data || []);
  context.footerRows = split.footerRows;
  context.pagination.setTotal(split.bodyRows.length);

  const pageRows = context.pagination.getRows(split.bodyRows);
  const serialColumn = {
    id: ux.SERIAL_NUMBER_FIELD,
    fieldname: ux.SERIAL_NUMBER_FIELD,
    name: "",
    align: "center",
    editable: false,
    resizable: false,
    sortable: false,
    focusable: false,
    dropdown: false,
    sticky: true,
    width: tableUX.calculateSerialNumberWidth(context.pagination.totalItems),
    format(value, _row, column, data) {
      return ux.isLeadingTotalColumn(column, data) ? "" : value;
    }
  };
  return {
    columns: [
      serialColumn,
      ...tableUX.calculateColumnWidths(columns, report.data || [])
    ],
    rows: ux.addAbsoluteSerialNumbers(pageRows, context.pagination.getOffset())
  };
}

function applyKaramGeneralLedgerDatatableOptions(options) {
  if (!options || !Array.isArray(options.data)) {
    return options;
  }
  const datatableOptions = options;
  datatableOptions.serialNoColumn = false;
  return datatableOptions;
}

function renderKaramGeneralLedgerFooterRows(report, datatable) {
  const context = getKaramGeneralLedgerContext(report);
  const footer = datatable?.footer;
  if (!footer) {
    return;
  }

  $(footer).find(".karam-gl-summary-row").remove();
  context.footerRows
    .filter((row) => row.row_type !== "separator")
    .forEach((footerData) => {
      const row = $("<div>", {
        class: `dt-row karam-gl-summary-row karam-gl-summary-row-${footerData.row_type}`
      });
      datatable.datamanager.getColumns().forEach((column) => {
        const cell = $("<div>", {
          class: `dt-cell dt-cell--col-${column.colIndex} karam-gl-summary-cell`
        });
        const content = $("<div>", {
          class: `dt-cell__content dt-cell__content--col-${column.colIndex}`
        });
        content.html(formatKaramGeneralLedgerFooterCell(column, footerData));
        cell.append(content);
        row.append(cell);
      });
      $(footer).append(row);
    });
}

function installKaramGeneralLedgerPreRender(report) {
  const target = report;
  if (target._karamGeneralLedgerPreRenderInstalled) {
    return;
  }

  const originalRenderDatatable = target.render_datatable;
  target.render_datatable = function renderKaramGeneralLedgerDatatable(...args) {
    // Frappe reuses the QueryReport instance when navigating between reports.
    if (this.report_name !== "General Ledger (Karam)") {
      this.$report?.next(".karam-gl-pagination").remove();
      this.$report?.find(".karam-gl-summary-row").remove();
      return originalRenderDatatable.apply(this, args);
    }
    const sourceData = this.data;
    const sourceColumns = this.columns;
    if (!Array.isArray(sourceData) || !Array.isArray(sourceColumns)) {
      return originalRenderDatatable.apply(this, args);
    }

    const context = getKaramGeneralLedgerContext(this);
    if (context.dataReference !== sourceData) {
      context.dataReference = sourceData;
      context.pagination.reset();
    }
    const visibleColumns = getKaramReportTableUX().getVisibleColumns(sourceColumns);
    const view = getKaramGeneralLedgerDatatableView(this, visibleColumns);
    this.data = view.rows;
    this.columns = view.columns;
    try {
      return originalRenderDatatable.apply(this, args);
    } finally {
      this.data = sourceData;
      this.columns = sourceColumns;
    }
  };
  target._karamGeneralLedgerPreRenderInstalled = true;
}

function updateKaramGeneralLedgerPaginationControls(report) {
  const context = getKaramGeneralLedgerContext(report);
  const controls = report.$report.next(".karam-gl-pagination");
  if (!controls.length) {
    return;
  }

  const range = context.pagination.getRange();
  controls
    .find(".karam-gl-pagination__status")
    .text(getKaramGeneralLedgerUX().formatRange(range));
  const ux = getKaramGeneralLedgerUX();
  const pageSizeValue =
    context.pagination.pageSize === ux.SHOW_ALL_PAGE_SIZE
      ? "all"
      : String(context.pagination.pageSize);
  controls
    .find(".karam-gl-pagination__size-label")
    .text(pageSizeValue === "all" ? __("Show All") : pageSizeValue);
  controls
    .find("[data-karam-gl-page-size]")
    .removeClass("active")
    .removeAttr("aria-current");
  controls
    .find(`[data-karam-gl-page-size="${pageSizeValue}"]`)
    .addClass("active")
    .attr("aria-current", "true");

  const first = context.pagination.page === 1;
  const last = context.pagination.page === context.pagination.getTotalPages();
  controls
    .find('[data-karam-gl-page="first"], [data-karam-gl-page="previous"]')
    .prop("disabled", first);
  controls
    .find('[data-karam-gl-page="next"], [data-karam-gl-page="last"]')
    .prop("disabled", last);
}

function rerenderKaramGeneralLedgerPage(report) {
  const target = report;
  if (target.datatable) {
    target.datatable.destroy();
    target.datatable = null;
  }
  target.render_datatable();
}

function createKaramGeneralLedgerPaginationControls(report) {
  return $(
    `<div class="karam-gl-pagination" role="navigation" aria-label="${__("Report pagination")}">
        <span class="karam-gl-pagination__status" role="status"
          aria-live="polite"></span>
        <div class="karam-gl-pagination__controls">
          <div class="custom-btn-group karam-gl-pagination__size">
            <button type="button" class="btn btn-default btn-sm ellipsis"
              data-toggle="dropdown" aria-haspopup="true" aria-expanded="false"
              aria-label="${__("Rows per page")}">
              <span class="karam-gl-pagination__size-label">250</span>
              ${frappe.utils.icon("select", "xs")}
            </button>
            <ul class="dropdown-menu" role="menu">
              <li>
                <a class="dropdown-item" href="#"
                  data-karam-gl-page-size="all">${__("Show All")}</a>
              </li>
              <li>
                <a class="dropdown-item" href="#"
                  data-karam-gl-page-size="250">250</a>
              </li>
              <li>
                <a class="dropdown-item" href="#"
                  data-karam-gl-page-size="500">500</a>
              </li>
              <li>
                <a class="dropdown-item" href="#"
                  data-karam-gl-page-size="1000">1000</a>
              </li>
            </ul>
          </div>
          <button type="button" class="btn btn-default btn-sm" data-karam-gl-page="first">${__("First")}</button>
          <button type="button" class="btn btn-default btn-sm" data-karam-gl-page="previous">${__("Previous")}</button>
          <button type="button" class="btn btn-default btn-sm" data-karam-gl-page="next">${__("Next")}</button>
          <button type="button" class="btn btn-default btn-sm" data-karam-gl-page="last">${__("Last")}</button>
        </div>
      </div>`
  ).insertAfter(report.$report);
}

function setupKaramGeneralLedgerPagination(report) {
  let controls = report.$report.next(".karam-gl-pagination");
  if (!controls.length) {
    controls = createKaramGeneralLedgerPaginationControls(report);

    controls.on("click", "[data-karam-gl-page]", (event) => {
      const context = getKaramGeneralLedgerContext(report);
      const action = $(event.currentTarget).data("karam-gl-page");
      const page = context.pagination.page;
      const lastPage = context.pagination.getTotalPages();
      const nextPage = {
        first: 1,
        previous: page - 1,
        next: page + 1,
        last: lastPage
      }[action];
      context.pagination.setPage(nextPage);
      rerenderKaramGeneralLedgerPage(report);
    });
    controls.on("click", "[data-karam-gl-page-size]", (event) => {
      event.preventDefault();
      const context = getKaramGeneralLedgerContext(report);
      const selectedPageSize = $(event.currentTarget).data("karam-gl-page-size");
      context.pagination.setPageSize(
        selectedPageSize === "all"
          ? getKaramGeneralLedgerUX().SHOW_ALL_PAGE_SIZE
          : Number(selectedPageSize)
      );
      rerenderKaramGeneralLedgerPage(report);
    });
  }
  updateKaramGeneralLedgerPaginationControls(report);
}

function installKaramGeneralLedgerFullDataActions(report) {
  if (report._karamGeneralLedgerFullDataActionsInstalled) {
    return;
  }
  const ux = getKaramGeneralLedgerUX();
  const target = report;

  target.get_validated_visible_indexes = function getKaramVisibleIndexes() {
    const totalRows = this.raw_data?.add_total_row
      ? this.data.length - 1
      : this.data.length;
    if (!totalRows) {
      frappe.throw({
        title: __("No data to perform this action"),
        message: __("Please adjust filters to include some data")
      });
    }
    return Array.from({ length: Math.max(0, totalRows) }, (_value, index) => index);
  };
  target.get_data_for_print = function getKaramPrintData() {
    const footerRows = new Set(ux.splitFooterRows(this.data || []).footerRows);
    return (this.data || []).map((row) => {
      if (footerRows.has(row) && ux.isFooterRow(row)) {
        return { ...row, is_total_row: true };
      }
      return row;
    });
  };
  target._karamGeneralLedgerFullDataActionsInstalled = true;
}

frappe.query_reports["General Ledger (Karam)"] = {
  onload(report) {
    frappe._karamGeneralLedgerReport = report;
    return frappe
      .require([
        "/assets/karam_finance/js/report_utils/report_table_ux.js",
        "/assets/karam_finance/js/report_utils/general_ledger_ux.js",
        "/assets/karam_finance/css/general_ledger_karam.css"
      ])
      .then(() => {
        const context = getKaramGeneralLedgerContext(report);
        context.pagination.reset();
        report.check_filter_area.addClass("karam-general-ledger-check-filters");
        setupKaramGeneralLedgerFilterGroups(report);
        installKaramGeneralLedgerPreRender(report);
        installKaramGeneralLedgerFullDataActions(report);
      });
  },
  formatter(value, row, column, data, default_formatter) {
    if (getKaramGeneralLedgerUX()?.isLeadingTotalColumn(column, data)) {
      return "";
    }
    if (data && (data.is_separator || data.row_type === "separator")) {
      return "";
    }
    const formatted = default_formatter(value, row, column, data);
    return window.alignCurrencyWithSharedHelper(
      "General Ledger (Karam)",
      value,
      column,
      formatted
    );
  },
  separate_check_filters: true,
  get_datatable_options(options) {
    return applyKaramGeneralLedgerDatatableOptions(options);
  },
  after_datatable_render(datatable) {
    const report = getActiveKaramGeneralLedgerReport();
    renderKaramGeneralLedgerFooterRows(report, datatable);
    setupKaramGeneralLedgerPagination(report);
  },
  after_refresh(report) {
    const context = getKaramGeneralLedgerContext(report);
    if (!report.data?.length) {
      context.pagination.reset();
      report.$report.next(".karam-gl-pagination").remove();
    }
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
      fieldname: "finance_book",
      label: __("Finance Book"),
      fieldtype: "Link",
      options: "Finance Book"
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
        if (!frappe.query_report.filters) {
          return undefined;
        }
        const party_type = frappe.query_report.get_filter_value("party_type");
        if (!party_type) {
          return undefined;
        }
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
        { label: __("Flat Chronological"), value: "Flat Chronological" },
        { label: __("Categorise by Account"), value: "Categorise by Account" },
        {
          label: __("Group by Account w/ Opening"),
          value: "Group by Account w/ Opening"
        },
        { label: __("Categorise by Party"), value: "Categorise by Party" }
      ],
      default: "Flat Chronological"
    },
    { fieldname: "tax_id", label: __("Tax Id"), fieldtype: "Data", hidden: 1 },
    {
      fieldname: "presentation_currency",
      label: __("Currency"),
      fieldtype: "Select",
      options: erpnext.get_presentation_currency_list()
    },
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
    // Karam-specific filters. Letter of Credit and Auxiliary are deliberately
    // not hard-coded; erpnext.utils.add_dimensions adds active site dimensions
    // generically when those doctypes are configured.
    { fieldname: "letter", label: __("Letter"), fieldtype: "Data" },
    {
      fieldname: "karam_series",
      label: __("Series"),
      fieldtype: "Link",
      options: "Karam Series"
    },
    { fieldname: "translation", label: __("Translation"), fieldtype: "Data" },
    {
      fieldname: "show_letter",
      label: __("Show Letter"),
      fieldtype: "Select",
      options: "\nOnly unassigned rows\nOnly assigned rows"
    },
    {
      fieldname: "include_dimensions",
      label: __("Consider Accounting Dimensions"),
      fieldtype: "Check",
      default: 1
    },
    {
      fieldname: "disable_opening_balance_calculation",
      label: __("Disable Opening Balance Calculation"),
      fieldtype: "Check"
    },
    {
      fieldname: "show_opening_entries",
      label: __("Show Opening Entries"),
      fieldtype: "Check",
      depends_on: "eval: !doc.disable_opening_balance_calculation"
    },
    {
      fieldname: "include_default_book_entries",
      label: __("Include Default FB Entries"),
      fieldtype: "Check",
      default: 1
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
    {
      fieldname: "add_values_in_transaction_currency",
      label: __("Add Columns in Transaction Currency"),
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
    }
  ]
};

erpnext.utils.add_dimensions("General Ledger (Karam)", 15);
