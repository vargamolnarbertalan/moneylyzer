import { useEffect, useState } from "react";

const W = 110;
const H = 32;

/** The mock's sparkline: one stroked path, green when the last value is at or above the first. */
export function Sparkline({ values }: { values: number[] }) {
  // Draw in once, on mount. The class then goes, so a row moved by a live re-sort (React
  // re-inserts the node, which would restart a CSS animation) does not draw in again.
  const [fresh, setFresh] = useState(true);
  useEffect(() => {
    const timer = window.setTimeout(() => setFresh(false), 1000);
    return () => window.clearTimeout(timer);
  }, []);
  const v = values.filter((x) => Number.isFinite(x));
  if (v.length < 2) return <span className="sp" aria-hidden="true" />;
  const min = Math.min(...v);
  const max = Math.max(...v);
  const span = max - min || 1;
  const d = v
    .map((x, i) => `${i ? "L" : "M"}${((i * W) / (v.length - 1)).toFixed(1)} ${(2 + ((max - x) * (H - 4)) / span).toFixed(1)}`)
    .join(" ");
  const up = (v[v.length - 1] as number) >= (v[0] as number);
  return (
    <svg className={fresh ? "sp fresh" : "sp"} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
      <path d={d} fill="none" strokeWidth="1.6" vectorEffect="non-scaling-stroke" style={{ stroke: up ? "var(--up)" : "var(--down)" }} />
    </svg>
  );
}
