import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Plus, Trash2 } from "lucide-react";
import { ApiError, endpoints, errorMessage } from "../../api";
import { useResource } from "../../hooks";
import { Alert } from "../Alert";
import { Card, CardHeader } from "../Card";
import { BlockSkeleton } from "../Skeleton";

// Mirrors analyzer/persona.py (MAX_RULES, MAX_RULE_CHARS); the server rejects anything larger with a 422.
const MAX_RULES = 30;
const MAX_CHARS = 300;

const EXAMPLES = [
  "When suggesting ETFs, only consider ones I can buy on Revolut in Hungary.",
  "I only trade holdings priced in EUR.",
  "Prefer accumulating (Acc) share classes over distributing ones.",
];

const oneLine = (s: string) => s.replace(/\s+/g, " ").trim();

/**
 * Standing rules for the AI analyst: short constraints sent with every question. Saved on their own
 * (PUT { rules }), so adding a rule never touches the persona or the preferences form.
 */
export function AdvisorRules() {
  const uid = useId();
  const input = useRef<HTMLInputElement>(null);
  const res = useResource(() => endpoints.aiSettings(), []);
  const [rules, setRules] = useState<string[] | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    if (res.data && rules === null) setRules(res.data.rules ?? []);
  }, [res.data, rules]);

  // After a delete the row's button is gone: focus the input once it is re-enabled (it is disabled at 30).
  const refocus = useRef(false);
  useEffect(() => {
    if (!refocus.current) return;
    refocus.current = false;
    input.current?.focus();
  }, [rules]);

  if (rules === null) {
    return (
      <Card as="section" id="rules" aria-labelledby="rules-h">
        <CardHeader id="rules-h" title="AI analyst rules" />
        {res.error ? <Alert tone="err">Could not load the rules: {res.error}</Alert> : <BlockSkeleton />}
      </Card>
    );
  }

  const save = async (next: string[], done: string): Promise<boolean> => {
    setBusy(true);
    setMsg(null);
    try {
      // rules_expected: the server refuses (409) if another tab or device changed the list meanwhile
      const saved = await endpoints.saveAiSettings({ rules: next, rules_expected: rules });
      setRules(saved.rules);
      // the server folds case-insensitive duplicates (e.g. "Straße"/"STRASSE") the browser cannot detect
      const dropped = saved.rules.length < next.length;
      setMsg(dropped ? { ok: false, text: "That rule is already on the list." } : { ok: true, text: done });
      return !dropped;
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        try {
          setRules((await endpoints.aiSettings()).rules);
        } catch {
          /* keep the list we have; the message below still explains */
        }
      }
      setMsg({ ok: false, text: errorMessage(err) });
      return false;
    } finally {
      setBusy(false);
    }
  };

  const add = async (e: FormEvent) => {
    e.preventDefault();
    const text = oneLine(draft);
    if (!text) return;
    if (rules.some((r) => r.toLowerCase() === text.toLowerCase())) {
      setMsg({ ok: false, text: "That rule is already on the list." });
      return;
    }
    if (await save([...rules, text], "Rule saved. It applies from your next question.")) setDraft("");
  };

  const remove = async (index: number) => {
    refocus.current = true;
    if (!(await save(
      rules.filter((_, i) => i !== index),
      "Rule deleted.",
    ))) refocus.current = false;
  };

  const full = rules.length >= MAX_RULES;
  const length = oneLine(draft).length;

  return (
    <Card as="section" id="rules" aria-labelledby="rules-h">
      <CardHeader id="rules-h" title="AI analyst rules" aside={<span className="tag">{rules.length} active</span>} />
      <p className="sub">
        Rules the AI analyst follows in every answer — which brokers, currencies or kinds of funds to stick to. The safety guardrails still come first.
      </p>

      {rules.length > 0 ? (
        <ol className="rules-list" aria-label="Your rules">
          {rules.map((r, i) => (
            <li key={r}>
              <span className="rule-n" aria-hidden="true">
                {i + 1}
              </span>
              <span className="rule-text">{r}</span>
              <button type="button" className="icon-btn" onClick={() => void remove(i)} disabled={busy} aria-label={`Delete rule ${i + 1}: ${r}`} title="Delete rule">
                <Trash2 />
              </button>
            </li>
          ))}
        </ol>
      ) : (
        <div className="rules-empty">
          <p className="sub">No rules yet. Start from an example, edit it, then add it:</p>
          <div className="chip-row">
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                className="link-btn"
                onClick={() => {
                  setDraft(ex);
                  input.current?.focus();
                }}
              >
                {ex}
              </button>
            ))}
          </div>
        </div>
      )}

      <form className="rules-add" onSubmit={add} noValidate>
        <div className="field">
          <label htmlFor={`${uid}r`}>New rule</label>
          <div className="rules-add-row">
            <input
              id={`${uid}r`}
              ref={input}
              value={draft}
              maxLength={MAX_CHARS}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="e.g. I only trade holdings priced in EUR."
              autoComplete="off"
              disabled={full}
              aria-describedby={`${uid}h`}
            />
            <button type="submit" className="btn small" disabled={busy || full || length === 0}>
              <Plus aria-hidden="true" />
              Add rule
            </button>
          </div>
          <span id={`${uid}h`} className="help">
            {full
              ? `You have the maximum of ${MAX_RULES} rules. Delete one to add another.`
              : `${length}/${MAX_CHARS} characters. Rules go to the selected model with every question, whatever its data-sharing level — keep account numbers and amounts out of them.`}
          </span>
        </div>
      </form>
      {msg && <Alert tone={msg.ok ? "ok" : "err"}>{msg.text}</Alert>}
    </Card>
  );
}
