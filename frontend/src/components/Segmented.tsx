import { useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import type { LucideIcon } from "lucide-react";

interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  /** With an icon the button shows only the icon; the label becomes its accessible name and tooltip. */
  icon?: LucideIcon;
  /** Spoken name when the visible label is an abbreviation ("1W" is read as "1 week"). */
  name?: string;
}

interface SegmentedProps<T extends string> {
  label: string;
  options: readonly SegmentedOption<T>[];
  value: T;
  onChange: (value: T) => void;
}

/** Range tabs: a group of real buttons, the active one has aria-pressed. A thumb slides behind
 *  the active button; until it has been measured the button paints its own background. */
export function Segmented<T extends string>({ label, options, value, onChange }: SegmentedProps<T>) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [thumb, setThumb] = useState<{ x: number; w: number } | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () => {
      const active = el.querySelector<HTMLElement>('button[aria-pressed="true"]');
      // Fractional layout widths, and no thumb at all while hidden (zero width): the button then
      // paints its own background until there is something to measure.
      const r = active?.getBoundingClientRect();
      const next = active && r && r.width > 0 ? { x: r.left - el.getBoundingClientRect().left - el.clientLeft, w: r.width } : null;
      // Same numbers keep the same object, so a caller passing a new options array each render cannot loop.
      setThumb((cur) => (cur && next && cur.x === next.x && cur.w === next.w ? cur : next));
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [value, options]);

  const style = thumb ? ({ "--thumb-x": `${thumb.x}px`, "--thumb-w": `${thumb.w}px` } as CSSProperties) : undefined;
  return (
    <div ref={ref} className={thumb ? "seg slide" : "seg"} style={style} role="group" aria-label={label}>
      {options.map(({ value: v, label: text, icon: OptionIcon, name }) => (
        <button
          key={v}
          type="button"
          className={OptionIcon ? "seg-icon" : undefined}
          aria-pressed={v === value}
          // The spoken name starts with the visible text ("1W, 1 week"), so speech input "click 1W" works.
          aria-label={OptionIcon ? text : name ? `${text}, ${name}` : undefined}
          title={OptionIcon ? text : undefined}
          onClick={() => onChange(v)}
        >
          {OptionIcon ? <OptionIcon /> : text}
        </button>
      ))}
    </div>
  );
}
