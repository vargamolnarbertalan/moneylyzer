// Change cards the AI analyst suggested (server: analyzer/proposals.py; spec: docs/specs/ai-analyst-actions.md,
// Part 2). Nothing changes until the user presses Apply: the card first re-checks itself against the settings as
// they are now (a card from an old answer may be out of date), shows any new before -> after and asks again, and
// then saves through the app's own endpoints, the same ones the Settings page uses.
import { useEffect, useRef, useState } from "react";
import { Check, Sparkles } from "lucide-react";
import { endpoints, errorMessage, type ProposalCard, type ProposalChange } from "../../api";

export type CardStatus = Exclude<ProposalCard["status"], "pending">;

async function save(card: ProposalCard, payload: ProposalCard["payload"]): Promise<void> {
  switch (card.type) {
    case "rules.add":
    case "rules.remove": {
      const now = await endpoints.aiSettings();
      const rules = card.type === "rules.add" ? [...now.rules, payload.rule as string] : now.rules.filter((r) => r !== payload.rule);
      // rules_expected: refused (409) if another tab changed the list in between
      await endpoints.saveAiSettings({ rules, rules_expected: now.rules });
      return;
    }
    case "preferences.update":
      await endpoints.saveAiSettings({ preferences: payload.changes ?? {} }); // the server merges: only these keys change
      return;
    case "settings.base_currency":
      await endpoints.saveSettings(payload.currency as string);
      return;
  }
}

/** The text Adjust puts in the question box: which card, and what it proposed, so the AI knows what is meant. */
export function adjustPrefix(card: ProposalCard): string {
  const values = card.changes.map((c) => (c.after === null ? `remove "${c.before}"` : c.after)).join("; ");
  const shown = values.length > 120 ? `${values.slice(0, 119)}…` : values;
  return `About the proposed change (${card.summary}: ${shown}): `;
}

const same = (a: ProposalChange[], b: ProposalChange[]) => JSON.stringify(a) === JSON.stringify(b);

function Change({ c }: { c: ProposalChange }) {
  return (
    <li>
      <span className="prop-label">{c.label}</span>
      {c.before === null ? (
        <span className="prop-after">{c.after}</span>
      ) : c.after === null ? (
        <span>
          <span className="prop-before">{c.before}</span> <span className="prop-gone">will be removed</span>
        </span>
      ) : (
        <span>
          <span className="prop-before">{c.before}</span>
          <span aria-hidden="true"> → </span>
          <span className="sr-only"> changes to </span>
          <span className="prop-after">{c.after}</span>
        </span>
      )}
    </li>
  );
}

const DONE: Record<CardStatus, string> = { applied: "Applied", dismissed: "Dismissed", adjusted: "Adjusted in a later question" };

function Card({ card, onStatus, onAdjust }: { card: ProposalCard; onStatus: (id: string, s: CardStatus) => void; onAdjust: (card: ProposalCard) => void }) {
  const [shown, setShown] = useState(card.changes);
  const [busy, setBusy] = useState(false);
  const [changed, setChanged] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [blocked, setBlocked] = useState(false); // no longer applicable (already so, rule gone): only Adjust or Dismiss
  const headRef = useRef<HTMLDivElement | null>(null);
  const acted = useRef(false);
  const done = card.status !== "pending";

  // Apply and Dismiss remove the buttons: keep keyboard focus on this card instead of losing it to the page
  useEffect(() => {
    if (done && acted.current) {
      acted.current = false;
      headRef.current?.focus();
    }
  }, [done]);

  const apply = async () => {
    setBusy(true);
    setProblem(null);
    try {
      const now = await endpoints.proposalPreview(card.type, card.payload);
      if (!now.ok) {
        setProblem(now.reason);
        setBlocked(true);
        return;
      }
      if (!same(now.changes, shown)) {
        // out of date: show what it would do now, and let the user decide again
        setShown(now.changes);
        setChanged(true);
        return;
      }
      await save(card, now.payload);
      acted.current = true;
      onStatus(card.id, "applied");
    } catch (err) {
      setProblem(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div role="group" className={done ? `prop-card done ${card.status}` : "prop-card"} aria-label={`Suggested change: ${card.summary}`}>
      <div className="prop-head" ref={headRef} tabIndex={-1}>
        {card.status === "applied" ? <Check aria-hidden="true" /> : <Sparkles aria-hidden="true" />}
        <b>{card.summary}</b>
        {done && <span className="prop-status">{DONE[card.status as CardStatus]}</span>}
      </div>
      <ul className="prop-rows">
        {shown.map((c, i) => (
          <Change key={i} c={c} />
        ))}
      </ul>
      {!done && (
        <>
          {card.reason && <p className="prop-reason">{card.reason}</p>}
          <p className="prop-warn" role="status">
            {problem ?? (changed ? "Your settings changed since this answer. Check the values above, then apply again." : "Suggested by the AI. Check before applying.")}
          </p>
          <div className="btn-row">
            {!blocked && (
              <button type="button" className="btn small" onClick={apply} disabled={busy} aria-busy={busy}>
                {changed ? "Apply these values" : "Apply"}
              </button>
            )}
            <button type="button" className="btn ghost small" disabled={busy} onClick={() => onAdjust(card)}>
              Adjust
            </button>
            <button
              type="button"
              className="btn ghost small"
              disabled={busy}
              onClick={() => {
                acted.current = true;
                onStatus(card.id, "dismissed");
              }}
            >
              Dismiss
            </button>
          </div>
        </>
      )}
    </div>
  );
}

export function ProposalCards({ cards, notes, onStatus, onAdjust }: {
  cards: ProposalCard[];
  notes: string[];
  onStatus: (id: string, status: CardStatus) => void;
  onAdjust: (card: ProposalCard) => void;
}) {
  if (!cards.length && !notes.length) return null;
  return (
    <div className="prop-list">
      {cards.map((c) => (
        <Card key={c.id} card={c} onStatus={onStatus} onAdjust={onAdjust} />
      ))}
      {notes.map((n, i) => (
        <p key={i} className="ai-note">
          {n}
        </p>
      ))}
    </div>
  );
}
