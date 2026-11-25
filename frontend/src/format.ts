// Hungarian number format (1 234 567 Ft, decimal comma). Every helper renders null/NaN as "—", never 0.

export const DASH = "—";
export const MINUS = "−";

const LOCALE = "hu-HU";
const nf = (min: number, max: number) =>
  new Intl.NumberFormat(LOCALE, { minimumFractionDigits: min, maximumFractionDigits: max, useGrouping: "always" });

const nf0 = nf(0, 0);
const nf2 = nf(2, 2);
const nf6 = nf(6, 6);
const nfQty = nf(0, 6);
const nfPrice2 = nf(2, 2);
const nfPrice4 = nf(2, 4);
const nfPrice6 = nf(2, 6);

export type Num = number | null | undefined;

export function isNum(v: Num): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

const NO_DECIMALS = new Set(["HUF", "JPY", "KRW"]);
const moneyFormats = new Map<string, Intl.NumberFormat>();

function moneyFormat(ccy: string): Intl.NumberFormat {
  const code = (ccy || "HUF").toUpperCase();
  let f = moneyFormats.get(code);
  if (!f) {
    const digits = NO_DECIMALS.has(code) ? 0 : 2;
    try {
      f = new Intl.NumberFormat(LOCALE, { style: "currency", currency: code, minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: "always" });
    } catch {
      f = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits, useGrouping: "always" });
    }
    moneyFormats.set(code, f);
  }
  return f;
}

/** The currency may wrap onto the next line (a no-break space before it would force a break in the middle of it). */
function breakable(text: string): string {
  return text.replace(/ ([^\d .,\s+\-−]+)$/, " $1");
}

/** Money in a currency, Hungarian style: 1 234 567 Ft, 1 234,56 €. */
export function money(v: Num, ccy: string): string {
  return isNum(v) ? breakable(moneyFormat(ccy).format(NO_DECIMALS.has((ccy || "").toUpperCase()) ? Math.round(v) : v)) : DASH;
}

/** +1 234 Ft / −1 234 Ft */
export function signedMoney(v: Num, ccy: string): string {
  if (!isNum(v)) return DASH;
  const r = NO_DECIMALS.has((ccy || "").toUpperCase()) ? Math.round(v) : Math.round(v * 100) / 100;
  return `${r < 0 ? MINUS : "+"}${breakable(moneyFormat(ccy).format(Math.abs(r)))}`;
}

/** The currency's symbol on its own ("Ft", "€", "$"). */
export function currencySymbol(ccy: string): string {
  const part = moneyFormat(ccy).formatToParts(0).find((p) => p.type === "currency");
  return part?.value ?? (ccy || "").toUpperCase();
}

/** 11,7 M Ft or 18,5 k EUR for big numbers, the full amount otherwise. */
export function compactMoney(v: Num, ccy: string): string {
  if (!isNum(v)) return DASH;
  if (Math.abs(v) >= 1e6) return `${nf(1, 1).format(v / 1e6)} M ${currencySymbol(ccy)}`;
  if (Math.abs(v) >= 1e4) return `${nf(1, 1).format(v / 1e3)} k ${currencySymbol(ccy)}`;
  return money(v, ccy);
}

/** A y-axis label formatter whose unit (M / k / plain) suits the size of the data. */
export function axisFormatter(values: readonly number[], ccy: string): (v: number) => string {
  const max = values.reduce((m, v) => Math.max(m, Math.abs(v)), 0);
  const sym = currencySymbol(ccy);
  if (max >= 1e6) return (v) => `${nf2.format(v / 1e6)} M ${sym}`;
  if (max >= 1e4) return (v) => `${nf(1, 1).format(v / 1e3)} k ${sym}`;
  return (v) => money(v, ccy);
}

/** Fraction -> percent with sign: 0.1248 -> +12,48% */
export function pct(fraction: Num): string {
  if (!isNum(fraction)) return DASH;
  const p = fraction * 100;
  const rounded = Math.round(p * 100) / 100;
  return `${rounded < 0 ? MINUS : "+"}${nf2.format(Math.abs(rounded))}%`;
}

/** Fraction -> percent without sign: 0.123 -> 12,30% */
export function plainPct(fraction: Num, digits = 1): string {
  if (!isNum(fraction)) return DASH;
  return `${nf(digits, digits).format(fraction * 100)}%`;
}

/** Percentage-point change: +0,12 pp */
export function signedPp(v: Num): string {
  if (!isNum(v)) return DASH;
  const r = Math.round(v * 100) / 100;
  return `${r < 0 ? MINUS : "+"}${nf2.format(Math.abs(r))} pp`;
}

export function qty(v: Num): string {
  return isNum(v) ? nfQty.format(v) : DASH;
}

export function ccyLabel(ccy: string | null | undefined): string {
  if (!ccy) return "";
  if (ccy.toUpperCase() === "HUF") return "Ft";
  // Yahoo's minor units keep their case: "GBp" is pence, and "GBP" on a pence price would be 100x off
  return /^[A-Z]{2}[a-z]$/.test(ccy) ? ccy : ccy.toUpperCase();
}

/** Price with a precision that suits its size: 186,43 / 3,2896 */
export function price(v: Num, precise = false): string {
  if (!isNum(v)) return DASH;
  if (precise) return nfPrice6.format(v);
  const a = Math.abs(v);
  if (a >= 100) return nfPrice2.format(v);
  if (a >= 1) return nfPrice4.format(v);
  return nfPrice6.format(v);
}

/** A signed price difference: +12,34 USD / −0,0412 USD. Precision follows `scale` (the price it is a
 *  difference of), so a small move on a big price is not printed with six decimals. */
export function signedPrice(v: Num, ccy: string | null | undefined, scale?: number): string {
  if (!isNum(v)) return DASH;
  const a = Math.abs(isNum(scale) ? scale : v);
  const digits = a >= 100 ? 2 : a >= 1 ? 4 : 6;
  const f = digits === 2 ? nfPrice2 : digits === 4 ? nfPrice4 : nfPrice6;
  // Round first, then pick the sign (as signedMoney does), so −0.001 on a 186 USD price is "+0,00".
  const r = Math.round(v * 10 ** digits) / 10 ** digits;
  const label = ccyLabel(ccy);
  return `${r < 0 ? MINUS : "+"}${f.format(Math.abs(r))}${label ? ` ${label}` : ""}`;
}

export function priceWithCcy(v: Num, ccy: string | null | undefined, precise = false): string {
  if (!isNum(v)) return DASH;
  const label = ccyLabel(ccy);
  return label ? `${price(v, precise)} ${label}` : price(v, precise);
}

export function fixed2(v: Num): string {
  return isNum(v) ? nf2.format(v) : DASH;
}

export function fixed6(v: Num): string {
  return isNum(v) ? nf6.format(v) : DASH;
}

export function int(v: Num): string {
  return isNum(v) ? nf0.format(Math.round(v)) : DASH;
}

export function signClass(v: Num): "up" | "down" | "" {
  if (!isNum(v)) return "";
  return v >= 0 ? "up" : "down";
}

/** HH:MM:SS in the browser's local time, from an ISO timestamp. */
export function clockTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  return d.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/** as_of is an ISO datetime for live quotes and YYYY-MM-DD otherwise. */
export function asOfLabel(asOf: string | null | undefined): string {
  if (!asOf) return DASH;
  if (/^\d{4}-\d{2}-\d{2}$/.test(asOf)) return asOf;
  const d = new Date(asOf);
  if (Number.isNaN(d.getTime())) return asOf;
  return `${localISODate(d)} ${hourMinute.format(d)}`;
}
// one formatter for every label: toLocaleTimeString builds a new one per call (slow on a 9,000-bar chart)
const hourMinute = new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit" });

export function localISODate(d: Date = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

export function daysAgoISO(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return localISODate(d);
}

const TYPE_LABELS: Record<string, string> = {
  stock: "Stock",
  etf: "ETF",
  mutual_fund: "Fund",
  government_bond: "Gov. bond",
  commodity: "Commodity",
};

export function typeLabel(t: string): string {
  return TYPE_LABELS[t] ?? t;
}

export const ASSET_TYPES = ["stock", "etf", "mutual_fund", "government_bond", "commodity"] as const;
export type AssetType = (typeof ASSET_TYPES)[number];

export function assetTypeOption(t: string): string {
  return (
    {
      stock: "Stock",
      etf: "ETF",
      mutual_fund: "Mutual fund",
      government_bond: "Government bond",
      commodity: "Commodity",
    } as Record<string, string>
  )[t] ?? t;
}

export function sourceLabel(src: string | null | undefined): string {
  switch (src) {
    case "live":
      return "Live quote · Yahoo Finance";
    case "daily":
      return "Daily fund price · published T+1–2";
    case "stored":
      return "Stored daily price";
    default:
      return "No price yet";
  }
}

/** Parse a number typed by a person: "1 234,5" and "1234.5" both work. Empty or invalid -> null. */
export function parseDecimal(input: string): number | null {
  const s = input.replace(/[\s\u00a0]/g, "").replace(",", ".");
  if (s === "") return null;
  const n = Number(s);
  return Number.isFinite(n) ? n : null;
}
