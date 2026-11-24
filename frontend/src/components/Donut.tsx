import { useId, useState } from "react";
import { useFrozenOrder } from "../useFrozenOrder";
import type { Row } from "../api";
import { chipColor } from "../chip";
import { compactMoney, plainPct } from "../format";
import { reducedMotion } from "../motion";
import { Link, navigate } from "../router";

const CX = 60;
const legendKey = (r: Row) => r.id;
const R_OUT = 52;
const R_IN = 34;

function arc(a0: number, a1: number): string {
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const pt = (a: number, r: number) =>
    [CX + r * Math.cos(a), CX + r * Math.sin(a)] as const;
  const [x0, y0] = pt(a0, R_OUT);
  const [x1, y1] = pt(a1, R_OUT);
  const [x2, y2] = pt(a1, R_IN);
  const [x3, y3] = pt(a0, R_IN);
  return `M${x0.toFixed(2)} ${y0.toFixed(2)}A${R_OUT} ${R_OUT} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}L${x2.toFixed(2)} ${y2.toFixed(2)}A${R_IN} ${R_IN} 0 ${large} 0 ${x3.toFixed(2)} ${y3.toFixed(2)}Z`;
}

/** The donut from the mock: segments by weight in the icon-chip colours, the total in the middle,
 *  a legend of the top six holdings. Hover or focus on a legend item lights up its segment. */
export function Donut({
  rows,
  total,
  base,
}: {
  rows: Row[];
  total: number | null;
  base: string;
}) {
  const [active, setActive] = useState<string | null>(null);
  const maskId = `${useId().replace(/[^a-zA-Z0-9]/g, "")}sweep`;
  // The sweep mask exists only while it animates: afterwards (or with reduced motion, where it
  // would not animate at all) the ring is drawn plainly, with no seam where the stroke meets itself.
  const [sweeping, setSweeping] = useState(() => !reducedMotion());
  const sum = rows.reduce((s, r) => s + (r.weight as number), 0) || 1;
  const legend = rows.slice(0, 6);
  // A live re-sort must not drop focus from a legend link (see useFrozenOrder).
  const legendOrder = useFrozenOrder(legend, legendKey);
  const rest = rows.slice(6);
  const restWeight = rest.reduce((s, r) => s + (r.weight as number), 0);

  let a0 = -Math.PI / 2;
  const segs = rows.map((r) => {
    const sweep = (2 * Math.PI * (r.weight as number)) / sum;
    const seg = { row: r, a0, a1: a0 + sweep };
    a0 += sweep;
    return seg;
  });

  return (
    <div className="donut-wrap">
      <svg
        className="donut"
        viewBox="0 0 120 120"
        role="img"
        aria-label={`Allocation donut chart, total ${compactMoney(total, base)}`}
      >
        {/* The ring sweeps in once: a mask stroke drawn from 12 o'clock (styles.css .donut-sweep). */}
        {sweeping && (
          <defs>
            <mask
              id={maskId}
              maskUnits="userSpaceOnUse"
              x="0"
              y="0"
              width="120"
              height="120"
            >
              <circle
                className="donut-sweep"
                cx={CX}
                cy={CX}
                r={(R_OUT + R_IN) / 2}
                fill="none"
                stroke="#fff"
                strokeWidth={R_OUT - R_IN + 4}
                pathLength={1}
                strokeDasharray="1 1"
                transform={`rotate(-90 ${CX} ${CX})`}
                onAnimationEnd={() => setSweeping(false)}
              />
            </mask>
          </defs>
        )}
        <g mask={sweeping ? `url(#${maskId})` : undefined}>
          {segs.length === 1 ? (
            <circle
              cx={CX}
              cy={CX}
              r={(R_OUT + R_IN) / 2}
              fill="none"
              strokeWidth={R_OUT - R_IN}
              style={{
                stroke: chipColor((segs[0] as (typeof segs)[number]).row.id),
              }}
            >
              <title>
                {(segs[0] as (typeof segs)[number]).row.name}: {plainPct(1)}
              </title>
            </circle>
          ) : (
            segs.map((s) => (
              <path
                key={s.row.id}
                d={arc(s.a0, s.a1)}
                className={
                  active === null
                    ? "donut-seg"
                    : active === s.row.id
                      ? "donut-seg on"
                      : "donut-seg dim"
                }
                style={{ fill: chipColor(s.row.id) }}
                onMouseEnter={() => setActive(s.row.id)}
                onMouseLeave={() => setActive(null)}
                onClick={() =>
                  navigate(`/holding/${encodeURIComponent(s.row.id)}`)
                }
              >
                <title>
                  {s.row.name}: {plainPct(s.row.weight)}
                </title>
              </path>
            ))
          )}
        </g>
        <text x={CX} y="57" textAnchor="middle" className="donut-k">
          total
        </text>
        <text x={CX} y="70" textAnchor="middle" className="donut-v">
          {compactMoney(total, base)}
        </text>
      </svg>
      <ul className="legend" aria-label="Largest holdings" onFocus={legendOrder.onFocus} onBlur={legendOrder.onBlur} onPointerDown={legendOrder.onPointerDown}>
        {legendOrder.ordered.map((r) => (
          <li key={r.id}>
            <Link
              to={`/holding/${encodeURIComponent(r.id)}`}
              onMouseEnter={() => setActive(r.id)}
              onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(r.id)}
              onBlur={() => setActive(null)}
            >
              <i style={{ background: chipColor(r.id) }} aria-hidden="true" />
              <span>{r.name}</span>
              <b>{plainPct(r.weight)}</b>
            </Link>
          </li>
        ))}
        {rest.length > 0 && (
          <li className="legend-more">
            <span>
              <i aria-hidden="true" />
              <span>{rest.length} more</span>
              <b>{plainPct(restWeight)}</b>
            </span>
          </li>
        )}
      </ul>
    </div>
  );
}
