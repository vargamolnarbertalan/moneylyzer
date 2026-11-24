import { useCallback, useLayoutEffect, useMemo, useRef, useState, type FocusEvent } from "react";

/** Holds a list's order still while keyboard focus is inside it. A live update that re-sorts the
 *  rows would otherwise move the focused node in the DOM, and the browser drops focus to <body>.
 *  Values keep updating; the new order applies as soon as focus leaves the list.
 *  - Only keyboard focus (:focus-visible) freezes: a click or a Ctrl+click that opens a new tab
 *    leaves the list sorting live, so the header's sort stays true for everyone else.
 *  - Switching window or tab does not release it: focus is still on the row when the user returns.
 *  - Rows added while frozen go last until the order is released. */
export function useFrozenOrder<T>(items: readonly T[], key: (item: T) => string | number) {
  const [frozen, setFrozen] = useState<(string | number)[] | null>(null);
  const box = useRef<HTMLElement | null>(null);
  const ordered = useMemo(() => {
    if (!frozen) return items;
    const byKey = new Map(items.map((it) => [key(it), it]));
    const kept = frozen.map((k) => byKey.get(k)).filter((it): it is T => it !== undefined);
    const known = new Set(frozen);
    return [...kept, ...items.filter((it) => !known.has(key(it)))];
  }, [items, frozen, key]);
  // Re-freezing must keep the order on screen, not jump to the current sort.
  const shown = useRef(ordered);
  useLayoutEffect(() => {
    shown.current = ordered;
  }, [ordered]);

  const onFocus = useCallback(
    (e: FocusEvent<HTMLElement>) => {
      box.current = e.currentTarget;
      if ((e.target as Element).matches(":focus-visible")) setFrozen((f) => f ?? shown.current.map(key));
    },
    [key],
  );
  const onBlur = useCallback((e: FocusEvent<HTMLElement>) => {
    const list = e.currentTarget;
    if (list.contains(e.relatedTarget as Node | null)) return;
    // relatedTarget is also null when the window loses focus, or while a removed button hands focus to
    // its replacement (Delete -> No); look again once that has settled.
    window.setTimeout(() => {
      if (document.hasFocus() && !list.contains(document.activeElement)) setFrozen(null);
    }, 0);
  }, []);
  // Not every engine fires blur when the focused node is removed: release once focus is elsewhere.
  useLayoutEffect(() => {
    if (frozen && box.current && document.hasFocus() && !box.current.contains(document.activeElement)) setFrozen(null);
  });
  return { ordered, onFocus, onBlur };
}
