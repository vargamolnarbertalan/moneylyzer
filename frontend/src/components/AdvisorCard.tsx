import { useId, useState, type FormEvent } from "react";
import { QUICK_PROMPTS, queueQuestion } from "../advisor";
import { RELATIVE_ITEMS } from "../api";
import { ShieldCheck, Sparkles } from "lucide-react";
import { Link, navigate } from "../router";
import { AiMark } from "./AiMark";
import { Card } from "./Card";

const CHIPS = QUICK_PROMPTS.slice(0, 3);

/** Overview card: one line to ask the AI analyst. Submitting opens its page with the question already sent. */
export function AdvisorCard({ className }: { className?: string }) {
  const uid = useId();
  const [text, setText] = useState("");

  const go = (question: string) => {
    const q = question.trim();
    if (!q) return;
    queueQuestion(q);
    navigate("/analyst");
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    go(text);
  };

  return (
    <Card as="section" className={`ai ${className ?? ""}`.trim()} aria-labelledby={`${uid}h`}>
      <div className="card-h">
        <div className="who">
          <AiMark />
          <h2 id={`${uid}h`}>AI analyst</h2>
        </div>
        <span className="tag">online · on send</span>
      </div>
      <form className="advisor-ask" onSubmit={submit}>
        <label className="sr-only" htmlFor={`${uid}q`}>
          Ask about your portfolio
        </label>
        <input id={`${uid}q`} className="input" value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask about your portfolio…" autoComplete="off" maxLength={2000} />
        <button type="submit" className="btn ask-btn" disabled={!text.trim()}>
          <Sparkles aria-hidden="true" />
          {/* Both labels share one grid cell, so the button is as wide as the longer one and the hover
              swap never moves anything. The accessible name stays "Ask". */}
          <span className="ask-labels">
            <span className="ask-rest">Ask</span>
            <span className="ask-hover" aria-hidden="true">
              Ask away
            </span>
          </span>
        </button>
      </form>
      <div className="chips" role="group" aria-label="Quick questions">
        {CHIPS.map((c) => (
          <button key={c.label} type="button" className="chip" onClick={() => go(c.question)}>
            {c.label}
          </button>
        ))}
      </div>
      <div className="ai-foot">
        <p className="note">Shares {RELATIVE_ITEMS} only, unless you turned on full detail for a model in Data &amp; privacy. Not investment advice.</p>
        <Link className="link-btn" to="/privacy">
          <ShieldCheck aria-hidden="true" />
          Data &amp; privacy
        </Link>
      </div>
    </Card>
  );
}
