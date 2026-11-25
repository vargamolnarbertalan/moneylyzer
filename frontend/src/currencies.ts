// Currencies offered in pickers, in a neutral order (widely used first). Wherever a list is shown, the
// user's reporting currency goes first (withFirst). Keep in step with server/main.py COMMON_CURRENCIES.
export const COMMON_CURRENCIES = ["EUR", "USD", "GBP", "CHF", "JPY", "CAD", "AUD", "CNY", "SEK", "NOK", "DKK", "PLN", "CZK", "HUF", "RON"];

/** The given currencies first (in order, empty ones skipped), then the rest of the list, no repeats. */
export function withFirst(first: readonly (string | null | undefined)[], list: readonly string[] = COMMON_CURRENCIES): string[] {
  return Array.from(new Set([...first.filter((c): c is string => !!c), ...list]));
}

// Regions whose currency is the euro; any other region not listed below also falls back to EUR.
const EURO = new Set(["AT", "BE", "CY", "DE", "EE", "ES", "FI", "FR", "GR", "HR", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PT", "SI", "SK"]);
const REGION: Record<string, string> = {
  HU: "HUF", US: "USD", GB: "GBP", CH: "CHF", LI: "CHF", JP: "JPY", CA: "CAD", AU: "AUD", CN: "CNY",
  SE: "SEK", NO: "NOK", DK: "DKK", PL: "PLN", CZ: "CZK", RO: "RON",
};

/** A new install's reporting currency, from the region of the browser's languages (hu-HU -> HUF,
 *  de-DE -> EUR, en -> USD). Read in the browser only: no IP lookup, nothing sent anywhere else. */
export function browserCurrency(): string {
  const tags = navigator.languages?.length ? navigator.languages : [navigator.language];
  for (const tag of tags) {
    let region: string | undefined;
    try {
      region = new Intl.Locale(tag).maximize().region;
    } catch {
      continue;
    }
    if (!region) continue;
    if (EURO.has(region)) return "EUR";
    const ccy = REGION[region];
    if (ccy) return ccy;
  }
  return "EUR";
}
