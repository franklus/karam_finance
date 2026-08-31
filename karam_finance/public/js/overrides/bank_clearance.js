frappe.ui.form.on("Bank Clearance", {
  refresh(frm) {
    addPostingDateToClearanceButton(frm);
  },
});

function addPostingDateToClearanceButton(frm) {
  const wrapper = frm.fields_dict.payment_entries.$wrapper;
  wrapper.find(".posting-date-to-clearance-btn").remove();

  const btnClass = "btn btn-xs btn-default posting-date-to-clearance-btn";
  const label = __("Posting Date \u2192 Clearance Date");
  const $btn = $(
    `<button class="${btnClass}" style="margin-bottom: 10px;">
      ${label}
    </button>`
  );

  $btn.on("click", () => {
    const rows = frm.doc.payment_entries || [];
    if (!rows.length) {
      frappe.msgprint(__("No payment entries to update."));
      return;
    }

    rows.forEach((row) => {
      if (row.posting_date) {
        frappe.model.set_value(
          row.doctype,
          row.name,
          "clearance_date",
          row.posting_date
        );
      }
    });

    frm.refresh_field("payment_entries");
  });

  wrapper.prepend($btn);
}
