"use strict";

function pollReportingCurrencySync(progressEvent, done) {
  let stopped = false;
  let timer;
  let failures = 0;
  let unavailable = 0;
  const stop = () => {
    stopped = true;
    clearTimeout(timer);
  };
  const finish = (payload) => {
    stop();
    done(payload);
  };
  const retry = () => {
    if (!stopped) {
      timer = setTimeout(check, 2000);
    }
  };
  function check() {
    if (stopped) {
      return;
    }
    requestReportingCurrencySyncStatus(progressEvent, {
      callback: (response) => {
        if (stopped) {
          return;
        }
        failures = 0;
        const status = response.message || {};
        if (status.state === "complete") {
          finish(status.result);
        } else if (
          ["queued", "started", "deferred", "scheduled"].includes(status.state)
        ) {
          unavailable = 0;
          retry();
        } else if (["missing", "finished"].includes(status.state) && unavailable < 5) {
          unavailable += 1;
          retry();
        } else {
          finish(syncStatusError(false));
        }
      },
      error: () => {
        if (stopped) {
          return;
        }
        failures += 1;
        if (failures < 3) {
          retry();
        } else {
          finish(syncStatusError(true));
        }
      }
    });
  }
  check();
  return stop;
}

function requestReportingCurrencySyncStatus(progressEvent, callbacks) {
  frappe.call({
    method:
      "karam_finance.reporting_currency.doctype.reporting_currency_gle.sync.orchestrator.get_reporting_currency_sync_status",
    args: { progress_event: progressEvent },
    ...callbacks
  });
}

window.pollReportingCurrencySync = pollReportingCurrencySync;

function syncStatusError(connectionLost) {
  return {
    status: "error",
    title: connectionLost ? __("Unable to Check Sync") : __("Sync Status Unavailable"),
    message: connectionLost
      ? __(
          "The connection to the sync job was lost. The job may still be running; reload and check its status before starting another sync."
        )
      : __(
          "The sync completion could not be confirmed. Check the background job before starting another sync."
        )
  };
}

function watchReportingCurrencySync(frm, button, data, finish) {
  const progressEvent = data.progress_event;
  const doneEvent = data.done_event;
  const title = __("Syncing Reporting Currency Data");

  if (!progressEvent || !doneEvent) {
    frappe.msgprint({
      title: __("Unable to Start Sync"),
      message: __("The server did not return progress information."),
      indicator: "red"
    });
    button?.prop("disabled", false);
    return;
  }

  frappe.show_progress(title, 0, 100, __("Job queued..."));
  let finished = false;
  let stopPolling = () => {};

  const progressHandler = (payload = {}) => {
    if (finished) {
      return;
    }
    const total = payload.total || 100;
    const current = Math.min(payload.current || 0, total);
    const message = payload.message || __("Processing...");
    frappe.show_progress(title, current, total, message);
  };

  const doneHandler = (payload = {}) => {
    if (finished) {
      return;
    }
    finished = true;
    stopPolling();
    frappe.realtime.off(progressEvent, progressHandler);
    frappe.realtime.off(doneEvent, doneHandler);
    frappe.hide_progress();

    finish(frm, button, payload);
  };

  frappe.realtime.on(progressEvent, progressHandler);
  frappe.realtime.on(doneEvent, doneHandler);
  if (
    progressEvent.startsWith("rc_gle_sync_") ||
    progressEvent.startsWith("rc_currency_change_")
  ) {
    stopPolling = pollReportingCurrencySync(progressEvent, doneHandler);
  }
}

window.watchReportingCurrencySync = watchReportingCurrencySync;
