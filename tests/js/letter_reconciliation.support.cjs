const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function recorder() {
  const calls = [];
  const fn = (...args) => calls.push(args);
  fn.calls = calls;
  return fn;
}

function loadForm(doctype, slug, helper) {
  const handlers = {};
  const events = new Map();
  const frappe = {
    ui: {
      form: {
        on: (name, value) => {
          handlers[name] = value;
        }
      }
    },
    utils: { escape_html: (value) => String(value).replaceAll("<", "&lt;") },
    realtime: {
      on: (name, fn) => events.set(name, fn),
      off: (name, fn) => {
        if (events.get(name) === fn) {
          events.delete(name);
        }
      }
    },
    confirm: (_message, yes) => yes(),
    throw: (message) => {
      throw new Error(message);
    }
  };
  for (const name of [
    "call",
    "msgprint",
    "show_alert",
    "show_progress",
    "hide_progress",
    "hide_msgprint"
  ]) {
    frappe[name] = recorder();
  }
  const context = vm.createContext({
    frappe,
    Set,
    Date,
    __: (message, args = []) => message.replace(/\{(\d+)\}/g, (_, i) => args[i]),
    flt: (value, precision) => Number(Number(value).toFixed(precision)),
    $: (target) => target
  });
  const root = path.resolve(__dirname, "../../karam_finance");
  // Desk appends hook JS after the controller. Evaluate in that exact order.
  for (const file of [
    `letter_reconciliation/doctype/${slug}/${slug}.js`,
    `public/js/letter_reconciliation/${helper}.js`
  ]) {
    vm.runInContext(fs.readFileSync(path.join(root, file), "utf8"), context);
  }
  return {
    frappe,
    context,
    events,
    handlers: handlers[doctype],
    allHandlers: handlers
  };
}

function makeForm(doc = {}) {
  return {
    doc,
    call: recorder(),
    reload_doc: recorder(),
    trigger: recorder(),
    refresh_field: recorder(),
    toggle_display: recorder(),
    set_df_property: recorder(),
    set_query: recorder(),
    disable_save: recorder(),
    set_value(field, value) {
      this.doc[field] = value;
    },
    clear_table(field) {
      this.doc[field] = [];
    },
    add_child(field, value) {
      this.doc[field].push(value);
    }
  };
}

module.exports = { loadForm, makeForm };
