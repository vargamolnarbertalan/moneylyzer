// Change cards the AI analyst suggested (server: analyzer/proposals.py; spec: docs/specs/ai-analyst-actions.md,
// Part 2). Nothing changes until the user presses Apply: the card first re-checks itself against the settings,
// holdings and transactions as they are now (a card from an old answer may be out of date), shows any new
// before -> after and asks again, and then saves through the app's own endpoints, the ones the rest of the app uses.
// Deletes, and changes that lose or overwrite something (the server's `confirm`), take a second, explicit press and
// are never part of "Apply all".
import { useCallback, useEffect, useRef, useState } from "react";
import { Check, Sparkles, Trash2 } from "lucide-react";
import { localISODate } from "../../format";
import { endpoints, errorMessage, type InstrumentInput, type LotInput, type ProposalApply, type ProposalCard, type ProposalChange } from "../../api";

export type CardStatus = Exclude<ProposalCard["status"], "pending">;

const DELETES: ProposalCard["type"][] = ["rules.remove", "holding.delete", "purchase.delete"];
const isDelete = (card: ProposalCard) => DELETES.includes(card.type);

async function save(card: ProposalCard, payload: ProposalCard["payload"], apply: ProposalApply | null): Promise<void> {
  const body = apply ?? {};
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
    case "holding.add":
      await endpoints.createInstrument({ notes: null, ...body } as InstrumentInput); // 409 if the id was taken meanwhile
      return;
    case "holding.update":
      await endpoints.saveInstrument(body as InstrumentInput); // 404 if the holding was deleted meanwhile
      return;
    case "purchase.add":
      await endpoints.addLot({ notes: null, ...body } as LotInput);
      return;
    case "holding.delete":
      await endpoints.deleteInstrument(body.instrument_id as string, body.confirm_purchases ?? 0);
      return;
    case "purchase.delete":
      await endpoints.deleteLot(body.lot_id as number, body.instrument_id as string);
      return;
  }
}

type Checked = { done: true } | { changed: ProposalChange[]; summary: string } | { blocked: string } | { confirm: string };

/** The rows a re-check compares: never the trade's date row (the user picks it), and in Apply all (`loose`) not the
 *  rows that follow from other cards of the same holding either. */
const rowsOf = (rows: ProposalChange[], loose: boolean) => rows.filter((r) => !r.date && !(loose && r.derived));

/** The card's payload as it is checked and applied: with the date the user picked when the card needs one. */
const payloadOf = (card: ProposalCard, date: string | undefined) => (card.needs_date && date ? { ...card.payload, date } : card.payload);

/** Re-check a card against today's state and apply it, unless its rows changed (the new ones come back), it can't
 *  be applied any more, or it asks first and the user hasn't said yes to it yet (`confirmed`). `loose` (Apply all):
 *  rows that follow from other cards of the same holding may differ. */
async function checkAndSave(
  card: ProposalCard,
  shown: ProposalChange[],
  title: string,
  { confirmed = false, loose = false, date }: { confirmed?: boolean; loose?: boolean; date?: string } = {},
): Promise<Checked> {
  if (card.needs_date && !date) return { blocked: "Pick the date first." };
  const now = await endpoints.proposalPreview(card.type, payloadOf(card, date));
  if (!now.ok) return { blocked: now.reason };
  // the summary names the holding: a different one now (its id reused) is a change even when the rows look alike
  if (now.summary !== title || JSON.stringify(rowsOf(now.changes, loose)) !== JSON.stringify(rowsOf(shown, loose)))
    return { changed: now.changes, summary: now.summary };
  // a delete always asks, even if a server older than the card sent no question
  const ask = now.confirm ?? (isDelete(card) ? (card.type === "rules.remove" ? "Remove this rule?" : "Delete for good? This can't be undone.") : null);
  if (ask && !confirmed) return { confirm: ask };
  await save(card, now.payload, now.apply);
  return { done: true };
}

const HOLDING_KINDS: ProposalCard["type"][] = ["holding.add", "holding.update", "purchase.add", "holding.delete", "purchase.delete"];

/** The text Adjust puts in the question box: which card, and what it proposed, so the AI knows what is meant. A
 *  holding or transaction card gives only its summary: its rows hold amounts, which a model that may only see
 *  relative figures must not get from the question. */
export function adjustPrefix(card: ProposalCard): string {
  if (HOLDING_KINDS.includes(card.type)) return `About the proposed change (${card.summary}): `;
  const values = card.changes.map((c) => (c.after === null ? `remove "${c.before}"` : c.after)).join("; ");
  const shown = values.length > 120 ? `${values.slice(0, 119)}…` : values;
  return `About the proposed change (${card.summary}: ${shown}): `;
}

function Change({ c, applied }: { c: ProposalChange; applied: boolean }) {
  return (
    <li>
      <span className="prop-label">{c.label}</span>
      {c.before === null ? (
        <span className="prop-after">{c.after}</span>
      ) : c.after === null ? (
        <span>
          <span className="prop-before">{c.before}</span> <span className="prop-gone">{applied ? "removed" : "will be removed"}</span>
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

function DateRow({ card, date, onDate, disabled }: { card: ProposalCard; date: string | undefined; onDate: (v: string) => void; disabled: boolean }) {
  const id = `prop-date-${card.id}`;
  return (
    <li>
      <label className="prop-label" htmlFor={id}>
        Date
      </label>
      <span className="prop-date">
        <input id={id} type="date" value={date ?? ""} max={localISODate()} disabled={disabled} onChange={(e) => onDate(e.target.value)} required />
        {!date && <span className="prop-before">not on the screenshot: pick it</span>}
      </span>
    </li>
  );
}

function Card({ card, onStatus, onAdjust, onBusy, locked, date, onDate }: {
  card: ProposalCard;
  onStatus: (id: string, s: CardStatus) => void;
  onAdjust: (card: ProposalCard) => void;
  /** The card's own Apply is running (or done running): "Apply all" waits meanwhile. */
  onBusy: (id: string, on: boolean) => void;
  /** "Apply all" or another card's Apply is running: this card's buttons wait (one save at a time, so two saves of
   *  one holding can't overwrite each other). */
  locked: boolean;
  /** The date picked for a card that needs one (kept by the list, so "Apply all" can use it too). */
  date: string | undefined;
  onDate: (id: string, v: string) => void;
}) {
  const [shown, setShown] = useState(card.changes);
  const [title, setTitle] = useState(card.summary);
  const [busy, setBusy] = useState(false);
  const [changed, setChanged] = useState(false);
  const [confirming, setConfirming] = useState<string | null>(null); // the server's question, waiting for "Yes, ..."
  const [problem, setProblem] = useState<string | null>(null);
  const headRef = useRef<HTMLDivElement | null>(null);
  const acted = useRef(false);
  const done = card.status !== "pending";
  const del = isDelete(card);
  const rule = card.type === "rules.remove"; // a rule can be added back: "remove", not "delete for good"

  // Apply and Dismiss remove the buttons: keep keyboard focus on this card instead of losing it to the page
  useEffect(() => {
    if (done && acted.current) {
      acted.current = false;
      headRef.current?.focus();
    }
  }, [done]);

  const apply = async () => {
    setBusy(true);
    onBusy(card.id, true);
    setProblem(null);
    try {
      // "Yes, ..." confirms exactly the rows on screen: if they changed meanwhile, the new ones are shown and asked again
      const out = await checkAndSave(card, shown, title, { confirmed: confirming !== null, date });
      if ("blocked" in out) {
        // the button stays: what refuses it now may pass later (another card applied first, the price source back)
        setProblem(out.blocked);
        setConfirming(null);
      } else if ("changed" in out) {
        setShown(out.changed);
        setTitle(out.summary);
        setChanged(true);
        setConfirming(null);
      } else if ("confirm" in out) {
        setConfirming(out.confirm);
      } else {
        acted.current = true;
        onStatus(card.id, "applied");
      }
    } catch (err) {
      setProblem(errorMessage(err));
    } finally {
      setBusy(false);
      onBusy(card.id, false);
    }
  };

  const wait = busy || locked;
  const message =
    problem ??
    confirming ??
    (changed ? "This changed since the answer. Check the values above, then apply again." : "Suggested by the AI. Check before applying.");
  // once applied, rows that followed from other cards (units held after) may no longer be what happened
  const rows = done ? shown.filter((c) => !c.derived) : shown;
  const picking = !!card.needs_date && !done; // the date row is a date field until the card is applied
  return (
    <div role="group" className={done ? `prop-card done ${card.status}` : "prop-card"} aria-label={`Suggested change: ${title}`}>
      <div className="prop-head" ref={headRef} tabIndex={-1}>
        {card.status === "applied" ? <Check aria-hidden="true" /> : del ? <Trash2 aria-hidden="true" /> : <Sparkles aria-hidden="true" />}
        <b>{title}</b>
        {done && <span className="prop-status">{DONE[card.status as CardStatus]}</span>}
      </div>
      <ul className={rows.length >= 3 ? "prop-rows compact" : "prop-rows"}>
        {rows.map((c, i) =>
          c.date && picking ? (
            <DateRow key={i} card={card} date={date} onDate={(v) => onDate(card.id, v)} disabled={wait} />
          ) : c.date && card.needs_date ? (
            <Change key={i} c={{ ...c, after: date ?? "picked when applied" }} applied={card.status === "applied"} />
          ) : (
            <Change key={i} c={c} applied={card.status === "applied"} />
          ),
        )}
      </ul>
      {!done && (
        <>
          {card.reason && <p className="prop-reason">{card.reason}</p>}
          <p className={confirming && !problem ? (del ? "prop-warn danger" : "prop-warn ask") : "prop-warn"} role="status">
            {message}
          </p>
          <div className="btn-row">
            <button
              type="button"
              className={confirming && del ? "btn danger small" : "btn small"}
              onClick={apply}
              disabled={wait || (picking && !date)}
              aria-busy={busy}
            >
                {confirming
                  ? del
                    ? rule
                      ? "Yes, remove"
                      : "Yes, delete"
                    : "Yes, apply"
                  : del
                    ? rule
                      ? "Remove…"
                      : "Delete…"
                    : changed
                      ? "Apply these values"
                      : card.confirm
                        ? "Apply…"
                        : "Apply"}
            </button>
            {confirming ? (
              <button type="button" className="btn ghost small" disabled={wait} onClick={() => setConfirming(null)}>
                Cancel
              </button>
            ) : (
              <>
                <button type="button" className="btn ghost small" disabled={wait} onClick={() => onAdjust(card)}>
                  Adjust
                </button>
                <button
                  type="button"
                  className="btn ghost small"
                  disabled={wait}
                  onClick={() => {
                    acted.current = true;
                    onStatus(card.id, "dismissed");
                  }}
                >
                  Dismiss
                </button>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/** Never in Apply all, besides deletes and cards that ask first: text the AI analyst reads with every later question
 *  (a standing rule, the notes), which one press over 25 imported cards must not store unseen. */
const ONE_BY_ONE: ProposalCard["type"][] = ["rules.add", "preferences.update"];

/** Apply the pending cards one after another (never deletes, rules, preferences, nor cards that ask first); stop at
 *  the first that fails, changed, or now asks first. Two cards on one holding go in a row: rows derived from the
 *  earlier one (units held after) are not compared. */
function ApplyAll({ cards, onStatus, onRunning, waiting, dates }: {
  cards: ProposalCard[];
  onStatus: (id: string, s: CardStatus) => void;
  onRunning: (on: boolean) => void;
  /** A card's own Apply is running: starting now would apply that card a second time. */
  waiting: boolean;
  /** Dates picked on cards that need one: a card still without its date waits for it. */
  dates: Readonly<Record<string, string>>;
}) {
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const todo = cards.filter(
    (c) => c.status === "pending" && !isDelete(c) && !c.confirm && !ONE_BY_ONE.includes(c.type) && (!c.needs_date || !!dates[c.id]),
  );
  const undated = cards.filter((c) => c.status === "pending" && c.needs_date && !dates[c.id]).length;
  if (todo.length < 2 && !note) return null;

  const run = async () => {
    setBusy(true);
    onRunning(true);
    setNote(null);
    let applied = 0;
    try {
      for (const card of todo) {
        setNote(`Applying ${applied + 1} of ${todo.length}…`);
        let out;
        try {
          out = await checkAndSave(card, card.changes, card.summary, { loose: true, date: dates[card.id] });
        } catch (err) {
          setNote(`Applied ${applied} of ${todo.length}. Stopped at "${card.summary}": ${errorMessage(err)}`);
          return;
        }
        if (!("done" in out)) {
          const why =
            "blocked" in out
              ? out.blocked
              : "confirm" in out
                ? "it needs your confirmation now; apply it on its own."
                : "it changed since the answer; check it and apply it on its own.";
          setNote(`Applied ${applied} of ${todo.length}. Stopped at "${card.summary}": ${why}`);
          return;
        }
        onStatus(card.id, "applied");
        applied += 1;
      }
      setNote(`Applied all ${applied}.`);
    } finally {
      setBusy(false);
      onRunning(false);
    }
  };

  return (
    <div className="prop-all">
      {todo.length >= 2 && (
        <button type="button" className="btn small" onClick={run} disabled={busy || waiting} aria-busy={busy}>
          Apply all ({todo.length})
        </button>
      )}
      <span className="prop-all-note" role="status">
        {note ??
          "Applies each card in order, as if you pressed its Apply; deletions, rules, preferences and cards that ask first are left for you." +
            (undated ? ` ${undated === 1 ? "One card waits" : `${undated} cards wait`} for a date.` : "")}
      </span>
    </div>
  );
}

export function ProposalCards({ cards, notes, onStatus, onAdjust }: {
  cards: ProposalCard[];
  notes: string[];
  onStatus: (id: string, status: CardStatus) => void;
  onAdjust: (card: ProposalCard) => void;
}) {
  const [running, setRunning] = useState(false);
  const [applying, setApplying] = useState<ReadonlySet<string>>(new Set()); // cards whose own Apply is running
  const [dates, setDates] = useState<Readonly<Record<string, string>>>({}); // picked on cards that need a date
  const onDate = useCallback((id: string, v: string) => setDates((now) => ({ ...now, [id]: v })), []);
  const onBusy = useCallback((id: string, on: boolean) => {
    setApplying((now) => {
      const next = new Set(now);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);
  if (!cards.length && !notes.length) return null;
  return (
    <div className="prop-list">
      <ApplyAll cards={cards} onStatus={onStatus} onRunning={setRunning} waiting={applying.size > 0} dates={dates} />
      {cards.map((c) => (
        <Card
          key={c.id}
          card={c}
          onStatus={onStatus}
          onAdjust={onAdjust}
          onBusy={onBusy}
          locked={running || (applying.size > 0 && !applying.has(c.id))}
          date={dates[c.id] || undefined}
          onDate={onDate}
        />
      ))}
      {notes.map((n, i) => (
        <p key={i} className="ai-note">
          {n}
        </p>
      ))}
    </div>
  );
}
