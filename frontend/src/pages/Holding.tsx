import { useMemo } from "react";
import { endpoints, type InstrumentDetail, type Row } from "../api";
import { Alert } from "../components/Alert";
import { AreaChart } from "../components/AreaChart";
import { Tween } from "../components/Tween";
import { Card, CardHeader } from "../components/Card";
import { ArrowLeft } from "lucide-react";
import { MetricsCard, ScenariosCard } from "../components/Metrics";
import { Pill } from "../components/Pill";
import { Purchases } from "../components/Purchases";
import { Segmented } from "../components/Segmented";
import { BlockSkeleton, Skeleton } from "../components/Skeleton";
import { asOfLabel, ccyLabel, daysAgoISO, isNum, money, price, priceWithCcy, qty, signClass, signedMoney, signedPrice, sourceLabel, typeLabel } from "../format";
import { useResource, useTitle } from "../hooks";
import { useHeaderTitle } from "../pageTitle";
import { usePref } from "../prefs";
import { useBase, useLive } from "../live";
import { Link } from "../router";

const RANGES = [
  { value: "1W", label: "1W", name: "1 week" },
  { value: "1M", label: "1M", name: "1 month" },
  { value: "6M", label: "6M", name: "6 months" },
  { value: "1Y", label: "1Y", name: "1 year" },
  { value: "ALL", label: "All", name: "All time" },
] as const;
type RangeValue = (typeof RANGES)[number]["value"];
const RANGE_DAYS: Record<RangeValue, number | null> = { "1W": 8, "1M": 31, "6M": 183, "1Y": 366, ALL: null };

export function HoldingPage({ id }: { id: string }) {
  const detail = useResource(() => endpoints.instrument(id), [id]);
  // The page header names the holding ("Apple"), not the route ("Holding").
  useHeaderTitle(detail.data?.instrument.name ?? null);
  const live = useLive();
  const base = useBase();
  const d = detail.data;
  useTitle(d ? d.instrument.name : "Holding");

  const liveRow = live.snapshot?.rows.find((r) => r.id === id) ?? null;
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
              <i aria-hidden="true" />
              <h2 id="ask-h">AI analyst</h2>
            </div>
            <span className="tag">online · on send</span>
          </div>
          <p className="sub">Open the AI analyst focused on this holding: its trend, its role in your portfolio and what to watch. Analysis, not advice.</p>
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
  const all = useMemo(() => [...detail.prices].sort((a, b) => a.date.localeCompare(b.date)), [detail.prices]);
  const points = useMemo(() => {
    const days = RANGE_DAYS[range];
    if (days === null) return all;
    const from = daysAgoISO(days);
    return all.filter((p) => p.date >= from);
  }, [all, range]);

  const sameCcy = (priceCcy ?? "").toUpperCase() === detail.instrument.currency.toUpperCase();
  const avg = row?.avg_cost;
  const ref = sameCcy && isNum(avg) ? avg : null;
  const ccy = ccyLabel(priceCcy);

  return (
    <Card as="section" className="span2" aria-labelledby="price-h">
      <CardHeader id="price-h" title="Price per unit" aside={<Segmented label="Chart range" options={RANGES} value={range} onChange={setRange} />} />
      {points.length < 2 ? (
        <div className="chart-empty">
          {all.length === 0
            ? "No stored prices yet — they fill in automatically within a minute of adding a holding with a market symbol."
            : "No stored prices in this range."}
        </div>
      ) : (
        <AreaChart
          values={points.map((p) => p.close)}
          labels={points.map((p) => p.date)}
          fmt={(v) => `${price(v)}${ccy ? ` ${ccy}` : ""}`}
          fmtDelta={(v, from) => signedPrice(v, priceCcy, from)}
          resetKey={`${range}|${priceCcy}`}
          refValue={ref}
          live
          label={`Price per unit, ${range}`}
        />
      )}
      <p className="note">
        {all.length} stored daily prices.
        {ref !== null && ` Dashed line: your average cost (${priceWithCcy(ref, detail.instrument.currency, true)}).`}
      </p>
    </Card>
  );
}
