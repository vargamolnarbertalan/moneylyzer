import { useId } from "react";
import { Sparkles } from "lucide-react";

/** The AI analyst's mark next to its heading: the Sparkles icon (the sidebar's AI analyst icon) stroked
 *  with the app's gradient (--grad in styles.css). Decorative: the heading beside it names the card. */
export function AiMark() {
  // useId can contain characters a url(#id) reference can't, and two cards may be on one page
  const id = `ai-mark-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return (
    <Sparkles className="ai-mark" aria-hidden="true" stroke={`url(#${id})`}>
      <defs>
        {/* one gradient across the whole 24x24 icon (not per stroke), angled like --grad's 110deg */}
        <linearGradient id={id} gradientUnits="userSpaceOnUse" x1="3" y1="8" x2="21" y2="15">
          <stop offset="0" stopColor="#8b6cff" />
          <stop offset="0.55" stopColor="#22d3ee" />
          <stop offset="1" stopColor="#f472b6" />
        </linearGradient>
      </defs>
    </Sparkles>
  );
}
