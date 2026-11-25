import { Fragment, useCallback, useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Check, ChevronDown, Copy, Download, ExternalLink, ShieldAlert, ShieldCheck, SlidersHorizontal, X } from "lucide-react";
import { AI_ANSWER_NOTE } from "../legal";
import { MAX_QUESTION, QUICK_PROMPTS, QUICK_PROMPTS_SHOWN, clearChat, conversationLevel, conversationMarkdown, historyBefore, loadChat, memoryStart, newId, safeUrl, saveChat, takeQueuedQuestion, type Chat, type ChatMessage } from "../advisor";
import { copyText, downloadText } from "../clipboard";
import { ApiError, FULL_DETAIL_ITEMS, RELATIVE_ITEMS, endpoints, errorMessage, type Effort, type Provider } from "../api";
import { effortPhrase } from "../effort";
import { ModelMenu, chatModel, withEffort } from "../components/ai/ChatPickers";
import { Alert } from "../components/Alert";
import { Card } from "../components/Card";
import { Markdown } from "../components/Markdown";
import { useResource, useTitle } from "../hooks";
import { useLive } from "../live";
import { Link, navigate, useSearch } from "../router";

export function AdvisorPage() {
  useTitle("AI analyst");
  const search = useSearch();
  const holdingId = new URLSearchParams(search).get("holding");
  const live = useLive();
  const providers = useResource(() => endpoints.aiProviders(), []);

  const [chat, setChat] = useState<Chat>(() => loadChat());
  const chatRef = useRef(chat);
  chatRef.current = chat;
  const [waiting, setWaiting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  // the model's sharing level changed (409): retrying cannot work, only a new conversation
  const [stale, setStale] = useState(false);
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement | null>(null);
  const areaRef = useRef<HTMLTextAreaElement | null>(null);
  const mounted = useRef(false);
  const busy = useRef(false);
  const [allPrompts, setAllPrompts] = useState(false);
  const prompts = allPrompts ? QUICK_PROMPTS : QUICK_PROMPTS.slice(0, QUICK_PROMPTS_SHOWN);
  const hiddenPrompts = QUICK_PROMPTS.length - QUICK_PROMPTS_SHOWN;

  const list: Provider[] = providers.data ?? [];
  const holdingName = holdingId ? (live.snapshot?.rows.find((r) => r.id === holdingId)?.name ?? holdingId) : null;
  // Claude models take an effort; a choice made here applies to this conversation only (never saved to the model).
  const { selected, effort } = chatModel(chat, list);
  // What the picker shows is what the next question sends.
  const effortRef = useRef<Effort | null>(effort);
  effortRef.current = effort;

  useEffect(() => {
    saveChat(chat);
  }, [chat]);

  // When an answer arrives and focus was lost (the button that started it is gone), put it back in the composer.
  const wasWaiting = useRef(false);
  useEffect(() => {
    if (wasWaiting.current && !waiting && (document.activeElement === document.body || document.activeElement === null)) areaRef.current?.focus();
    wasWaiting.current = waiting;
  }, [waiting]);

  // Keep the newest message in view (not on the first render: a restored conversation stays where it is).
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    endRef.current?.scrollIntoView({ block: "end", behavior: reduce ? "auto" : "smooth" });
  }, [chat.messages.length, waiting, failure]);

  const run = useCallback(
    async (question: string, before: ChatMessage[], holding: string | null) => {
      busy.current = true;
      setWaiting(true);
      setFailure(null);
      try {
        const body: Parameters<typeof endpoints.ask>[0] = { question, history: historyBefore(before, before.length) };
        const level = conversationLevel(before);
        if (level) body.expected_level = level;
        const pid = chatRef.current.providerId;
        if (pid) body.provider_id = pid;
        const shown = effortRef.current;
        if (shown) body.effort = shown;
        if (holding) body.instrument_id = holding;
        const res = await endpoints.ask(body);
        setChat((c) => ({
          ...c,
          providerId: c.providerId ?? res.provider.id, // later questions go to the model that answered, not a new default
          messages: [...c.messages, { id: newId(), role: "assistant", content: res.text, meta: { provider: res.provider, model: res.model, effort: res.effort ?? null, sources: res.sources ?? [], shared: res.shared } }],
        }));
      } catch (err) {
        setFailure(errorMessage(err));
        if (err instanceof ApiError && err.status === 409) {
          setStale(true);
          providers.reload();
        }
      } finally {
        busy.current = false;
        setWaiting(false);
      }
    },
    [],
  );

  const send = useCallback(
    (text: string, holding: string | null = holdingId) => {
      const question = text.trim().slice(0, MAX_QUESTION);
      if (!question || busy.current) return;
      const before = chatRef.current.messages;
      setChat((c) => ({ ...c, messages: [...c.messages, { id: newId(), role: "user", content: question }] }));
      setDraft("");
      void run(question, before, holding);
    },
    [run, holdingId],
  );

  const retry = () => {
    const msgs = chatRef.current.messages;
    const last = msgs[msgs.length - 1];
    if (!last || last.role !== "user" || busy.current) return;
    void run(last.content, msgs.slice(0, -1), holdingId);
  };

  // A question handed over from the Overview card is sent as soon as the page opens.
  useEffect(() => {
    const queued = takeQueuedQuestion();
    if (!queued) return;
    if (queued.holding) navigate(`/analyst?holding=${encodeURIComponent(queued.holding)}`, { replace: true });
    send(queued.question, queued.holding ?? holdingId);
  }, []);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    send(draft);
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send(draft);
    }
  };

  const newConversation = () => {
    clearChat();
    setChat((c) => ({ providerId: selected?.id ?? null, effort: c.providerId === selected?.id ? (c.effort ?? null) : null, messages: [] }));
    setFailure(null);
    setStale(false);
    setDraft("");
  };

  const textareaId = useId();
  const rows = Math.min(6, Math.max(2, draft.split("\n").length));
  const empty = chat.messages.length === 0;
  // Only the last MAX_HISTORY messages go with the next question: mark where the analyst's memory begins.
  const forgotten = memoryStart(chat.messages);
  const lastIsUser = chat.messages[chat.messages.length - 1]?.role === "user";

  return (
    <div className="advisor-layout">
      <Card as="section" className="chat" aria-label="AI analyst chat">
        <div className="chat-head">
          <div className="chat-head-side">
            {selected && (
              <Link className={selected.data_level === "full" && !selected.local ? "share-pill warn" : "share-pill"} to="/privacy#ai">
                {selected.data_level === "full" ? <ShieldAlert aria-hidden="true" /> : <ShieldCheck aria-hidden="true" />}
                <span>
                  {selected.local ? "Local model · " : ""}
                  {selected.data_level === "full" ? "Shares full detail" : "Shares relative figures"}
                </span>
                <span className="sr-only"> — change on the Data &amp; privacy page</span>
              </Link>
            )}
            <Link className="btn ghost small" to="/settings#ai">
              <SlidersHorizontal aria-hidden="true" />
              Models, rules &amp; persona
            </Link>
            {!empty && (
              <button
                type="button"
                className="btn ghost small"
                onClick={() => {
                  const now = new Date();
                  downloadText(`moneylyzer-conversation-${now.toISOString().slice(0, 10)}.md`, conversationMarkdown(chat.messages, now, AI_ANSWER_NOTE));
                }}
              >
                <Download aria-hidden="true" />
                Export
              </button>
            )}
            {!empty && (
              <button type="button" className="btn ghost small" onClick={newConversation} disabled={waiting}>
                New conversation
              </button>
            )}
          </div>
        </div>
        {providers.error && !providers.data && <Alert tone="err">Could not load the models: {providers.error}</Alert>}

        <div className="chips" role="group" aria-label="Quick questions">
          {prompts.map((q) => (
            <button key={q.label} type="button" className="chip" onClick={() => send(q.question)} disabled={waiting}>
              {q.label}
            </button>
          ))}
          {hiddenPrompts > 0 && (
            <button
              key="more"
              type="button"
              className="chip chip-more"
              aria-expanded={allPrompts}
              onClick={(e) => {
                const group = e.currentTarget.parentElement;
                setAllPrompts((v) => !v);
                // the revealed questions appear before this button: take focus to the first of them
                if (!allPrompts) requestAnimationFrame(() => group?.querySelectorAll<HTMLButtonElement>("button.chip")[QUICK_PROMPTS_SHOWN]?.focus());
              }}
            >
              More questions
              <ChevronDown aria-hidden="true" className={allPrompts ? "flip" : undefined} />
            </button>
          )}
        </div>

        {selected?.data_level === "full" && !selected.local && (
          <Alert tone="warn" className="share-warn">
            <b>Full detail is on for {selected.name}.</b> Each question sends your {FULL_DETAIL_ITEMS} to its provider, linked to
            your account there.{" "}
            <Link className="btn ghost small" to="/privacy#ai">
              Change
            </Link>
          </Alert>
        )}

        {holdingId && (
          <div className="focus-chip">
            <span>About: {holdingName}</span>
            <button type="button" aria-label={`Remove focus on ${holdingName}`} onClick={() => navigate("/analyst", { replace: true })}>
              <X />
            </button>
          </div>
        )}

        <div className="chat-log" role="log" aria-label="Conversation" aria-live="polite" aria-relevant="additions">
          {empty && !waiting && (
            <div className="empty">
              <b>Ask about your portfolio, a holding or the markets</b>
              The AI analyst sees your holdings ({RELATIVE_ITEMS}, unless you turned on full detail for the model), market benchmarks and recent news. Pick a quick question or type your own.
            </div>
          )}
          {chat.messages.map((m, i) => (
            <Fragment key={m.id}>
              {i === forgotten && forgotten > 0 && (
                <div className="memory-line" role="separator" aria-label="The analyst no longer sees the messages above">
                  <span>The analyst no longer sees the messages above. Start a new conversation, or repeat what still matters.</span>
                </div>
              )}
              {m.role === "user" ? <div className="msg user"><p>{m.content}</p></div> : <Answer m={m} />}
            </Fragment>
          ))}
          {waiting && (
            <div className="msg assistant pending" role="status">
              <span className="spinner" aria-hidden="true" /> The AI analyst is thinking
              {selected ? ` (${selected.model}${effort ? `, ${effortPhrase(effort)}` : ""})` : ""}… this can take up to two minutes.
            </div>
          )}
          {failure && !waiting && (
            <Alert tone="err">
              {failure}
              {lastIsUser && !stale && (
                <>
                  {" "}
                  <button type="button" className="link-btn" onClick={retry}>
                    Try again
                  </button>
                </>
              )}
            </Alert>
          )}
          <div ref={endRef} />
        </div>

        <form className="composer" onSubmit={submit}>
          <label className="sr-only" htmlFor={textareaId}>
            Ask the AI analyst
          </label>
          <textarea
            id={textareaId}
            ref={areaRef}
            rows={rows}
            value={draft}
            maxLength={MAX_QUESTION}
            placeholder="Ask the AI analyst…  (Enter sends, Shift+Enter adds a line)"
            disabled={waiting}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKey}
          />
          {/* Model and effort live in the message box, where the question is written (as in Claude's own app). */}
          <div className="composer-bar">
            <ModelMenu
              chat={chat}
              list={list}
              loading={providers.loading}
              disabled={waiting}
              onModel={(v) => {
                // another model starts a new conversation: earlier answers stay with the model (and the
                // sharing level) they were written for
                clearChat();
                setChat({ providerId: v, messages: [] });
                setFailure(null);
                setStale(false);
              }}
              onEffort={(v) => setChat((c) => withEffort(c, selected, v))}
            />
            <button type="submit" className="btn send-btn" disabled={waiting || !draft.trim() || !selected}>
              {waiting && <span className="spinner" aria-hidden="true" />}
              {waiting ? "Waiting…" : "Send"}
            </button>
          </div>
        </form>
        <p className="note">
          AI-generated research and decision support, not financial advice. It can be wrong, and it won't push you to buy out of greed or sell out of fear. You decide.
        </p>
      </Card>

    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [state, setState] = useState<"idle" | "done" | "failed">("idle");
  useEffect(() => {
    if (state === "idle") return;
    const t = window.setTimeout(() => setState("idle"), 2000);
    return () => window.clearTimeout(t);
  }, [state]);
  return (
    <>
      <button type="button" className="btn ghost small copy-btn" onClick={async () => setState((await copyText(text)) ? "done" : "failed")}>
        {state === "done" ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
        {state === "done" ? "Copied" : state === "failed" ? "Copy failed" : "Copy"}
        <span className="sr-only"> answer</span>
      </button>
      <span className="sr-only" role="status">
        {state === "done" ? "Answer copied" : state === "failed" ? "Could not copy the answer" : ""}
      </span>
    </>
  );
}

function Answer({ m }: { m: ChatMessage }) {
  const meta = m.meta;
  const sources = (meta?.sources ?? []).map((s) => ({ ...s, href: safeUrl(s.url) })).filter((s) => s.href);
  const shared = meta?.shared;
  return (
    <div className="msg assistant" data-ai-generated="true" aria-label="AI-generated answer" role="article">
      {meta && (
        <div className="msg-meta">
          {meta.provider.name} · {meta.model}
          {meta.effort ? ` · ${effortPhrase(meta.effort)}` : ""}
        </div>
      )}
      <Markdown source={m.content} />
      <div className="msg-tools">
        <p className="ai-note">{AI_ANSWER_NOTE}</p>
        <CopyButton text={m.content} />
      </div>
      {sources.length > 0 && (
        <div className="sources">
          <b>Sources</b>
          <ul className="chip-list">
            {sources.map((s, i) => (
              <li key={`${s.href}-${i}`}>
                <a className="chip-link" href={s.href as string} target="_blank" rel="noopener noreferrer">
                  <span className="chip-text">{s.title || (s.href as string)}</span>
                  <ExternalLink aria-hidden="true" />
                  <span className="sr-only"> (opens in a new tab)</span>
                </a>
              </li>
            ))}
          </ul>
        </div>
      )}
      {shared && (
        <details className="shared">
          <summary>What was shared</summary>
          <ul className="plain-list">
            <li>{shared.data_level === "full" ? `Full detail (${FULL_DETAIL_ITEMS})` : `Relative figures only (${RELATIVE_ITEMS})`}</li>
            <li>
              {shared.holdings} {shared.holdings === 1 ? "holding" : "holdings"} · {shared.benchmarks} market {shared.benchmarks === 1 ? "benchmark" : "benchmarks"} · {shared.news_items} news {shared.news_items === 1 ? "item" : "items"}
            </li>
            {(shared.notes ?? 0) > 0 && <li>{shared.notes === 1 ? "1 holding note" : `${shared.notes} holding notes`}</li>}
            <li>Web research: {shared.web_research ? "on" : "off"}</li>
          </ul>
        </details>
      )}
    </div>
  );
}
