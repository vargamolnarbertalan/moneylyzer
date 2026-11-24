import type { ReactNode } from "react";
import { CircleAlert, CircleCheck, Info, TriangleAlert, X, type LucideIcon } from "lucide-react";

type Tone = "warn" | "err" | "ok" | "info";

const ICON: Record<Tone, LucideIcon> = { warn: TriangleAlert, err: CircleAlert, ok: CircleCheck, info: Info };

interface AlertProps {
  tone: Tone;
  children: ReactNode;
  onDismiss?: () => void;
  className?: string;
}

/** A notice with an icon, so colour is never the only signal. Errors are announced at once. */
export function Alert({ tone, children, onDismiss, className }: AlertProps) {
  const ToneIcon = ICON[tone];
  return (
    <div className={`alert ${tone === "info" ? "" : tone} ${className ?? ""}`.trim()} role={tone === "err" ? "alert" : "status"}>
      <ToneIcon />
      <div className="alert-body">{children}</div>
      {onDismiss && (
        <button type="button" className="dismiss" onClick={onDismiss} aria-label="Dismiss">
          <X />
        </button>
      )}
    </div>
  );
}
