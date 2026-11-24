import { Coffee } from "lucide-react";

/**
 * The donation link as an Aurora-gradient button. Dark text on the bright gradient keeps >= 5.2:1
 * contrast on every stop in both themes. In the collapsed sidebar only the icon shows, so the
 * accessible name comes from aria-label rather than the (then hidden) text.
 */
export function CoffeeButton({ url }: { url: string }) {
  return (
    <a className="coffee" href={url} target="_blank" rel="noopener noreferrer" aria-label="Buy me a coffee (opens in a new tab)" title="Buy me a coffee">
      <Coffee />
      <span>Buy me a coffee</span>
    </a>
  );
}
