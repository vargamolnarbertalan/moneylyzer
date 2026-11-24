// Advisor conversation helpers: the quick prompts, the sessionStorage-backed conversation, the hand-over
// from the Overview card, and the history the API wants.
import type { AdvisorAnswer, ChatTurn } from "./api";
import { readSession, removeSession, writeSession } from "./storage";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** Only on answers. */
  meta?: Pick<AdvisorAnswer, "provider" | "model" | "sources" | "shared">;
}

export interface Chat {
  providerId: string | null;
  messages: ChatMessage[];
}

const CHAT_KEY = "lia-advisor-chat";
const PENDING_KEY = "lia-advisor-pending";
export const MAX_HISTORY = 24;
export const MAX_QUESTION = 8000;

export interface QuickPrompt {
  label: string;
  question: string;
}

// What paid portfolio tools sell (diversification and risk scores, SWOT-style reviews, stress tests, goal
// planners, fee analysers, rebalancing checks), written as prompts the analyst can answer from the data it
// gets. The Overview card shows the first three; the AI analyst page shows QUICK_PROMPTS_SHOWN before "More".
export const QUICK_PROMPTS: readonly QuickPrompt[] = [
  {
    label: "Daily briefing",
    question:
      "Daily briefing: what moved in my portfolio today and why, which news items matter for my holdings and which are noise, and what, if anything, deserves my attention. Keep it short.",
  },
  {
    label: "Diversification score",
    question:
      "Score my diversification from 0 to 100 and show the working: (1) concentration: the Herfindahl index (the sum of squared weights) and the effective number of holdings (1 divided by it); (2) the combined weight of my top 3 holdings; (3) how spread my currencies are; (4) the share of holding pairs correlated above 0.7. For each part give the formula, its sub-score and its weight in the total. Then give the total, the three biggest drivers and what would raise the score most. Where sector or region data is not supplied, say so instead of guessing.",
  },
  {
    label: "SWOT analysis",
    question:
      "Write a SWOT analysis of my portfolio: strengths, weaknesses, opportunities and threats, at most four points each, every point backed by a figure from my data, the market context or a dated news item. Finish with the three questions I should think about.",
  },
  {
    label: "Risk & safety score",
    question:
      "Give my portfolio a risk score from 1 (very low) to 10 (very high) and show the working: weighted volatility, worst drawdown, each holding's share of the total risk, and currency risk. Compare the score with my risk tolerance and horizon and say plainly whether they match. Then check safety beyond price swings: how much sits with one platform or one issuer, which investor and deposit protection limits apply (check them live), and how easily each holding can be sold.",
  },
  {
    label: "Set up a goal",
    question:
      "Help me set up a financial goal. Interview me one question at a time, at most 8 questions: what the goal is, how much it needs, by when, what I can add each month, what I already have for it (an amount, or a share of my portfolio), how I would react to a 20% drop on the way, and any fixed constraints. Wait for each answer before asking the next. Then summarise the goal, show how my current portfolio and contributions cover it in a pessimistic, a base and an optimistic case with the assumptions stated, and give me a short version to paste into the Notes field of my investing preferences in Settings.",
  },
  {
    label: "Platforms & fees",
    question:
      "Review where I hold my investments and what that costs me. Compare the platforms in my purchases, and the main alternatives available in my country, on commissions, currency conversion, custody and account fees, plans, investor protection and product range, checked live with sources and dates. Then set out my options (keep as is, consolidate or split) with the cost, risk and effort of each, and for government securities whether buying directly from the issuer is better. If you cannot see my platforms, my country or my fund fees, ask me first.",
  },
  {
    label: "Stress test",
    question:
      "Stress-test my portfolio, one scenario at a time: global equities fall 20%; interest rates rise 1 percentage point; oil rises 30%; the US dollar moves 10% either way against my reporting currency; a 2008-style crisis. Use my weights, the correlations and my currency exposure. For each scenario give a rough range for the whole portfolio, the holdings hit hardest, and the assumptions behind the estimate.",
  },
  {
    label: "Five perspectives",
    question:
      "Review my portfolio from five perspectives, a short paragraph each: a cautious investor close to retirement, a long-term growth investor, a macro strategist, a planner focused on costs and taxes, and a sceptic looking for what could go badly wrong. Then say where they agree and where they disagree.",
  },
  {
    label: "Rules & drift check",
    question:
      "Check my portfolio against my standing rules, my preferences and my notes, and list every breach or drift with its size. Then describe three ways to rebalance (new money only, partial, full) with the effect, costs and tax points of each, without naming instruments to buy or sell.",
  },
  {
    label: "Buying habits",
    question:
      "Review my buying habits from my purchase history: did I buy after big rises or during drawdowns, how regular are my contributions, how spread across platforms, and do the purchases follow my rules and horizon? Score each from 0 to 5 with the evidence, then suggest process rules that would help. If you cannot see my purchases because this model gets relative figures only, say so in one line and stop.",
  },
  {
    label: "What to watch next",
    question: "What should I watch next: upcoming events, price levels and news that could affect my holdings, with dates where known.",
  },
];
export const QUICK_PROMPTS_SHOWN = 6;

export function newId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

export function loadChat(): Chat {
  const c = readSession<Chat>(CHAT_KEY);
  if (!c || !Array.isArray(c.messages)) return { providerId: null, messages: [] };
  const messages = c.messages.filter((m) => m && (m.role === "user" || m.role === "assistant") && typeof m.content === "string");
  return { providerId: typeof c.providerId === "string" ? c.providerId : null, messages };
}

export function saveChat(chat: Chat): void {
  writeSession(CHAT_KEY, { providerId: chat.providerId, messages: chat.messages.slice(-80) });
}

export function clearChat(): void {
  removeSession(CHAT_KEY);
}

/** The Overview card hands a question over to the advisor page through sessionStorage. */
export function queueQuestion(question: string, holding?: string): void {
  writeSession(PENDING_KEY, { question, holding: holding ?? null });
}

export function takeQueuedQuestion(): { question: string; holding: string | null } | null {
  const p = readSession<{ question?: unknown; holding?: unknown }>(PENDING_KEY);
  removeSession(PENDING_KEY);
  if (!p || typeof p.question !== "string" || !p.question.trim()) return null;
  return { question: p.question, holding: typeof p.holding === "string" ? p.holding : null };
}

/** The last turns before a question, as the API wants them: plain question and answer text, at most 24 entries. */
export function historyBefore(messages: readonly ChatMessage[], index: number): ChatTurn[] {
  return messages
    .slice(0, index)
    .map((m) => ({ role: m.role, content: m.content }) satisfies ChatTurn)
    .slice(-MAX_HISTORY);
}

/** Only http(s) links are ever rendered as links. */
export function safeUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.href : null;
  } catch {
    return null;
  }
}

export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 14) return `${d} d ago`;
  return new Date(t).toISOString().slice(0, 10);
}
