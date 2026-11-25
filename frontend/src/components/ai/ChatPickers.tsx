import * as DM from "@radix-ui/react-dropdown-menu";
import { Check, ChevronDown, SlidersHorizontal } from "lucide-react";
import type { Chat } from "../../advisor";
import type { Effort, Provider } from "../../api";
import { EFFORTS, EFFORT_LABEL } from "../../effort";
import { navigate } from "../../router";

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

interface ModelMenuProps {
  chat: Chat;
  list: Provider[];
  loading: boolean;
  disabled?: boolean;
  /** Another model starts a new conversation; the caller clears it. */
  onModel: (providerId: string) => void;
  onEffort: (effort: Effort) => void;
  /** Where the menu opens: above a message box at the bottom of the screen, below one higher up. */
  side?: "top" | "bottom";
}

/**
 * One compact button for the model and the effort, like Claude's own model picker: it reads
 * "claude-opus-5-5 · high" and opens a menu with both. Effort applies to the current conversation
 * only; the model's saved setting is never changed here.
 */
export function ModelMenu({ chat, list, loading, disabled, onModel, onEffort, side = "top" }: ModelMenuProps) {
  const { selected, takesEffort, effort, overridden } = chatModel(chat, list);
  const ongoing = chat.messages.length > 0;
  const effortText = effort ? (effort === "default" ? "default effort" : effort) : null;
  const name = selected ? [selected.model, effortText].filter(Boolean).join(" · ") : loading ? "Loading…" : "No model";
  const spoken = selected
    ? `Model: ${selected.name}, ${selected.model}. Effort: ${effort ? EFFORT_LABEL[effort] : selected.kind === "openai_compat" ? "set by the provider" : "not supported"}. Change`
    : name;

  return (
    <DM.Root modal={false}>
      <DM.Trigger asChild disabled={disabled || !selected}>
        <button type="button" className="model-btn" aria-label={spoken} title={selected ? `${selected.name} · ${selected.model}` : undefined}>
          <span className="model-btn-text">{name}</span>
          <ChevronDown aria-hidden="true" />
        </button>
      </DM.Trigger>
      <DM.Portal>
        <DM.Content className="glass menu-pop model-menu" side={side} align="start" sideOffset={6} collisionPadding={8}>
          <DM.Label className="menu-head">Model</DM.Label>
          <DM.RadioGroup value={selected?.id ?? ""} onValueChange={(v) => v !== chat.providerId && onModel(v)}>
            {list.map((p) => (
              <DM.RadioItem key={p.id} value={p.id} className="menu-item menu-radio">
                <span className="menu-text">
                  <span>{p.name}</span>
                  <small>{p.model}</small>
                </span>
                <DM.ItemIndicator className="menu-check">
                  <Check aria-hidden="true" />
                </DM.ItemIndicator>
              </DM.RadioItem>
            ))}
          </DM.RadioGroup>
          {ongoing && <p className="menu-note">Another model starts a new conversation.</p>}
          <DM.Separator className="menu-sep" />
          <DM.Label className="menu-head">
            Effort
            {overridden && selected && <small> · this chat only (saved: {EFFORT_LABEL[selected.effort].toLowerCase()})</small>}
          </DM.Label>
          {takesEffort && effort ? (
            <>
              <DM.RadioGroup value={effort} onValueChange={(v) => onEffort(v as Effort)}>
                {EFFORTS.map((e) => (
                  <DM.RadioItem key={e} value={e} className="menu-item menu-radio">
                    <span className="menu-text">{EFFORT_LABEL[e]}</span>
                    <DM.ItemIndicator className="menu-check">
                      <Check aria-hidden="true" />
                    </DM.ItemIndicator>
                  </DM.RadioItem>
                ))}
              </DM.RadioGroup>
              <p className="menu-note">Higher is slower and uses more of your plan or budget.</p>
            </>
          ) : (
            <p className="menu-note">{selected?.kind === "openai_compat" ? "Set by the provider." : "Not supported by this model."}</p>
          )}
          <DM.Separator className="menu-sep" />
          <DM.Item className="menu-item" onSelect={() => navigate("/settings#ai")}>
            <SlidersHorizontal aria-hidden="true" />
            Manage models
          </DM.Item>
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}
