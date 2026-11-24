import { useEffect, useSyncExternalStore } from "react";

/** The page header's title is the route's ("Holding"); a page that knows a better one (the
 *  holding's name) puts it here while it is mounted, and Shell shows it instead. */
let override: string | null = null;
const listeners = new Set<() => void>();

function set(next: string | null): void {
  if (next === override) return;
  override = next;
  listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Read by Shell. */
export function useHeaderOverride(): string | null {
  return useSyncExternalStore(subscribe, () => override, () => null);
}

/** Called by a page; null keeps the route's title. Cleared when the page goes. */
export function useHeaderTitle(title: string | null): void {
  useEffect(() => {
    set(title);
    return () => set(null);
  }, [title]);
}
