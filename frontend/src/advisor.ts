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

export const QUICK_PROMPTS: readonly QuickPrompt[] = [
  { label: "Daily briefing", question: "Daily briefing: what moved in my portfolio today, what matters in the news, and what (if anything) deserves my attention?" },
  { label: "What changed this week?", question: "What changed in my portfolio and in the markets this week?" },
  { label: "Risk & concentration check", question: "Check my portfolio for risk and concentration: position sizes, how my holdings move together, and currency exposure." },
  { label: "Correlations & global trends", question: "How do my holdings relate to each other and to global trends such as rates, oil, the dollar and the major indices?" },
  { label: "What should I watch next?", question: "What should I watch next: events, levels and news that could affect my holdings?" },
];

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
