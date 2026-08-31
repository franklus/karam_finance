frappe.ui.form.on("Karam Series Settings", {
  setup(frm) {
    frm.set_df_property("doctype_list", "cannot_add_rows", true);
    frm.set_df_property("doctype_list", "cannot_delete_rows", true);
  },
  refresh(frm) {
    add_update_doctype_list_button(frm);
  }
});

frappe.ui.form.on("Doctype List", {
  karam_series_mandatory(frm) {
    // The saved child table is the policy source of truth. Projection onto
    // Custom Field happens from the controller's on_update hook after Save.
    frm.dirty();
  }
});

function add_update_doctype_list_button(frm) {
  frm.add_custom_button(__("Update Doctype List"), () => {
    frappe.call({
      method:
        "karam_finance.karam_series.doctype.karam_series_settings.karam_series_settings.populate_doctype_list",
      callback: () => {
        frm.reload_doc();
        frappe.msgprint(__("Doctype list updated successfully"));
      },
      error: () => {
        frappe.msgprint(__("Error updating doctype list"));
      }
    });
  });
}

// Informational HTML and static list previews were removed.
// The table remains the single source of truth; a manual update
// button is still provided for convenience.
