// Historical rebuild rendering, loaded only with its settings DocType.

const TABLE_FONT_SIZE_PX = 10;
const TABLE_CELL_STYLE = `vertical-align:top;font-size:${TABLE_FONT_SIZE_PX}px;`;
const BULLET_LIST_STYLE = "margin:0;padding-left:1.15rem;";

function buildBulletList(items = []) {
  if (!items.length) {
    return "—";
  }
  const rows = items.map(
    (item) => `<li>${frappe.utils.escape_html(String(item))}</li>`
  );
  return `<ul style="${BULLET_LIST_STYLE}font-size:${TABLE_FONT_SIZE_PX}px;">${rows.join("")}</ul>`;
}

function formatFailureSummaryTable(groups) {
  if (!groups || !groups.length) {
    return "";
  }

  const rows = groups.map((group) => {
    const reason = frappe.utils.escape_html(group.reason || "");
    const count = group.voucher_count || 0;
    const accounts = buildBulletList(group.accounts || group.account_samples || []);
    return `
      <tr>
        <td style="${TABLE_CELL_STYLE}width:25%;"><b>${reason}</b></td>
        <td style="${TABLE_CELL_STYLE}width:55%;">${accounts}</td>
        <td style="${TABLE_CELL_STYLE}width:20%;text-align:left;">${count}</td>
      </tr>
    `;
  });

  return `
    <table
      class="table table-bordered"
      style="margin-top:8px;font-size:${TABLE_FONT_SIZE_PX}px;"
    >
      <thead>
        <tr>
          <th style="font-size:${TABLE_FONT_SIZE_PX}px;width:25%;">${__("Reason")}</th>
          <th style="font-size:${TABLE_FONT_SIZE_PX}px;width:55%;">${__("Affected Accounts")}</th>
          <th style="font-size:${TABLE_FONT_SIZE_PX}px;width:20%;text-align:left;">${__("Vouchers")}</th>
        </tr>
      </thead>
      <tbody>${rows.join("")}</tbody>
    </table>
  `;
}

function buildRebuildCompletionHtml(payload) {
  const lines = [
    __("Vouchers rebuilt: {0}", [payload.rebuilt_count || 0]),
    __("Blocked vouchers skipped: {0}", [payload.blocked_count || 0]),
    __("Already-correct vouchers skipped: {0}", [payload.already_correct_count || 0]),
    __("Failed vouchers: {0}", [payload.failed_count || 0])
  ];

  if (payload.failed_groups?.length) {
    lines.push(`<p><b>${__("Failure summary")}:</b></p>`);
    lines.push(formatFailureSummaryTable(payload.failed_groups));
  }

  return lines.join("<br>");
}

function previewScopeHtml(filters) {
  return filters.whole_history
    ? `<p><b>${__("Scope")}:</b> ${__("Entire company history")}</p>`
    : `<p><b>${__("Posting Date Range")}:</b> ${frappe.utils.escape_html(filters.from_posting_date || "")} → ${frappe.utils.escape_html(filters.to_posting_date || "")}</p>`;
}

function previewScopeLines(filters) {
  return [
    `<p><b>${__("Company")}:</b> ${frappe.utils.escape_html(filters.company || "")}</p>`,
    previewScopeHtml(filters)
  ];
}

function previewHeadingLines(data) {
  const filters = data.filters || {};
  return [
    ...previewScopeLines(filters),
    `<p><b>${__("Submitted vouchers in scope")}:</b> ${data.total_submitted_vouchers || 0}</p>`,
    `<p><b>${__("Eligible")}:</b> ${data.eligible?.count || 0}</p>`,
    `<p><b>${__("Blocked")}:</b> ${data.blocked?.count || 0}</p>`
  ];
}

function buildPreviewHtml(data) {
  const blocked = data.blocked || {};
  const alreadyCorrect = data.already_correct || {};
  const lines = previewHeadingLines(data);
  if ((blocked.groups || []).length) {
    lines.push(formatFailureSummaryTable(blocked.groups));
  }
  lines.push(`<p><b>${__("Already Correct")}:</b> ${alreadyCorrect.count || 0}</p>`);
  return lines.join("");
}

frappe.karam_letter_rebuild = { buildRebuildCompletionHtml, buildPreviewHtml };
