import { Component, Suspense, lazy, useEffect, useRef, type ComponentType, type ReactNode } from "react";
import { AuthProvider, rememberReturnTo, useAuth } from "./auth";
import { Alert } from "./components/Alert";
import { Card } from "./components/Card";
import { Shell } from "./components/Shell";
import { PageSkeleton, Skeleton } from "./components/Skeleton";
import { CurrencySwitcher } from "./components/CurrencySwitcher";
import { DisclaimerGate } from "./components/DisclaimerGate";
import { LoginPage } from "./pages/Login";
import { OverviewPage } from "./pages/Overview";
import { useTitle } from "./hooks";
import { Link, matchPath, navigate, usePath } from "./router";

/** A page loaded on first use, so the first screen does not wait for every page's code.
 *  `preload` fetches it ahead of time (see AuthedApp). Once the code is in, the page renders
 *  directly: React.lazy alone would still suspend (and flash the skeleton) on the first visit
 *  of a page whose code was preloaded long ago. */
function page<P extends object>(load: () => Promise<ComponentType<P>>) {
  let Loaded: ComponentType<P> | null = null;
  let pending: Promise<ComponentType<P>> | null = null;
  const preload = () =>
    (pending ??= load().then(
      (C) => (Loaded = C),
      (err: unknown) => {
        pending = null; // forget the failure: the next attempt asks again
        throw err;
      },
    ));
  // React.lazy keeps a rejection for good. The failure must reach the error card first, so the
  // lazy is replaced only on the next navigation (retryIfFailed, called by AuthedApp), never in
  // the rejection itself: that would make React retry at once, in a loop. (The browser may also
  // refuse to re-fetch a module that failed; the card's Reload is the dependable way back.)
  let failed = false;
  const makeLazy = () =>
    lazy(() =>
      preload().then(
        (C) => ({ default: C }),
        (err: unknown) => {
          failed = true;
          throw err;
        },
      ),
    );
  let Lazy = makeLazy();
  const retryIfFailed = () => {
    if (!failed) return;
    failed = false;
    Lazy = makeLazy();
  };
  function Page(props: P) {
    return Loaded ? <Loaded {...props} /> : <Lazy {...props} />;
  }
  return Object.assign(Page, { preload, retryIfFailed });
}

/** Errors from a page whose code did not arrive, as Chrome/Edge, Firefox and Safari word them. */
const CHUNK_ERROR = /dynamically imported module|module script failed|error loading dynamically imported/i;

const AdvisorPage = page(() => import("./pages/Advisor").then((m) => m.AdvisorPage));
const HoldingPage = page(() => import("./pages/Holding").then((m) => m.HoldingPage));
const HoldingsPage = page(() => import("./pages/Holdings").then((m) => m.HoldingsPage));
const ImportPage = page(() => import("./pages/Import").then((m) => m.ImportPage));
const MarketsPage = page(() => import("./pages/Markets").then((m) => m.MarketsPage));
const PrivacyPage = page(() => import("./pages/Privacy").then((m) => m.PrivacyPage));
const SettingsPage = page(() => import("./pages/Settings").then((m) => m.SettingsPage));
/** In the order they are preloaded: the likeliest next click from the Overview first. */
const LAZY_PAGES = [HoldingPage, HoldingsPage, AdvisorPage, MarketsPage, SettingsPage, PrivacyPage, ImportPage];

/** A page whose code could not be loaded (offline, or the server was updated and the old
 *  files are gone) gets a reload button instead of taking the whole app down. Keyed by path
 *  in AuthedApp, so moving to another page starts fresh. */
class PageBoundary extends Component<{ children: ReactNode }, { failed: "load" | "render" | null }> {
  state: { failed: "load" | "render" | null } = { failed: null };
  static getDerivedStateFromError(err: unknown) {
    return { failed: err instanceof Error && CHUNK_ERROR.test(err.message) ? "load" : "render" };
  }
  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <Card>
        <Alert tone="err">
          {this.state.failed === "load"
            ? "This page could not be loaded. If you are offline or the app was just updated, reloading fixes it."
            : "Something went wrong while showing this page. Reloading may fix it."}
        </Alert>
        <div className="btn-row">
          <button type="button" className="btn" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      </Card>
    );
  }
}

function NotFound() {
  useTitle("Not found");
  return (
    <Card>
      <div className="empty">
        <b>Page not found</b>
        There is nothing at this address.
        <div className="btn-row">
          <Link className="btn" to="/">
            Back to the overview
          </Link>
        </div>
      </div>
    </Card>
  );
}

interface Routed {
  title: string;
  element: React.ReactNode;
  headerExtra?: React.ReactNode;
}

function route(path: string): Routed {
  if (path === "/") return { title: "Overview", element: <OverviewPage />, headerExtra: <CurrencySwitcher /> };
  if (path === "/analyst") return { title: "AI analyst", element: <AdvisorPage /> };
  if (path === "/holdings") return { title: "Holdings", element: <HoldingsPage /> };
  if (path === "/markets") return { title: "Markets", element: <MarketsPage /> };
  if (path === "/privacy") return { title: "Data & privacy", element: <PrivacyPage /> };
  if (path === "/import") return { title: "Import", element: <ImportPage /> };
  if (path === "/settings") return { title: "Settings", element: <SettingsPage /> };
  const holding = matchPath("/holding/:id", path);
  if (holding) return { title: "Holding", element: <HoldingPage key={holding.id} id={holding.id as string} /> };
  return { title: "Not found", element: <NotFound /> };
}

function AuthedApp({ path }: { path: string }) {
  // A navigation gives a page whose code failed to load one fresh attempt (before it renders).
  const retriedFor = useRef<string | null>(null);
  if (retriedFor.current !== path) {
    retriedFor.current = path;
    LAZY_PAGES.forEach((p) => p.retryIfFailed());
  }
  const routed = route(path);
  const lastPath = useRef(path);
  // After a client-side navigation, move keyboard/screen-reader focus to the new page's content.
  // (Compared by path, not a first-run flag, so StrictMode's double effect cannot fake a navigation.)
  useEffect(() => {
    if (lastPath.current === path) return;
    lastPath.current = path;
    document.getElementById("main")?.focus({ preventScroll: true });
  }, [path]);
  // Once the first page is up, fetch the other pages' code in the background, one at a time so
  // the current page's own requests are never queued behind them. Skipped when the user asked
  // to save data or the link is slow: then each page loads when it is opened.
  useEffect(() => {
    const conn = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection;
    if (conn?.saveData || /(^|-)2g$|^3g$/.test(conn?.effectiveType ?? "")) return;
    // Each chunk waits for idle time of its own, so parsing one never lands mid-animation.
    let cancel = () => {};
    const idle = (fn: () => void) => {
      if (typeof window.requestIdleCallback === "function") {
        const id = window.requestIdleCallback(fn, { timeout: 4000 });
        cancel = () => window.cancelIdleCallback(id);
      } else {
        const timer = window.setTimeout(fn, 1500);
        cancel = () => window.clearTimeout(timer);
      }
    };
    let stopped = false;
    const next = (i: number) => {
      const p = LAZY_PAGES[i];
      if (stopped || !p) return;
      idle(() => void p.preload().catch(() => undefined).finally(() => next(i + 1)));
    };
    next(0);
    return () => {
      stopped = true;
      cancel();
    };
  }, []);
  return (
    <Shell path={path} title={routed.title} headerExtra={routed.headerExtra}>
      <PageBoundary key={path}>
        <Suspense fallback={<PageSkeleton />}>{routed.element}</Suspense>
      </PageBoundary>
    </Shell>
  );
}

function Gate() {
  const path = usePath();
  const auth = useAuth();
  const status = auth.status;

  // Signed out: everything leads to /login. Signed in: /login leads home.
  useEffect(() => {
    if (status.kind === "out" && path !== "/login") {
      rememberReturnTo(path);
      navigate("/login", { replace: true });
    } else if (status.kind === "in" && path === "/login") {
      navigate("/", { replace: true });
    } else if (path === "/advisor") {
      navigate(`/analyst${window.location.search}`, { replace: true }); // the page's name until 2026-11
    }
  }, [status.kind, path]);

  if (status.kind === "loading") {
    return (
      <div className="auth" role="status" aria-label="Loading">
        <Skeleton w={220} h={24} />
        <span className="sr-only">Loading…</span>
      </div>
    );
  }
  if (status.kind === "error") {
    return (
      <main className="auth">
        <div className="card form">
          <Alert tone="err">Cannot reach the dashboard server: {status.message}</Alert>
          <button type="button" className="btn" onClick={auth.retry}>
            Try again
          </button>
        </div>
      </main>
    );
  }
  if (status.kind === "out") return <LoginPage setupRequired={status.setupRequired} />;
  if (path === "/login" || path === "/advisor") return null;
  return (
    <DisclaimerGate>
      <AuthedApp path={path} />
    </DisclaimerGate>
  );
}

export function App() {
  return (
    <AuthProvider>
      <Gate />
    </AuthProvider>
  );
}
