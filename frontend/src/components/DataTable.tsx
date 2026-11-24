// The one table used everywhere: sortable headers, filter box, select filters, "N of M", empty-state row.
// It renders a real <table>; the layout variants (card rows, stacked cards on phones) are CSS classes.
import { Fragment, useId, type ReactNode } from "react";
import { useFrozenOrder } from "../useFrozenOrder";
import { ArrowDown, ArrowUp, ArrowUpDown, X } from "lucide-react";
import { Link } from "../router";
import { Select } from "./Select";
import type { SortFilter } from "../useSortFilter";

export interface Column<T> {
  key: string;
  header: string;
  /** Key of a sortable field in useSortFilter. Omit for columns that cannot be sorted. */
  sortKey?: string;
  /** Right-align numbers. */
  align?: "right";
  className?: string;
  /** Visually hide the header text (still read by screen readers). */
  hideHeader?: boolean;
  /** Label shown in front of the value in the stacked phone layout. */
  label?: string;
  /** Render the cell content as the row's link (stretched over the whole row). */
  link?: boolean;
  cell: (row: T) => ReactNode;
}

export interface PhoneSortOption {
  key: string;
  label: string;
  dir: "asc" | "desc";
}

interface FilterBarProps<T> {
  sf: SortFilter<T>;
  label: string;
  /** "Sort by" choices shown on phones, where there are no clickable headers. */
  phoneSort?: readonly PhoneSortOption[];
  /** Show the "Sort by" select at every width (lists that have no header row). */
  alwaysSort?: boolean;
  /** Offer the reverse-order button next to the "Sort by" select (default true). */
  reverse?: boolean;
}

export function FilterBar<T>({ sf, label, phoneSort, alwaysSort = false, reverse = true }: FilterBarProps<T>) {
  const uid = useId();
  const active = phoneSort?.find((o) => o.key === sf.sort?.key && o.dir === sf.sort?.dir) ?? phoneSort?.find((o) => o.key === sf.sort?.key);
  return (
    <div className="fbar">
      <div className="fbar-main">
        <div className="fbar-field fbar-search">
          <label className="sr-only" htmlFor={`${uid}q`}>
            Filter {label}
          </label>
          <input id={`${uid}q`} type="search" className="input" placeholder="Filter…" value={sf.query} maxLength={200} onChange={(e) => sf.setQuery(e.target.value)} autoComplete="off" spellCheck={false} />
        </div>
        {sf.selects.map((def) => (
          <div className="fbar-field" key={def.key}>
            <label className="sr-only" htmlFor={`${uid}s${def.key}`}>
              {def.label}
            </label>
            <Select id={`${uid}s${def.key}`} value={sf.selectValue(def.key, def)} onValueChange={(v) => sf.setSelect(def.key, v)} options={def.options} />
          </div>
        ))}
        {phoneSort && (
          <div className={`fbar-field fbar-sort${alwaysSort ? " always" : ""}`}>
            <label className="sr-only" htmlFor={`${uid}o`}>
              Sort by
            </label>
            <Select
              id={`${uid}o`}
              value={active ? `${active.key}:${active.dir}` : `${phoneSort[0]?.key}:${phoneSort[0]?.dir}`}
              onValueChange={(v) => {
                const o = phoneSort.find((p) => `${p.key}:${p.dir}` === v);
                if (o) sf.setSort(o.key, o.dir);
              }}
              options={phoneSort.map((o) => ({ value: `${o.key}:${o.dir}`, label: `Sort by: ${o.label}` }))}
            />
            {reverse && (
            <button
              type="button"
              className="icon-btn"
              aria-label={sf.sort?.dir === "asc" ? "Sorted ascending. Reverse the order" : "Sorted descending. Reverse the order"}
              onClick={() => {
                const cur = sf.sort ?? { key: phoneSort[0]?.key ?? "", dir: "desc" as const };
                sf.setSort(cur.key, cur.dir === "asc" ? "desc" : "asc");
              }}
            >
              {sf.sort?.dir === "asc" ? <ArrowUp /> : <ArrowDown />}
            </button>
            )}
          </div>
        )}
      </div>
      <div className="fbar-meta">
        <span aria-live="polite">
          {sf.shown} of {sf.total}
        </span>
        {sf.isFiltered && (
          <button type="button" className="link-btn" onClick={sf.clear}>
            <X aria-hidden="true" />
            Clear filters
          </button>
        )}
      </div>
    </div>
  );
}

/** Lists this short show no filter bar. */
const SMALL_LIST = 5;

interface DataTableProps<T> {
  sf: SortFilter<T>;
  columns: readonly Column<T>[];
  rowKey: (row: T) => string | number;
  /** Accessible name of the table, also the noun in "Filter …". */
  label: string;
  /** CSS layout variant: "rows" (Overview cards), "stack" (cards on phones) or "plain". */
  variant?: "rows" | "stack" | "plain";
  className?: string;
  rowClassName?: (row: T) => string | undefined;
  rowHref?: (row: T) => string;
  /** Extra full-width row under a data row (inline delete confirmation). */
  expanded?: (row: T) => ReactNode;
  phoneSort?: readonly PhoneSortOption[];
  /** Hide the filter bar: fixed-size lists (the trend-signal metrics). */
  noFilter?: boolean;
}

export function DataTable<T>({ sf, columns, rowKey, label, variant = "plain", className, rowClassName, rowHref, expanded, phoneSort, noFilter }: DataTableProps<T>) {
  const colCount = columns.length;
  // Re-sorting live rows must not drop focus from a row link (useFrozenOrder); the header sort
  // buttons are outside the body, so a sort the user asks for always applies.
  const { ordered, paused, onFocus, onBlur, onPointerDown } = useFrozenOrder(sf.view, rowKey);
  // A filter box and a count are clutter on a handful of rows (one purchase, three scenarios).
  // A filter still in force (a saved one) always shows the bar, so it can be cleared.
  const showBar = sf.isFiltered || (!noFilter && sf.total > SMALL_LIST);
  return (
    <div className={`dt-wrap dt-${variant}${className ? ` ${className}` : ""}`}>
      {showBar && <FilterBar sf={sf} label={label} phoneSort={phoneSort} />}
      <div className="tw">
        <table className="dt" role="table" aria-label={label}>
          <thead role="rowgroup">
            <tr role="row">
              {columns.map((c) => {
                const sortable = !!c.sortKey;
                const cls = [c.className, c.align === "right" ? "r" : ""].filter(Boolean).join(" ");
                return (
                  <th key={c.key} role="columnheader" scope="col" className={cls || undefined} aria-sort={sortable && !paused ? sf.ariaSort(c.sortKey as string) : undefined}>
                    {sortable ? (
                      <button type="button" className="th-btn" onClick={() => sf.toggleSort(c.sortKey as string)}>
                        <span className={c.hideHeader ? "sr-only" : undefined}>{c.header}</span>
                        <span className="arrow" aria-hidden="true">
                          {sf.ariaSort(c.sortKey as string) === "ascending" ? <ArrowUp /> : sf.ariaSort(c.sortKey as string) === "descending" ? <ArrowDown /> : <ArrowUpDown />}
                        </span>
                      </button>
                    ) : (
                      <span className={c.hideHeader ? "sr-only" : undefined}>{c.header}</span>
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody role="rowgroup" onFocus={onFocus} onBlur={onBlur} onPointerDown={onPointerDown}>
            {ordered.map((row) => {
              const href = rowHref?.(row);
              const extra = expanded?.(row);
              const k = rowKey(row);
              return (
                <Fragment key={k}>
                  <tr role="row" className={rowClassName?.(row)}>
                    {columns.map((c) => {
                      const cls = [c.className, c.align === "right" ? "r" : ""].filter(Boolean).join(" ");
                      const content = c.cell(row);
                      return (
                        <td key={c.key} role="cell" className={cls || undefined} data-label={c.label ?? (c.hideHeader ? undefined : c.header)}>
                          {c.link && href ? (
                            <Link to={href} className="row-link">
                              {content}
                            </Link>
                          ) : (
                            content
                          )}
                        </td>
                      );
                    })}
                  </tr>
                  {extra && (
                    <tr role="row" className="dt-extra">
                      <td role="cell" colSpan={colCount}>
                        {extra}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
            {sf.shown === 0 && sf.total > 0 && (
              <tr role="row" className="dt-empty">
                <td role="cell" colSpan={colCount}>
                  No rows match.{" "}
                  <button type="button" className="link-btn" onClick={sf.clear}>
                    <X aria-hidden="true" />
                    Clear filters
                  </button>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
