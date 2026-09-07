// Copyright (c) 2026, Noospheric
// For license information, please see license.txt

frappe.ui.form.on("Reporting Currency GLE", {
  async onload(frm) {
    if (frm.is_new() && !frm.doc.gl_entry && !frm.doc.reporting_doe) {
      const currency = await frappe.db.get_single_value(
        "Reporting Currency Settings",
        "reporting_currency"
      );
      if (frm.is_new() && !frm.doc.gl_entry && !frm.doc.reporting_doe) {
        await frm.set_value("reporting_currency", currency);
      }
    }
  },
  refresh(frm) {
    // Make DOE records read-only
    if (frm.doc.reporting_doe === 1) {
      // Disable the form completely
      frm.set_df_property(null, "read_only", 1);

      // Hide all action buttons
      frm.page.clear_actions();
      frm.page.clear_menu();

      // Show a message to the user
      frm.dashboard.set_headline_alert(
        __(
          "This is a DOE (Difference of Exchange) record and cannot be edited. DOE records are automatically regenerated during sync."
        ),
        "blue"
      );
    }
  }
});
