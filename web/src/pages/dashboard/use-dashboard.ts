import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type CostReport, type Stats } from "@/lib/api";

/**
 * Poll /ratchet/stats. Keeps the last good snapshot when a poll fails (the page shows a
 * stale banner instead of going blank), skips polls while the tab is hidden, and refreshes
 * as soon as it becomes visible again.
 */
export function useDashboardStats(intervalMs = 15000) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const inflight = useRef<Promise<Stats | null> | null>(null);

  const refresh = useCallback(async (): Promise<Stats | null> => {
    if (inflight.current) return inflight.current;
    setRefreshing(true);
    const p = (async () => {
      try {
        const s = await api.stats();
        setStats(s);
        setError(null);
        setUpdatedAt(Date.now());
        return s;
      } catch (e) {
        setError(e instanceof Error ? e.message : "unreachable");
        return null;
      } finally {
        setLoading(false);
        setRefreshing(false);
        inflight.current = null;
      }
    })();
    inflight.current = p;
    return p;
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") void refresh();
    }, intervalMs);
    const onVis = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [refresh, intervalMs]);

  return { stats, error, updatedAt, loading, refreshing, refresh };
}

/** Re-render every `ms` so relative times ("12 s ago") stay current. */
export function useNow(ms = 5000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return now;
}

export type CostState = {
  /** The last report that loaded; kept while a refetch fails, so the panel never blanks on one bad poll. */
  report: CostReport | null;
  error: string | null;
  /** The gateway has no GET /ratchet/cost (older than v0.5): the panel hides and polling stops. */
  missing: boolean;
  loading: boolean;
};

/** Enough of a report to draw the panel; anything else in it is optional. */
function isCostReport(x: unknown): x is CostReport {
  const r = x as CostReport | null;
  const view = (v: unknown) => v != null && typeof (v as { total_usd?: unknown }).total_usd === "number";
  return r != null && typeof r === "object" && typeof r.recommendation === "string" && view(r.views?.per_token) && view(r.views?.gpu_hours);
}

/**
 * GET /ratchet/cost, fetched again whenever `key` changes (pass the stats poll's `updatedAt`, so the report follows
 * every poll and demo action without a second timer). A newer response always wins over an older one.
 */
export function useCostReport(key: number | null): CostState {
  const [state, setState] = useState<CostState>({ report: null, error: null, missing: false, loading: true });
  const seq = useRef(0);
  const missing = useRef(false);

  useEffect(() => {
    if (key == null || missing.current) return;
    const id = ++seq.current;
    let cancelled = false;
    (async () => {
      try {
        const r: unknown = await api.cost();
        if (cancelled || id !== seq.current) return;
        if (!isCostReport(r)) throw new Error("the cost report has an unexpected shape");
        setState({ report: r, error: null, missing: false, loading: false });
      } catch (e) {
        if (cancelled || id !== seq.current) return;
        const gone = e instanceof ApiError && (e.status === 404 || e.status === 405);
        if (gone) missing.current = true;
        const error = e instanceof Error ? e.message : "unreachable";
        setState((s) => ({ report: gone ? null : s.report, error, missing: gone, loading: false }));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [key]);

  return state;
}
