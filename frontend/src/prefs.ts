// View preferences synced across the owner's devices (server: analyzer/prefs.py, /api/prefs).
//
//  - The browser keeps a copy (localStorage "lia-prefs"), so a page opens with the last choices at once.
//  - After sign-in, and whenever the tab becomes visible again, the server copy is pulled in;
//    changes made here that are not confirmed yet win over it.
//  - Unconfirmed changes are kept in localStorage too ("lia-prefs-pending"), so a reload, a crash or
//    being offline does not lose them; they are sent in small batches (PATCH, changed keys only),
//    retried with backoff, and flushed with keepalive when the tab is hidden or closed.
//  - A write equal to what is already stored is dropped, so mounting a page never overwrites a newer
//    choice from another device. If the server rejects a batch (422), its keys are retried one by one
//    and only the rejected ones are dropped.
//  - The first sync on a browser uploads the choices it had before syncing existed (old per-key storage);
//    it counts as done only once that upload succeeded.
import { useCallback, useSyncExternalStore } from "react";
import { ApiError, endpoints } from "./api";
import { readJson, readStored, writeJson, writeStored } from "./storage";

export type Prefs = Record<string, unknown>;

const CACHE = "lia-prefs";
const PENDING = "lia-prefs-pending";
const SYNCED_ONCE = "lia-prefs-synced";
const FLUSH_MS = 400;
const RETRY_MS = [5_000, 15_000, 60_000];
// Pre-sync storage keys, read once to migrate (theme also stays in "lia-theme" for theme-init.js).
const LEGACY: Record<string, string> = { "lia-theme": "theme", "lia-sidebar": "sidebar", "lia-alloc-view": "alloc_view" };
const LEGACY_TABLE = "lia-table:";

const listeners = new Set<() => void>();
const pending: Prefs = loadPending(); // set here, not yet confirmed by the server (null = forget the key)
let store: Prefs = { ...loadCache(), ...withoutNulls(pending) };
let active = false; // only talk to the server while signed in
let timer: number | undefined;
let flushing = false;
let failures = 0;
let migrating = false; // the first-sync upload is in `pending`

function isObject(v: unknown): v is Prefs {
  return !!v && typeof v === "object" && !Array.isArray(v);
}

function withoutNulls(p: Prefs): Prefs {
  return Object.fromEntries(Object.entries(p).filter(([, v]) => v !== null));
}

function loadPending(): Prefs {
  const p = readJson<Prefs>(PENDING);
  return isObject(p) ? p : {};
}

function savePending(): void {
  writeJson(PENDING, pending);
}

function loadCache(): Prefs {
  const cached = readJson<Prefs>(CACHE);
  if (isObject(cached)) return cached;
  const migrated: Prefs = {};
  for (const [old, key] of Object.entries(LEGACY)) {
    const v = readStored(old);
    if (v !== null) migrated[key] = v;
  }
  try {
    for (let i = 0; i < window.localStorage.length; i++) {
      const k = window.localStorage.key(i);
      if (k?.startsWith(LEGACY_TABLE)) {
        const v = readJson<unknown>(k);
        if (isObject(v)) migrated[`table:${k.slice(LEGACY_TABLE.length)}`] = v;
      }
    }
  } catch {
    /* storage blocked: start empty */
  }
  return migrated;
}

function emit(): void {
  writeJson(CACHE, store);
  listeners.forEach((fn) => fn());
}

const same = (a: unknown, b: unknown) => a === b || JSON.stringify(a) === JSON.stringify(b);

export function getPref(key: string): unknown {
  return store[key];
}

/** Remember a choice here at once and send it to the server shortly (null forgets it). */
export function setPref(key: string, value: unknown): void {
  const current = key in pending ? pending[key] : store[key];
  if (same(current ?? null, value ?? null)) return;
  if (value === null || value === undefined) {
    const rest = { ...store };
    delete rest[key];
    store = rest;
  } else {
    store = { ...store, [key]: value };
  }
  pending[key] = value ?? null;
  savePending();
  emit();
  schedule(FLUSH_MS);
}

function schedule(ms: number): void {
  if (!active) return;
  window.clearTimeout(timer);
  timer = window.setTimeout(() => void flush(), ms);
}

/** Forget pending keys whose value is still the one just confirmed (or rejected). */
function settle(batch: Prefs): void {
  for (const [k, v] of Object.entries(batch)) if (same(pending[k], v)) delete pending[k];
  savePending();
}

async function flush(): Promise<void> {
  if (!active || flushing || Object.keys(pending).length === 0) return;
  flushing = true;
  const batch = { ...pending };
  let retryLater = false;
  try {
    await endpoints.savePrefs(batch);
    settle(batch);
    failures = 0;
  } catch (err) {
    if (err instanceof ApiError && err.status === 422) {
      // One bad key must not cost the others: try them one by one, drop only what is refused.
      for (const [k, v] of Object.entries(batch)) {
        try {
          await endpoints.savePrefs({ [k]: v });
          settle({ [k]: v });
        } catch (e) {
          if (e instanceof ApiError && e.status === 422) settle({ [k]: v });
          else retryLater = true;
        }
      }
    } else {
      retryLater = true; // offline, 5xx: keep everything pending
    }
  } finally {
    flushing = false;
  }
  if (migrating && Object.keys(pending).length === 0) {
    migrating = false;
    writeStored(SYNCED_ONCE, "1");
  }
  if (retryLater) {
    schedule(RETRY_MS[Math.min(failures, RETRY_MS.length - 1)] as number);
    failures += 1;
  } else if (Object.keys(pending).length) {
    schedule(FLUSH_MS); // changed while this batch was in flight
  }
}

/** Last chance when the tab is hidden or closed: send what is pending with keepalive. */
function flushOnExit(): void {
  if (!active || Object.keys(pending).length === 0) return;
  try {
    void fetch("/api/prefs", {
      method: "PATCH",
      keepalive: true,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Requested-With": "lia" },
      body: JSON.stringify(pending),
    });
  } catch {
    /* still pending in localStorage; sent on the next visit */
  }
}

/** Pull the server copy (local unconfirmed changes win). The first sync here uploads pre-sync choices. */
export async function syncPrefs(): Promise<void> {
  if (!active) return;
  let server: Prefs;
  try {
    server = await endpoints.prefs();
  } catch {
    return; // keep the cached copy
  }
  if (readStored(SYNCED_ONCE) === null && !migrating) {
    let added = false;
    for (const [k, v] of Object.entries(store)) {
      if (!(k in server) && !(k in pending)) {
        pending[k] = v;
        added = true;
      }
    }
    if (added) {
      migrating = true;
      savePending();
    } else {
      writeStored(SYNCED_ONCE, "1");
    }
  }
  const next: Prefs = { ...server };
  for (const [k, v] of Object.entries(pending)) {
    if (v === null) delete next[k];
    else next[k] = v;
  }
  if (!same(next, store)) {
    store = next;
    emit();
  } else {
    writeJson(CACHE, store); // the first sync on a browser must still create the local copy
  }
  await flush();
}

function onVisibility(): void {
  if (document.visibilityState === "visible") void syncPrefs();
  else flushOnExit();
}

/** Called by auth: start syncing when signed in, stop when signed out. */
export function setPrefsSync(on: boolean): void {
  if (on === active) return;
  active = on;
  if (on) {
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", flushOnExit);
    void syncPrefs();
  } else {
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("pagehide", flushOnExit);
    window.clearTimeout(timer);
  }
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Subscribe to every preference change (used by the theme, which also applies it to <html>). */
export function onPrefsChange(fn: () => void): () => void {
  return subscribe(fn);
}

/** The raw stored value of a key, following changes (including ones synced from another device). */
export function usePrefValue(key: string): unknown {
  return useSyncExternalStore(subscribe, () => store[key], () => undefined);
}

/** A synced choice from a fixed list; anything else (old or foreign value) reads as the fallback. */
export function usePref<T extends string>(key: string, options: readonly T[], fallback: T): [T, (v: T) => void] {
  const raw = usePrefValue(key);
  const value = options.find((o) => o === raw) ?? fallback;
  const set = useCallback((v: T) => setPref(key, v), [key]);
  return [value, set];
}
