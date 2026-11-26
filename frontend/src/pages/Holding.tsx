import { useMemo, useState } from "react";
import { endpoints, type InstrumentDetail, type IntradayChart, type Row } from "../api";
import { INTERVALS, defaultInterval, intervalOptions, intervalsFor, isYahooSymbol, useIntraday, type Interval } from "../intraday";
import { Alert } from "../components/Alert";
import { AreaChart } from "../components/AreaChart";
import { Tween } from "../components/Tween";
import { AiMark } from "../components/AiMark";
import { Card, CardHeader } from "../components/Card";
import { ArrowLeft } from "lucide-react";
import { MetricsCard, ScenariosCard } from "../components/Metrics";
import { Pill } from "../components/Pill";
import { Purchases } from "../components/Purchases";
import { Segmented } from "../components/Segmented";
import { BlockSkeleton, Skeleton } from "../components/Skeleton";
import { asOfLabel, ccyLabel, daysAgoISO, isNum, money, mwrBasis, price, priceWithCcy, qty, signClass, signedMoney, signedPrice, sourceLabel, typeLabel } from "../format";
import { useResource, useTitle } from "../hooks";
import { useHeaderTitle } from "../pageTitle";
import { usePref } from "../prefs";
import { useBase, useLive } from "../live";
import { Link } from "../router";

const RANGES = [
  { value: "1W", label: "1W", name: "1 week" },
  { value: "1M", label: "1M", name: "1 month" },
  { value: "3M", label: "3M", name: "3 months" },
  { value: "6M", label: "6M", name: "6 months" },
  { value: "1Y", label: "1Y", name: "1 year" },
  { value: "ALL", label: "All", name: "All time" },
] as const;
type RangeValue = (typeof RANGES)[number]["value"];
const RANGE_DAYS: Record<RangeValue, number | null> = { "1W": 8, "1M": 31, "3M": 92, "6M": 183, "1Y": 366, ALL: null };

export function HoldingPage({ id }: { id: string }) {
  const live = useLive();
  const base = useBase();
  const liveRow = live.snapshot?.rows.find((r) => r.id === id) ?? null;
  // The sales' realized P/L is computed on the server in the reporting currency: reload it when the
  // currency changes, or when the holding's realized total moves (a sale recorded elsewhere, a rate arriving),
  // so a row never shows an old number under the new currency's symbol.
  const detail = useResource(() => endpoints.instrument(id), [id, base, liveRow?.realized_base ?? null]);
  // The page header names the holding ("Apple"), not the route ("Holding").
  useHeaderTitle(detail.data?.instrument.name ?? null);
  const d = detail.data;
  useTitle(d ? d.instrument.name : "Holding");

  const row = liveRow ?? d?.position ?? null;

  if (detail.loading && !d) {
    return (
      <>
        <BackLink />
        <div className="stats">
          {[0, 1, 2, 3].map((i) => (
            <div className="card" key={i}>
              <Skeleton w="50%" h={14} />
              <Skeleton w="70%" h={26} style={{ margin: "10px 0 6px" }} />
              <Skeleton w="40%" h={12} />
            </div>
          ))}
        </div>
        <BlockSkeleton rows={4} />
      </>
    );
  }
  if (!d) {
    return (
      <>
        <BackLink />
        <Alert tone="err">
          {detail.error ?? "Could not load this holding."}
        </Alert>
        <div className="btn-row" style={{ marginTop: 12 }}>
          <Link className="btn ghost small" to="/holdings">
            <ArrowLeft aria-hidden="true" />
            Back to holdings
          </Link>
        </div>
      </>
    );
  }

  const inst = d.instrument;
  const priceCcy = d.price_currency ?? row?.price_currency ?? inst.currency;
  return (
    <>
      <BackLink />
      <div className="dhead">
        <div className="t">
          <div className="sub">
            {[inst.instrument_id, typeLabel(inst.asset_type), inst.currency, inst.market_symbol].filter(Boolean).join(" · ")}
          </div>
        </div>
        {row?.closed && <span className="badge">Closed</span>}
        <span className="src">{sourceLabel(row?.price_source)}</span>
      </div>

      {row?.check && (
        <div style={{ marginBottom: "var(--gap)" }}>
          <Alert tone="warn">{row.check}</Alert>
        </div>
      )}

      <Stats row={row} currency={inst.currency} base={base} />

      <div className="grid">
        <PriceChart detail={d} row={row} priceCcy={priceCcy} />
        <div className="span2 cols2">
          <MetricsCard metrics={d.metrics} ccy={ccyLabel(priceCcy)} />
          {d.scenarios.length > 0 ? (
            <ScenariosCard scenarios={d.scenarios} ccy={ccyLabel(priceCcy)} />
          ) : (
            <Card as="section" aria-labelledby="scen-h">
              <CardHeader id="scen-h" title="Scenario ranges" />
              <div className="empty">
                <b>Not available</b>
                {inst.asset_type === "government_bond"
                  ? "Bonds are not simulated from price history."
                  : "At least 61 stored daily prices are needed."}
              </div>
            </Card>
          )}
        </div>
        <Purchases instrumentId={inst.instrument_id} currency={inst.currency} base={base} lots={d.lots} onChanged={detail.reload} />
        <Card as="section" className="ai" aria-labelledby="ask-h">
          <div className="card-h">
            <div className="who">
              <AiMark />
              <h2 id="ask-h">AI analyst</h2>
            </div>
            <span className="tag">online · on send</span>
          </div>
          <p className="sub">Open the AI analyst focused on this holding: its trend, its role in your portfolio and what to watch.</p>
          <div className="btn-row" style={{ marginTop: 12 }}>
            <Link className="btn" to={`/analyst?holding=${encodeURIComponent(inst.instrument_id)}`}>
              Ask the AI analyst
            </Link>
          </div>
        </Card>
      </div>
      {inst.notes && <p className="note" style={{ marginTop: 12 }}>Note: {inst.notes}</p>}
    </>
  );
}

function BackLink() {
  return (
    <Link className="back" to="/">
      <ArrowLeft /> Overview
    </Link>
  );
}

function Stats({ row, currency, base }: { row: Row | null; currency: string; base: string }) {
  const lots = row?.lots ?? 0;
  const sells = row?.sells ?? 0;
  const buys = lots - sells;
  return (
    <div className={sells > 0 ? "stats five" : "stats"}>
      <Card>
        <div className="k">Value</div>
        <div className="mid">
          <Tween value={row?.value_base} format={(v) => money(v, base)} unit={base} />
        </div>
        <div className="sub">{isNum(row?.quantity) ? `${qty(row?.quantity)} units` : "—"}</div>
      </Card>
      <Card>
        <div className="k">Unrealized P/L</div>
        <div className="mid">
          <Tween tone={signClass} value={row?.pnl_base} format={(v) => signedMoney(v, base)} unit={base} />
        </div>
        <div className="sub">
          {row?.closed ? (
            <span className="keep">nothing held any more</span>
          ) : (
            <>
              <Pill fraction={row?.pnl_pct} /> <span className="keep">vs. what you paid</span>
            </>
          )}
        </div>
        {row?.mwr && (
          <div className="sub" title="Money-weighted return (XIRR): your buys, sales and today's value, in the order and size you made them">
            <span className="keep">Return</span> <Pill fraction={row.mwr.rate} /> <span className="keep">{mwrBasis(row.mwr)}</span>
          </div>
        )}
      </Card>
      {sells > 0 && (
        <Card>
          <div className="k">Realized P/L</div>
          <div className="mid">
            <Tween tone={signClass} value={row?.realized_base} format={(v) => signedMoney(v, base)} unit={base} />
          </div>
          <div className="sub">
            {isNum(row?.realized_base)
              ? `from ${sells} ${sells === 1 ? "sale" : "sales"}, oldest units first`
              : "a sale is missing its price or an exchange rate"}
          </div>
        </Card>
      )}
      <Card>
        <div className="k">Price per unit</div>
        <div className="mid">
          <Tween value={row?.price} format={(v) => priceWithCcy(v, row?.price_currency, true)} unit={row?.price_currency ?? ""} />
        </div>
        <div className="sub">as of {asOfLabel(row?.as_of)}</div>
      </Card>
      <Card>
        <div className="k">Average cost</div>
        <div className="mid">{priceWithCcy(row?.avg_cost, currency, true)}</div>
        <div className="sub">{buys > 0 ? `from ${buys} ${buys === 1 ? "purchase" : "purchases"}` : "from your holding record"}</div>
      </Card>
    </div>
  );
}

function PriceChart({ detail, row, priceCcy }: { detail: InstrumentDetail; row: Row | null; priceCcy: string }) {
  const [range, setRange] = usePref<RangeValue>("holding_range", RANGES.map((r) => r.value), "1Y");
  const id = detail.instrument.instrument_id;
  const yahoo = isYahooSymbol(detail.instrument.market_symbol);
  // The interval picked for this range; another range starts from its own default again.
  const [picked, setPicked] = useState<{ range: RangeValue; interval: Interval } | null>(null);
  const offered = intervalsFor(range, yahoo);
  const interval: Interval = picked?.range === range && offered.includes(picked.interval) ? picked.interval : defaultInterval(range, yahoo);
  const intraday = interval !== "1d";

  const all = useMemo(() => [...detail.prices].sort((a, b) => a.date.localeCompare(b.date)), [detail.prices]);
  const daily = useMemo(() => {
    const days = RANGE_DAYS[range];
    if (days === null) return all;
    const from = daysAgoISO(days);
    return all.filter((p) => p.date >= from);
  }, [all, range]);

  // Intraday bars come from Yahoo when asked, and again every minute while the page is in view.
  const { current, stale } = useIntraday<IntradayChart>(`${id}|${range}|${interval}`, intraday, () => endpoints.instrumentChart(id, range, interval));
  const bars = useMemo(() => current?.data?.points ?? [], [current?.data]);

  const shownCcy = intraday ? (current?.data?.currency ?? priceCcy) : priceCcy;
  // The average cost is in the holding's currency (GBP); a London chart is in pence (GBp): scale it by the
  // holding's price divisor rather than drawing pounds on a pence axis (100x off) or leaving it out.
  const holdingCcy = detail.instrument.currency;
  const divisor = detail.instrument.price_divisor || 1;
  const minorUnit = !!shownCcy && shownCcy !== holdingCcy && shownCcy.toUpperCase() === holdingCcy.toUpperCase() && divisor > 1;
  const sameCcy = shownCcy === holdingCcy || minorUnit;
  const avg = isNum(row?.avg_cost) ? row.avg_cost * (minorUnit ? divisor : 1) : null;
  const ccy = ccyLabel(shownCcy);
  const values = useMemo(() => (intraday ? bars.map((p) => p.close) : daily.map((p) => p.close)), [intraday, bars, daily]);
  // memoised: thousands of bars, and the page re-renders on every live tick
  const labels = useMemo(() => (intraday ? bars.map((p) => asOfLabel(p.t)) : daily.map((p) => p.date)), [intraday, bars, daily]);
  // The average-cost line only when it sits near the prices: far below a week of 15-minute moves it would
  // squeeze them into a flat line (the chart's scale always includes it). The note still gives the figure.
  const lo = values.length ? Math.min(...values) : 0;
  const hi = values.length ? Math.max(...values) : 0;
  const avgShown = sameCcy && isNum(avg) ? avg : null;
  const ref = avgShown !== null && avgShown >= lo - (hi - lo) / 2 && avgShown <= hi + (hi - lo) / 2 ? avgShown : null;
  const what = INTERVALS[interval].adjective;

  // One live region that stays mounted, so loading, failure and arrival are each announced.
  const status = !intraday
    ? ""
    : !current
      ? `Loading ${what} prices from Yahoo…`
      : current.error
        ? current.error
        : stale
          ? "Could not refresh the prices from Yahoo."
          : `Showing ${what} prices.`; // no count: it would be announced again on every refresh

  let empty: string | null = null;
  if (intraday && !current) empty = `Loading ${what} prices from Yahoo…`;
  else if (intraday && current?.error) empty = `${current.error} Pick 1D for the stored daily prices.`;
  else if (values.length < 2)
    empty = intraday
      ? `Yahoo has no ${what} prices for this range.`
      : all.length === 0
        ? "No stored prices yet — they fill in automatically within a minute of adding a holding with a market symbol."
        : "No stored prices in this range.";

  return (
    <Card as="section" className="span2" aria-labelledby="price-h">
      <CardHeader
        id="price-h"
        title="Price per unit"
        aside={
          <div className="chart-controls">
            {offered.length > 1 && (
              <Segmented
                label="Price interval"
                options={intervalOptions(offered)}
                value={interval}
                onChange={(v) => setPicked({ range, interval: v })}
              />
            )}
            <Segmented label="Chart range" options={RANGES} value={range} onChange={setRange} />
          </div>
        }
      />
      <p className="sr-only" role="status">
        {status}
      </p>
      {empty ? (
        <div className="chart-empty">{empty}</div>
      ) : (
        <AreaChart
          values={values}
          labels={labels}
          fmt={(v) => `${price(v)}${ccy ? ` ${ccy}` : ""}`}
          fmtDelta={(v, from) => signedPrice(v, shownCcy, from)}
          resetKey={`${range}|${interval}|${shownCcy}`}
          refValue={ref}
          live={!stale}
          label={`Price per unit, ${range}, ${INTERVALS[interval].name} steps`}
        />
      )}
      <p className="note">
        {stale && current?.at && `Could not refresh from Yahoo: these are the prices from ${asOfLabel(current.at)}. `}
        {intraday
          ? bars.length > 0 &&
            `${bars.length} ${what} prices from Yahoo, in your local time; hours without trading are left out. As traded, not adjusted for dividends, so a long range can differ from 1D. Fetched when you look, not stored.`
          : `${all.length} stored daily prices.${yahoo ? "" : " This holding has daily prices only."}`}
        {ref !== null && ` Dashed line: your average cost (${priceWithCcy(ref, shownCcy, true)}).`}
        {ref === null && avgShown !== null && values.length > 1 && ` Your average cost (${priceWithCcy(avgShown, shownCcy, true)}) is ${avgShown < lo ? "below" : "above"} this range, so it isn't drawn.`}
      </p>
    </Card>
  );
}
