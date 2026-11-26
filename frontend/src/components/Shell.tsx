import { useState, type ReactNode } from "react";
import { useHeaderOverride } from "../pageTitle";
import { ChartLine, FileUp, Gauge, LayoutDashboard, PanelLeftClose, PanelLeftOpen, RefreshCw, Settings, ShieldCheck, Sparkles, Wallet, type LucideIcon } from "lucide-react";
import { errorMessage } from "../api";
import { clockTime, isNum, price } from "../format";
import { useMediaQuery } from "../hooks";
import { usePref } from "../prefs";
import { Brand } from "./Brand";
import { refreshNow, useLive } from "../live";
import { useMeta } from "../meta";
import { Link } from "../router";
import { CoffeeButton } from "./Coffee";
import { UserMenu } from "./UserMenu";

interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  group: NavGroup;
}

type NavGroup = "Portfolio" | "Insights" | "Account";
const GROUPS: readonly NavGroup[] = ["Portfolio", "Insights", "Account"];

/** The sidebar, in labelled groups (the order within a group is the order shown). */
const NAV: readonly NavItem[] = [
  { to: "/", label: "Overview", icon: LayoutDashboard, group: "Portfolio" },
  { to: "/holdings", label: "Holdings", icon: Wallet, group: "Portfolio" },
  { to: "/import", label: "Import", icon: FileUp, group: "Portfolio" },
  { to: "/analyst", label: "AI analyst", icon: Sparkles, group: "Insights" },
  { to: "/markets", label: "Markets", icon: ChartLine, group: "Insights" },
  { to: "/risk", label: "Risk", icon: Gauge, group: "Insights" },
  { to: "/privacy", label: "Data & privacy", icon: ShieldCheck, group: "Account" },
  { to: "/settings", label: "Settings", icon: Settings, group: "Account" },
];
/** The phone tab bar: five fit, in this order; Import, Data & privacy and Risk are reached from Settings. */
const TABS = ["/", "/analyst", "/holdings", "/markets", "/settings"];

/** Below this width the sidebar is always the icon rail (and below 760px it becomes the bottom tab bar). */
const NARROW = "(max-width: 1100px)";

/** Which nav item is "here": exact for pages, the Holdings item for a holding's detail page. */
function navState(item: NavItem, path: string): "page" | "true" | undefined {
  if (item.to === path) return "page";
  if (item.to === "/holdings" && path.startsWith("/holding/")) return "true";
  return undefined;
}

function NavLink({ item, path }: { item: NavItem; path: string }) {
  const NavIcon = item.icon;
  return (
    <Link to={item.to} aria-current={navState(item, path)} aria-label={item.label} title={item.label}>
      <NavIcon />
      <span>{item.label}</span>
    </Link>
  );
}

/** Sidebar: groups with a label (in the icon rail the label gives way to a thin divider). */
function NavLinks({ path }: { path: string }) {
  return (
    <>
      {GROUPS.map((g) => (
        <div key={g} className="nav-group" role="group" aria-label={g}>
          <span className="nav-label" aria-hidden="true">
            {g}
          </span>
          {NAV.filter((item) => item.group === g).map((item) => (
            <NavLink key={item.to} item={item} path={path} />
          ))}
        </div>
      ))}
    </>
  );
}

function TabLinks({ path }: { path: string }) {
  return (
    <>
      {TABS.map((to) => NAV.find((item) => item.to === to)).map((item) => (item ? <NavLink key={item.to} item={item} path={path} /> : null))}
    </>
  );
}

function LivePill() {
  const live = useLive();
  const snap = live.snapshot;
  let cls = "live";
  let text: string;
  if (live.phase === "reconnecting") {
    cls += " warn";
    text = "Reconnecting…";
  } else if (live.phase === "connecting" && !snap) {
    cls += " idle";
    text = "Connecting…";
  } else if (live.phase === "connecting") {
    cls += " idle";
    text = `Updated ${clockTime(snap?.updated_at)}`;
  } else {
    text = `Live · updated ${clockTime(snap?.updated_at)}`;
  }
  return (
    <span className={cls}>
      {/* Re-keyed per update: the dot pulses a few times for each new snapshot, then rests. */}
      <b key={snap?.updated_at ?? ""} aria-hidden="true" />
      <span>{text}</span>
    </span>
  );
}

function RefreshButton() {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const click = async () => {
    setBusy(true);
    setProblem(null);
    try {
      await refreshNow();
    } catch (err) {
      setProblem(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button
        type="button"
        className={busy ? "icon-btn spinning" : "icon-btn"}
        onClick={click}
        disabled={busy}
        aria-label={busy ? "Refreshing prices…" : "Refresh prices now"}
        title={busy ? "Refreshing prices…" : "Refresh prices now"}
      >
        <RefreshCw />
      </button>
      <span className="sr-only" role="status">
        {busy ? "Refreshing prices…" : ""}
      </span>
      {problem && <span className="sr-only" role="alert">{problem}</span>}
    </>
  );
}

/** Up to three rate chips (<CCY>/<base>) for the currencies the portfolio is most exposed to. */
function FxChips() {
  const { snapshot } = useLive();
  if (!snapshot) return null;
  const base = snapshot.base_currency;
  const exposure = new Map<string, number>();
  for (const r of snapshot.rows) exposure.set(r.currency, (exposure.get(r.currency) ?? 0) + (isNum(r.value_base) ? Math.abs(r.value_base) : 0));
  const entries = Object.entries(snapshot.fx ?? {})
    .filter((e): e is [string, number] => isNum(e[1]))
    .sort(([a], [b]) => (exposure.get(b) ?? 0) - (exposure.get(a) ?? 0) || a.localeCompare(b))
    .slice(0, 3);
  return (
    <>
      {entries.map(([ccy, rate]) => (
        <span className="fx" key={ccy}>
          <span>
            {ccy}/{base}
          </span>{" "}
          <strong>{price(rate)}</strong>
        </span>
      ))}
    </>
  );
}

interface ShellProps {
  path: string;
  title: string;
  /** Controls shown in the page header next to the live pill (the currency switcher). */
  headerExtra?: ReactNode;
  children: ReactNode;
}

export function Shell({ path, title, headerExtra, children }: ShellProps) {
  const headerOverride = useHeaderOverride();
  const meta = useMeta();
  const narrow = useMediaQuery(NARROW);
  const [sidebar, setSidebar] = usePref<"open" | "collapsed">("sidebar", ["open", "collapsed"], "open");
  const collapsed = sidebar === "collapsed";
  const rail = narrow || collapsed;
  return (
    <div className={rail ? "app rail" : "app"}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="side" id="sidebar" aria-label="Sidebar">
        <div className="side-head">
          <Brand />
          {/* The toggle only exists where it changes something: on narrow screens the rail is fixed. */}
          {!narrow && (
            <button
              type="button"
              className="icon-btn side-toggle"
              aria-expanded={!collapsed}
              aria-controls="sidebar"
              aria-label="Sidebar"
              title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              onClick={() => setSidebar(collapsed ? "open" : "collapsed")}
            >
              {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
            </button>
          )}
        </div>
        <nav className="nav" aria-label="Main navigation">
          <NavLinks path={path} />
        </nav>
        <div className="foot">
          {meta?.donateUrl && <CoffeeButton url={meta.donateUrl} />}
          <div className="foot-row">
            <UserMenu />
            {meta?.version && <span className="version">v{meta.version}</span>}
          </div>
        </div>
      </aside>
      <div className="main">
        <header className="top">
          <h1>{headerOverride ?? title}</h1>
          {headerExtra}
          <div className="top-account">
            <UserMenu placement="header" />
          </div>
          {/* One unit: when the header wraps on a phone they move to the next row together. */}
          <div className="top-live">
            <LivePill />
            <RefreshButton />
          </div>
          <FxChips />
        </header>
        <main id="main" className="screen" tabIndex={-1}>
          {children}
        </main>
        <nav className="tabbar" aria-label="Main navigation">
          <TabLinks path={path} />
        </nav>
      </div>
    </div>
  );
}
