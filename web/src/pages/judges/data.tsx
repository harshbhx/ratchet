import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { Stats } from "@/lib/api";
import { gatewayStatus, type RealStatus } from "@/lib/real-model";
import { errorMessage, type Fetched } from "./judges-model";

/**
 * One read from the gateway this page is served by. Never throws: a failure is a `Fetched` error with the status (0
 * when nothing answered) and the gateway's own message, which the chapters turn into a "not available" line.
 */
export async function getJson<T>(path: string, init?: RequestInit): Promise<Fetched<T>> {
  let r: Response;
  try {
    r = await fetch(path, { ...init, headers: { "content-type": "application/json", ...(init?.headers ?? {}) } });
  } catch {
    return { state: "error", status: 0, message: "" };
  }
  let body: unknown = null;
  const text = await r.text().catch(() => "");
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!r.ok) return { state: "error", status: r.status, message: errorMessage(body) };
  // A static host answers an unknown /ratchet path with the app's HTML: that is no gateway.
  if (typeof body === "string" && /^\s*</.test(body)) return { state: "error", status: 0, message: "" };
  return { state: "ok", data: body as T };
}

/** The same, for a body that is text (NDJSON). */
export async function getText(path: string): Promise<Fetched<string>> {
  try {
    const r = await fetch(path);
    const text = await r.text();
    if (!r.ok) {
      let body: unknown = null;
      try {
        body = JSON.parse(text);
      } catch {
        /* not JSON */
      }
      return { state: "error", status: r.status, message: errorMessage(body) };
    }
    if (/^\s*</.test(text)) return { state: "error", status: 0, message: "" };
    return { state: "ok", data: text };
  } catch {
    return { state: "error", status: 0, message: "" };
  }
}

export type DemoAction = "day" | "break" | "outage";

/** A request sent from this page's "Send a request" box, kept in memory for the cascade chapter. */
export interface SentRequest {
  requestId: string;
  task: string;
  servedBy: string;
  model: string;
  route: string | null;
  at: number;
}

interface JudgesState {
  sent: SentRequest[];
  addSent: (r: SentRequest) => void;
  stats: Stats | null;
  statsRead: Fetched<Stats>;
  /** Simulation on the mock demo, measured on a live gateway, null while unknown. */
  status: RealStatus | null;
  mock: boolean;
  /** Bumped whenever the gateway's state may have moved (a new stats read with more requests, a demo action). */
  generation: number;
  refresh: () => Promise<void>;
  busy: DemoAction | null;
  /** Run a mock-only demo action, then read the stats again. Returns the line to show. */
  act: (a: DemoAction) => Promise<{ ok: boolean; text: string }>;
}

const Ctx = createContext<JudgesState | null>(null);

export function useJudges(): JudgesState {
  const v = useContext(Ctx);
  if (!v) throw new Error("useJudges outside JudgesProvider");
  return v;
}

const ACTION: Record<DemoAction, { path: string; done: (b: Record<string, unknown>) => string }> = {
  day: {
    path: "/ratchet/demo/day?workload=mixed&requests=1500",
    done: (b) => {
      const night = (b.night ?? {}) as { trained?: boolean; version?: number; reason?: string };
      return night.trained && night.version
        ? `Simulated day ${String(b.day ?? "")} ran; Night School trained v${night.version}.`
        : `Simulated day ${String(b.day ?? "")} ran; no training tonight${night.reason ? ` (${String(night.reason).replace(/_/g, " ")})` : ""}.`;
    },
  },
  break: {
    path: "/ratchet/demo/break-next",
    done: (b) => `The next trained student (${String(b.corrupt ?? "next version")}) will be deployed corrupted. Run a day to watch the gate catch it.`,
  },
  outage: {
    path: "/ratchet/demo/student-outage?seconds=45",
    done: (b) => `The student endpoint is down for ${String(b.seconds ?? 45)} s. Requests in that time go to the teacher.`,
  },
};

/** Polls `/ratchet/stats` and runs the mock demo's actions, for every chapter on the page. */
export function JudgesProvider({ children }: { children: ReactNode }) {
  const [statsRead, setStatsRead] = useState<Fetched<Stats>>({ state: "loading" });
  const [generation, setGeneration] = useState(0);
  const [busy, setBusy] = useState<DemoAction | null>(null);
  const lastRequests = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    const r = await getJson<Stats>("/ratchet/stats");
    setStatsRead((prev) => (r.state === "error" && prev.state === "ok" ? prev : r));
    if (r.state === "ok") {
      const n = r.data.totals?.requests ?? 0;
      if (lastRequests.current !== n) {
        lastRequests.current = n;
        setGeneration((g) => g + 1);
      }
    }
  }, []);

  useEffect(() => {
    void refresh();
    const id = window.setInterval(() => void refresh(), 15000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const act = useCallback(
    async (a: DemoAction) => {
      setBusy(a);
      try {
        const r = await getJson<Record<string, unknown>>(ACTION[a].path, { method: "POST" });
        await refresh();
        setGeneration((g) => g + 1);
        if (r.state === "ok") return { ok: true, text: ACTION[a].done(r.data) };
        if (r.state === "error" && r.status === 409) return { ok: false, text: "Another demo action is running. Try again in a moment." };
        if (r.state === "error" && r.status === 403) return { ok: false, text: "Demo controls are on in mock mode only." };
        if (r.state === "error" && r.status === 0) return { ok: false, text: "The gateway did not answer." };
        return { ok: false, text: r.state === "error" && r.message ? r.message : "The action failed." };
      } finally {
        setBusy(null);
      }
    },
    [refresh],
  );

  const [sent, setSent] = useState<SentRequest[]>([]);
  const addSent = useCallback((r: SentRequest) => {
    setSent((xs) => [r, ...xs.filter((x) => x.requestId !== r.requestId)].slice(0, 8));
    setGeneration((g) => g + 1);
  }, []);

  const stats = statsRead.state === "ok" ? statsRead.data : null;
  const value = useMemo<JudgesState>(
    () => ({
      sent,
      addSent,
      stats,
      statsRead,
      status: gatewayStatus(stats?.mode),
      mock: stats?.mode === "mock" && (stats.demo?.controls ?? true) !== false,
      generation,
      refresh,
      busy,
      act,
    }),
    [sent, addSent, stats, statsRead, generation, refresh, busy, act],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/**
 * Read one route, again whenever the gateway's state may have moved. `path` null waits (a version id not known yet).
 * Keeps the last good answer while a new read runs, so numbers do not flash away.
 */
export function useGateway<T>(path: string | null): Fetched<T> {
  const { generation } = useJudges();
  const [res, setRes] = useState<Fetched<T>>({ state: path ? "loading" : "idle" });
  useEffect(() => {
    if (!path) {
      setRes({ state: "idle" });
      return;
    }
    let live = true;
    void getJson<T>(path).then((r) => {
      if (live) setRes((prev) => (r.state === "error" && prev.state === "ok" && r.status === 0 ? prev : r));
    });
    return () => {
      live = false;
    };
  }, [path, generation]);
  return res;
}

/** The same for a text body. */
export function useGatewayText(path: string | null): Fetched<string> {
  const { generation } = useJudges();
  const [res, setRes] = useState<Fetched<string>>({ state: path ? "loading" : "idle" });
  useEffect(() => {
    if (!path) {
      setRes({ state: "idle" });
      return;
    }
    let live = true;
    void getText(path).then((r) => {
      if (live) setRes(r);
    });
    return () => {
      live = false;
    };
  }, [path, generation]);
  return res;
}
