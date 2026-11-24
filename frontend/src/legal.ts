// The "not financial advice / no liability" notice: one source for the gate, Settings -> About and
// the short notes. Changing the wording? Bump DISCLAIMER_VERSION (YYYY-MM-DD, optionally ".n") so the
// owner sees and accepts the new text once; keep DISCLAIMER.md in the repository in step.
//
// Legal basis reviewed 2026-11-24 against primary sources (EUR-Lex, Nemzeti Jogszabálytár). Not legal
// advice; the wording still needs a lawyer's sign-off before v1.0.0 is published. The liability
// paragraph is shown separately and accepted expressly at the gate (cf. Ptk. 6:78 on standard terms
// that depart substantially from the law).

export const DISCLAIMER_VERSION = "2026-11-24.1";

export const DISCLAIMER_TITLE = "Not financial advice";

export const DISCLAIMER_PARAGRAPHS: readonly string[] = [
  "Moneylyzer is free, open-source software that you run yourself. It is a tool for recording and analysing your own investments. Its author does not provide it to you as a service, and is not your adviser, broker or bank.",
  "Nothing it shows — prices, values, profit and loss, signals, scenarios or news — is investment, financial, tax or legal advice from its author or contributors. AI-generated text comes from the AI model you choose to connect; it is unverified analysis, not a recommendation suitable for your situation.",
  "Data can be delayed, incomplete or wrong, and AI models can misread data or state false things with confidence. Check anything that matters against the original sources and your broker.",
  "Investing involves risk, including the loss of the money you invest. Past performance does not predict future results. For advice suited to your situation, consult a licensed financial adviser.",
  "Moneylyzer is not affiliated with or endorsed by Yahoo, OTP Alapkezelő, BAMOSZ, Anthropic or any other AI provider, broker, bank or data source it can connect to.",
];

/** Shown separately and accepted expressly at the gate. */
export const LIABILITY_PARAGRAPH =
  "You alone decide, and you are responsible for your investment decisions and their results. The software is provided “as is”, without warranty of any kind. To the fullest extent the law allows, its author and contributors are not liable for any loss or damage arising from its use, including lost profits (see the MIT licence). Liability that the law does not allow to be excluded is not excluded.";

export const ACCEPT_LABEL =
  "I have read this notice. I accept that I use Moneylyzer at my own risk, that it is provided “as is”, and that its author and contributors are not liable for my losses to the extent the law allows.";

/** One line for places with little room (login page, notes under results). */
export const SHORT_NOTICE = "Not financial advice. Data and AI output can be wrong; you decide and are responsible for your investments.";

/** Under every AI answer. */
export const AI_ANSWER_NOTE = "AI-generated analysis from the model you connected, not financial advice. It can be wrong; check the sources and decide yourself.";
