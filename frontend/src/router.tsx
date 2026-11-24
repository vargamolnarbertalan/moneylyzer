// A tiny client-side router on history.pushState + popstate. No library.
import { useSyncExternalStore, type AnchorHTMLAttributes, type MouseEvent, type ReactNode } from "react";

const listeners = new Set<() => void>();

function emit(): void {
  listeners.forEach((fn) => fn());
}

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  window.addEventListener("popstate", fn);
  return () => {
    listeners.delete(fn);
    window.removeEventListener("popstate", fn);
  };
}

export function normalizePath(p: string): string {
  const clean = p.length > 1 ? p.replace(/\/+$/, "") : p;
  return clean || "/";
}

function currentPath(): string {
  return normalizePath(window.location.pathname);
}

function currentFull(): string {
  return currentPath() + window.location.search + window.location.hash;
}

/** The current pathname, re-rendering on navigation and on back/forward. */
export function usePath(): string {
  return useSyncExternalStore(subscribe, currentPath, () => "/");
}

/** The current query string, e.g. "?holding=T1". */
export function useSearch(): string {
  return useSyncExternalStore(subscribe, () => window.location.search, () => "");
}

/** The current hash, e.g. "#ai". */
export function useHash(): string {
  return useSyncExternalStore(subscribe, () => window.location.hash, () => "");
}

/** Navigate to "/path", "/path?query" or "/path#hash". */
export function navigate(to: string, opts: { replace?: boolean } = {}): void {
  const url = new URL(to, window.location.origin);
  const target = normalizePath(url.pathname) + url.search + url.hash;
  if (target === currentFull()) {
    if (!opts.replace && !url.hash) window.scrollTo({ top: 0 });
    return;
  }
  if (opts.replace) window.history.replaceState(null, "", target);
  else window.history.pushState(null, "", target);
  if (!url.hash) window.scrollTo({ top: 0 });
  emit();
}

/** Match "/holding/:id" style patterns. Returns the params or null. */
export function matchPath(pattern: string, path: string): Record<string, string> | null {
  const a = pattern.split("/").filter(Boolean);
  const b = path.split("/").filter(Boolean);
  if (a.length !== b.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < a.length; i++) {
    const seg = a[i] as string;
    const val = b[i] as string;
    if (seg.startsWith(":")) {
      try {
        params[seg.slice(1)] = decodeURIComponent(val);
      } catch {
        return null;
      }
    } else if (seg !== val) {
      return null;
    }
  }
  return params;
}

interface LinkProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href"> {
  to: string;
  children?: ReactNode;
}

/** A real <a href>: middle-click, new tab and copy-link keep working; a plain click navigates in place. */
export function Link({ to, onClick, children, ...rest }: LinkProps) {
  const handle = (e: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(e);
    if (e.defaultPrevented) return;
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    if (rest.target && rest.target !== "_self") return;
    e.preventDefault();
    navigate(to);
  };
  return (
    <a href={to} onClick={handle} {...rest}>
      {children}
    </a>
  );
}
