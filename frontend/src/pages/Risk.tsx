// Risk: concentration and exposure from today's weights, and how today's mix would have moved over the past
// year or three (GET /api/risk). Stored prices only: nothing is fetched for this page, and nothing is a forecast.
import { useMemo } from "react";
import { endpoints, type RiskHistory, type RiskHolding, type RiskReport } from "../api";
import { Alert } from "../components/Alert";
import { AreaChart } from "../components/AreaChart";
import { Card, CardHeader } from "../components/Card";
import { Explain } from "../components/Explain";
import { DataTable, type Column } from "../components/DataTable";
import { Segmented } from "../components/Segmented";
import { BlockSkeleton } from "../components/Skeleton";
import { DASH, fixed2, isNum, MINUS, pct, plainPct, signedPp, typeLabel } from "../format";
import { useResource, useTitle } from "../hooks";
import { useBase } from "../live";
import { usePref } from "../prefs";
import { useSortFilter } from "../useSortFilter";

const RANGES = [
  { value: "1Y", label: "1Y", name: "1 year" },
  { value: "3Y", label: "3Y", name: "3 years" },
] as const;
type RiskRange = (typeof RANGES)[number]["value"];
type Beta = NonNullable<RiskHistory["beta"]>;

/** Red only for a figure shown as a fall: a "worst" day or month can be a gain, and −0.003% shows as +0,00%. */
const downClass = (text: string, cls = "mid") => (text.startsWith(MINUS) ? `${cls} down` : cls);

/** Two decimals with the typographic minus the other figures use (Intl gives a hyphen in hu-HU). */
const num2 = (v: number) => fixed2(v).replace("-", MINUS);

/** Distance below the previous high: −12,3% or 0,0% (never "+0%", which would read as above it). */
function fallPct(v: number, digits: number): string {
  const body = plainPct(Math.abs(v), digits);
  return v < 0 && /[1-9]/.test(body) ? `${MINUS}${body}` : body;
}

function betaSub(b: Beta, from: string, to: string): string {
  // weekly returns are dated by their Friday, a few days off the range's ends: only a wider gap is news
  const days = (a: string, z: string) => (Date.parse(z) - Date.parse(a)) / 86_400_000;
  const since = days(from, b.from) > 14 ? ` · since ${b.from}` : "";
  const until = days(b.to, to) > 14 ? ` · until ${b.to}` : "";
  return `vs ${b.benchmark} in ${b.currency} · correlation ${num2(b.correlation)}${since}${until}`;
}

export function RiskPage() {
  useTitle("Risk");
  const base = useBase();
  const [range, setRange] = usePref<RiskRange>("risk_range", RANGES.map((r) => r.value), "1Y");
  const report = useResource(() => endpoints.risk(range), [range, base]);
  const data = report.data;

  if (!data) {
    return report.error ? (
      <Alert tone="err">
        Could not load the risk figures: {report.error}{" "}
        <button type="button" className="link-btn" onClick={report.reload}>
          Try again
        </button>
      </Alert>
    ) : (
      <BlockSkeleton rows={6} />
    );
  }
  if (!data.concentration) {
    return (
      <div className="chart-empty">
        {data.unvalued.length > 0
          ? `No holding has a value right now: ${data.unvalued.join(", ")} ${data.unvalued.length === 1 ? "has" : "have"} no price or exchange rate yet. Refresh the prices at the top of the page, or check their symbols under Holdings.`
          : "Add holdings with a price to see how concentrated and how risky the portfolio is."}
      </div>
    );
  }
  return (
    <div className="stack">
      {report.error && !report.loading && (
        // a failed reload keeps the figures already shown: say which range they are for
        <Alert tone="err">
          Could not load the {range} figures: {report.error}. Still showing {data.range}.{" "}
          <button type="button" className="link-btn" onClick={report.reload}>
            Try again
          </button>
        </Alert>
      )}
      <div className="cols2">
        <Concentration data={data} />
        <Exposure data={data} />
      </div>
      <History data={data} range={range} setRange={setRange} loading={report.loading} />
      <p className="note">Past prices of what you hold today, not a forecast: the next year can look nothing like the last one.</p>
    </div>
  );
}

function Concentration({ data }: { data: RiskReport }) {
  const c = data.concentration!;
  return (
    <Card as="section" aria-labelledby="conc-h">
      <CardHeader id="conc-h" title="Concentration" />
      <div className="risk-kpis">
        <div>
          <div className="k">Largest holding</div>
          <div className="mid">{plainPct(c.largest.weight)}</div>
          <div className="sub">{c.largest.name}</div>
        </div>
        <div>
          <div className="k">Top 3 together</div>
          <div className="mid">{plainPct(c.top3)}</div>
          <div className="sub">of the value</div>
        </div>
        <div>
          <div className="k">
            Effective holdings <Explain term="effectiveHoldings" />
          </div>
          <div className="mid">{fixed2(c.effective)}</div>
          <div className="sub">of {c.holdings} by weight</div>
        </div>
      </div>
      {data.unvalued.length > 0 && <p className="note">Left out, no price or exchange rate yet: {data.unvalued.join(", ")}.</p>}
    </Card>
  );
}

function Exposure({ data }: { data: RiskReport }) {
  return (
    <Card as="section" aria-labelledby="exp-h">
      <CardHeader id="exp-h" title="Exposure" term="exposure" />
      <div className="risk-exposure">
        <ShareBars title="By currency" rows={data.exposure.currency.map((x) => ({ label: x.key === "HUF" ? "HUF (Ft)" : x.key, weight: x.weight }))} />
        <ShareBars title="By asset type" rows={data.exposure.type.map((x) => ({ label: typeLabel(x.key), weight: x.weight }))} />
      </div>
      <p className="note">By each holding's own currency: a fund bought in forints that holds US shares still moves with the dollar.</p>
    </Card>
  );
}

function ShareBars({ title, rows }: { title: string; rows: { label: string; weight: number }[] }) {
  const max = Math.max(...rows.map((r) => r.weight), 0) || 1;
  return (
    <div>
      <h3 className="sub-h">{title}</h3>
      <div className="bars">
        {rows.map((r) => (
          <div className="b" key={r.label}>
            <div className="l">
              <span>{r.label}</span>
              <b>{plainPct(r.weight)}</b>
            </div>
            <div className="t" aria-hidden="true">
              <i style={{ width: `${((r.weight / max) * 100).toFixed(1)}%` }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function History({ data, range, setRange, loading }: { data: RiskReport; range: RiskRange; setRange: (r: RiskRange) => void; loading: boolean }) {
  const h = data.history;
  const shown = data.range; // the range these figures are for: the picker may already show the next one
  const fall = h.error ? "" : fallPct(h.max_drawdown.value, 2);
  const day = h.error ? "" : pct(h.worst_day.value);
  const month = h.error || !h.worst_month ? DASH : pct(h.worst_month.value);
  const values = useMemo(() => (h.error ? [] : h.drawdown.map((p) => p.value)), [h]);
  const labels = useMemo(() => (h.error ? [] : h.drawdown.map((p) => p.date)), [h]);
  return (
    <>
      <Card as="section" aria-labelledby="hist-h">
        <CardHeader id="hist-h" title="How today's mix would have moved" term="riskHistory" aside={<Segmented label="Risk range" options={RANGES} value={range} onChange={setRange} />} />
        {h.error ? (
          <>
            <div className="chart-empty">{h.error}</div>
            <LeftOut excluded={h.excluded ?? []} />
          </>
        ) : (
          <>
            <div className={loading ? "risk-kpis five busy" : "risk-kpis five"} aria-busy={loading}>
              <div>
                <div className="k">
                  Volatility <Explain term="volatility" />
                </div>
                <div className="mid">{plainPct(h.volatility)}</div>
                <div className="sub">a year, typical swing</div>
              </div>
              <div>
                <div className="k">
                  Largest fall <Explain term="maxDrawdown" />
                </div>
                <div className={downClass(fall)}>{fall}</div>
                <div className="sub">{fall.startsWith(MINUS) ? `${h.max_drawdown.peak} → ${h.max_drawdown.trough}` : "never below a previous high"}</div>
              </div>
              <div>
                <div className="k">Worst day</div>
                <div className={downClass(day)}>{day}</div>
                <div className="sub">{h.worst_day.date}</div>
              </div>
              <div>
                <div className="k">Worst month</div>
                <div className={downClass(month)}>{month}</div>
                <div className="sub">{h.worst_month ? h.worst_month.month : "needs a full month"}</div>
              </div>
              <div>
                <div className="k">
                  Beta <Explain term="beta" />
                </div>
                <div className="mid">{h.beta ? num2(h.beta.value) : DASH}</div>
                <div className="sub">{h.beta ? betaSub(h.beta, h.from, h.to) : (h.beta_note ?? "not available")}</div>
              </div>
            </div>
            <div className="card-title" style={{ marginTop: 16 }}>
              <h3 className="sub-h">Below its last high</h3>
              <Explain term="drawdownChart" />
            </div>
            <div className={loading ? "chart-refresh busy" : "chart-refresh"}>
              {loading && <span className="spinner chart-spinner" aria-hidden="true" />}
              <AreaChart
              values={values}
              labels={labels}
              fmt={(v) => fallPct(v, 1)}
              fmtTip={(v) => fallPct(v, 2)}
              fmtDelta={(v) => signedPp(v * 100)}
              relative={false}
              resetKey={`${shown}|${data.base_currency}`}
              busy={loading}
              tone="trend"
              label={`How far today's mix was below its previous high, ${shown}`}
              />
            </div>
            <p className="note">
              {h.from} to {h.to}, in {data.base_currency}, from {h.observations} days with prices.
              {h.covered_weight < 0.999 &&
                ` Covers ${plainPct(h.covered_weight)} of today's value: the figures describe that part as if it were all you hold.`}
            </p>
            <LeftOut excluded={h.excluded ?? []} />
          </>
        )}
      </Card>
      {!h.error && <PerHolding rows={h.holdings} covered={h.covered_weight} />}
    </>
  );
}

function LeftOut({ excluded }: { excluded: { name: string; reason: string }[] }) {
  if (!excluded.length) return null;
  return (
    <div className="note">
      Left out of these figures:
      <ul className="risk-excluded">
        {excluded.map((x, i) => (
          <li key={`${i}|${x.name}`}>
            <b>{x.name}</b>: {x.reason}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Fall({ v }: { v: number }) {
  const text = fallPct(v, 2);
  return <span className={downClass(text, "")}>{text}</span>;
}

function PerHolding({ rows, covered }: { rows: RiskHolding[]; covered: number }) {
  const sf = useSortFilter<RiskHolding>({
    id: "risk-holdings",
    rows,
    fields: {
      name: { type: "text", value: (r) => r.name },
      mix_weight: { type: "number", value: (r) => r.mix_weight },
      volatility: { type: "number", value: (r) => r.volatility },
      max_drawdown: { type: "number", value: (r) => r.max_drawdown },
      risk_share: { type: "number", value: (r) => r.risk_share },
    },
    search: (r) => `${r.name} ${r.id}`,
    defaultSort: { key: "risk_share", dir: "desc" },
  });
  const columns: readonly Column<RiskHolding>[] = [
    { key: "name", header: "Holding", sortKey: "name", link: true, cell: (r) => <b>{r.name}</b> },
    { key: "weight", header: "Share of value", sortKey: "mix_weight", align: "right", label: "Share of value", cell: (r) => plainPct(r.mix_weight) },
    { key: "vol", header: "Volatility", sortKey: "volatility", align: "right", label: "Volatility", cell: (r) => plainPct(r.volatility) },
    { key: "dd", header: "Largest fall", sortKey: "max_drawdown", align: "right", label: "Largest fall", cell: (r) => <Fall v={r.max_drawdown} /> },
    { key: "share", header: "Share of risk", sortKey: "risk_share", align: "right", label: "Share of risk", cell: (r) => (isNum(r.risk_share) ? plainPct(r.risk_share).replace("-", MINUS) : DASH) },
  ];
  return (
    <Card as="section" aria-labelledby="perh-h">
      <CardHeader id="perh-h" title="Per holding" term="shareOfRisk" />
      <DataTable sf={sf} columns={columns} rowKey={(r) => r.id} label="holdings by risk" variant="stack" rowHref={(r) => `/holding/${encodeURIComponent(r.id)}`} noFilter />
      {covered < 0.999 && (
        <p className="note">Share of value and of risk are both of the holdings measured ({plainPct(covered)} of today's value), so they compare.</p>
      )}
    </Card>
  );
}
