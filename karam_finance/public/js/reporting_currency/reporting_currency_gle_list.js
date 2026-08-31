// Copyright (c) 2026, Noospheric
// For license information, please see license.txt

frappe.listview_settings["Reporting Currency GLE"] = {
  onload(listview) {
    // Add "Delete All" button to list view
    listview.page.add_inner_button(__("Delete All"), () => {
      frappe.confirm(
        __(
          "Are you sure you want to delete all Reporting Currency GLE records? This action cannot be undone."
        ),
        () => {
          // User confirmed - proceed with deletion
          frappe.call({
            method:
              "karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.delete_all_entries",
            callback(_r) {
              frappe.show_alert({
                message: __("All Reporting Currency GLE records have been deleted."),
                indicator: "green",
              });
              listview.refresh();
            },
            error(_r) {
              frappe.msgprint({
                title: __("Error"),
                message: __(
                  "Failed to delete records. Please check if you have the required permissions (System Manager role)."
                ),
                indicator: "red",
              });
            },
          });
        }
      );
    });
  },

  // Add indicator for DOE entries
  get_indicator(doc) {
    if (doc.reporting_doe === 1) {
      return [__("DOE Entry"), "orange", "reporting_doe,=,1"];
    }
    if (doc.gl_entry) {
      return [__("Synced"), "blue", "gl_entry,!=,"];
    }
  },
};
