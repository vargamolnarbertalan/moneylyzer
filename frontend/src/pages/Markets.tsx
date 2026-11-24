import { ResearchPanel } from "../components/ResearchPanel";
import { useTitle } from "../hooks";

/** Benchmarks, correlations-ready market context and official news (GET /api/research); the AI analyst uses the same data. */
export function MarketsPage() {
  useTitle("Markets");
  return <ResearchPanel />;
}
