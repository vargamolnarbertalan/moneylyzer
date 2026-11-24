import { useEffect, useState } from "react";
import { ArrowDownLeft, ArrowUpRight, ShieldAlert, ShieldCheck } from "lucide-react";
import { FULL_DETAIL_ITEMS, endpoints, errorMessage, type DataLevel, type Provider } from "../api";
import { Alert } from "../components/Alert";
import { Card, CardHeader } from "../components/Card";
import { BlockSkeleton } from "../components/Skeleton";
import { useResource, useTitle } from "../hooks";

type Linkable = "no" | "ip" | "account";

interface Flow {
  name: string;
  sends: string;
  gets: string;
  /** Can the receiver tie what it gets to you? */
  linkable: Linkable;
  linkNote: string;
  /** What it reveals about you, if anything. */
  sensitive: string | null;
}

// Every outside service the server talks to. Keep in step with server/live.py, analyzer/research.py,
// analyzer/market.py, analyzer/advisor.py and analyzer/claude_install.py.
// Every request leaves from this server's internet address (IP): for a home server that is your home
// connection, which your internet provider can tie to you, so "ip" means "indirectly linkable".
const FLOWS: readonly Flow[] = [
  {
    name: "Yahoo Finance — prices",
    sends: "the ticker symbols of your holdings, every 60 seconds (daily history once a day).",
    gets: "live quotes and daily closing prices.",
    linkable: "ip",
    linkNote: "No name or account; Yahoo sees the server's IP address with the tickers.",
    sensitive: "Which listed instruments you hold (not how many).",
  },
  {
    name: "Yahoo Finance — exchange rates",
    sends: "currency pairs such as USDEUR=X for the currencies you hold and report in, every 60 seconds.",
    gets: "exchange rates.",
    linkable: "ip",
    linkNote: "No name or account; the server's IP address.",
    sensitive: "Which currencies you are exposed to (low).",
  },
  {
    name: "Yahoo Finance — news",
    sends: "the ticker symbols of your individual stocks (not ETFs or funds), hourly.",
    gets: "recent headlines about those companies.",
    linkable: "ip",
    linkNote: "No name or account; the server's IP address.",
    sensitive: "Which companies you hold shares in.",
  },
  {
    name: "Yahoo Finance — search",
    sends: "what you type in the add-holding search (a name, ticker or ISIN), only while you search.",
    gets: "matching instruments with their tickers and exchanges.",
    linkable: "ip",
    linkNote: "No name or account; the server's IP address.",
    sensitive: "What you are considering buying.",
  },
  {
    name: "OTP Alapkezelő / BAMOSZ",
    sends: "the ISINs of your Hungarian funds, hourly.",
    gets: "fund prices (published 1–2 business days late).",
    linkable: "ip",
    linkNote: "No name or account; the server's IP address.",
    sensitive: "Which Hungarian funds you hold.",
  },
  {
    name: "Central banks, statistics offices, benchmarks",
    sends: "nothing about you — public news feed addresses (hourly) and benchmark index symbols (daily).",
    gets: "official news and index levels for market context.",
    linkable: "ip",
    linkNote: "Only the server's IP address, with requests every installation makes.",
    sensitive: null,
  },
  {
    name: "AI model (only when you ask)",
    sends: "your question, your AI analyst rules, persona and investing preferences (including your country, if set), and portfolio weights and % changes; with full detail also your " + FULL_DETAIL_ITEMS + ".",
    gets: "the answer and the sources it used.",
    linkable: "account",
    linkNote: "Yes: the provider knows the account you connected (Claude login or API key) and can link every question to it. A local model stays on your network.",
    sensitive: "Your portfolio's composition and your goals; with full detail also your amounts, purchase history and where you hold what (financial data).",
  },
  {
    name: "npm (once)",
    sends: "a download request for the pinned Claude Code version, the first time you connect Claude.",
    gets: "the Claude Code program, checked against npm's integrity hash.",
    linkable: "ip",
    linkNote: "The server's IP address, once.",
    sensitive: null,
  },
];

const LINK_LABEL: Record<Linkable, string> = { no: "Not linkable", ip: "Indirectly (IP address)", account: "Yes (your account)" };

export function PrivacyPage() {
  useTitle("Data & privacy");
  return (
    <div className="stack">
      <AiSharing />
      <Card as="section" aria-labelledby="flows-h">
        <CardHeader id="flows-h" title="What leaves this server" />
        <p className="sub">
          To fetch prices, news and answers, the server sends requests out. For each service: what goes out, what comes back, whether it can be tied to
          you, and what it reveals. Everything else — holdings, purchases, amounts, the database and your account — stays on this server.
        </p>
        <ul className="flow-list">
          {FLOWS.map((f) => (
            <li key={f.name}>
              <div className="flow-head">
                <b>{f.name}</b>
                <span className={f.linkable === "account" ? "badge amber" : "badge"}>{LINK_LABEL[f.linkable]}</span>
              </div>
              <span className="flow">
                <ArrowUpRight aria-hidden="true" />
                <span>
                  <span className="flow-k">Sends:</span> {f.sends}
                </span>
              </span>
              <span className="flow">
                <ArrowDownLeft aria-hidden="true" />
                <span>
                  <span className="flow-k">Gets back:</span> {f.gets}
                </span>
              </span>
              <span className="flow">
                {f.linkable === "account" ? <ShieldAlert aria-hidden="true" /> : <ShieldCheck aria-hidden="true" />}
                <span>
                  <span className="flow-k">Tied to you:</span> {f.linkNote}
                  {f.sensitive ? (
                    <>
                      {" "}
                      <span className="flow-k">Reveals:</span> {f.sensitive}
                    </>
                  ) : (
                    " Reveals nothing about your portfolio."
                  )}
                </span>
              </span>
            </li>
          ))}
        </ul>
        <p className="note">Links you open yourself (GitHub, the donation page, news articles) are opened by your browser, not by the server.</p>
      </Card>
    </div>
  );
}

/** Per AI model: what it may see. "Full detail" needs an explicit confirmation (also enforced by the server). */
function AiSharing() {
  const providers = useResource(() => endpoints.aiProviders(), []);
  return (
    <Card as="section" id="ai" aria-labelledby="ai-share-h">
      <CardHeader id="ai-share-h" title="What the AI analyst may see" />
      <p className="sub">
        Set per connected model. Relative figures are weights and percentage changes only. Full detail adds your {FULL_DETAIL_ITEMS}.
        A local model keeps everything on your network.
      </p>
      {providers.loading && !providers.data ? (
        <BlockSkeleton rows={2} />
      ) : providers.error && !providers.data ? (
        <Alert tone="err">Could not load your models: {providers.error}</Alert>
      ) : (
        <ul className="share-list">
          {(providers.data ?? []).map((p) => (
            <ShareRow key={p.id} p={p} onChanged={providers.reload} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function ShareRow({ p, onChanged }: { p: Provider; onChanged: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The level the server just confirmed, shown until the reloaded list says the same: without it
  // the row would flip back to the old level for a moment and invite a second confirmation.
  const [confirmed, setConfirmed] = useState<DataLevel | null>(null);
  useEffect(() => setConfirmed(null), [p.data_level]);

  const apply = async (level: DataLevel) => {
    setBusy(true);
    setError(null);
    try {
      await endpoints.setDataLevel(p.id, level, level === "full");
      setConfirmed(level);
      setConfirming(false);
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const full = (confirmed ?? p.data_level) === "full";
  return (
    <li className={full ? "share-row full" : "share-row"}>
      <span className="sr-only" role="status">
        {busy ? "Saving…" : ""}
      </span>
      <div className="share-head">
        <b>{p.name}</b>
        <span className="muted">{p.model}</span>
        {p.local && <span className="badge">Local</span>}
        <span className={full ? "badge amber" : "badge"}>{full ? "Full detail" : "Relative figures"}</span>
      </div>
      {full ? (
        <div className="btn-row">
          <span className="sub">This model receives your {FULL_DETAIL_ITEMS}.</span>
          <button type="button" className="btn ghost small" disabled={busy} onClick={() => void apply("relative")}>
            {busy && <span className="spinner" aria-hidden="true" />}
            {busy ? "Switching…" : "Switch to relative figures"}
          </button>
        </div>
      ) : confirming ? (
        <div className="share-confirm">
          <Alert tone="warn">
            Full detail sends your {FULL_DETAIL_ITEMS} to {p.name}'s provider, linked to your account there.
          </Alert>
          <div className="btn-row">
            <button type="button" className="btn small" disabled={busy} onClick={() => void apply("full")}>
              {busy && <span className="spinner" aria-hidden="true" />}
              {busy ? "Saving…" : "Share full detail"}
            </button>
            <button type="button" className="btn ghost small" disabled={busy} onClick={() => setConfirming(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="btn-row">
          {/* a local model keeps everything on your network: no warning to confirm */}
          <button type="button" className="btn ghost small" disabled={busy} onClick={() => (p.local ? void apply("full") : setConfirming(true))}>
            {busy && <span className="spinner" aria-hidden="true" />}
            {p.local ? "Allow full detail" : "Allow full detail…"}
          </button>
        </div>
      )}
      {error && <Alert tone="err">{error}</Alert>}
    </li>
  );
}
