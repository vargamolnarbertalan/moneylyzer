import type { HTMLAttributes, ReactNode } from "react";

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

export function CardHeader({ title, aside, id }: { title: ReactNode; aside?: ReactNode; id?: string }) {
  return (
    <div className="card-h">
      <h2 id={id}>{title}</h2>
      {aside}
    </div>
  );
}
