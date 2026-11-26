import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ChartBar, ChartPie, LayoutGrid } from "lucide-react";
import { endpoints, type HistoryRange, type IntradayHistory, type Row, type Snapshot } from "../api";
import { INTERVALS, defaultInterval, intervalOptions, intervalsFor, isYahooSymbol, useIntraday, type Interval } from "../intraday";
import { AdvisorCard } from "../components/AdvisorCard";
import { Alert } from "../components/Alert";
import { AreaChart } from "../components/AreaChart";
import { Tween } from "../components/Tween";
import { Card, CardHeader } from "../components/Card";
import { Explain } from "../components/Explain";
import { DataTable, type Column, type PhoneSortOption } from "../components/DataTable";
import { Donut } from "../components/Donut";
import { Pill } from "../components/Pill";
import { Segmented } from "../components/Segmented";
import { OverviewSkeleton, Skeleton } from "../components/Skeleton";
import { Sparkline } from "../components/Sparkline";
import { Treemap } from "../components/Treemap";
import { chipColor, chipText } from "../chip";
import { DASH, asOfLabel, axisFormatter, isNum, localISODate, money, mwrBasis, plainPct, priceWithCcy, qty, signClass, signedMoney, typeLabel } from "../format";
import { useResource, useTitle } from "../hooks";
import { usePref } from "../prefs";
import { useLive } from "../live";
import { Link } from "../router";
import { useSortFilter } from "../useSortFilter";

const RANGES = [
  { value: "1W", label: "1W", name: "1 week" },
  { value: "1M", label: "1M", name: "1 month" },
  { value: "3M", label: "3M", name: "3 months" },
  { value: "6M", label: "6M", name: "6 months" },
  { value: "1Y", label: "1Y", name: "1 year" },
  { value: "3Y", label: "3Y", name: "3 years" },
  { value: "ALL", label: "All", name: "All time" },
] as const satisfies readonly { value: HistoryRange; label: string; name: string }[];
type RangeValue = (typeof RANGES)[number]["value"];

export function OverviewPage() {
  useTitle("Overview");
  const live = useLive();
  const snap = live.snapshot;
  const [dismissed, setDismissed] = useState("");

  if (!live.loaded && !snap) return <OverviewSkeleton />;
  if (!snap) {
    return (
      <Alert tone="err">
        Could not load the portfolio: {live.loadError ?? "unknown error"}. It will keep retrying through the live connection.
      </Alert>
    );
  }

  const errorKey = snap.errors.join("\n");
  const showErrors = snap.errors.length > 0 && dismissed !== errorKey;

  if (snap.rows.length === 0) {
    return (
      <>
        {showErrors && <ErrorStrip errors={snap.errors} onDismiss={() => setDismissed(errorKey)} />}
        <Card>
          <div className="empty">
            <b>No holdings yet</b>
            Import your holdings from a CSV, or add them one by one. Prices, FX rates and your portfolio value fill in automatically.
            <div className="btn-row">
              <Link className="btn" to="/import">
                Import holdings
              </Link>
              <Link className="btn ghost" to="/holdings?add=1">
                Add a holding
              </Link>
            </div>
          </div>
        </Card>
      </>
    );
  }

  return (
    <>
      {showErrors && <ErrorStrip errors={snap.errors} onDismiss={() => setDismissed(errorKey)} />}
      <Hero snap={snap} />
      <div className="grid">
        <ValueChart snap={snap} />
        <Allocation rows={snap.rows} total={snap.totals.value_base} base={snap.base_currency} />
        <HoldingsList rows={snap.rows} base={snap.base_currency} flashes={live.flashes} />
        <AdvisorCard className="span2" />
      </div>
    </>
  );
}

function ErrorStrip({ errors, onDismiss }: { errors: string[]; onDismiss: () => void }) {
  return (
    <div className="alert-strip" style={{ marginBottom: 12 }}>
      <Alert tone="warn" onDismiss={onDismiss}>
        <strong>Some data could not be updated.</strong>
        <ul>
          {errors.map((e, i) => (
            <li key={i}>{e}</li>
          ))}
        </ul>
      </Alert>
    </div>
  );
}

function Hero({ snap }: { snap: Snapshot }) {
  const t = snap.totals;
  const base = snap.base_currency;
  const currencies = new Set(snap.rows.filter((r) => !r.closed).map((r) => r.currency)).size;
  const unvalued = t.holdings - t.valued;
  const hasSales = snap.rows.some((r) => r.sells > 0); // only then is there a realized P/L to show
  return (
    <div className={hasSales ? "hero four" : "hero"}>
      <Card className="total">
        <div className="k">Portfolio value</div>
        <div className="big">
          <Tween value={t.value_base} format={(v) => money(v, base)} unit={base} />
        </div>
        <div className="sub">
          {t.holdings} {t.holdings === 1 ? "holding" : "holdings"} · {currencies} {currencies === 1 ? "currency" : "currencies"} → {base}
          {unvalued > 0 && ` · ${unvalued} without a price`}
        </div>
      </Card>
      <Card className="pnl">
        <div className="k">
          Unrealized P/L <Explain term="unrealizedPL" />
        </div>
        <div className="mid">
          <Tween tone={signClass} value={t.pnl_base} format={(v) => signedMoney(v, base)} unit={base} />
        </div>
        <div className="sub">
          <Pill fraction={t.pnl_pct} /> <span className="keep">vs. what you paid</span>
          {t.with_pnl < t.holdings && <> · {t.with_pnl} of {t.holdings} holdings</>}
        </div>
        {(t.mwr || t.mwr_left_out > 0) && (
          <div className="sub">
            <span className="keep">Return</span> <Explain term="xirr" /> {t.mwr ? <Pill fraction={t.mwr.rate} /> : DASH} {t.mwr && <span className="keep">{mwrBasis(t.mwr)}</span>}
            {t.mwr_left_out > 0 && (
              <>
                {" "}
                · without {t.mwr_left_out} {t.mwr_left_out === 1 ? "holding" : "holdings"} whose value or a transaction amount is unknown
              </>
            )}
          </div>
        )}
      </Card>
      {hasSales && (
        <Card className="realized">
          <div className="k">
            Realized P/L <Explain term="realizedPL" />
          </div>
          <div className="mid">
            <Tween tone={signClass} value={t.realized_base} format={(v) => signedMoney(v, base)} unit={base} />
          </div>
          <div className="sub">
            <span className="keep">{isNum(t.realized_base) ? "on units you sold, oldest first" : "a sale is missing its price or a rate"}</span>
            {t.closed > 0 && <> · {t.closed} closed</>}
          </div>
        </Card>
      )}
      <Card className="today">
        <div className="k">Today</div>
        <div className="mid">
          <Tween tone={signClass} value={t.today_base} format={(v) => signedMoney(v, base)} unit={base} /> <Pill fraction={t.today_pct} />
        </div>
        <div className="sub">
          <span className="keep">vs. previous close</span>
          {t.with_today < t.holdings && <> · {t.with_today} of {t.holdings} holdings</>}
        </div>
      </Card>
    </div>
  );
}

/** Numbers each history response (see ValueChart). */
let historySeq = 0;

function ValueChart({ snap }: { snap: Snapshot }) {
  const [range, setRange] = usePref<RangeValue>("overview_range", RANGES.map((r) => r.value), "1M");
  const holdingsCount = snap.rows.length;
  const base = snap.base_currency;
  // Reload when the set of holdings or the display currency changes: the rebuilt curve depends on both.
  // The answer carries the range it was asked for: while a new range loads (or fails), the chart
  // keeps describing the data it actually shows.
  // Each answer carries the range it was asked for and its own number: while a new range loads
  // (or fails) the chart keeps describing the data it shows, and a reload after a holding is added
  // (dates can be inserted mid-series) counts as new data for the comparison.
  const history = useResource(() => endpoints.history(range).then((d) => ({ ...d, range, seq: ++historySeq })), [range, holdingsCount, base]);
  const sameCurrency = history.data?.currency === base;
  // The live last point only sums holdings the stored curve already includes, so a holding whose
  // daily prices are still being fetched cannot make the right edge jump.
  const included = history.data?.included;
  const live = useMemo(() => {
    if (!sameCurrency) return null;
    if (!included) return snap.totals.value_base;
    const set = new Set(included);
    const rows = snap.rows.filter((r) => set.has(r.id) && isNum(r.value_base));
    return rows.length ? rows.reduce((sum, r) => sum + (r.value_base as number), 0) : null;
  }, [included, snap, sameCurrency]);

  // Right after holdings are added the server is still fetching daily prices: look again until some arrive.
  const noPointsYet = history.data !== null && history.data.points.length === 0;
  const reloadHistory = history.reload;
  useEffect(() => {
    if (!noPointsYet) return;
    const timer = window.setInterval(reloadHistory, 15000);
    return () => window.clearInterval(timer);
  }, [noPointsYet, reloadHistory]);

  const { values, labels } = useMemo(() => {
    const pts = (history.data?.points ?? []).map((p) => ({ date: p.date, v: p.value }));
    if (isNum(live) && pts.length > 0) {
      const today = localISODate();
      const last = pts[pts.length - 1];
      if (last && last.date >= today) pts[pts.length - 1] = { date: last.date, v: live };
      else pts.push({ date: today, v: live });
    }
    return { values: pts.map((p) => p.v), labels: pts.map((p) => p.date) };
  }, [history.data, live]);

  const chartCcy = history.data?.currency || base;
  const axis = useMemo(() => axisFormatter(values, chartCcy), [values, chartCcy]);

  // Minute/hour intervals: the total rebuilt from Yahoo's bars, refreshed every minute while in view.
  const yahoo = snap.rows.some((r) => isYahooSymbol(r.symbol) && r.type !== "government_bond"); // as the server counts
  const [picked, setPicked] = useState<{ range: RangeValue; interval: Interval } | null>(null);
  const offered = intervalsFor(range, yahoo);
  const interval: Interval = picked?.range === range && offered.includes(picked.interval) ? picked.interval : defaultInterval(range, yahoo);
  const intraday = interval !== "1d";
  const { current, stale } = useIntraday<IntradayHistory>(`${range}|${interval}|${base}|${holdingsCount}`, intraday, () =>
    endpoints.intradayHistory(range, interval),
  );
  const bars = useMemo(() => current?.data?.points ?? [], [current?.data]);
  const barValues = useMemo(() => bars.map((p) => p.value), [bars]);
  const barLabels = useMemo(() => bars.map((p) => asOfLabel(p.t)), [bars]); // memoised: thousands of bars
  const barCcy = current?.data?.currency || base;
  const barAxis = useMemo(() => axisFormatter(barValues, barCcy), [barValues, barCcy]);
  const what = INTERVALS[interval].adjective;
  const dailyOnly = (current?.data?.daily_only ?? []).map((id) => snap.rows.find((r) => r.id === id)?.name ?? id);
  const status = !intraday
    ? ""
    : !current
      ? `Loading ${what} values from Yahoo…`
      : current.error
        ? current.error
        : stale
          ? "Could not refresh the values from Yahoo."
          : `Showing ${what} values.`; // no count: it would be announced again on every refresh

  return (
    <Card as="section" aria-labelledby="value-chart-h">
      <CardHeader
        id="value-chart-h"
        title="Value over time"
        term="valueChart"
        aside={
          <div className="chart-controls">
            {offered.length > 1 && (
              <Segmented label="Value interval" options={intervalOptions(offered)} value={interval} onChange={(v) => setPicked({ range, interval: v })} />
            )}
            <Segmented label="Chart range" options={RANGES} value={range} onChange={setRange} />
          </div>
        }
      />
      <p className="sr-only" role="status">
        {status}
      </p>
      {intraday ? (
        !current ? (
          <div className="chart-empty">Loading {what} values from Yahoo…</div>
        ) : current.error ? (
          <div className="chart-empty">{current.error} Pick 1D for the stored daily curve.</div>
        ) : barValues.length < 2 ? (
          <div className="chart-empty">Yahoo has no {what} prices for this range.</div>
        ) : (
          <AreaChart
            values={barValues}
            labels={barLabels}
            fmt={barAxis}
            fmtTip={(v) => money(v, barCcy)}
            fmtDelta={(v) => signedMoney(v, barCcy)}
            resetKey={`${range}|${interval}|${barCcy}`}
            tone="accent"
            live={!stale}
            label={`Portfolio value in ${barCcy}, ${range}, ${INTERVALS[interval].name} steps`}
          />
        )
      ) : history.loading && !history.data ? (
        <Skeleton h="calc(var(--chart-h) + 50px)" /* the chart plus its compare row: nothing below moves */ />
      ) : history.error && !history.data ? (
        <Alert tone="err">
          Could not load the history: {history.error}{" "}
          <button type="button" className="link-btn" onClick={history.reload}>
            Try again
          </button>
        </Alert>
      ) : values.length < 2 ? (
        <div className="chart-empty">No stored price history yet — it fills in automatically within a minute of adding holdings.</div>
      ) : (
        <div className={history.loading ? "chart-refresh busy" : "chart-refresh"} aria-busy={history.loading}>
          {history.loading && <span className="spinner chart-spinner" aria-hidden="true" />}
          {/* A failed reload over the curve that is still shown: an overlay, so nothing below moves. */}
          {history.error && history.data && !history.loading && (
            <div className="chart-error">
              <Alert tone="err">
                Could not load the {range} history: {history.error}. Still showing {history.data.range}.{" "}
                <button type="button" className="link-btn" onClick={history.reload}>
                  Try again
                </button>
              </Alert>
            </div>
          )}
          <AreaChart values={values} labels={labels} fmt={axis} fmtTip={(v) => money(v, chartCcy)} fmtDelta={(v) => signedMoney(v, chartCcy)} resetKey={`${history.data?.range ?? range}|${chartCcy}|${history.data?.seq ?? 0}`} busy={history.loading} tone="accent" live label={`Portfolio value in ${chartCcy}, ${history.data?.range ?? range}`} />
        </div>
      )}
      {intraday && current?.data && bars.length > 1 && (
        <p className="note">
          {stale && current.at && `Could not refresh from Yahoo: these are the values from ${asOfLabel(current.at)}. `}
          {bars.length} {what} values in your local time, at the moments your holdings traded.
          {dailyOnly.length > 0 && ` At their latest daily price: ${dailyOnly.join(", ")} (Yahoo had no ${what} prices).`}
        </p>
      )}
    </Card>
  );
}

const ALLOC_VIEWS = [
  { value: "bars", label: "Bars", icon: ChartBar },
  { value: "grid", label: "Grid", icon: LayoutGrid },
  { value: "pie", label: "Pie", icon: ChartPie },
] as const;
type AllocView = (typeof ALLOC_VIEWS)[number]["value"];

/** The bars grow in once, when the view opens. Only then: on a live re-sort React moves rows,
 *  which would restart a CSS animation on every tick. */
function Bars({ children }: { children: ReactNode }) {
  const [entering, setEntering] = useState(true);
  useEffect(() => {
    const timer = window.setTimeout(() => setEntering(false), 900);
    return () => window.clearTimeout(timer);
  }, []);
  return <div className={entering ? "bars entering" : "bars"}>{children}</div>;
}

function Allocation({ rows, total, base }: { rows: Row[]; total: number | null; base: string }) {
  const [view, setView] = usePref<AllocView>("alloc_view", ALLOC_VIEWS.map((v) => v.value), "bars");
  const valued = rows
    .filter((r) => isNum(r.weight) && r.weight > 0)
    .sort((a, b) => (b.weight as number) - (a.weight as number));
  const top = valued.slice(0, 7);
  const rest = valued.slice(7);
  const max = top.length ? (top[0]?.weight as number) : 1;
  const restWeight = rest.reduce((a, r) => a + (r.weight as number), 0);
  return (
    <Card as="section" className="alloc" aria-labelledby="alloc-h">
      <CardHeader id="alloc-h" title="Allocation" term="allocation" aside={<Segmented label="Allocation view" options={ALLOC_VIEWS} value={view} onChange={setView} />} />
      {valued.length === 0 ? (
        <p className="sub">{rows.length > 0 && rows.every((r) => r.closed) ? "No open holdings: everything has been sold." : "Nothing has a price yet."}</p>
      ) : view === "grid" ? (
        <>
          <Treemap rows={valued} />
          <p className="note">Tile area = share of portfolio value. Green = gain since purchase, red = loss, grey = P/L unknown.</p>
        </>
      ) : view === "pie" ? (
        <Donut rows={valued} total={total} base={base} />
      ) : (
        <Bars>
          {top.map((r) => (
            <div className="b" key={r.id}>
              <div className="l">
                <span>{r.name}</span>
                <b>{plainPct(r.weight)}</b>
              </div>
              <div className="t" aria-hidden="true">
                <i style={{ width: `${(((r.weight as number) / max) * 100).toFixed(1)}%` }} />
              </div>
            </div>
          ))}
          {rest.length > 0 && (
            <div className="more">
              <span>{rest.length} more</span>
              <span>{plainPct(restWeight)}</span>
            </div>
          )}
          <p className="note" style={{ marginTop: 10 }}>by value, {base}</p>
        </Bars>
      )}
    </Card>
  );
}

const unitBase = (r: Row): number | null => (isNum(r.value_base) && isNum(r.quantity) && r.quantity > 0 ? r.value_base / r.quantity : null);

const PL_OPTIONS = [
  { value: "all", label: "All P/L" },
  { value: "gains", label: "Gains only" },
  { value: "losses", label: "Losses only" },
] as const;

const PHONE_SORT: readonly PhoneSortOption[] = [
  { key: "value", label: "Value", dir: "desc" },
  { key: "pnl_pct", label: "P/L %", dir: "desc" },
  { key: "today_pct", label: "Today %", dir: "desc" },
  { key: "name", label: "Name", dir: "asc" },
  { key: "weight", label: "Weight", dir: "desc" },
];

function makeColumns(base: string): readonly Column<Row>[] {
  return [
    {
      key: "icon",
      header: "Symbol",
      hideHeader: true,
      className: "c-ic",
      cell: (r) => (
        <span className="ic" style={{ background: chipColor(r.id) }} aria-hidden="true">
          {chipText(r.id)}
        </span>
      ),
    },
    {
      key: "name",
      header: "Name",
      sortKey: "name",
      className: "c-nm",
      link: true,
      cell: (r) => (
        <span className="nm">
          <b>{r.name}</b>
          <small>{[r.symbol, typeLabel(r.type)].filter(Boolean).join(" · ")}</small>
          {r.check && (
            <small className="check" title={r.check}>
              {r.check}
            </small>
          )}
        </span>
      ),
    },
    { key: "trend", header: "Trend", className: "c-sp", cell: (r) => <Sparkline values={r.spark} /> },
    {
      key: "price",
      header: "Price",
      sortKey: "price",
      align: "right",
      className: "c-px",
      cell: (r) => (
        <span className="px">
          {isNum(r.price) ? priceWithCcy(r.price, r.price_currency) : DASH}
          <small>
            {isNum(r.quantity) ? `${qty(r.quantity)} ${r.quantity === 1 ? "unit" : "units"}` : DASH} · <span className="tag">{r.price_source}</span>
          </small>
          {isNum(r.price) && r.as_of && <span className="sr-only"> as of {asOfLabel(r.as_of)}</span>}
        </span>
      ),
    },
    {
      key: "value",
      header: `Value (${base})`,
      sortKey: "value",
      align: "right",
      className: "c-val",
      cell: (r) => (
        <span className="val">
          {money(r.value_base, base)}
          <small className={signClass(r.pnl_base)}>{signedMoney(r.pnl_base, base)}</small>
        </span>
      ),
    },
    {
      key: "pl",
      header: "P/L",
      term: "unrealizedPL",
      sortKey: "pnl_pct",
      align: "right",
      className: "c-pl",
      cell: (r) => (
        <span className="pl">
          <Pill fraction={r.pnl_pct} />
          <small>{money(r.value_base, base)}</small>
        </span>
      ),
    },
    {
      key: "mwr",
      header: "Return",
      term: "xirr",
      sortKey: "mwr",
      align: "right",
      className: "c-ret",
      cell: (r) =>
        r.mwr ? (
          <span className="ret" title={mwrBasis(r.mwr)}>
            <Pill fraction={r.mwr.rate} />
            {/* the column is narrow: the short form shows, the full one is read out and in the tooltip */}
            <small aria-hidden="true">{r.mwr.annualised ? "a year" : `since ${r.mwr.since}`}</small>
            <span className="sr-only">{mwrBasis(r.mwr)}</span>
          </span>
        ) : (
          <span className="ret" title="Needs transactions with known amounts">
            {DASH}
          </span>
        ),
    },
  ];
}

function HoldingsList({ rows: all, base, flashes }: { rows: Row[]; base: string; flashes: Record<string, "up" | "down"> }) {
  const columns = useMemo(() => makeColumns(base), [base]);
  // Closed holdings (everything sold) are not part of the portfolio: they live on the Holdings page.
  const rows = useMemo(() => all.filter((r) => !r.closed), [all]);
  const sf = useSortFilter<Row>({
    id: "overview-holdings",
    rows,
    fields: {
      name: { type: "text", value: (r) => r.name },
      price: { type: "number", value: unitBase },
      value: { type: "number", value: (r) => r.value_base },
      pnl_pct: { type: "number", value: (r) => r.pnl_pct },
      today_pct: { type: "number", value: (r) => r.today_pct },
      weight: { type: "number", value: (r) => r.weight },
      mwr: { type: "number", value: (r) => r.mwr?.rate ?? null },
    },
    search: (r) => [r.name, r.id, r.symbol, typeLabel(r.type), r.currency].filter(Boolean).join(" "),
    defaultSort: { key: "value", dir: "desc" },
    selects: [
      {
        key: "pl",
        label: "Profit and loss",
        options: PL_OPTIONS,
        test: (r, v) => (v === "gains" ? isNum(r.pnl_pct) && r.pnl_pct > 0 : v === "losses" ? isNum(r.pnl_pct) && r.pnl_pct < 0 : true),
      },
    ],
  });
  return (
    <Card as="section" className="span2" aria-labelledby="holdings-h">
      <CardHeader id="holdings-h" title="Holdings" />
      <DataTable
        sf={sf}
        columns={columns}
        rowKey={(r) => r.id}
        label="holdings"
        variant="rows"
        rowHref={(r) => `/holding/${encodeURIComponent(r.id)}`}
        rowClassName={(r) => (flashes[r.id] ? `flash-${flashes[r.id]}` : undefined)}
        phoneSort={PHONE_SORT}
      />
    </Card>
  );
}
