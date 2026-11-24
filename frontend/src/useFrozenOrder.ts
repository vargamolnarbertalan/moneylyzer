import { useCallback, useMemo, useState, type FocusEvent } from "react";

/** Holds a list's order still while keyboard focus is inside it. A live update that re-sorts the
 *  rows would otherwise move the focused node in the DOM, and the browser drops focus to <body>.
 *  Values keep updating; the new order applies as soon as focus leaves the list. */
export function useFrozenOrder<T>(items: readonly T[], key: (item: T) => string | number) {
  const [frozen, setFrozen] = useState<(string | number)[] | null>(null);
  const onFocus = useCallback(() => setFrozen((f) => f ?? items.map(key)), [items, key]);
  const onBlur = useCallback((e: FocusEvent<HTMLElement>) => {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setFrozen(null);
  }, []);
  const ordered = useMemo(() => {
    if (!frozen) return items;
    const byKey = new Map(items.map((it) => [key(it), it]));
    const kept = frozen.map((k) => byKey.get(k)).filter((it): it is T => it !== undefined);
    const known = new Set(frozen);
    return [...kept, ...items.filter((it) => !known.has(key(it)))]; // new rows go last until then
  }, [items, frozen, key]);
  return { ordered, onFocus, onBlur };
}
