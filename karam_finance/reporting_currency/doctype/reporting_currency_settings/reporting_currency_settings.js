// Copyright (c) 2026, Noospheric
// For license information, please see license.txt

frappe.ui.form.on("Reporting Currency Settings", {
  refresh(frm) {
    let syncButton;
    const handler = () => queue_reporting_currency_sync(frm, syncButton);
    syncButton = frm.add_custom_button(__("Sync from GL Entry"), handler);
    syncButton.addClass("btn-primary");
  },

  add_accounts(frm) {
    if (!frm.doc.parent_account) {
      frappe.msgprint({
        message: __("Select a Parent Account before adding exclusions."),
        indicator: "orange",
      });
      return;
    }

    fetch_accounts(frm, (accounts) => {
      const existing = new Set(
        (frm.doc.account_exclusions || []).map((row) => row.account)
      );
      let added = 0;

      (accounts || []).forEach((account) => {
        if (!account || account.is_group) {
          return;
        }
        if (!existing.has(account.account)) {
          const row = frm.add_child("account_exclusions");
          row.account = account.account;
          row.is_group = account.is_group;
          added += 1;
        }
      });

      if (added) {
        renumber_exclusions(frm);
        frm.refresh_field("account_exclusions");
        frappe.show_alert({
          indicator: "green",
          message: __("Added {0} account(s) to the exclusion list.", [added]),
        });
      } else {
        frappe.show_alert({
          indicator: "blue",
          message: __("All child accounts are already excluded."),
        });
      }
    });
  },

  remove_accounts(frm) {
    if (!frm.doc.parent_account) {
      frappe.msgprint({
        message: __("Select a Parent Account before removing exclusions."),
        indicator: "orange",
      });
      return;
    }

    fetch_accounts(frm, (accounts) => {
      const removable = new Set((accounts || []).map((account) => account.account));
      const rows = frm.doc.account_exclusions || [];
      const initialLength = rows.length;

      frm.doc.account_exclusions = rows.filter((row) => !removable.has(row.account));
      const removed = initialLength - frm.doc.account_exclusions.length;

      if (removed) {
        renumber_exclusions(frm);
        frm.refresh_field("account_exclusions");
        frappe.show_alert({
          indicator: "green",
          message: __("Removed {0} account(s) from the exclusion list.", [removed]),
        });
      } else {
        frappe.show_alert({
          indicator: "blue",
          message: __("No matching child accounts were found in the exclusion list."),
        });
      }
    });
  },
});

function fetch_accounts(frm, callback) {
  frappe.call({
    method:
      "karam_finance.reporting_currency.doctype.reporting_currency_settings.reporting_currency_settings.get_accounts_under_parent",
    args: {
      parent_account: frm.doc.parent_account,
    },
    freeze: true,
    freeze_message: __("Fetching child accounts..."),
    callback: (r) => {
      callback(r.message || []);
    },
    error: () => {
      callback([]);
    },
  });
}

function renumber_exclusions(frm) {
  (frm.doc.account_exclusions || []).forEach((row, idx) => {
    row.idx = idx + 1;
  });
}

function queue_reporting_currency_sync(frm, button) {
  button?.prop("disabled", true);
  const method =
    "karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.enqueue_reporting_currency_sync";

  frappe.call({
    method,
    callback: (r) => {
      const data = r.message || {};
      const progressEvent = data.progress_event;
      const doneEvent = data.done_event;
      const title = __("Syncing Reporting Currency Data");

      if (!progressEvent || !doneEvent) {
        frappe.msgprint({
          title: __("Unable to Start Sync"),
          message: __("The server did not return progress information."),
          indicator: "red",
        });
        reenable_button(button);
        return;
      }

      frappe.show_progress(title, 0, 100, __("Job queued..."));

      const progressHandler = (payload = {}) => {
        const total = payload.total || 100;
        const current = Math.min(payload.current || 0, total);
        const message = payload.message || __("Processing...");
        frappe.show_progress(title, current, total, message);
      };

      const doneHandler = (payload = {}) => {
        frappe.realtime.off(progressEvent, progressHandler);
        frappe.realtime.off(doneEvent, doneHandler);
        frappe.hide_progress();

        if (payload.status === "error") {
          frappe.msgprint({
            title: payload.title || __("Sync Failed"),
            message: payload.message || __("Check the error log for details."),
            indicator: "red",
          });
          reenable_button(button);
          return;
        }

        const inserted = payload.inserted || 0;
        const updated = payload.updated || 0;
        const deleted = payload.deleted || 0;
        const duration = payload.duration_seconds;
        const parts = [
          inserted ? __("inserted: {0}", [inserted]) : null,
          updated ? __("updated: {0}", [updated]) : null,
          deleted ? __("deleted: {0}", [deleted]) : null,
        ].filter(Boolean);

        const summary = parts.length ? parts.join(", ") : __("no changes");
        const detail = duration
          ? __("Reporting Currency sync completed ({0}) in {1}s.", [summary, duration])
          : __("Reporting Currency sync completed ({0}).", [summary]);

        if (payload.status === "partial_success") {
          frappe.msgprint({
            title: payload.title || __("Partial Success"),
            message: payload.message || __("Check the error log for details."),
            indicator: "orange",
          });
          frappe.show_alert({
            indicator: "orange",
            message: detail,
          });
        } else {
          frappe.show_alert({
            indicator: "green",
            message: detail,
          });
        }
        frm.reload_doc();
        reenable_button(button);
      };

      frappe.realtime.on(progressEvent, progressHandler);
      frappe.realtime.on(doneEvent, doneHandler);
    },
    error: () => {
      // frappe.throw() already displays the error message automatically
      // We just need to re-enable the button
      reenable_button(button);
    },
  });
}

function reenable_button(button) {
  button?.prop("disabled", false);
}

// Global function for downloading GL entries CSV (called from error dialog)
function downloadCurrencyGLEntries(account_currency, reporting_currency) {
  const method =
    "karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.export_missing_currency_gl_entries_csv";
  const params = new URLSearchParams({
    account_currency,
    reporting_currency,
  });
  const url = `/api/method/${method}?${params.toString()}`;

  // Show a loading indicator
  frappe.show_alert({
    message: __("Preparing CSV download..."),
    indicator: "blue",
  });

  window.location.href = url;
}

window.downloadCurrencyGLEntries = downloadCurrencyGLEntries;

// Global function for downloading temporal validation entries CSV (called from error dialog)
function downloadTemporalValidationCSV(
  cache_key,
  default_currency,
  reporting_currency
) {
  const method =
    "karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.export_temporal_validation_entries_csv";
  const params = new URLSearchParams({
    cache_key,
    default_currency,
    reporting_currency,
  });
  const url = `/api/method/${method}?${params.toString()}`;

  // Show a loading indicator
  frappe.show_alert({
    message: __("Preparing CSV download..."),
    indicator: "blue",
  });

  window.location.href = url;
}

window.downloadTemporalValidationCSV = downloadTemporalValidationCSV;
