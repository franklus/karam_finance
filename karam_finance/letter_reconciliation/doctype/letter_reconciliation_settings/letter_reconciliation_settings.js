// Copyright (c) 2026, Noospheric
// For licence information, please see licence.txt

frappe.ui.form.on("Letter Reconciliation Settings", {
  refresh(frm) {
    toggleHistoricalScopeFields(frm);
  },

  rebuild_whole_history(frm) {
    toggleHistoricalScopeFields(frm);
  },

  preview_historical_rebuild(frm) {
    previewHistoricalRebuild(frm);
  },

  run_historical_rebuild(frm) {
    runHistoricalRebuild(frm);
  }
});

function getSettingsMethod(methodName) {
  return (
    "karam_finance.letter_reconciliation.doctype.letter_reconciliation_settings" +
    `.letter_reconciliation_settings.${methodName}`
  );
}

const STATUS_SUCCESS = "success";
const STATUS_PARTIAL_SUCCESS = "partial_success";
const STATUS_ERROR = "error";
const STATUS_FATAL_ERROR = "fatal_error";

function handleRebuildDone(frm, subscription, payload = {}) {
  frappe.realtime.off(subscription.progressEvent, subscription.progressHandler);
  frappe.realtime.off(subscription.doneEvent, subscription.doneHandler);
  frappe.hide_progress();

  showRebuildResult(payload);
  frm.reload_doc();
}

function showRebuildResult(payload) {
  if (payload.status === STATUS_FATAL_ERROR || payload.status === STATUS_ERROR) {
    frappe.hide_msgprint(true);
    frappe.msgprint({
      title: __("Historical Rebuild Failed"),
      message: payload.message || __("Check the Error Log for details."),
      indicator: "red"
    });
    return;
  }

  if (payload.status === STATUS_SUCCESS) {
    frappe.hide_msgprint(true);
    showRebuildSuccess(payload);
    return;
  }

  frappe.hide_msgprint(true);
  frappe.msgprint({
    title:
      payload.status === STATUS_PARTIAL_SUCCESS
        ? __("Historical Rebuild Partially Complete")
        : __("Historical Rebuild Complete"),
    message: frappe.karam_letter_rebuild.buildRebuildCompletionHtml(payload),
    indicator: payload.status === STATUS_PARTIAL_SUCCESS ? "orange" : "green"
  });
}

function showRebuildSuccess(payload) {
  frappe.show_alert({
    message: __(
      "Historical GL Rebuild complete: {0} rebuilt, {1} blocked, {2} already correct.",
      [
        payload.rebuilt_count || 0,
        payload.blocked_count || 0,
        payload.already_correct_count || 0
      ]
    ),
    indicator: "green"
  });
}

function toggleHistoricalScopeFields(frm) {
  const wholeHistory = Boolean(frm.doc.rebuild_whole_history);
  frm.toggle_display("rebuild_from_posting_date", !wholeHistory);
  frm.toggle_display("rebuild_to_posting_date", !wholeHistory);
  frm.set_df_property("rebuild_from_posting_date", "reqd", wholeHistory ? 0 : 1);
  frm.set_df_property("rebuild_to_posting_date", "reqd", wholeHistory ? 0 : 1);
}

function previewHistoricalRebuild(frm) {
  frappe.call({
    method: getSettingsMethod("preview_historical_gl_rebuild"),
    freeze: true,
    freeze_message: __("Previewing historical scope..."),
    callback: (r) => {
      const data = r.message || {};
      frappe.hide_msgprint(true);
      frappe.msgprint({
        title: __("Historical Rebuild Preview"),
        message: frappe.karam_letter_rebuild.buildPreviewHtml(data),
        indicator: (data.blocked?.count || 0) > 0 ? "orange" : "green"
      });
      frm.reload_doc();
    }
  });
}

function runHistoricalRebuild(frm) {
  const company = frm.doc.rebuild_company || __("Not set");
  const wholeHistory = Boolean(frm.doc.rebuild_whole_history);
  const scopeLine = wholeHistory
    ? `<b>${__("Scope")}:</b> ${__("Entire company history")}`
    : `<b>${__("From Posting Date")}:</b> ${frm.doc.rebuild_from_posting_date || __("Not set")}<br><b>${__("To Posting Date")}:</b> ${frm.doc.rebuild_to_posting_date || __("Not set")}`;

  frappe.confirm(
    __(
      "This rebuild will repost historical Journal Entries in the selected scope.<br><br><b>Company:</b> {0}<br>{1}<br><br>Continue?",
      [company, scopeLine]
    ),
    () => {
      frappe.call({
        method: getSettingsMethod("enqueue_historical_gl_rebuild"),
        callback: (r) => {
          const data = r.message || {};
          const progressEvent = data.progress_event;
          const doneEvent = data.done_event;
          const title = __("Historical GL Rebuild");
          let rebuildFinished = false;

          if (!progressEvent || !doneEvent) {
            frappe.msgprint({
              title: __("Unable to Start Rebuild"),
              message: __("The server did not return progress information."),
              indicator: "red"
            });
            return;
          }

          frappe.show_progress(title, 0, 100, __("Job queued..."), true);

          const progressHandler = (payload = {}) => {
            if (rebuildFinished) {
              return;
            }
            const total = payload.total || 100;
            const current = Math.min(payload.current || 0, total);
            const message = payload.message || __("Processing...");
            frappe.show_progress(title, current, total, message, true);
          };

          const doneHandler = (payload = {}) => {
            rebuildFinished = true;
            handleRebuildDone(
              frm,
              { progressEvent, progressHandler, doneEvent, doneHandler },
              payload
            );
          };

          frappe.realtime.on(progressEvent, progressHandler);
          frappe.realtime.on(doneEvent, doneHandler);
        }
      });
    }
  );
}
