import { pct, isNum, type Num } from "../format";

/** A +/- percentage pill. `fraction` is 0.1248 for +12.48%; null renders a neutral dash. */
export function Pill({ fraction }: { fraction: Num }) {
  if (!isNum(fraction)) return <span className="pill none">—</span>;
  return <span className={`pill ${fraction >= 0 ? "up" : "down"}`}>{pct(fraction)}</span>;
}
