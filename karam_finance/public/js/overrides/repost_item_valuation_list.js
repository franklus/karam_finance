// Copyright (c) 2026, Noospheric
// For license information, please see license.txt

frappe.listview_settings["Repost Item Valuation"] = {
  onload(listview) {
    listview.page.add_inner_button(__("Run Repost Queue Now"), () => {
      frappe.confirm(
        __("Start reposting immediately for all queued and in-progress entries?"),
        () => {
          frappe.call({
            method:
              "erpnext.stock.doctype.repost_item_valuation.repost_item_valuation.execute_repost_item_valuation",
            callback() {
              frappe.show_alert({
                message: __(
                  "Queued repost entries are now being processed in the background."
                ),
                indicator: "green"
              });
              listview.refresh();
            }
          });
        }
      );
    });
  }
};
