const assert = require("node:assert/strict");
const test = require("node:test");

test("repost list adds bulk action that triggers queued repost execution", () => {
  let confirmMessage = "";
  let confirmHandler;
  let callArgs;
  let alertArgs;
  let refreshed = false;
  const addedButtons = [];

  global.__ = (value) => value;
  global.frappe = {
    listview_settings: {},
    confirm(message, onYes) {
      confirmMessage = message;
      confirmHandler = onYes;
    },
    call(args) {
      callArgs = args;
      if (typeof args.callback === "function") {
        args.callback();
      }
    },
    show_alert(args) {
      alertArgs = args;
    },
  };

  require("../../karam_finance/public/js/overrides/repost_item_valuation_list.js");

  const config = global.frappe.listview_settings["Repost Item Valuation"];
  assert.ok(config);
  assert.equal(typeof config.onload, "function");

  config.onload({
    page: {
      add_inner_button(label, handler) {
        addedButtons.push({ label, handler });
      },
    },
    refresh() {
      refreshed = true;
    },
  });

  assert.equal(addedButtons.length, 1);
  assert.equal(addedButtons[0].label, "Run Repost Queue Now");

  addedButtons[0].handler();
  assert.match(confirmMessage, /queued and in-progress entries/i);
  assert.equal(typeof confirmHandler, "function");

  confirmHandler();
  assert.equal(
    callArgs.method,
    "erpnext.stock.doctype.repost_item_valuation.repost_item_valuation.execute_repost_item_valuation"
  );
  assert.equal(alertArgs.indicator, "green");
  assert.equal(refreshed, true);
});
