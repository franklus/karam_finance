// Selection state helpers loaded through the Letter Reconciliation DocType hook.

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
      if (!row_data) {
        return;
      }

      if ($(e.target).is(":checked")) {
        items_set.add(row_data);
      } else {
        items_set.delete(row_data);
      }
      update_sums(fieldname, items_set);
    };
  }

  frm.fields_dict.jv_debit.grid.wrapper
    .off("change.karamLetterSelection")
    .on(
      "change.karamLetterSelection",
      'input[type="checkbox"]',
      handle_row_selection("jv_debit", frm.selected_debit_items)
    );

  frm.fields_dict.jv_credit.grid.wrapper
    .off("change.karamLetterSelection")
    .on(
      "change.karamLetterSelection",
      'input[type="checkbox"]',
      handle_row_selection("jv_credit", frm.selected_credit_items)
    );

  frm.doc.jv_debit = frm.doc.jv_debit || [];
  frm.doc.jv_credit = frm.doc.jv_credit || [];
}

function get_selected_items(frm) {
  return {
    cr_items: Array.from(frm.selected_credit_items || []),
    dt_items: Array.from(frm.selected_debit_items || [])
  };
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

function clear_selections(frm) {
  if (frm.selected_debit_items) {
    frm.selected_debit_items.clear();
  }
  if (frm.selected_credit_items) {
    frm.selected_credit_items.clear();
  }
  reset_sums(frm);
}

function reset_sums(frm) {
  frm.set_value("debit_sum", 0);
  frm.set_value("credit_sum", 0);
  frm.set_value("difference", 0);
}

frappe.karam_letter_selection = {
  init_row_selection,
  get_selected_items,
  get_latest_year,
  clear_selections,
  reset_sums
};
