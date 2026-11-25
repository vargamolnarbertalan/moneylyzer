// Minute and hour chart intervals, shared by the holding and portfolio charts: which ones Yahoo serves for a
// range, the default for each range, and fetching the bars with a refresh every minute while in view.
import { useEffect, useRef, useState } from "react";
import { errorMessage } from "./api";

export type Interval = "1m" | "5m" | "15m" | "30m" | "1h" | "4h" | "1d";

export const INTERVALS: Record<Interval, { label: string; name: string; adjective: string }> = {
  "1m": { label: "1m", name: "1 minute", adjective: "1-minute" },
  "5m": { label: "5m", name: "5 minutes", adjective: "5-minute" },
  "15m": { label: "15m", name: "15 minutes", adjective: "15-minute" },
  "30m": { label: "30m", name: "30 minutes", adjective: "30-minute" },
  "1h": { label: "1h", name: "1 hour", adjective: "hourly" },
  "4h": { label: "4h", name: "4 hours", adjective: "4-hour" },
  "1d": { label: "1D", name: "1 day", adjective: "daily" },
};

// What Yahoo serves for each span (the server checks the same): 1-minute bars for a week, up to 30 minutes
// for 60 days, hourly for two years. Daily always, from the stored prices; longer ranges are daily only.
const RANGE_INTERVALS: Record<string, Interval[]> = {
  "1W": ["1m", "5m", "15m", "30m", "1h", "1d"],
  "1M": ["5m", "15m", "30m", "1h", "4h", "1d"],
  "3M": ["1h", "4h", "1d"],
  "6M": ["1h", "4h", "1d"],
  "1Y": ["1h", "4h", "1d"],
};
const DEFAULT_INTERVAL: Record<string, Interval> = { "1W": "15m", "1M": "1h", "3M": "4h" };
export const INTRADAY_REFRESH_MS = 60_000;

export function intervalsFor(range: string, intradayPossible: boolean): Interval[] {
  return intradayPossible ? (RANGE_INTERVALS[range] ?? ["1d"]) : ["1d"];
}

/** The interval a range opens with: its own default, or daily where minute/hour prices are not possible. */
export function defaultInterval(range: string, intradayPossible: boolean): Interval {
  return intradayPossible ? (DEFAULT_INTERVAL[range] ?? "1d") : "1d";
}

export function intervalOptions(offered: readonly Interval[]) {
  return offered.map((v) => ({ value: v, label: INTERVALS[v].label, name: INTERVALS[v].name }));
}

/** Yahoo tickers never contain ":"; funds ("fund:ISIN") and holdings without a symbol have daily prices only. */
export function isYahooSymbol(symbol: string | null | undefined): boolean {
  return !!symbol && !!symbol.trim() && !symbol.includes(":");
}

interface Bars {
  currency: string;
  points: { t: string }[];
}

export interface Fetched<T extends Bars> {
  key: string;
  data?: T;
  error?: string;
  /** When the bars on screen were fetched (ISO). */
  at?: string;
  /** The last refresh failed: the bars shown are from `at`. */
  refreshError?: string;
}

/** A refresh's bars, keeping the older ones the window has since moved past: the chart's first point stays
 *  put, so a refresh only adds at the end and never clears a hover or a picked comparison. */
export function keepStart<T extends Bars>(before: T | undefined, next: T): T {
  const first = next.points[0]?.t;
  if (!before || !first || before.currency !== next.currency) return next;
  return { ...next, points: [...before.points.filter((p) => p.t < first), ...next.points] } as T;
}

/** Bars for `key` while `enabled`: fetched at once, then every minute while the page is in view. A failed
 *  refresh keeps the bars on screen and marks them stale; a failed first load carries the reason. */
export function useIntraday<T extends Bars>(key: string, enabled: boolean, load: () => Promise<T>): { current: Fetched<T> | null; stale: boolean } {
  const [fetched, setFetched] = useState<Fetched<T> | null>(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    // One request at a time: a portfolio chart costs a Yahoo call per ticker, and while Yahoo is slow a new
    // one each minute would pile up (and an older answer landing last would replace newer bars).
    let inFlight = false;
    const run = () => {
      if (inFlight) return;
      inFlight = true;
      loadRef
        .current()
        .then(
          (data) => alive && setFetched((cur) => ({ key, data: keepStart(cur?.key === key ? cur.data : undefined, data), at: new Date().toISOString() })),
          (err) => alive && setFetched((cur) => (cur?.key === key && cur.data ? { ...cur, refreshError: errorMessage(err) } : { key, error: errorMessage(err) })),
        )
        .finally(() => {
          inFlight = false;
        });
    };
    void run();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void run();
    }, INTRADAY_REFRESH_MS);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [key, enabled]);
  const current = enabled && fetched?.key === key ? fetched : null;
  return { current, stale: !!current?.refreshError };
}
