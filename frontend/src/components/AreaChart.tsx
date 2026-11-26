import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type PointerEvent, type RefObject } from "react";
import { ArrowLeftRight, X } from "lucide-react";
import { MINUS, isNum, pct } from "../format";

interface AreaChartProps {
  values: number[];
  /** One label per value (usually the date), shown in the tooltip and on the x axis. */
  labels?: string[];
  fmt: (v: number) => string;
  /** Longer format for the tooltip (defaults to fmt). */
  fmtTip?: (v: number) => string;
  /** Signed format for the difference between two picked points; `from` is the earlier value
   *  (for precision). Defaults to a sign + fmtTip. */
  fmtDelta?: (v: number, from: number) => string;
  /** Also show the relative % change between two picked points (off for values that are themselves
   *  percentages, such as a drawdown, where "−5% to −10%" as "−100%" would mislead). */
  relative?: boolean;
  /** What the data is (range, currency, which response). A change drops the picked comparison
   *  and replays the draw-in, even when the new data looks the same. Live ticks keep the key. */
  resetKey?: string;
  /** New data is on its way: the chart takes no picks and hides a comparison made on the old data. */
  busy?: boolean;
  /** Dashed horizontal reference line (the average cost). */
  refValue?: number | null;
  /** "accent" = the cyan accent (portfolio chart); "trend" = green/red by first vs last value. */
  tone?: "accent" | "trend";
  /** Pulse the last point: it is the live value. */
  live?: boolean;
  label: string;
}

const PAD_L = 4;
const PAD_T = 10;
const PAD_B = 20;
/** Pointer travel (px) between press and release above which a click is a drag, not a pick. */
const DRAG = 8;

/** The picked interval. `b === null` while the user still has to pick the end point.
 *  `key` ties it to the data it was picked on (see dataKey). */
interface Selection {
  key: string;
  a: number;
  b: number | null;
}

function useSize(ref: RefObject<HTMLElement | null>): { w: number; h: number } {
  const [size, setSize] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return size;
}

/** Change from `from` to `to` as a fraction, or null when it has no meaning (start at or below zero). */
function change(from: number, to: number): number | null {
  return from > 0 && isNum(to) ? (to - from) / from : null;
}

/** Colour of a formatted signed number, judged on what is shown: "+0 Ft" is neutral even when
 *  the raw difference was −0.4. */
function toneOf(text: string): "up" | "down" | "" {
  if (!/[1-9]/.test(text)) return "";
  return text.startsWith(MINUS) || text.startsWith("-") ? "down" : "up";
}

/** The percent beside an amount, never contradicting it: a change too small to show at two
 *  decimals reads "<0,01%" in the amount's colour, a zero amount gives a neutral "0,00%". */
function pctText(frac: number, amountTone: "up" | "down" | ""): { text: string; tone: "up" | "down" | "" } {
  if (amountTone === "") return { text: pct(0).replace(/^\+/, ""), tone: "" };
  const text = pct(frac);
  if (toneOf(text) === "") return { text: pct(0.0001).replace(/^\+/, "<"), tone: amountTone };
  return { text, tone: toneOf(text) };
}

/** The mock's inline-SVG area chart: gradient fill, glowing line, dashed reference line,
 *  crosshair + tooltip on hover/touch/arrow keys, and a pulsing last point. No chart library.
 *
 *  Picking two points (click/tap, Enter, or the screen-reader point controls) compares them: the
 *  series outside the interval fades and a badge over the interval shows the change in units and
 *  percent. The next pick clears it. */
export function AreaChart({ values, labels, fmt, fmtTip, fmtDelta, relative = true, resetKey, busy = false, refValue = null, tone = "trend", live = false, label }: AreaChartProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const badgeRef = useRef<HTMLDivElement | null>(null);
  const { w: W, h: H } = useSize(wrapRef);
  const uid = useId().replace(/[^a-zA-Z0-9]/g, "");
  const [hover, setHover] = useState<number | null>(null);
  const [sel, setSel] = useState<Selection | null>(null);
  const [announce, setAnnounce] = useState("");
  const [badgeSize, setBadgeSize] = useState({ w: 220, h: 60 });
  const downX = useRef<number | null>(null);
  const pointerType = useRef("mouse");
  const wasBusy = useRef(busy);
  const shownBefore = useRef(false);
  const n = values.length;

  // Live ticks only add or drop the last point, so the caller's key plus the first label identify
  // the data; the point count stands in only when the caller gives no key.
  const dataKey = `${resetKey ?? n}|${labels?.[0] ?? ""}`;
  // A selection is trusted only on the data it was made on. An end point dropped by a live tick
  // is clamped to the last point instead of making the comparison vanish.
  const s: Selection | null = (() => {
    if (!sel || sel.key !== dataKey || n < 2 || sel.a > n - 1) return null;
    const b = sel.b === null ? null : Math.min(sel.b, n - 1);
    return b === sel.a ? null : { ...sel, b };
  })();
  const visible = s !== null && !busy;

  const indexAt = useCallback(
    (clientX: number, padR: number): number | null => {
      const el = wrapRef.current;
      if (!el || n < 2 || W === 0) return null;
      const r = el.getBoundingClientRect();
      if (r.width === 0) return null;
      const px = ((clientX - r.left) * W) / r.width;
      const i = Math.round(((px - PAD_L) * (n - 1)) / Math.max(W - PAD_L - padR, 1));
      return Math.max(0, Math.min(n - 1, i));
    },
    [W, n],
  );

  // Reloads are announced (WCAG 4.1.3). Declared before the reset effect, so when new data drops a
  // comparison in the same commit, "Comparison cleared" is the message that stays.
  useEffect(() => {
    if (busy) setAnnounce("Loading the chart…");
    else if (wasBusy.current) setAnnounce("Chart updated.");
    wasBusy.current = busy;
  }, [busy]);
  // Declared before the effect below, so on a key change it still sees whether a comparison was
  // on screen in the previous commit.
  useEffect(() => {
    setHover(null);
    setSel(null);
    // Only a dropped comparison is worth saying; a second reset (range, then its data) must not
    // blank the first one's message.
    if (shownBefore.current) setAnnounce("Comparison cleared: the chart changed.");
  }, [dataKey]);
  useEffect(() => {
    shownBefore.current = s !== null; // a comparison hidden by a reload still counts as dropped
  });

  // The badge's real size, so it can be kept inside the chart whatever its content.
  useLayoutEffect(() => {
    const el = badgeRef.current;
    if (!el) return;
    const next = { w: el.offsetWidth, h: el.offsetHeight };
    setBadgeSize((cur) => (cur.w === next.w && cur.h === next.h ? cur : next));
  });

  // One structure for every state, so the measured element never changes under the ref.
  if (n < 2)
    return (
      <div className="chart-box">
        <div ref={wrapRef} className="chart" />
      </div>
    );

  const tipFmt = fmtTip ?? fmt;
  const deltaFmt =
    fmtDelta ??
    ((v: number) => {
      const body = tipFmt(Math.abs(v));
      return `${v < 0 && /[1-9]/.test(body) ? MINUS : "+"}${body}`;
    });
  const at = (i: number) => labels?.[i] ?? `#${i + 1}`;

  const hasRef = typeof refValue === "number" && Number.isFinite(refValue);
  const refV = hasRef ? (refValue as number) : null;
  const lo0 = Math.min(...values, refV ?? Infinity);
  const hi0 = Math.max(...values, refV ?? -Infinity);
  const span = hi0 - lo0 || 1;
  const lo = lo0 - span * 0.08;
  const hi = hi0 + span * 0.08;
  const ticks = [hi - (hi - lo) * 0.1, (hi + lo) / 2, lo + (hi - lo) * 0.1];
  const tickText = ticks.map((t) => fmt(t));
  const padR = Math.max(44, Math.ceil(Math.max(...tickText.map((t) => t.length)) * 6.3) + 12);

  const innerW = Math.max(W - PAD_L - padR, 1);
  const x = (i: number) => PAD_L + (i * innerW) / (n - 1);
  const y = (v: number) => PAD_T + ((hi - v) * (H - PAD_T - PAD_B)) / (hi - lo);
  const first = values[0] as number;
  const last = values[n - 1] as number;
  const lineColor = tone === "accent" ? "var(--accent2)" : last >= first ? "var(--up)" : "var(--down)";

  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(" ");
  const fill = `${d} L${x(n - 1).toFixed(1)} ${H - PAD_B} L${x(0).toFixed(1)} ${H - PAD_B} Z`;

  const hIdx = hover !== null && hover < n ? hover : null;
  const pending = visible && s !== null && s.b === null;

  /** The difference between two indices, in time order whichever was picked first. */
  const compare = (i: number, j: number) => {
    const from = Math.min(i, j);
    const to = Math.max(i, j);
    const vFrom = values[from] as number;
    const vTo = values[to] as number;
    const text = deltaFmt(vTo - vFrom, vFrom);
    const t = toneOf(text);
    const f = relative ? change(vFrom, vTo) : null;
    return { from, to, text, tone: t, pct: f === null ? null : pctText(f, t) };
  };
  const describe = (c: ReturnType<typeof compare>) => `${at(c.from)} to ${at(c.to)}: ${c.text}${c.pct ? `, ${c.pct.text}` : ""}.`;

  // The interval to draw: the picked one, or while picking, start → hover as a preview.
  const result = visible && s && s.b !== null ? compare(s.a, s.b) : null;
  const preview = pending && s && hIdx !== null && hIdx !== s.a ? compare(s.a, hIdx) : null;
  const shown = result ?? preview;
  const markColor = result ? (result.tone === "up" ? "var(--up)" : result.tone === "down" ? "var(--down)" : "var(--muted)") : lineColor;

  const hoverVal = hIdx !== null ? (values[hIdx] as number) : null;
  const hoverLabel = hIdx !== null && labels ? labels[hIdx] : undefined;
  const tipText = hoverVal !== null ? `${hoverLabel ? `${hoverLabel} · ` : ""}${tipFmt(hoverVal)}` : "";
  const tipDelta = preview ? `${preview.text}${preview.pct ? ` · ${preview.pct.text}` : ""}` : null;
  const tipMargin = tipDelta ? 110 : 80;
  const tipLeft = hIdx !== null && W > 0 ? Math.min(Math.max(x(hIdx), tipMargin), Math.max(W - tipMargin, tipMargin)) : 0;
  const tipY = hoverVal !== null ? y(hoverVal) : 0;
  // Near the top the tooltip opens below the point, so it never covers the card header.
  const tipBelow = tipY < (tipDelta ? 64 : 44);

  /** One pick: start → end. A click on the start point again cancels. */
  const pick = (i: number, viaPointer: boolean) => {
    if (!s || s.b !== null) {
      setSel({ key: dataKey, a: i, b: null });
      setAnnounce(`Start: ${at(i)}, ${tipFmt(values[i] as number)}. Now pick the end point.`);
    } else if (i === s.a) {
      if (viaPointer) {
        setSel(null);
        setAnnounce("Comparison cleared.");
      } else {
        // Keyboard and screen readers: a second press on an unmoved point is not a cancel.
        setAnnounce("Move to another point first, or press Escape to cancel.");
      }
    } else {
      setSel({ ...s, b: i });
      setAnnounce(describe(compare(s.a, i)));
    }
  };
  const clear = () => {
    setSel(null);
    setAnnounce("Comparison cleared.");
  };
  /** Enter, the point controls, or a screen reader's activation: act on the inspected point. */
  const activate = () => {
    if (busy) return;
    if (result) return clear();
    const cur = hIdx ?? n - 1;
    setHover(cur);
    pick(cur, false);
  };

  const onClick = (e: MouseEvent<HTMLDivElement>) => {
    if (busy) return;
    // detail 0: not a real pointer (VoiceOver/TalkBack double-tap, NVDA browse mode). Those click
    // the middle of the element, so use the point the cursor is on instead.
    if (e.detail === 0) return activate();
    const start = downX.current;
    downX.current = null;
    if (start !== null && Math.abs(e.clientX - start) > DRAG) return; // a scrub, not a pick
    if (result) return clear(); // "next click drops the highlight"
    const i = indexAt(e.clientX, padR);
    if (i === null) return;
    const completes = s !== null && s.b === null && i !== s.a;
    pick(i, true);
    // On touch the tooltip stays where the finger was and would sit on top of the new badge.
    if (completes && pointerType.current !== "mouse") setHover(null);
  };

  const inspect = (i: number) => {
    setHover(i);
    const p = pending && s && i !== s.a ? compare(s.a, i) : null;
    setAnnounce(`${at(i)}, ${tipFmt(values[i] as number)}${p ? `. ${describe(p)}` : ""}`);
  };
  /** One step left/right; from nothing inspected the first step lands on the latest point. */
  const step = (dir: -1 | 1) => inspect(hIdx === null ? n - 1 : Math.max(0, Math.min(n - 1, hIdx + dir)));
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    // Keys pressed on the badge's × belong to the button, and browser shortcuts stay theirs.
    if (e.target !== e.currentTarget || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.key === "ArrowLeft") step(-1);
    else if (e.key === "ArrowRight") step(1);
    else if (e.key === "Home") inspect(0);
    else if (e.key === "End") inspect(n - 1);
    else if (e.key === "Enter") activate();
    else if (e.key === "Escape") {
      // First dismiss the tooltip (WCAG 1.4.13), then the comparison.
      if (hIdx !== null) setHover(null);
      else if (s) clear();
      else return;
    } else return;
    e.preventDefault();
  };
  // Capture phase: recorded even for a press on the badge (which stops the bubbling pointerdown),
  // so the drag check in onClick never compares against a stale press.
  const onPointerDownCapture = (e: PointerEvent<HTMLDivElement>) => {
    downX.current = e.clientX;
    pointerType.current = e.pointerType;
  };
  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    const i = indexAt(e.clientX, padR);
    if (i !== null) setHover(i);
  };
  const forgetPress = () => {
    downX.current = null;
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const i = indexAt(e.clientX, padR);
    if (i !== null) setHover(i);
  };
  const clearFromButton = () => {
    clear();
    wrapRef.current?.focus(); // the button is about to unmount: keep keyboard focus on the chart
  };
  // Over the badge the chart does not track the pointer (no tooltip on top of what is being read).
  const leaveChart = (e: PointerEvent<HTMLDivElement>) => {
    e.stopPropagation();
    setHover(null);
  };

  const xLabelIdx = labels && labels.length === n ? [0, Math.floor((n - 1) / 2), n - 1] : [];
  const showMid = W > 460;
  const plotR = W - padR;
  const plotB = H - PAD_B;
  const iLo = shown ? x(shown.from) : 0;
  const iHi = shown ? x(shown.to) : 0;

  // The result badge sits over the interval: at the top of the plot, unless the line runs high
  // there and the space under it is larger, then at the bottom. Centred on the interval, kept
  // inside the chart using its measured size.
  let badge: { left: number; top: number } | null = null;
  if (result && W > 0) {
    const seg = values.slice(result.from, result.to + 1);
    const roomTop = y(Math.max(...seg)) - PAD_T;
    const roomBottom = plotB - y(Math.min(...seg));
    const atTop = roomTop >= badgeSize.h + 4 || roomTop >= roomBottom;
    const half = Math.min(badgeSize.w, W - 4) / 2;
    const left = Math.min(Math.max((iLo + iHi) / 2, half + 2), W - half - 2);
    badge = { left, top: atTop ? PAD_T : Math.max(PAD_T, plotB - badgeSize.h - 6) };
  }

  return (
    <div className="chart-box">
      <div
        ref={wrapRef}
        className="chart"
        role="group"
        tabIndex={0}
        aria-label={`${label}. Use the left and right arrow keys to inspect values; press Enter on two points to compare them.`}
        onPointerMove={onPointerMove}
        onPointerDownCapture={onPointerDownCapture}
        onPointerDown={onPointerDown}
        onPointerLeave={(e) => {
          forgetPress();
          if (e.pointerType === "mouse") setHover(null);
        }}
        onPointerCancel={() => {
          forgetPress(); // a scroll that started on the chart
          setHover(null);
        }}
        onClick={onClick}
        onKeyDown={onKey}
        onBlur={() => setHover(null)}
      >
        {W > 0 && H > 0 && (
          <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${label}: from ${fmt(first)} to ${fmt(last)}`}>
            <defs>
              <linearGradient id={`${uid}g`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="0" style={{ stopColor: lineColor }} stopOpacity=".42" />
                <stop offset="1" style={{ stopColor: lineColor }} stopOpacity="0" />
              </linearGradient>
              <filter id={`${uid}f`} filterUnits="userSpaceOnUse" x="0" y="0" width={W} height={H}>
                <feGaussianBlur stdDeviation="4" />
              </filter>
              {/* Comparing: the series keeps full strength inside the interval and fades outside,
                  through masks on the series alone (the translucent card behind stays untouched).
                  The line keeps more strength than the fill and glow, so it stays traceable. */}
              {shown &&
                (
                  [
                    ["m", result ? 0.22 : 0.45],
                    ["l", result ? 0.5 : 0.7],
                  ] as const
                ).map(([k, o]) => (
                  <mask key={k} id={`${uid}${k}`} maskUnits="userSpaceOnUse" x={-20} y={-20} width={W + 40} height={H + 40}>
                    <rect className="cmp-fade" x={-20} y={-20} width={W + 40} height={H + 40} fill="#fff" fillOpacity={o} />
                    <rect x={iLo} y={-20} width={Math.max(iHi - iLo, 0)} height={H + 40} fill="#fff" />
                  </mask>
                ))}
            </defs>
            {ticks.map((t, i) => (
              <g key={i}>
                <line className="grid-line" x1={PAD_L} x2={plotR} y1={y(t)} y2={y(t)} />
                <text className="axis" x={plotR + 6} y={y(t) + 4}>
                  {tickText[i]}
                </text>
              </g>
            ))}
            {refV !== null && <line className="ref-line" x1={PAD_L} x2={plotR} y1={y(refV)} y2={y(refV)} />}
            {shown && <rect className={result ? "cmp-band" : "cmp-band preview"} x={iLo} y={0} width={Math.max(iHi - iLo, 0)} height={plotB} style={{ fill: markColor }} aria-hidden="true" />}
            {/* Re-keyed per data set: a new range draws in again, a live tick does not. */}
            <g key={dataKey} className="draw-in">
              <g mask={shown ? `url(#${uid}m)` : undefined}>
                <path d={fill} fill={`url(#${uid}g)`} />
                <path d={d} fill="none" strokeWidth="5" opacity=".55" filter={`url(#${uid}f)`} style={{ stroke: lineColor }} />
              </g>
              <path d={d} fill="none" strokeWidth="2" strokeLinejoin="round" mask={shown ? `url(#${uid}l)` : undefined} style={{ stroke: lineColor }} />
            </g>
            <g key={`${dataKey}dot`} className="pop-in" mask={shown ? `url(#${uid}l)` : undefined}>
              {/* Re-keyed per live value: it pulses a few times on each tick, then rests. */}
              {live && <circle key={`h${last}`} className="halo" cx={x(n - 1)} cy={y(last)} r="4" style={{ fill: lineColor }} />}
              <circle cx={x(n - 1)} cy={y(last)} r="4" style={{ fill: lineColor }} />
            </g>
            {xLabelIdx.map((i, k) => {
              if (k === 1 && !showMid) return null;
              const anchor = k === 0 ? "start" : k === 2 ? "end" : "middle";
              const tx = k === 2 ? plotR : x(i);
              return (
                <text key={k} className="axis" x={tx} y={H - 4} textAnchor={anchor}>
                  {labels?.[i]}
                </text>
              );
            })}
            {visible &&
              s &&
              [s.a, s.b].map((i, k) =>
                i === null ? null : (
                  <g key={k}>
                    <line className="cmp-edge" x1={x(i)} x2={x(i)} y1={PAD_T} y2={plotB} />
                    <circle cx={x(i)} cy={y(values[i] as number)} r="5" strokeWidth="2.5" style={{ fill: "var(--surface-solid)", stroke: markColor }} />
                  </g>
                ),
              )}
            {hIdx !== null && (
              <g>
                <line className="xhair" x1={x(hIdx)} x2={x(hIdx)} y1={PAD_T} y2={plotB} opacity=".5" />
                <circle cx={x(hIdx)} cy={tipY} r="4.5" strokeWidth="2" style={{ fill: "var(--surface-solid)", stroke: lineColor }} />
              </g>
            )}
          </svg>
        )}
        {hIdx !== null && (
          <div className={tipBelow ? "tip below" : "tip"} style={{ left: tipLeft, top: tipY }} aria-hidden="true">
            {tipText}
            {tipDelta && preview && <span className={`tip-delta ${preview.tone}`}>{tipDelta}</span>}
          </div>
        )}
        {result && badge && (
          // A click on the badge counts as a click on the chart (it clears); only × stops there.
          <div ref={badgeRef} key={`${result.from}-${result.to}`} className="cmp-badge" style={{ left: badge.left, top: badge.top }} onPointerDown={leaveChart} onPointerMove={leaveChart}>
            <div className="cmp-badge-row">
              <span className="sr-only">Change, </span>
              <strong className={`cmp-diff ${result.tone}`}>{result.text}</strong>
              {result.pct ? <span className={`pill ${result.pct.tone || "none"}`}>{result.pct.text}</span> : relative && <span className="cmp-range">no % from zero</span>}
              <button
                type="button"
                className="cmp-x"
                onClick={(e) => {
                  e.stopPropagation();
                  clearFromButton();
                }}
                aria-label="Clear the comparison"
                title="Clear the comparison"
              >
                <X aria-hidden="true" />
              </button>
            </div>
            <div className="cmp-range">
              {at(result.from)}
              <span aria-hidden="true"> → </span>
              <span className="sr-only"> to </span>
              {at(result.to)}
            </div>
          </div>
        )}
      </div>
      {/* Point controls for screen readers without arrow keys (VoiceOver/TalkBack swipe to them).
          Hidden until one of them has keyboard focus, then shown, so focus is never invisible. */}
      <div className="cmp-at" role="group" aria-label="Chart point controls">
        <button type="button" className="btn ghost small" onClick={() => step(-1)}>
          Previous point
        </button>
        <button type="button" className="btn ghost small" onClick={() => step(1)}>
          Next point
        </button>
        <button type="button" className="btn ghost small" onClick={activate} disabled={busy}>
          {result ? "Clear the comparison" : pending ? "Mark as end point" : "Mark as start point"}
        </button>
      </div>
      <div className="chart-compare">
        {busy ? (
          <span className="cmp-hint muted">Loading…</span>
        ) : result ? (
          <span className="cmp-hint muted">
            <ArrowLeftRight aria-hidden="true" />
            Click or tap the chart again to clear
          </span>
        ) : pending && s ? (
          <>
            <span className="cmp-hint">
              From <strong>{at(s.a)}</strong> · now pick the end point
            </span>
            <button type="button" className="icon-btn cmp-clear" onClick={clearFromButton} aria-label="Cancel the comparison" title="Cancel the comparison">
              <X aria-hidden="true" />
            </button>
          </>
        ) : (
          <span className="cmp-hint muted">
            <ArrowLeftRight aria-hidden="true" />
            Click, tap or press Enter on two points to compare them
          </span>
        )}
      </div>
      <span className="sr-only" aria-live="polite" aria-atomic="true">
        {announce}
      </span>
    </div>
  );
}
