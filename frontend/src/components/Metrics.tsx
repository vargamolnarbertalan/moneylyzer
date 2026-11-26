import { useMemo, type ReactNode } from "react";
import type { Metrics, Scenario } from "../api";
import { DASH, fixed2, isNum, pct, plainPct, price, signClass } from "../format";
import { useSortFilter } from "../useSortFilter";
import { Card, CardHeader } from "./Card";
import { DataTable, type Column } from "./DataTable";
import { Labelled } from "./Explain";
import type { TermId } from "../glossary";
import { Check, X } from "lucide-react";

function signalTone(signal: string): "up" | "down" | "" {
  if (signal === "BUY CANDIDATE") return "up";
  if (signal === "SELL / REVIEW") return "down";
  return "";
}

function Ret({ v }: { v: number | null }) {
  return <span className={signClass(v)}>{pct(v)}</span>;
}

interface MetricRow {
  key: string;
  label: string;
  /** Raw number for sorting (unknown = null, which always sorts last). */
  num: number | null;
  node: ReactNode;
  text: string;
  /** An ⓘ after the label explaining the metric. */
  term?: TermId;
}

function metricRows(m: Metrics, unit: string): MetricRow[] {
  const ret = (key: string, label: string, v: number | null): MetricRow => ({ key, label, num: v, node: <Ret v={v} />, text: pct(v) });
  const px = (key: string, label: string, v: number | null): MetricRow => {
    const text = isNum(v) ? `${price(v)}${unit}` : DASH;
    return { key, label, num: v, node: text, text };
  };
  return [
    {
      key: "signal",
      label: "Signal",
      num: m.score,
      node: (
        <>
          <span className={`signal ${signalTone(m.signal)}`}>{m.signal}</span>
          {isNum(m.score) && <span className="muted"> · score {m.score}/5</span>}
        </>
      ),
      text: `${m.signal} ${isNum(m.score) ? `score ${m.score}/5` : ""}`,
    },
    ret("r1m", "Return 1 month", m.return_1m),
    ret("r3m", "Return 3 months", m.return_3m),
    ret("r6m", "Return 6 months", m.return_6m),
    ret("r1y", "Return 1 year", m.return_1y),
    { ...ret("dd", "Drawdown from peak", m.drawdown), term: "drawdownNow" },
    { key: "vol", label: "Volatility (annualised)", num: m.annual_volatility, node: plainPct(m.annual_volatility), text: plainPct(m.annual_volatility), term: "holdingVolatility" },
    { key: "rsi", label: "RSI (14)", num: m.rsi_14, node: fixed2(m.rsi_14), text: fixed2(m.rsi_14), term: "rsi" },
    { ...px("sma50", "SMA 50", m.sma_50), term: "movingAverage" },
    { ...px("sma200", "SMA 200", m.sma_200), term: "movingAverage" },
    {
      key: "obs",
      label: "Observations",
      num: m.observations,
      node: (
        <>
          {m.observations}
          {m.as_of && <span className="muted"> · latest {m.as_of}</span>}
        </>
      ),
      text: `${m.observations} ${m.as_of ?? ""}`,
    },
  ];
}

const METRIC_COLUMNS: readonly Column<MetricRow>[] = [
  {
    key: "label",
    header: "Metric",
    sortKey: "label",
    className: "c-metric",
    cell: (r) => (r.term ? <Labelled text={r.label} term={r.term} /> : r.label),
  },
  { key: "value", header: "Value", sortKey: "value", cell: (r) => r.node },
];

export function MetricsCard({ metrics, ccy }: { metrics: Metrics; ccy: string }) {
  const failed = new Set(metrics.failed_checks ?? []);
  const unit = ccy ? ` ${ccy}` : "";
  const rows = useMemo(() => metricRows(metrics, unit), [metrics, unit]);
  const sf = useSortFilter<MetricRow>({
    id: "metrics",
    rows,
    fields: {
      label: { type: "text", value: (r) => r.label },
      value: { type: "number", value: (r) => r.num },
    },
    search: (r) => `${r.label} ${r.text}`,
  });
  return (
    <Card as="section" aria-labelledby="metrics-h">
      <CardHeader id="metrics-h" title="Trend signals" term="signal" aside={<span className="tag">price-only · no LLM</span>} />
      <DataTable sf={sf} columns={METRIC_COLUMNS} rowKey={(r) => r.key} label="trend signals" variant="plain" noFilter />
      {metrics.reasons.length > 0 && (
        <ul className="reasons" aria-label="Signal reasons">
          {metrics.reasons.map((r, i) => {
            const bad = failed.has(r);
            return (
              <li key={i}>
                <span className={bad ? "down" : "up"}>
                  {bad ? <X /> : <Check />}
                </span>
                <span>
                  <span className="sr-only">{bad ? "Not met: " : "Met: "}</span>
                  {r}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      {metrics.signal_note && <p className="note">{metrics.signal_note}</p>}
      <p className="note">Signals are price-trend heuristics, not advice to buy, sell or hold.</p>
    </Card>
  );
}

function horizon(obs: number): string {
  if (obs <= 21) return "≈ 1 month";
  if (obs <= 63) return "≈ 3 months";
  if (obs <= 126) return "≈ 6 months";
  return `${obs} trading days`;
}

export function ScenariosCard({ scenarios, ccy }: { scenarios: Scenario[]; ccy: string }) {
  const unit = ccy ? ` ${ccy}` : "";
  const sf = useSortFilter<Scenario>({
    id: "scenarios",
    rows: scenarios,
    fields: {
      horizon: { type: "number", value: (s) => s.observations_ahead },
      p10: { type: "number", value: (s) => s.p10 },
      median: { type: "number", value: (s) => s.median },
      p90: { type: "number", value: (s) => s.p90 },
    },
    search: (s) => `${horizon(s.observations_ahead)} ${s.observations_ahead} trading days ${price(s.p10)} ${price(s.median)} ${price(s.p90)}`,
  });
  const cell = (value: number, change: number) => (
    <>
      {price(value)}
      {unit}
      <span className={`cell-sub ${signClass(change)}`}>{pct(change)}</span>
    </>
  );
  const columns: Column<Scenario>[] = [
    {
      key: "horizon",
      header: "Horizon",
      sortKey: "horizon",
      cell: (s) => (
        <>
          {horizon(s.observations_ahead)}
          <span className="cell-sub muted">{s.observations_ahead} trading days</span>
        </>
      ),
    },
    { key: "p10", header: "Low (p10)", sortKey: "p10", align: "right", cell: (s) => cell(s.p10, s.change_p10) },
    { key: "median", header: "Median", sortKey: "median", align: "right", cell: (s) => cell(s.median, s.change_median) },
    { key: "p90", header: "High (p90)", sortKey: "p90", align: "right", cell: (s) => cell(s.p90, s.change_p90) },
  ];
  return (
    <Card as="section" aria-labelledby="scen-h">
      <CardHeader id="scen-h" title="Scenario ranges" term="scenarios" aside={<span className="tag">not predictions</span>} />
      <DataTable sf={sf} columns={columns} rowKey={(s) => s.observations_ahead} label="scenario ranges" variant="plain" noFilter />
      <p className="note">Historical bootstrap of this holding's own daily returns: 10% of simulated paths ended below Low, 10% above High. Not a forecast; past returns do not predict future ones.</p>
    </Card>
  );
}
