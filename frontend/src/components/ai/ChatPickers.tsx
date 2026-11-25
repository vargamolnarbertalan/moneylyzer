import { useId } from "react";
import type { Chat } from "../../advisor";
import type { Effort, Provider } from "../../api";
import { EFFORT_LABEL, effortOptions } from "../../effort";
import { Select } from "../Select";

/** Which model a chat goes to, and at which effort: the same rule on every page that starts or continues one. */
export function chatModel(chat: Chat, list: Provider[]) {
  const selected = list.find((p) => p.id === chat.providerId) ?? list.find((p) => p.is_default) ?? list[0] ?? null;
  const takesEffort = selected?.takes_effort === true;
  // The choice belongs to the model it was made for: any other model (a new default, a deleted one) starts from its own.
  const chosen = selected && chat.providerId === selected.id ? (chat.effort ?? null) : null;
  const effort: Effort | null = selected && takesEffort ? (chosen ?? selected.effort) : null;
  const overridden = takesEffort && chosen != null && chosen !== selected?.effort;
  return { selected, takesEffort, effort, overridden };
}

/** The chat as it is after picking an effort: tied to the model it was picked for. */
export const withEffort = (c: Chat, selected: Provider | null, effort: Effort): Chat => ({
  ...c,
  providerId: selected?.id ?? c.providerId,
  effort,
});

interface ChatPickersProps {
  chat: Chat;
  list: Provider[];
  loading: boolean;
  disabled?: boolean;
  /** Another model starts a new conversation; the caller clears it. */
  onModel: (providerId: string) => void;
  onEffort: (effort: Effort) => void;
  /** The model picker's id, so another control can bring it into view. */
  modelId?: string;
  className?: string;
}

/** Model and effort pickers. Effort applies to the current conversation only; the model's setting is never changed. */
export function ChatPickers({ chat, list, loading, disabled, onModel, onEffort, modelId, className }: ChatPickersProps) {
  const uid = useId();
  const mid = modelId ?? `${uid}m`;
  const { selected, takesEffort, effort, overridden } = chatModel(chat, list);
  const ongoing = chat.messages.length > 0;
  return (
    <div className={`chat-pickers ${className ?? ""}`.trim()}>
      <div className="field chat-model">
        <label htmlFor={mid}>Model</label>
        <Select
          id={mid}
          value={selected?.id ?? ""}
          disabled={disabled || list.length === 0}
          noClosedTypeahead
          aria-describedby={ongoing ? `${uid}mh` : undefined}
          onValueChange={(v) => {
            if (v !== chat.providerId) onModel(v);
          }}
          options={
            list.length === 0
              ? [{ value: "", label: loading ? "Loading…" : "No model" }]
              : list.map((p) => ({ value: p.id, label: `${p.name} · ${p.model}` }))
          }
        />
        {ongoing && (
          <span id={`${uid}mh`} className="help">
            Changing the model starts a new conversation.
          </span>
        )}
      </div>
      <div className="field chat-effort">
        {takesEffort && effort ? (
          <>
            <label htmlFor={`${uid}e`}>Effort</label>
            <Select
              id={`${uid}e`}
              value={effort}
              disabled={disabled}
              noClosedTypeahead
              aria-describedby={`${uid}eh`}
              onValueChange={(v) => onEffort(v as Effort)}
              options={effortOptions()}
            />
            <span id={`${uid}eh`} className="help">
              {overridden && selected ? `This chat only (saved: ${EFFORT_LABEL[selected.effort].toLowerCase()}).` : "Higher is slower and uses more."}
            </span>
          </>
        ) : (
          <>
            <span className="lbl">Effort</span>
            <span className="static-value">
              {!selected ? "—" : selected.kind === "openai_compat" ? "Set by the provider" : "Not supported by this model"}
            </span>
          </>
        )}
      </div>
    </div>
  );
}
