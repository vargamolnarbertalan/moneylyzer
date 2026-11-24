import type { ReactNode } from "react";
import * as RS from "@radix-ui/react-select";
import { Check, ChevronDown, ChevronUp } from "lucide-react";

export interface SelectOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

interface SelectProps {
  /** Pair with <label htmlFor={id}>: the trigger is a <button>, which a label can name. */
  id?: string;
  value: string;
  onValueChange: (value: string) => void;
  options: readonly SelectOption[];
  disabled?: boolean;
  /** Extra classes for the trigger (it always has "input sel-trigger"). */
  className?: string;
  "aria-label"?: string;
  "aria-describedby"?: string;
  /** Radix focuses the trigger when the list closes; call e.preventDefault() to move focus elsewhere. */
  onCloseAutoFocus?: (e: Event) => void;
  /** Typing a letter on the CLOSED trigger normally selects the first match at once; turn that off where
   *  a change has wide effects (the reporting currency). Opening with Space/Enter/arrows still works. */
  noClosedTypeahead?: boolean;
}

// Radix reserves "" (it means "no value"); our filters use "" for "All", so it is swapped for a sentinel.
const EMPTY = "\u0000empty";
const enc = (v: string) => (v === "" ? EMPTY : v);
const dec = (v: string) => (v === EMPTY ? "" : v);

/**
 * The app's dropdown: Radix Select (keyboard, typeahead, screen-reader semantics, collision handling)
 * with the frosted-glass list from styles.css (.sel-*). Use it instead of a native <select>.
 */
export function Select({ id, value, onValueChange, options, disabled, className, onCloseAutoFocus, noClosedTypeahead, ...aria }: SelectProps) {
  return (
    <RS.Root value={enc(value)} onValueChange={(v) => onValueChange(dec(v))} disabled={disabled}>
      <RS.Trigger
        id={id}
        className={`input sel-trigger${className ? ` ${className}` : ""}`}
        onKeyDown={(e) => {
          // Radix skips its own handler when this one prevents the default.
          if (noClosedTypeahead && e.key.length === 1 && e.key !== " " && !e.ctrlKey && !e.metaKey && !e.altKey) e.preventDefault();
        }}
        {...aria}
      >
        <RS.Value />
        <RS.Icon className="sel-chevron">
          <ChevronDown />
        </RS.Icon>
      </RS.Trigger>
      <RS.Portal>
        <RS.Content className="glass sel-pop" position="popper" sideOffset={6} collisionPadding={8} onCloseAutoFocus={onCloseAutoFocus}>
          <RS.ScrollUpButton className="sel-scroll">
            <ChevronUp />
          </RS.ScrollUpButton>
          <RS.Viewport className="sel-list">
            {options.map((o) => (
              <RS.Item key={enc(o.value)} value={enc(o.value)} disabled={o.disabled} className="sel-item">
                <RS.ItemText>{o.label}</RS.ItemText>
                <RS.ItemIndicator className="sel-check">
                  <Check />
                </RS.ItemIndicator>
              </RS.Item>
            ))}
          </RS.Viewport>
          <RS.ScrollDownButton className="sel-scroll">
            <ChevronDown />
          </RS.ScrollDownButton>
        </RS.Content>
      </RS.Portal>
    </RS.Root>
  );
}
