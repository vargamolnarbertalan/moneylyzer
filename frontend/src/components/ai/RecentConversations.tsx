import { useEffect, useId, useRef, useState } from "react";
import { Trash2, X } from "lucide-react";
import { ApiError, endpoints, errorMessage, type SavedConversation } from "../../api";
import { useResource } from "../../hooks";
import { Alert } from "../Alert";
import { Skeleton } from "../Skeleton";

/** "2026-11-25 14:03:00" (UTC, from the server) in the viewer's own date and time format. */
function when(utc: string): string {
  const d = new Date(utc.replace(" ", "T") + "Z");
  return Number.isNaN(d.getTime()) ? utc : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

interface RecentProps {
  currentId: string;
  /** An answer is on its way: nothing here may switch or delete the conversation meanwhile. */
  disabled?: boolean;
  onOpen: (conversation: SavedConversation) => void;
  /** The conversation on screen was deleted (alone or with all the others): the page starts a new one. */
  onCurrentDeleted: () => void;
  onClose: () => void;
}

/** Saved AI analyst conversations: open one to read or continue it, delete one or all (two-step). */
export function RecentConversations({ currentId, disabled = false, onOpen, onCurrentDeleted, onClose }: RecentProps) {
  const uid = useId();
  const list = useResource(() => endpoints.conversations(), []);
  const [confirm, setConfirm] = useState<string | null>(null); // a conversation id, or "all"
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState("");
  const panel = useRef<HTMLElement | null>(null);
  const items = list.data ?? [];
  const off = disabled || busy !== null;

  /** After a step removes the focused control, put focus somewhere sensible inside the panel, once the list has
   *  re-rendered and its buttons are enabled again (focusing a disabled button does nothing). */
  const pendingFocus = useRef<string | null>(null);
  const focusSoon = (selector: string) => {
    pendingFocus.current = selector;
  };
  useEffect(() => {
    const selector = pendingFocus.current;
    if (!selector || busy !== null || list.loading) return;
    pendingFocus.current = null;
    (panel.current?.querySelector<HTMLElement>(`${selector}:not(:disabled)`) ?? panel.current?.querySelector<HTMLElement>(".recent-head button"))?.focus();
  }, [busy, list.loading, list.data, confirm]);

  const run = async (key: string, action: () => Promise<void>) => {
    setBusy(key);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(null);
    }
  };
  const open = (id: string) => {
    if (id === currentId) return onClose(); // already on screen: reopening would replace it with the saved copy
    return run(`open:${id}`, async () => onOpen(await endpoints.conversation(id)));
  };
  const remove = (id: string) =>
    run(`del:${id}`, async () => {
      try {
        await endpoints.deleteConversation(id);
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 404)) throw err; // already gone (another tab, the cap): fine
      }
      setConfirm(null);
      if (id === currentId) onCurrentDeleted();
      setStatus("Conversation deleted.");
      list.reload();
      focusSoon(".recent-open");
    });
  const removeAll = () =>
    run("del:all", async () => {
      await endpoints.deleteAllConversations();
      setConfirm(null);
      onCurrentDeleted();
      setStatus("All saved conversations deleted.");
      list.reload();
      focusSoon(".recent-head button");
    });
  const cancel = (selector: string) => {
    setConfirm(null);
    focusSoon(selector);
  };

  return (
    <section className="recent" aria-labelledby={`${uid}h`} ref={panel}>
      <div className="recent-head">
        <h3 id={`${uid}h`} className="sub-h">
          Recent conversations
        </h3>
        <button type="button" className="icon-btn" aria-label="Close recent conversations" onClick={onClose}>
          <X />
        </button>
      </div>
      <p className="help">Answers are saved here as they arrive, on this server (and in its backups), for 90 days after the last one.</p>
      <span className="sr-only" role="status">
        {status}
      </span>
      {list.loading && !list.data ? (
        <Skeleton h={44} />
      ) : list.error && !list.data ? (
        <Alert tone="err">Could not load the saved conversations: {list.error}</Alert>
      ) : items.length === 0 ? (
        <p className="sub">No saved conversations yet.</p>
      ) : (
        <ul className="recent-list">
          {items.map((c) => {
            const questions = Math.ceil(c.count / 2);
            return (
              <li key={c.id}>
                <button type="button" className="recent-open" onClick={() => open(c.id)} disabled={off} aria-current={c.id === currentId ? "true" : undefined}>
                  <span className="recent-title">{c.title}</span>
                  <span className="recent-meta">
                    {c.provider_name ?? "Removed model"}
                    {c.model ? ` · ${c.model}` : ""} · {when(c.updated_at)} · {questions} {questions === 1 ? "question" : "questions"}
                    {c.id === currentId ? " · open now" : ""}
                  </span>
                </button>
                {confirm === c.id ? (
                  <div className="confirm" role="group" aria-label={`Confirm delete ${c.title}`}>
                    <span>Delete it?</span>
                    <button type="button" className="btn danger small" onClick={() => remove(c.id)} disabled={off}>
                      {busy === `del:${c.id}` ? "Deleting…" : "Yes, delete"}
                    </button>
                    <button type="button" className="btn ghost small" onClick={() => cancel(`[data-del="${c.id}"]`)} disabled={off} autoFocus>
                      No
                    </button>
                  </div>
                ) : (
                  <button type="button" className="icon-btn" data-del={c.id} aria-label={`Delete ${c.title}`} onClick={() => setConfirm(c.id)} disabled={off}>
                    <Trash2 />
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {items.length > 0 &&
        (confirm === "all" ? (
          <div className="confirm" role="group" aria-label="Confirm delete all conversations">
            <span>{items.length === 1 ? "Delete the saved conversation?" : `Delete all ${items.length} saved conversations?`}</span>
            <button type="button" className="btn danger small" onClick={removeAll} disabled={off}>
              {busy === "del:all" ? "Deleting…" : "Yes, delete all"}
            </button>
            <button type="button" className="btn ghost small" onClick={() => cancel("[data-del-all]")} disabled={off} autoFocus>
              No
            </button>
          </div>
        ) : (
          <div className="btn-row">
            <button type="button" className="btn ghost small" data-del-all onClick={() => setConfirm("all")} disabled={off}>
              Delete all
            </button>
          </div>
        ))}
      {error && <Alert tone="err">{error}</Alert>}
    </section>
  );
}
