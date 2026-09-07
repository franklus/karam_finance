// Shared currency formatter helpers for custom query reports.

(function initCurrencyFormatter(root) {
  const HELPER_VERSION = "2026.09.01";
  const NUMERIC_FIELD_TYPES = new Set(["Currency", "Float", "Int", "Percent"]);
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
    return `<div style="display:flex;justify-content:space-between;width:100%;gap:20px;${styleSuffix}"><span>${parts.symbol}</span><span>${parts.number}</span></div>`;
  }

  function alignCurrencyCell({ value, column, formatted, extraStyle = "" }) {
    if (!column || column.fieldtype !== "Currency") {
      return formatted;
    }
    if (value == null || value === "") {
      return "";
    }
    if (!formatted) {
      return formatted;
    }
    return formatCurrencyHtml(formatted, extraStyle);
  }

  function tintNegativeValue(value, column, formatted) {
    const numericValue = Number(value);
    if (
      !NUMERIC_FIELD_TYPES.has(column?.fieldtype) ||
      !Number.isFinite(numericValue) ||
      numericValue >= 0
    ) {
      return formatted;
    }
    // Values displayed as zero should remain neutral even if raw precision is negative.
    if (!/[1-9]/.test(extractText(formatted))) {
      return formatted;
    }
    return `<div class="karam-negative-value">${formatted}</div>`;
  }

  function installNegativeValueStyle() {
    if (
      typeof document === "undefined" ||
      document.getElementById("karam-negative-values")
    ) {
      return;
    }
    const style = document.createElement("style");
    style.id = "karam-negative-values";
    style.textContent =
      ".dt-cell:has(.karam-negative-value) { background-color: var(--red-100, #fff0f0); }";
    document.head.appendChild(style);
  }

  installNegativeValueStyle();

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
    tintNegativeValue,
    formatCurrencyHtml,
    splitSymbolAndNumber,
    warnFallbackUsage,
    __version__: HELPER_VERSION,
    __loaded_at__: loadedAt
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
    if (!column || !formatted) {
      return formatted;
    }

    if (api) {
      const aligned = api.alignCurrencyCell({ value, column, formatted, extraStyle });
      return api.tintNegativeValue(value, column, aligned);
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
