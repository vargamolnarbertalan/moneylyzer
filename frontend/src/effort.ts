import type { Effort } from "./api";

/** Effort levels in order; "default" sends none, so the model's own default applies. */
export const EFFORTS: readonly Effort[] = ["default", "low", "medium", "high", "xhigh", "max"];

export const EFFORT_LABEL: Record<Effort, string> = {
  default: "Model default",
  low: "low",
  medium: "medium",
  high: "high",
  xhigh: "xhigh",
  max: "max",
};

export const effortOptions = () => EFFORTS.map((value) => ({ value, label: EFFORT_LABEL[value] }));

/** "effort high", "model default effort": for labels next to a model name. */
export const effortPhrase = (effort: Effort) => (effort === "default" ? "model default effort" : `effort ${effort}`);
