import { useEffect, useState, type CSSProperties } from "react";

/** A shimmering placeholder block for first loads. */
export function Skeleton({ w = "100%", h = 16, style }: { w?: number | string; h?: number | string; style?: CSSProperties }) {
  return <span className="skel" style={{ width: w, height: h, ...style }} aria-hidden="true" />;
}

export function OverviewSkeleton() {
  return (
    <div role="status" aria-label="Loading portfolio">
      <div className="hero">
        {[0, 1, 2].map((i) => (
          <div key={i} className="card">
            <Skeleton w="40%" h={14} />
            <Skeleton w="70%" h={34} style={{ margin: "12px 0 8px" }} />
            <Skeleton w="50%" h={12} />
          </div>
        ))}
      </div>
      <div className="grid">
        <div className="card">
          <Skeleton w="30%" h={18} />
          <Skeleton h={220} style={{ marginTop: 14 }} />
        </div>
        <div className="card">
          <Skeleton w="40%" h={18} />
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} h={22} style={{ marginTop: 14 }} />
          ))}
        </div>
        <div className="card span2">
          <Skeleton w="20%" h={18} />
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} h={40} style={{ marginTop: 12 }} />
          ))}
        </div>
      </div>
      <span className="sr-only">Loading…</span>
    </div>
  );
}

/** Stand-in for a page whose code is still loading. It appears only after 350ms (a timer,
 *  so it holds with reduced motion too), so a fast load shows nothing instead of grey blocks. */
export function PageSkeleton() {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    // Longer than React's 300ms hold on a just-shown fallback, so a fast load never flashes it.
    const timer = window.setTimeout(() => setVisible(true), 350);
    return () => window.clearTimeout(timer);
  }, []);
  if (!visible) return null;
  return (
    <div className="skel-page" role="status" aria-label="Loading page">
      <div className="grid">
        <div className="card span2">
          <Skeleton w="28%" h={18} />
          <Skeleton h={180} style={{ marginTop: 14 }} />
        </div>
        <div className="card">
          <Skeleton w="40%" h={18} />
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} h={22} style={{ marginTop: 14 }} />
          ))}
        </div>
        <div className="card">
          <Skeleton w="40%" h={18} />
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} h={22} style={{ marginTop: 14 }} />
          ))}
        </div>
      </div>
      <span className="sr-only">Loading…</span>
    </div>
  );
}

export function BlockSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="card" role="status" aria-label="Loading">
      <Skeleton w="35%" h={18} />
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} h={36} style={{ marginTop: 12 }} />
      ))}
      <span className="sr-only">Loading…</span>
    </div>
  );
}
