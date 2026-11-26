import { useId, useMemo } from "react";
import { ExternalLink } from "lucide-react";
import { endpoints, type MarketItem, type NewsItem } from "../api";
import { relativeTime, safeUrl } from "../advisor";
import { isNum, pct, price, signedPp } from "../format";
import { useResource } from "../hooks";
import { useSortFilter } from "../useSortFilter";
import { Alert } from "./Alert";
import { Card, CardHeader } from "./Card";
import { DataTable, FilterBar, type Column, type PhoneSortOption } from "./DataTable";
import { Labelled } from "./Explain";
import type { TermId } from "../glossary";
import { BlockSkeleton } from "./Skeleton";

function Change({ item, value }: { item: MarketItem; value: number | null }) {
  if (!isNum(value)) return <span className="pill none">—</span>;
  if (item.change_unit === "pp") return <span className={`pill ${value >= 0 ? "up" : "down"}`}>{signedPp(value)}</span>;
  return <span className={`pill ${value >= 0 ? "up" : "down"}`}>{pct(value)}</span>;
}

/** Benchmarks an everyday reader is least likely to know (by symbol, as stored in analyzer/research.py). */
const BENCHMARK_TERMS: Record<string, TermId | undefined> = { "IWDA.AS": "msciWorld", "^VIX": "vix", "^TNX": "yield10y" };

const MARKET_COLUMNS: readonly Column<MarketItem>[] = [
  {
    key: "name",
    header: "Name",
    sortKey: "name",
    cell: (m) => {
      const term = BENCHMARK_TERMS[m.symbol];
      return (
        <span>
          {term ? <Labelled text={m.name} term={term} /> : m.name}
          <small className="cell-sub muted">{m.symbol}</small>
        </span>
      );
    },
  },
  { key: "last", header: "Last", sortKey: "last", align: "right", cell: (m) => (isNum(m.last) ? price(m.last) : "—") },
  { key: "w1", header: "1W", sortKey: "w1", align: "right", cell: (m) => <Change item={m} value={m.change_1w} /> },
  { key: "m1", header: "1M", sortKey: "m1", align: "right", cell: (m) => <Change item={m} value={m.change_1m} /> },
  { key: "m3", header: "3M", sortKey: "m3", align: "right", cell: (m) => <Change item={m} value={m.change_3m} /> },
];

const KIND_LABEL: Record<string, string> = { index: "Indices", commodity: "Commodities", fx: "Currencies", crypto: "Crypto", yield: "Yields", rate: "Rates" };

function MarketCard({ market }: { market: MarketItem[] }) {
  const kinds = useMemo(() => Array.from(new Set(market.map((m) => m.kind))).sort(), [market]);
  const sf = useSortFilter<MarketItem>({
    id: "market",
    rows: market,
    fields: {
      name: { type: "text", value: (m) => m.name },
      last: { type: "number", value: (m) => m.last },
      w1: { type: "number", value: (m) => m.change_1w },
      m1: { type: "number", value: (m) => m.change_1m },
      m3: { type: "number", value: (m) => m.change_3m },
    },
    search: (m) => `${m.name} ${m.symbol} ${m.kind} ${KIND_LABEL[m.kind] ?? ""}`,
    selects:
      kinds.length > 1
        ? [
            {
              key: "kind",
              label: "Kind",
              options: [{ value: "all", label: "All markets" }, ...kinds.map((k) => ({ value: k, label: KIND_LABEL[k] ?? k }))],
              test: (m, v) => v === "all" || m.kind === v,
            },
          ]
        : [],
  });
  return (
    <Card as="section" aria-labelledby="market-h">
      <CardHeader id="market-h" title="Benchmarks" term="benchmarks" />
      {market.length === 0 ? (
        <div className="empty">
          <b>No market data yet</b>
          Benchmarks fill in shortly after the app starts.
        </div>
      ) : (
        <>
          <DataTable sf={sf} columns={MARKET_COLUMNS} rowKey={(m) => m.symbol} label="markets" variant="plain" className="dt-market" />
          <p className="note">1W, 1M and 3M are price changes; yields show the change in percentage points (pp).</p>
        </>
      )}
    </Card>
  );
}

const NEWS_SORT: readonly PhoneSortOption[] = [
  { key: "published", label: "Newest first", dir: "desc" },
  { key: "published", label: "Oldest first", dir: "asc" },
  { key: "source", label: "Source A–Z", dir: "asc" },
  { key: "title", label: "Headline A–Z", dir: "asc" },
];

function NewsCard({ news, feeds }: { news: NewsItem[]; feeds: { name: string; url: string }[] }) {
  const uid = useId();
  const sf = useSortFilter<NewsItem>({
    id: "news",
    rows: news,
    fields: {
      published: { type: "number", value: (n) => (n.published ? Date.parse(n.published) : null) },
      source: { type: "text", value: (n) => n.source },
      title: { type: "text", value: (n) => n.title },
    },
    search: (n) => [n.title, n.source, n.summary, n.tickers].filter(Boolean).join(" "),
    defaultSort: { key: "published", dir: "desc" },
  });
  return (
    <Card as="section" aria-labelledby="news-h">
      <CardHeader id="news-h" title="News" aside={<span className="tag">last days</span>} />
      {news.length === 0 ? (
        <div className="empty">
          <b>No news yet</b>
          News arrives within an hour of starting the app.
        </div>
      ) : (
        <>
          <FilterBar sf={sf} label="news" phoneSort={NEWS_SORT} alwaysSort reverse={false} />
          <ul className="news" aria-label="News">
            {sf.view.map((n, i) => {
              const href = safeUrl(n.url);
              return (
                <li key={`${n.url}-${i}`} className="news-item">
                  {/* The whole item is the click target (no inline text link); the title names it. */}
                  {href ? (
                    <a
                      href={href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="news-card"
                      aria-labelledby={`${uid}t${i}`}
                      aria-describedby={`${uid}m${i}`}
                    >
                      <span className="news-title" id={`${uid}t${i}`}>
                        {n.title}
                        <ExternalLink aria-hidden="true" />
                        <span className="sr-only"> (opens in a new tab)</span>
                      </span>
                      <span id={`${uid}m${i}`}>
                        <span className="news-meta">{[n.source, relativeTime(n.published), n.tickers].filter(Boolean).join(" · ")}</span>
                        {n.summary && <span className="news-sum">{n.summary}</span>}
                      </span>
                    </a>
                  ) : (
                    <div className="news-card">
                      <span className="news-title">{n.title}</span>
                      <span className="news-meta">{[n.source, relativeTime(n.published), n.tickers].filter(Boolean).join(" · ")}</span>
                      {n.summary && <span className="news-sum">{n.summary}</span>}
                    </div>
                  )}
                </li>
              );
            })}
            {sf.shown === 0 && (
              <li className="news-empty">
                No rows match.{" "}
                <button type="button" className="link-btn" onClick={sf.clear}>
                  Clear filters
                </button>
              </li>
            )}
          </ul>
        </>
      )}
      {feeds.length > 0 && (
        <details className="feeds">
          <summary>Sources ({feeds.length})</summary>
          <ul className="chip-list">
            {feeds.map((f) => {
              const href = safeUrl(f.url);
              return (
                <li key={f.url}>
                  {href ? (
                    <a href={href} target="_blank" rel="noopener noreferrer" className="chip-link">
                      {f.name}
                      <ExternalLink aria-hidden="true" />
                      <span className="sr-only"> (opens in a new tab)</span>
                    </a>
                  ) : (
                    <span className="chip-link static">{f.name}</span>
                  )}
                </li>
              );
            })}
          </ul>
        </details>
      )}
    </Card>
  );
}

/** The right-hand column of the advisor page: market benchmarks and recent news from GET /api/research. */
export function ResearchPanel() {
  const r = useResource(() => endpoints.research(), []);
  if (r.loading && !r.data) {
    return (
      <div className="stack">
        <BlockSkeleton rows={4} />
        <BlockSkeleton rows={3} />
      </div>
    );
  }
  if (!r.data) return <Alert tone="err">Could not load markets and news: {r.error ?? "unknown error"}</Alert>;
  return (
    <div className="stack">
      <MarketCard market={r.data.market} />
      <NewsCard news={r.data.news} feeds={r.data.feeds} />
    </div>
  );
}

