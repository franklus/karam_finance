// Letter Reconciliation Controller (Desk)

frappe.ui.form.on("Letter Reconciliation", {
  refresh(frm) {
    frm.disable_save();
    set_account_filter(frm);
    set_party_type_filter(frm);
    init_row_selection(frm);
    if (frm.doc.account) {
      clear_selections(frm);
      fetch_journal_entries(frm);
    }
  },

  account(frm) {
    clear_selections(frm);
    fetch_journal_entries(frm);
  },

  start_date(frm) {
    clear_selections(frm);
    fetch_journal_entries(frm);
  },

  end_date(frm) {
    clear_selections(frm);
    fetch_journal_entries(frm);
  },

  party_type(frm) {
    frm.set_value("party", "");
    frm.set_value("party_name", "");
    clear_selections(frm);
    fetch_journal_entries(frm);
  },

  party(frm) {
    fetch_party_name(frm);
    clear_selections(frm);
    fetch_journal_entries(frm);
  },

  show_letter(frm) {
    clear_selections(frm);
    fetch_journal_entries(frm);
  },

  previous(frm) {
    navigate_account(frm, "previous");
  },

  next(frm) {
    navigate_account(frm, "next");
  },

  assign_letter(frm) {
    assign_letter(frm);
  },

  remove_letter(frm) {
    remove_letter(frm);
  },
});

function set_account_filter(frm) {
  frm.set_query("account", () => {
    return {
      filters: {
        enable_lettering: 1,
        company: frm.doc.company,
      },
    };
  });
}

function set_party_type_filter(frm) {
  frm.set_query("party_type", () => {
    return {
      filters: {
        name: ["in", ["Customer", "Employee", "Shareholder", "Supplier"]],
      },
    };
  });
}

function navigate_account(frm, direction) {
  if (direction === "previous" && !frm.doc.account) return;

  frappe.call({
    method:
      "karam_finance.letter_reconciliation.doctype.letter_reconciliation.letter_reconciliation.get_adjacent_account",
    args: {
      current_account: frm.doc.account || "",
      direction,
      company: frm.doc.company || "",
    },
    callback(r) {
      if (r.message) {
        frm.set_value("account", r.message);
      } else if (frm.doc.account) {
        frappe.show_alert({
          message: __("No {0} account found.", [direction]),
          indicator: "blue",
        });
      }
    },
  });
}

const PARTY_NAME_FIELD = {
  Customer: "customer_name",
  Supplier: "supplier_name",
  Shareholder: "title",
  Employee: "employee_name",
};

function fetch_party_name(frm) {
  const { party_type, party } = frm.doc;
  if (!party_type || !party) {
    frm.set_value("party_name", "");
    return;
  }

  const name_field = PARTY_NAME_FIELD[party_type];
  if (!name_field) {
    frm.set_value("party_name", "");
    return;
  }

  frappe.db.get_value(party_type, party, name_field, (r) => {
    frm.set_value("party_name", (r && r[name_field]) || "");
  });
}

function fetch_journal_entries(frm) {
  if (!frm.doc.account) return null;

  return frm.call({
    method:
      "karam_finance.letter_reconciliation.doctype.letter_reconciliation.letter_reconciliation.journal_entry_list",
    args: {
      account: frm.doc.account,
      start_date: frm.doc.start_date,
      end_date: frm.doc.end_date,
      party_type: frm.doc.party_type,
      party: frm.doc.party,
      show_letter: frm.doc.show_letter,
    },
    freeze: true,
    freeze_message: __("Fetching entries..."),
    callback(res) {
      if (res.message && !res.message.error) {
        frm.clear_table("jv_credit");
        frm.clear_table("jv_debit");

        add_entries(frm, res.message.cr || [], "jv_credit");
        add_entries(frm, res.message.dr || [], "jv_debit");

        frm.refresh_field("jv_credit");
        frm.refresh_field("jv_debit");
      } else if (res.message && res.message.error) {
        frappe.msgprint(__("Error fetching entries: {0}", [res.message.error]));
      }
    },
    error(e) {
      frappe.msgprint(__("Error fetching journal entries: {0}", [e.message]));
    },
  });
}

function add_entries(frm, entries, fieldname) {
  entries.forEach((entry) => {
    const amount_field = fieldname === "jv_credit" ? "credit" : "debit";
    const currency_field =
      fieldname === "jv_credit"
        ? "credit_in_account_currency"
        : "debit_in_account_currency";

    frm.add_child(fieldname, {
      jv_row_name: entry.jv_row_name,
      journal_entry: entry.journal_entry,
      [amount_field]: entry[currency_field] || 0,
      letter: entry.letter || "",
      account: entry.account,
      party_type: entry.party_type || "",
      party: entry.party || "",
      party_name: entry.party_name || "",
      posting_date: entry.posting_date,
      user_remark: entry.user_remark || "",
    });
  });
}

function init_row_selection(frm) {
  frm.selected_debit_items = frm.selected_debit_items || new Set();
  frm.selected_credit_items = frm.selected_credit_items || new Set();

  function calculate_total(items_set) {
    const raw = Array.from(items_set).reduce((sum, item) => {
      return sum + (parseFloat(item.credit || item.debit || 0) || 0);
    }, 0);
    return flt(raw, 2);
  }

  function update_difference() {
    const debit_total = calculate_total(frm.selected_debit_items || new Set());
    const credit_total = calculate_total(frm.selected_credit_items || new Set());
    frm.set_value("difference", debit_total - credit_total);
  }

  function update_sums(fieldname, items_set) {
    const sum_field = fieldname.includes("credit") ? "credit_sum" : "debit_sum";
    const total = calculate_total(items_set);
    frm.set_value(sum_field, total);
    update_difference();
  }

  function handle_row_selection(fieldname, items_set) {
    return (e) => {
      const row = $(e.target).closest(".grid-row");
      const idx = parseInt(row.data("idx"), 10) - 1;
      const row_data = frm.doc[fieldname][idx];
      if (!row_data) return;

      if ($(e.target).is(":checked")) {
        items_set.add(row_data);
      } else {
        items_set.delete(row_data);
      }
      update_sums(fieldname, items_set);
    };
  }

  frm.fields_dict.jv_debit.grid.wrapper.on(
    "change",
    'input[type="checkbox"]',
    handle_row_selection("jv_debit", frm.selected_debit_items)
  );

  frm.fields_dict.jv_credit.grid.wrapper.on(
    "change",
    'input[type="checkbox"]',
    handle_row_selection("jv_credit", frm.selected_credit_items)
  );

  frm.doc.jv_debit = frm.doc.jv_debit || [];
  frm.doc.jv_credit = frm.doc.jv_credit || [];
}

function get_selected_items(frm) {
  return {
    cr_items: Array.from(frm.selected_credit_items || []),
    dt_items: Array.from(frm.selected_debit_items || []),
  };
}

function assign_letter(frm) {
  if (!frm.doc.account) {
    frappe.throw(__("Please select an account."));
  }

  const selected = get_selected_items(frm);
  if (!selected.cr_items.length || !selected.dt_items.length) {
    frappe.throw(__("Select at least one credit and one debit entry."));
  }

  const latest_year = get_latest_year(frm);

  frappe.call({
    method:
      "karam_finance.letter_reconciliation.doctype.letter_reconciliation.letter_reconciliation.set_letter",
    args: { ...selected, latest_year, account: frm.doc.account },
    freeze: true,
    freeze_message: __("Assigning letter..."),
    callback(r) {
      if (r.message && !r.exc) {
        frm.set_value("last_letter", r.message.last_letter);
        frm.set_value("next_letter", r.message.next_letter);
        frm.trigger("account");
        clear_selections(frm);
        frappe.show_alert({
          message: __("Letter assigned successfully"),
          indicator: "green",
        });
      }
    },
    error() {
      frappe.msgprint({
        title: __("Letter Assignment Failed"),
        message: __("Could not assign letter. Check the error log for details."),
        indicator: "red",
      });
    },
  });
}

function get_latest_year(frm) {
  const credit_dates = Array.from(frm.selected_credit_items || []).map(
    (i) => new Date(i.posting_date)
  );
  const debit_dates = Array.from(frm.selected_debit_items || []).map(
    (i) => new Date(i.posting_date)
  );
  const all = [...credit_dates, ...debit_dates];
  return all.length
    ? new Date(Math.max(...all)).getFullYear()
    : new Date().getFullYear();
}

function remove_letter(frm) {
  const selected = get_selected_items(frm);
  if (!selected.cr_items.length || !selected.dt_items.length) {
    frappe.throw(__("Select at least one credit and one debit entry."));
  }

  frappe.call({
    method:
      "karam_finance.letter_reconciliation.doctype.letter_reconciliation.letter_reconciliation.remove_letter",
    args: selected,
    freeze: true,
    freeze_message: __("Removing letter..."),
    callback(r) {
      if (r.message && !r.exc) {
        frm.trigger("account");
        clear_selections(frm);
        frappe.show_alert({
          message: __("Letter removed successfully"),
          indicator: "green",
        });
      }
    },
    error() {
      frappe.msgprint({
        title: __("Letter Removal Failed"),
        message: __("Could not remove letter. Check the error log for details."),
        indicator: "red",
      });
    },
  });
}

function clear_selections(frm) {
  if (frm.selected_debit_items) frm.selected_debit_items.clear();
  if (frm.selected_credit_items) frm.selected_credit_items.clear();
  reset_sums(frm);
}

function reset_sums(frm) {
  frm.set_value("debit_sum", 0);
  frm.set_value("credit_sum", 0);
  frm.set_value("difference", 0);
}

["Journal Entry Debit", "Journal Entry Credit"].forEach((doctype) => {
  frappe.ui.form.on(doctype, {
    debit: clear_selections,
    credit: clear_selections,
    journal_entry_debit_remove: clear_selections,
    journal_entry_credit_remove: clear_selections,
  });
});
