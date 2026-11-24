// One sort + filter engine for every table and list in the app.
//  - sort: click cycle asc -> desc -> default; numbers sort numerically with unknown values always LAST;
//    text uses localeCompare(..., "hu").
//  - filter: free text (case- and diacritics-insensitive, every word must match) + optional select filters.
//  - the choice is remembered per table and synced across devices (prefs key "table:<id>", src/prefs.ts).
// Live updates only change `rows`; the state (sort, filter) lives here and is never reset by them.
import { useCallback, useMemo, useRef, useState } from "react";
import { getPref, setPref, usePrefValue } from "./prefs";

export type Dir = "asc" | "desc";
export interface SortSpec {
  key: string;
  dir: Dir;
}

export interface Field<T> {
  type: "text" | "number";
  value: (row: T) => string | number | null | undefined;
}

export interface SelectDef<T> {
  key: string;
  label: string;
  options: readonly { value: string; label: string }[];
  /** The first option's value ("all") must always pass. */
  test: (row: T, value: string) => boolean;
}

export interface SortFilterConfig<T> {
  /** Storage key: one per table. */
  id: string;
  rows: readonly T[];
  fields: Record<string, Field<T>>;
  /** All the visible text of a row, for the free-text filter. */
  search: (row: T) => string;
  /** The order when the user has not picked one. */
  defaultSort?: SortSpec;
  selects?: readonly SelectDef<T>[];
  /** false: only the sort order is saved (shared by every copy of the table); the filter belongs to
   *  this one view and is forgotten when it goes. For tables shown per item (a holding's purchases),
   *  where a filter typed on one item must not silently apply on the next. Default true. */
  saveFilter?: boolean;
}

interface State {
  sort: SortSpec | null;
  query: string;
  selects: Record<string, string>;
}

const PREFIX = "table:"; // id must match analyzer/prefs.py TABLE_KEY: lowercase letters, digits, dashes

export function normalizeText(s: string): string {
  return s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

function isEmpty(v: unknown): boolean {
  return v === null || v === undefined || v === "" || (typeof v === "number" && !Number.isFinite(v));
}

function loadState<T>(cfg: SortFilterConfig<T>, raw: unknown): State {
  const stored = raw as Partial<State> | undefined;
  const state: State = { sort: null, query: "", selects: {} };
  if (!stored || typeof stored !== "object") return state;
  const s = stored.sort;
  if (s && typeof s === "object" && typeof s.key === "string" && (s.dir === "asc" || s.dir === "desc") && s.key in cfg.fields) {
    state.sort = { key: s.key, dir: s.dir };
  }
  if (typeof stored.query === "string") state.query = stored.query.slice(0, 200);
  if (stored.selects && typeof stored.selects === "object") {
    for (const def of cfg.selects ?? []) {
      const v = (stored.selects as Record<string, unknown>)[def.key];
      if (typeof v === "string" && def.options.some((o) => o.value === v)) state.selects[def.key] = v;
    }
  }
  return state;
}

export function compareValues(a: unknown, b: unknown, type: "text" | "number", dir: Dir): number {
  const an = isEmpty(a);
  const bn = isEmpty(b);
  if (an || bn) return an && bn ? 0 : an ? 1 : -1; // unknown values go last whichever way we sort
  const c = type === "number" ? (a as number) - (b as number) : String(a).localeCompare(String(b), "hu", { sensitivity: "base", numeric: true });
  return dir === "asc" ? c : -c;
}

export function useSortFilter<T>(config: SortFilterConfig<T>) {
  const cfgRef = useRef(config);
  cfgRef.current = config;
  // The state IS the synced pref: a change from another device shows at once, and every edit starts
  // from the freshest stored value (never from a copy taken at mount).
  const stored = usePrefValue(PREFIX + config.id);
  const saveFilter = config.saveFilter ?? true;
  const [local, setLocal] = useState<Pick<State, "query" | "selects">>({ query: "", selects: {} });
  const localRef = useRef(local);
  localRef.current = local;
  const state = useMemo(() => {
    const loaded = loadState(cfgRef.current, stored);
    return saveFilter ? loaded : { ...loaded, query: local.query, selects: local.selects };
  }, [stored, saveFilter, local]);
  const setState = useCallback((fn: (s: State) => State) => {
    const cfg = cfgRef.current;
    const saved = loadState(cfg, getPref(PREFIX + cfg.id));
    if (cfg.saveFilter ?? true) {
      setPref(PREFIX + cfg.id, fn(saved));
      return;
    }
    const next = fn({ ...saved, query: localRef.current.query, selects: localRef.current.selects });
    if (next.sort !== saved.sort) setPref(PREFIX + cfg.id, { sort: next.sort, query: "", selects: {} });
    // updated at once, so a second edit before the re-render starts from this one, not the stale state
    localRef.current = { query: next.query, selects: next.selects };
    setLocal(localRef.current);
  }, []);

  const { rows } = config;
  const view = useMemo(() => {
    const cfg = cfgRef.current;
    const words = normalizeText(state.query).split(/\s+/).filter(Boolean);
    const tests = (cfg.selects ?? []).filter((d) => (state.selects[d.key] ?? d.options[0]?.value) !== d.options[0]?.value);
    const kept: { row: T; i: number }[] = [];
    rows.forEach((row, i) => {
      if (tests.some((d) => !d.test(row, state.selects[d.key] as string))) return;
      if (words.length) {
        const hay = normalizeText(cfg.search(row));
        if (!words.every((w) => hay.includes(w))) return;
      }
      kept.push({ row, i });
    });
    const sort = state.sort ?? cfg.defaultSort ?? null;
    const field = sort ? cfg.fields[sort.key] : undefined;
    if (sort && field) {
      kept.sort((x, y) => compareValues(field.value(x.row), field.value(y.row), field.type, sort.dir) || x.i - y.i);
    }
    return kept.map((k) => k.row);
  }, [rows, state]);

  const defaultSort = config.defaultSort ?? null;
  const effective = state.sort ?? defaultSort;
  const selectValue = (key: string, def: SelectDef<T>) => state.selects[key] ?? (def.options[0]?.value as string);
  const isFiltered = state.query.trim() !== "" || (config.selects ?? []).some((d) => selectValue(d.key, d) !== d.options[0]?.value);

  const setSort = useCallback((key: string, dir: Dir) => {
    setState((s) => {
      const d = cfgRef.current.defaultSort;
      return { ...s, sort: d && d.key === key && d.dir === dir ? null : { key, dir } };
    });
  }, []);

  /** Header click: asc -> desc -> back to the default order. */
  const toggleSort = useCallback((key: string) => {
    setState((s) => {
      if (s.sort && s.sort.key === key) return { ...s, sort: s.sort.dir === "asc" ? { key, dir: "desc" } : null };
      return { ...s, sort: { key, dir: "asc" } };
    });
  }, []);

  const setQuery = useCallback((query: string) => setState((s) => ({ ...s, query })), []);
  const setSelect = useCallback((key: string, value: string) => setState((s) => ({ ...s, selects: { ...s.selects, [key]: value } })), []);
  const clear = useCallback(() => setState((s) => ({ ...s, query: "", selects: {} })), []);

  const ariaSort = (key: string): "ascending" | "descending" | "none" =>
    effective && effective.key === key ? (effective.dir === "asc" ? "ascending" : "descending") : "none";

  return {
    view,
    total: rows.length,
    shown: view.length,
    sort: effective,
    query: state.query,
    selectValue,
    selects: config.selects ?? [],
    isFiltered,
    setSort,
    toggleSort,
    setQuery,
    setSelect,
    clear,
    ariaSort,
  };
}

export type SortFilter<T> = ReturnType<typeof useSortFilter<T>>;
