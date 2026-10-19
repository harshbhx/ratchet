import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Stats } from "@/lib/api";

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
