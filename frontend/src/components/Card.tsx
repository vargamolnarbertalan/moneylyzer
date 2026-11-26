import type { HTMLAttributes, ReactNode } from "react";
import type { TermId } from "../glossary";
import { Explain } from "./Explain";

interface CardProps extends HTMLAttributes<HTMLElement> {
  as?: "div" | "section" | "article";
}

export function Card({ as: Tag = "div", className, children, ...rest }: CardProps) {
  return (
    <Tag className={className ? `card ${className}` : "card"} {...rest}>
      {children}
    </Tag>
  );
}

/** `term`: an ⓘ after the title explaining the card (beside the heading, not inside it, so the heading's
 *  name, which labels the section, stays just the title). */
export function CardHeader({ title, aside, id, term }: { title: ReactNode; aside?: ReactNode; id?: string; term?: TermId }) {
  return (
    <div className="card-h">
      {term ? (
        <div className="card-title">
          <h2 id={id}>{title}</h2>
          <Explain term={term} />
        </div>
      ) : (
        <h2 id={id}>{title}</h2>
      )}
      {aside}
    </div>
  );
}
