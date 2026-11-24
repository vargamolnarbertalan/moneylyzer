import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type RefObject,
} from "react";
import type { Row } from "../api";
import { isNum, pct, plainPct } from "../format";
import { Link } from "../router";
import { squarify } from "../treemap";

/** Tile colour from P/L, the contrast-safe way: a tint of green/red mixed into the card colour,
 *  so the theme's text colour always stays readable. Unknown P/L is the neutral chip colour. */
export function tileColor(pnlPct: number | null): string {
  if (!isNum(pnlPct)) return "var(--chip)";
  const p = pnlPct * 100; // percent
  if (p >= 0)
    return `color-mix(in srgb, var(--up) ${Math.round(Math.min(50, 14 + p))}%, var(--surface-solid))`;
  return `color-mix(in srgb, var(--down) ${Math.round(Math.min(50, 14 - p * 2))}%, var(--surface-solid))`;
}

function useBox(ref: RefObject<HTMLElement | null>): { w: number; h: number } {
  const [box, setBox] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return box;
}

/** The allocation "memory map": tile area = weight, colour = P/L, every tile links to its holding. */
export function Treemap({ rows }: { rows: Row[] }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const { w, h } = useBox(ref);
  // Tiles grow in once, on mount; afterwards a tile moved by a re-sort must not replay it.
  const [entering, setEntering] = useState(true);
  useEffect(() => {
    const timer = window.setTimeout(() => setEntering(false), 900);
    return () => window.clearTimeout(timer);
  }, []);
  const byId = useMemo(() => new Map(rows.map((r) => [r.id, r])), [rows]);
  const tiles = useMemo(
    () =>
      squarify(
        rows
          .filter((r) => isNum(r.weight))
          .map((r) => ({ id: r.id, weight: r.weight as number })),
        w,
        h,
      ),
    [rows, w, h],
  );

  return (
    <div
      ref={ref}
      className={entering ? "tree entering" : "tree"}
      role="group"
      aria-label="Treemap of holdings by share of portfolio value, coloured by profit and loss"
    >
      {/* DOM order by id, not by weight: tiles are absolutely placed, and a live re-sort that moved
          DOM nodes could throw keyboard focus off the focused tile. --i keeps the weight order. */}
      {tiles
        .map((t, i) => ({ t, i }))
        .sort((a, b) => (a.t.id < b.t.id ? -1 : a.t.id > b.t.id ? 1 : 0))
        .map(({ t, i }) => {
          const r = byId.get(t.id);
          if (!r) return null;
          const label = `${r.name}: ${plainPct(r.weight)} of the portfolio, P/L ${pct(r.pnl_pct)}`;
          const showId = t.w > 44 && t.h > 26;
          const showWeight = showId && t.h > 42;
          const showPnl =
            showWeight && t.h > 58 && t.w > 64 && isNum(r.pnl_pct);
          return (
            <Link
              key={t.id}
              to={`/holding/${encodeURIComponent(t.id)}`}
              className="tile"
              title={label}
              aria-label={label}
              style={
                {
                  left: t.x,
                  top: t.y,
                  width: t.w,
                  height: t.h,
                  background: tileColor(r.pnl_pct),
                  "--i": Math.min(i, 12),
                } as CSSProperties
              }
            >
              {showId && <b>{r.id}</b>}
              {showWeight && <span>{plainPct(r.weight)}</span>}
              {showPnl && <span className="tile-pnl">{pct(r.pnl_pct)}</span>}
            </Link>
          );
        })}
    </div>
  );
}
