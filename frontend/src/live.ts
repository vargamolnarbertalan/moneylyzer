// Live portfolio store: one GET on load + one Server-Sent Events connection shared by every screen.
// No simulated data: everything shown comes from the snapshot the backend pushes.
import { useEffect, useSyncExternalStore } from "react";
import { endpoints, errorMessage, type Snapshot } from "./api";
import { isNum } from "./format";

export type FlashDir = "up" | "down";
export type StreamPhase = "connecting" | "open" | "reconnecting";

export interface LiveState {
  snapshot: Snapshot | null;
  /** The first load finished, successfully or not. */
  loaded: boolean;
  loadError: string | null;
  phase: StreamPhase;
  /** Rows whose value_huf changed in the latest snapshot; cleared after the flash animation. */
  flashes: Record<string, FlashDir>;
}

const INITIAL: LiveState = { snapshot: null, loaded: false, loadError: null, phase: "connecting", flashes: {} };

let state: LiveState = INITIAL;
const listeners = new Set<() => void>();
let holders = 0;
let source: EventSource | null = null;
let flashTimer: number | undefined;
let retryTimer: number | undefined;
let releaseTimer: number | undefined;

function setState(patch: Partial<LiveState>): void {
  state = { ...state, ...patch };
  listeners.forEach((fn) => fn());
}

function applySnapshot(snap: Snapshot): void {
  const prev = state.snapshot;
  if (prev && snap.updated_at < prev.updated_at) return; // an older response arriving late
  const flashes: Record<string, FlashDir> = {};
  // A new base currency changes every figure at once; that is not a price move, so nothing flashes.
  if (prev && prev.base_currency === snap.base_currency) {
    const before = new Map(prev.rows.map((r) => [r.id, r.value_base] as const));
    for (const r of snap.rows) {
      const old = before.get(r.id);
      if (isNum(old) && isNum(r.value_base) && old !== r.value_base) flashes[r.id] = r.value_base > old ? "up" : "down";
    }
  }
  setState({ snapshot: snap, loaded: true, loadError: null, flashes });
  window.clearTimeout(flashTimer);
  if (Object.keys(flashes).length > 0) {
    flashTimer = window.setTimeout(() => setState({ flashes: {} }), 1300);
  }
}

function connect(): void {
  if (source) return;
  const es = new EventSource("/api/stream");
  source = es;
  es.onopen = () => setState({ phase: "open" });
  es.addEventListener("snapshot", (ev) => {
    try {
      applySnapshot(JSON.parse((ev as MessageEvent<string>).data) as Snapshot);
      setState({ phase: "open" });
    } catch {
      /* a malformed event is ignored; the next one replaces it */
    }
  });
  es.onerror = () => {
    setState({ phase: "reconnecting" });
    if (es.readyState === EventSource.CLOSED) {
      // The browser gave up (for example a 401). Ask the API: a 401 there sends the user to the sign-in page.
      void endpoints.portfolio().then(applySnapshot).catch(() => undefined);
      window.clearTimeout(retryTimer);
      retryTimer = window.setTimeout(() => {
        if (source === es) {
          es.close();
          source = null;
          if (holders > 0) connect();
        }
      }, 5000);
    }
  };
}

function disconnect(): void {
  window.clearTimeout(retryTimer);
  source?.close();
  source = null;
}

async function loadFirst(): Promise<void> {
  try {
    applySnapshot(await endpoints.portfolio());
  } catch (err) {
    setState({ loaded: true, loadError: errorMessage(err) });
  }
}

function retain(): void {
  window.clearTimeout(releaseTimer);
  holders += 1;
  if (holders === 1 && !source) {
    connect();
    if (!state.snapshot) void loadFirst();
  }
}

function release(): void {
  holders -= 1;
  if (holders > 0) return;
  // A short delay avoids tearing the connection down between two screens (and in React StrictMode).
  releaseTimer = window.setTimeout(() => {
    if (holders === 0) disconnect();
  }, 200);
}

/** Forget everything (sign-out, session expired). */
export function resetLive(): void {
  disconnect();
  window.clearTimeout(flashTimer);
  state = { ...INITIAL };
  listeners.forEach((fn) => fn());
}

/** Ask the server for fresh quotes now (it throttles to one run per 30 s). */
export async function refreshNow(): Promise<void> {
  applySnapshot(await endpoints.refresh());
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function useLive(): LiveState {
  useEffect(() => {
    retain();
    return release;
  }, []);
  return useSyncExternalStore(subscribe, () => state, () => INITIAL);
}

/** The reporting currency of the latest snapshot (HUF until the first one arrives). */
export function useBase(): string {
  const { snapshot } = useLive();
  return snapshot?.base_currency || "HUF";
}
