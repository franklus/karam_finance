// Dynamically constrain Karam Series options to doctypes flagged in the record.
(function karamSeriesFilter() {
  const scrub = (value) => {
    if (!value) {
      return "";
    }
    if (frappe.scrub) {
      return frappe.scrub(value);
    }
    return String(value)
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "_")
      .replace(/(^_)|(_$)/g, "");
  };

  const applyFilter = (frm, fieldname) => {
    if (!frm || !frm.fields_dict || !frm.fields_dict.karam_series || !fieldname) {
      return;
    }

    frm.set_query("karam_series", () => ({ filters: { [fieldname]: "1" } }));
  };

  frappe.call({
    method:
      "karam_finance.karam_series.doctype.karam_series_settings.karam_series_settings.get_doctype_list",
    callback(r) {
      const doctypes = r.message || [];

      doctypes.forEach((dt) => {
        const fieldname = scrub(dt);

        frappe.ui.form.on(dt, {
          onload(frm) {
            applyFilter(frm, fieldname);
          },
          refresh(frm) {
            applyFilter(frm, fieldname);
          }
        });

        const currentForm = frappe.ui.form?.get_cur_form?.();
        if (currentForm && currentForm.doctype === dt) {
          applyFilter(currentForm, fieldname);
        }
      });
    }
  });
})();
