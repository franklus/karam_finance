frappe.ui.form.on("Stock Settings", {
  setup(frm) {
    frm.set_df_property("ka_item_price_mismatch_doctypes", "cannot_add_rows", true);
    frm.set_df_property("ka_item_price_mismatch_doctypes", "cannot_delete_rows", true);
  }
});
