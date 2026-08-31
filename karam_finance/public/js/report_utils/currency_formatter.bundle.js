// Shared currency formatter helpers for custom query reports.

(function initCurrencyFormatter(root) {
  const HELPER_VERSION = "2026.02.12";
  const loadedAt = new Date().toISOString();
  const fallbackWarningKeys = new Set();

  function extractText(html) {
    if (!html) return "";
    if (typeof document === "undefined") {
      const raw = String(html);
      let out = "";
      let insideTag = false;
      for (let i = 0; i < raw.length; i += 1) {
        const ch = raw[i];
        if (ch === "<") {
          insideTag = true;
        } else if (ch === ">") {
          insideTag = false;
        } else if (!insideTag) {
          out += ch;
        }
      }
      return out.trim();
    }
    const el = document.createElement("span");
    el.innerHTML = String(html);
    return (el.textContent || "").trim();
  }

  function splitSymbolAndNumber(text) {
    const idx = text.search(/[-\d(]/);
    if (idx <= 0) return null;
    const symbol = text.substring(0, idx).trim();
    const number = text.substring(idx);
    if (!symbol || !number) return null;
    return { symbol, number };
  }

  function formatCurrencyHtml(formatted, extraStyle) {
    const text = extractText(formatted);
    const parts = splitSymbolAndNumber(text);
    if (!parts) return formatted;

    const styleSuffix = extraStyle ? String(extraStyle) : "";
    // eslint-disable-next-line max-len
    return `<div style="display:flex;justify-content:space-between;width:100%;gap:0.25rem;${styleSuffix}"><span>${parts.symbol}</span><span>${parts.number}</span></div>`;
  }

  function alignCurrencyCell({ value, column, formatted, extraStyle = "" }) {
    if (!column || column.fieldtype !== "Currency") return formatted;
    if (value == null || value === "" || !formatted) return formatted;
    return formatCurrencyHtml(formatted, extraStyle);
  }

  function warnFallbackUsage(reportName, columnField = "") {
    const key = `${reportName || "unknown"}:${columnField || ""}`;
    if (fallbackWarningKeys.has(key)) return;
    fallbackWarningKeys.add(key);
    const effectiveReport = reportName || "Unknown Report";
    const fieldSuffix = columnField ? ` (${columnField})` : "";
    // eslint-disable-next-line no-console
    console.warn(
      "[Karam Reports] Deprecated currency fallback used in " +
        `${effectiveReport}${fieldSuffix}.`
    );
  }

  const api = {
    alignCurrencyCell,
    formatCurrencyHtml,
    splitSymbolAndNumber,
    warnFallbackUsage,
    __version__: HELPER_VERSION,
    __loaded_at__: loadedAt,
  };

  const globalScope = root;
  globalScope.karamReportFormatters = globalScope.karamReportFormatters || {};
  globalScope.karamReportFormatters.currency = api;

  const missingHelperReports = new Set();

  globalScope.alignCurrencyWithSharedHelper = function alignCurrencyWithSharedHelper(
    reportName,
    value,
    column,
    formatted,
    extraStyle = ""
  ) {
    if (
      !column ||
      column.fieldtype !== "Currency" ||
      value == null ||
      value === "" ||
      !formatted
    ) {
      return formatted;
    }

    if (api) {
      return api.alignCurrencyCell({ value, column, formatted, extraStyle });
    }

    if (!missingHelperReports.has(reportName)) {
      missingHelperReports.add(reportName);
      // eslint-disable-next-line no-console
      console.error(`[Karam Reports] Missing shared currency helper in ${reportName}.`);
    }

    return (
      '<span data-karam-currency-helper-missing="1" ' +
      `title="Currency helper missing">${formatted}</span>`
    );
  };

  if (typeof module === "object" && module.exports) {
    module.exports = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
