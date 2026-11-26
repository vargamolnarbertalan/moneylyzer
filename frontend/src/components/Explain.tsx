// The ⓘ next to a financial term, chart or section: tap or click (or Enter/Space) opens a short plain-language
// explanation with an example; Esc, a tap outside or the close button shuts it. A popover, not a hover tooltip:
// hover doesn't exist on a phone and can't be reached with the keyboard.
import { useId } from "react";
import * as Popover from "@radix-ui/react-popover";
import { Info, X } from "lucide-react";
import { GLOSSARY, type GlossaryEntry, type TermId } from "../glossary";

/** "~" in glossary text is a non-breaking space inside a number (see glossary.ts). */
const nb = (text: string) => text.replace(/~/g, "\u00a0");

/** A label followed by its ⓘ, where the ⓘ never wraps onto a line of its own: it stays with the last word. */
export function Labelled({ text, term }: { text: string; term: TermId }) {
  const cut = text.lastIndexOf(" ");
  return (
    <>
      {text.slice(0, cut + 1)}
      <span className="keep">
        {text.slice(cut + 1)}
        {"\u00a0"}
        <Explain term={term} />
      </span>
    </>
  );
}

export function Explain({ term }: { term: TermId }) {
  const entry: GlossaryEntry = GLOSSARY[term];
  const titleId = useId();
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button type="button" className="explain-btn" aria-label={entry.kind ? `About this ${entry.kind}: ${entry.title}` : `What is ${entry.title}?`}>
          <Info aria-hidden="true" />
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          className="glass explain-pop"
          side="bottom"
          align="start"
          sideOffset={6}
          collisionPadding={12}
          aria-labelledby={titleId}
        >
          <div className="explain-head">
            <p id={titleId} className="explain-title">
              {entry.title}
            </p>
            <Popover.Close className="explain-x" aria-label="Close">
              <X aria-hidden="true" />
            </Popover.Close>
          </div>
          <p>{nb(entry.what)}</p>
          <p className="explain-example">
            <span className="explain-label">Example</span>
            {nb(entry.example)}
          </p>
          {entry.how && (
            <p className="explain-how">
              <span className="explain-label">In Moneylyzer</span>
              {nb(entry.how)}
            </p>
          )}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
