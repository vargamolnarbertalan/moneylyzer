import { useLayoutEffect, useRef, useState } from "react";
import { isNum, type Num } from "../format";
import { reducedMotion } from "../motion";

interface TweenProps {
  value: Num;
  /** Formats the number; gets null when there is no value (the format's dash). */
  format: (v: Num) => string;
  /** What the number is measured in (the currency). When it changes the value jumps instead of
   *  counting across currencies. */
  unit?: string;
  /** Class for the number, from the value on screen (so the colour turns with the sign mid-count). */
  tone?: (v: Num) => string;
}

/** A number that counts to its new value when it changes (a live tick) and briefly lights up
 *  green or red. Only a change the reader can see animates: a tick that leaves the formatted
 *  text as it was does nothing. Screen readers get only the final value, never the frames in
 *  between; with reduced motion it simply jumps. */
export function Tween({ value, format, unit = "", tone }: TweenProps) {
  const target = isNum(value) ? value : null;
  const [shown, setShown] = useState<number | null>(target);
  const [flash, setFlash] = useState<{ dir: "up" | "down"; n: number } | null>(null);
  const current = useRef<number | null>(target); // the value on screen right now
  const lastUnit = useRef(unit);
  const formatRef = useRef(format);
  formatRef.current = format;

  // A layout effect: a jump (new currency, no value) lands before paint, never one frame late.
  useLayoutEffect(() => {
    const from = current.current;
    const unitChanged = lastUnit.current !== unit;
    lastUnit.current = unit;
    const fmt = formatRef.current;
    if (target === null || from === null || unitChanged || reducedMotion() || fmt(from) === fmt(target)) {
      current.current = target;
      setShown(target);
      return;
    }
    setFlash((f) => ({ dir: target > from ? "up" : "down", n: (f?.n ?? 0) + 1 }));
    const t0 = performance.now();
    const duration = 650;
    let frame = requestAnimationFrame(function step(now) {
      const p = Math.min((now - t0) / duration, 1);
      const eased = 1 - (1 - p) ** 3;
      const v = p === 1 ? target : from + (target - from) * eased;
      current.current = v;
      setShown(v);
      if (p < 1) frame = requestAnimationFrame(step);
    });
    return () => cancelAnimationFrame(frame);
  }, [target, unit]);

  const cls = tone?.(shown) ?? "";
  return (
    <span className={cls || undefined}>
      <span key={flash?.n ?? 0} className={flash ? `tween tw-${flash.dir}` : "tween"} aria-hidden="true">
        {format(shown)}
      </span>
      <span className="sr-only tween-sr">{format(target)}</span>
    </span>
  );
}
