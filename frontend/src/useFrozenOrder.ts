import { useCallback, useLayoutEffect, useMemo, useRef, useState, type FocusEvent } from "react";

/** Holds a list's order still while keyboard or screen-reader focus is inside it. A live update that
 *  re-sorts the rows would otherwise move the focused node in the DOM, and the browser drops focus to
 *  <body>. Values keep updating; the new order applies as soon as focus leaves the list.
 *  - Focus that follows a mouse or touch press does not freeze (a click, or a Ctrl+click that opens a
 *    new tab, leaves the list sorting live).
 *  - Switching window or tab does not release it: focus is still on the row when the user returns.
 *  - Rows added while frozen go last until the order is released.
 *  - `paused` is true while frozen, so the sorted column can stop claiming an order the rows lost. */
export function useFrozenOrder<T>(items: readonly T[], key: (item: T) => string | number) {
  const [frozen, setFrozen] = useState<(string | number)[] | null>(null);
  const box = useRef<HTMLElement | null>(null);
  const pressedAt = useRef(0);
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

  const onPointerDown = useCallback(() => {
    pressedAt.current = Date.now();
  }, []);
  const onFocus = useCallback(
    (e: FocusEvent<HTMLElement>) => {
      box.current = e.currentTarget;
      if (Date.now() - pressedAt.current < 1000) return; // focus from a press, not from the keyboard
      setFrozen((f) => f ?? shown.current.map(key));
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
  return { ordered, paused: frozen !== null, onFocus, onBlur, onPointerDown };
}
