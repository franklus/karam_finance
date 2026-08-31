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
  },
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

const TABLE_FONT_SIZE_PX = 10;
const TABLE_CELL_STYLE = `vertical-align:top;font-size:${TABLE_FONT_SIZE_PX}px;`;
const BULLET_LIST_STYLE = "margin:0;padding-left:1.15rem;";

function buildBulletList(items = []) {
  if (!items.length) return "—";
  const rows = items.map(
    (item) => `<li>${frappe.utils.escape_html(String(item))}</li>`
  );
  return `<ul style="${BULLET_LIST_STYLE}font-size:${TABLE_FONT_SIZE_PX}px;">${rows.join("")}</ul>`;
}

function formatFailureSummaryTable(groups) {
  if (!groups || !groups.length) return "";

  const rows = groups.map((group) => {
    const reason = frappe.utils.escape_html(group.reason || "");
    const count = group.voucher_count || 0;
    const accounts = buildBulletList(group.accounts || group.account_samples || []);
    return `
      <tr>
        <td style="${TABLE_CELL_STYLE}width:25%;"><b>${reason}</b></td>
        <td style="${TABLE_CELL_STYLE}width:55%;">${accounts}</td>
        <td style="${TABLE_CELL_STYLE}width:20%;text-align:left;">${count}</td>
      </tr>
    `;
  });

  return `
    <table
      class="table table-bordered"
      style="margin-top:8px;font-size:${TABLE_FONT_SIZE_PX}px;"
    >
      <thead>
        <tr>
          <th style="font-size:${TABLE_FONT_SIZE_PX}px;width:25%;">${__("Reason")}</th>
          <th style="font-size:${TABLE_FONT_SIZE_PX}px;width:55%;">${__("Affected Accounts")}</th>
          <th style="font-size:${TABLE_FONT_SIZE_PX}px;width:20%;text-align:left;">${__("Vouchers")}</th>
        </tr>
      </thead>
      <tbody>${rows.join("")}</tbody>
    </table>
  `;
}

function buildRebuildCompletionHtml(payload) {
  const lines = [
    __("Vouchers rebuilt: {0}", [payload.rebuilt_count || 0]),
    __("Blocked vouchers skipped: {0}", [payload.blocked_count || 0]),
    __("Already-correct vouchers skipped: {0}", [payload.already_correct_count || 0]),
    __("Failed vouchers: {0}", [payload.failed_count || 0]),
  ];

  if (payload.failed_groups?.length) {
    lines.push(`<p><b>${__("Failure summary")}:</b></p>`);
    lines.push(formatFailureSummaryTable(payload.failed_groups));
  }

  return lines.join("<br>");
}

function handleRebuildDone(
  frm,
  progressEvent,
  progressHandler,
  doneEvent,
  doneHandler,
  payload = {}
) {
  frappe.realtime.off(progressEvent, progressHandler);
  frappe.realtime.off(doneEvent, doneHandler);
  frappe.hide_progress();

  if (payload.status === STATUS_FATAL_ERROR || payload.status === STATUS_ERROR) {
    frappe.hide_msgprint(true);
    frappe.msgprint({
      title: __("Historical Rebuild Failed"),
      message: payload.message || __("Check the Error Log for details."),
      indicator: "red",
    });
    frm.reload_doc();
    return;
  }

  if (payload.status === STATUS_SUCCESS) {
    frappe.hide_msgprint(true);
    frappe.show_alert({
      message: __(
        "Historical GL Rebuild complete: {0} rebuilt, {1} blocked, {2} already correct.",
        [
          payload.rebuilt_count || 0,
          payload.blocked_count || 0,
          payload.already_correct_count || 0,
        ]
      ),
      indicator: "green",
    });
    frm.reload_doc();
    return;
  }

  frappe.hide_msgprint(true);
  frappe.msgprint({
    title:
      payload.status === STATUS_PARTIAL_SUCCESS
        ? __("Historical Rebuild Partially Complete")
        : __("Historical Rebuild Complete"),
    message: buildRebuildCompletionHtml(payload),
    indicator: payload.status === STATUS_PARTIAL_SUCCESS ? "orange" : "green",
  });

  frm.reload_doc();
}

function buildPreviewHtml(data) {
  const eligible = data.eligible || {};
  const blocked = data.blocked || {};
  const alreadyCorrect = data.already_correct || {};
  const filters = data.filters || {};
  const wholeHistory = Boolean(filters.whole_history);
  const scopeLine = wholeHistory
    ? `<p><b>${__("Scope")}:</b> ${__("Entire company history")}</p>`
    : `<p><b>${__("Posting Date Range")}:</b> ${frappe.utils.escape_html(filters.from_posting_date || "")} → ${frappe.utils.escape_html(filters.to_posting_date || "")}</p>`;

  const lines = [
    `<p><b>${__("Company")}:</b> ${frappe.utils.escape_html(filters.company || "")}</p>`,
    scopeLine,
    `<p><b>${__("Submitted vouchers in scope")}:</b> ${data.total_submitted_vouchers || 0}</p>`,
    `<p><b>${__("Eligible")}:</b> ${eligible.count || 0}</p>`,
    `<p><b>${__("Blocked")}:</b> ${blocked.count || 0}</p>`,
  ];

  if ((blocked.groups || []).length) {
    lines.push(formatFailureSummaryTable(blocked.groups));
  }

  lines.push(`<p><b>${__("Already Correct")}:</b> ${alreadyCorrect.count || 0}</p>`);

  return lines.join("");
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
        message: buildPreviewHtml(data),
        indicator: (data.blocked?.count || 0) > 0 ? "orange" : "green",
      });
      frm.reload_doc();
    },
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
              indicator: "red",
            });
            return;
          }

          frappe.show_progress(title, 0, 100, __("Job queued..."), true);

          const progressHandler = (payload = {}) => {
            if (rebuildFinished) return;
            const total = payload.total || 100;
            const current = Math.min(payload.current || 0, total);
            const message = payload.message || __("Processing...");
            frappe.show_progress(title, current, total, message, true);
          };

          const doneHandler = (payload = {}) => {
            rebuildFinished = true;
            handleRebuildDone(
              frm,
              progressEvent,
              progressHandler,
              doneEvent,
              doneHandler,
              payload
            );
          };

          frappe.realtime.on(progressEvent, progressHandler);
          frappe.realtime.on(doneEvent, doneHandler);
        },
      });
    }
  );
}
