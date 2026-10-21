import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ListTree, PenLine } from "lucide-react";
import type { ServedBy } from "@/lib/api";
import { cn } from "@/lib/utils";
import { studentCopy } from "@/lib/who-model";
import { AGENTS, AgentDisc, stateLabel } from "./bits";
import {
  PLACEHOLDER_PER_QUERY,
  PLACEHOLDER_QUERIES,
  READ_LIMIT,
  agentState,
  type AgentRow,
  type Derived,
  type NodeState,
  type RunState,
} from "./state";

/**
 * Scout's five agents as a live pipeline: Planner → queries → one dot per triage call →
 * one dot per reader call → Writer. Dots take the color of who answered (owned blue = your
 * model, rented orange = Ultra). Laid out left-to-right on wide screens and top-to-bottom on phones.
 * Geometry is in CSS pixels (the SVG's viewBox tracks its measured width) so text never scales.
 */

type Who = ServedBy | null;
type Pt = { x: number; y: number };

interface CallNode {
  key: string;
  r: number;
  x: number;
  y: number;
  state: NodeState;
  who: Who;
  faded: boolean;
  escalated: boolean;
  cite: number | null;
  title: string;
}
interface QueryNode {
  key: string;
  x: number;
  y: number;
  w: number;
  h: number;
  state: NodeState;
  text: string;
  compact: boolean;
  title: string;
}
interface Edge {
  key: string;
  a: Pt;
  b: Pt;
  state: "idle" | "active" | "lit";
  who: Who;
  faded: boolean;
}
interface Label {
  agent: AgentRow["agent"];
  x: number;
  y: number;
  align: "center" | "left";
  state: NodeState;
  progress: string;
}

const AGENT_R = 22;
const DOT_R = 7;

function whoColor(who: Who) {
  return who === "student" ? "var(--owned)" : who === "teacher" ? "var(--rented)" : "var(--muted)";
}

function truncate(s: string, max: number) {
  return s.length <= max ? s : s.slice(0, Math.max(1, max - 1)).trimEnd() + "…";
}

interface Model {
  w: number;
  h: number;
  vertical: boolean;
  planner: Pt & { state: NodeState; who: Who };
  writer: Pt & { state: NodeState; who: Who };
  queries: QueryNode[];
  triage: CallNode[];
  reader: CallNode[];
  edges: Edge[];
  labels: Label[];
}

function buildModel(run: RunState, d: Derived, w: number): Model {
  const vertical = w < 720;
  const running = run.status === "running";
  const halted = run.status === "error" || run.status === "stopped";
  const st = (a: AgentRow["agent"]) => agentState(run, d, a);

  // ---- slots: query groups and their result ids (placeholders until search returns)
  const groupsSrc =
    d.groups.length > 0 ? d.groups : Array.from({ length: PLACEHOLDER_QUERIES }, (_, qi) => ({ query: null, qi, step: null, ids: null }));
  const groups = groupsSrc.map((g) => ({ ...g, n: g.ids ? g.ids.length : PLACEHOLDER_PER_QUERY }));
  const totalSlots = groups.reduce((a, g) => a + g.n, 0);

  // ---- reader slots: the read queue plus ghosts for reads that may still come
  const untriaged = d.searchesDone ? d.order.filter((id) => !run.triage[id]).length : Infinity;
  const knownRelevant = d.readQueue.length;
  const ghosts = d.triageDone || halted || run.status === "done" ? 0 : Math.max(0, Math.min(READ_LIMIT, knownRelevant + untriaged) - knownRelevant);
  const readerSlots = knownRelevant + ghosts;

  // ---- geometry
  let plannerPt: Pt, writerPt: Pt, h: number;
  let dotR = DOT_R;
  let queryPos: (qi: number, n: number) => { x: number; y: number; w: number; h: number };
  let triagePos: (idx: number, qi: number) => Pt;
  let readerPos: (k: number, n: number) => Pt;
  let labelPos: Record<AgentRow["agent"], { x: number; y: number; align: "center" | "left" }>;

  // index of each slot within the triage column, with group gaps
  const slotIndex: { qi: number; k: number; flat: number }[] = [];
  groups.forEach((g, gi) => {
    for (let k = 0; k < g.n; k++) slotIndex.push({ qi: gi, k, flat: slotIndex.length });
  });

  if (!vertical) {
    const top = 92;
    const pad = 92;
    const span = w - 2 * pad;
    const cx = [pad, pad + span * 0.25, pad + span * 0.53, pad + span * 0.78, w - pad];
    const sp = 22;
    const gap = 18;
    const colH = Math.max(totalSlots * sp + Math.max(0, groups.length - 1) * gap, 7 * 30);
    h = top + colH + 14;
    const cy = top + colH / 2;
    const startY = cy - (totalSlots * sp + Math.max(0, groups.length - 1) * gap) / 2 + sp / 2;
    const ty = (flat: number, qi: number) => startY + flat * sp + qi * gap;
    plannerPt = { x: cx[0], y: cy };
    writerPt = { x: cx[4], y: cy };
    const pillW = Math.max(120, Math.min(196, (cx[2] - cx[0]) * 0.5));
    queryPos = (qi) => {
      const first = slotIndex.find((s) => s.qi === qi);
      const g = groups[qi];
      const y0 = first ? ty(first.flat, qi) : cy;
      const y1 = first ? ty(first.flat + g.n - 1, qi) : cy;
      return { x: cx[1], y: (y0 + y1) / 2, w: pillW, h: 30 };
    };
    triagePos = (flat, qi) => ({ x: cx[2], y: ty(flat, qi) });
    readerPos = (k, n) => {
      const rsp = Math.min(36, colH / Math.max(1, n));
      return { x: cx[3], y: cy + (k - (n - 1) / 2) * rsp };
    };
    labelPos = {
      planner: { x: cx[0], y: 8, align: "center" },
      searcher: { x: cx[1], y: 8, align: "center" },
      triage: { x: cx[2], y: 8, align: "center" },
      reader: { x: cx[3], y: 8, align: "center" },
      writer: { x: cx[4], y: 8, align: "center" },
    };
  } else {
    // Phones: rows top to bottom, labels in a left gutter so edges never cross text.
    const pad = 6;
    const labelW = w >= 340 ? 106 : 96;
    const x0 = labelW;
    const x1 = w - pad;
    const mid = (x0 + x1) / 2;
    const avail = x1 - x0;
    const gap = 40;
    const rowH: Record<AgentRow["agent"], number> = { planner: 52, searcher: 50, triage: 50, reader: 50, writer: 52 };
    const c = {} as Record<AgentRow["agent"], number>;
    let y = 4;
    for (const a of ["planner", "searcher", "triage", "reader", "writer"] as const) {
      c[a] = y + rowH[a] / 2;
      y += rowH[a] + gap;
    }
    h = y - gap + 6;
    plannerPt = { x: mid, y: c.planner };
    writerPt = { x: mid, y: c.writer };
    const gGap = 10;
    const sp = Math.min(24, (avail - Math.max(0, groups.length - 1) * gGap) / Math.max(1, totalSlots));
    dotR = Math.max(4.5, Math.min(DOT_R, sp / 2 - 2));
    const tTotal = totalSlots * sp + Math.max(0, groups.length - 1) * gGap;
    const tx = (flat: number, qi: number) => mid - tTotal / 2 + sp / 2 + flat * sp + qi * gGap;
    triagePos = (flat, qi) => ({ x: tx(flat, qi), y: c.triage });
    queryPos = (qi) => {
      const g = groups[qi];
      const first = slotIndex.find((s) => s.qi === qi);
      const xa = first ? tx(first.flat, qi) : mid;
      const xb = first ? tx(first.flat + g.n - 1, qi) : mid;
      const pw = Math.max(40, Math.min(120, xb - xa + sp + gGap - 8));
      return { x: (xa + xb) / 2, y: c.searcher, w: pw, h: 28 };
    };
    readerPos = (k, n) => {
      const rsp = Math.min(40, avail / Math.max(1, n));
      return { x: mid + (k - (n - 1) / 2) * rsp, y: c.reader };
    };
    labelPos = {
      planner: { x: 0, y: c.planner - 25, align: "left" },
      searcher: { x: 0, y: c.searcher - 25, align: "left" },
      triage: { x: 0, y: c.triage - 25, align: "left" },
      reader: { x: 0, y: c.reader - 25, align: "left" },
      writer: { x: 0, y: c.writer - 25, align: "left" },
    };
  }

  // ---- nodes
  const plannerState = st("planner");
  const writerState = st("writer");
  const queries: QueryNode[] = groups.map((g, qi) => {
    const p = queryPos(qi, groups.length);
    const state: NodeState = g.step
      ? "done"
      : halted
        ? run.planner
          ? "skipped"
          : "skipped"
        : run.planner && running
          ? "working"
          : run.status === "idle"
            ? "idle"
            : "queued";
    const maxChars = Math.floor((p.w - 30) / 6.6);
    return {
      key: `q${qi}`,
      ...p,
      state: run.status === "idle" ? "idle" : state,
      text: vertical ? `Q${qi + 1}` : g.query ? truncate(g.query, maxChars) : "",
      compact: vertical,
      title: g.query ? `Search: ${g.query}` : "Search query",
    };
  });

  const posById = new Map<number, Pt>();
  const triage: CallNode[] = slotIndex.map(({ qi, k, flat }) => {
    const p = triagePos(flat, qi);
    const id = groups[qi].ids?.[k];
    const step = id != null ? run.triage[id] : undefined;
    if (id != null) posById.set(id, p);
    let state: NodeState;
    if (step) state = "done";
    else if (run.status === "idle") state = "idle";
    else if (halted) state = "skipped";
    else if (id != null && running) state = "working";
    else state = "queued";
    const r = id != null ? run.results[id] : undefined;
    return {
      key: `t-${qi}-${k}`,
      r: dotR,
      ...p,
      state,
      who: step?.served_by ?? null,
      faded: !!step && step.detail?.relevant !== true,
      escalated: !!step?.escalated,
      cite: null,
      title: step
        ? `Triage · ${step.served_by === "student" ? studentCopy(d.hosted).tried : "Ultra"} · ${step.detail?.relevant ? "relevant" : "not relevant"}${step.escalated ? " · escalated" : ""}: ${r?.title ?? ""}`
        : r
          ? `Triage pending: ${r.title}`
          : "Triage call",
    };
  });

  const readIds = d.readQueue;
  const reader: CallNode[] = Array.from({ length: readerSlots }, (_, k) => {
    const p = readerPos(k, readerSlots);
    const id = readIds[k];
    const step = id != null ? run.reader[id] : undefined;
    let state: NodeState;
    if (step) state = "done";
    else if (run.status === "idle") state = "idle";
    else if (halted) state = "skipped";
    else if (id != null && d.triageDone && running) state = "working";
    else state = "queued";
    const r = id != null ? run.results[id] : undefined;
    const cite = r?.url && d.citedByUrl[r.url] != null ? d.citedByUrl[r.url] : null;
    return {
      key: `r-${k}`,
      r: dotR,
      ...p,
      state: id == null && state !== "idle" ? (halted ? "skipped" : "idle") : state,
      who: step?.served_by ?? null,
      faded: !!step && step.detail?.answers_question !== true,
      escalated: !!step?.escalated,
      cite,
      title: step
        ? `Reader · ${step.served_by === "student" ? studentCopy(d.hosted).tried : "Ultra"} · ${step.detail?.answers_question ? "answers" : "does not answer"}: ${step.detail?.key_fact ?? ""}`
        : "Reader call",
    };
  });

  // ---- edges
  const edges: Edge[] = [];
  const edgeState = (target: NodeState): Edge["state"] => (target === "done" ? "lit" : target === "working" ? "active" : "idle");
  queries.forEach((q) => {
    const a = vertical ? { x: plannerPt.x, y: plannerPt.y + AGENT_R } : { x: plannerPt.x + AGENT_R, y: plannerPt.y };
    const b = vertical ? { x: q.x, y: q.y - q.h / 2 } : { x: q.x - q.w / 2, y: q.y };
    edges.push({ key: `e-p-${q.key}`, a, b, state: edgeState(q.state), who: "tool", faded: false });
  });
  slotIndex.forEach(({ qi }, i) => {
    const q = queries[qi];
    const t = triage[i];
    const a = vertical ? { x: q.x, y: q.y + q.h / 2 } : { x: q.x + q.w / 2, y: q.y };
    const b = vertical ? { x: t.x, y: t.y - dotR } : { x: t.x - dotR, y: t.y };
    edges.push({ key: `e-${t.key}`, a, b, state: edgeState(t.state), who: t.who, faded: t.faded });
  });
  reader.forEach((r, k) => {
    const id = readIds[k];
    const from = id != null ? posById.get(id) : undefined;
    if (from) {
      const a = vertical ? { x: from.x, y: from.y + dotR } : { x: from.x + dotR, y: from.y };
      const b = vertical ? { x: r.x, y: r.y - dotR } : { x: r.x - dotR, y: r.y };
      edges.push({ key: `e-tr-${r.key}`, a, b, state: edgeState(r.state), who: r.who, faded: r.faded });
    }
    if (id != null) {
      const a = vertical ? { x: r.x, y: r.y + dotR } : { x: r.x + dotR, y: r.y };
      const b = vertical ? { x: writerPt.x, y: writerPt.y - AGENT_R } : { x: writerPt.x - AGENT_R, y: writerPt.y };
      edges.push({ key: `e-rw-${r.key}`, a, b, state: edgeState(writerState), who: run.writer?.served_by ?? "teacher", faded: r.faded });
    }
  });

  // ---- labels
  const tri = Object.values(run.triage);
  const rd = Object.values(run.reader);
  const yoursT = tri.filter((s) => s.served_by === "student").length;
  const yoursR = rd.filter((s) => s.served_by === "student").length;
  const progress: Record<AgentRow["agent"], string> = {
    planner: run.planner ? `${run.planner.detail?.queries?.length ?? 0} queries` : stateLabel(plannerState),
    searcher:
      run.status === "idle" || (!d.groups.length && halted)
        ? stateLabel(st("searcher"))
        : d.searchesDone
          ? `${d.order.length} results`
          : `${d.groups.filter((g) => g.step).length}/${d.groups.length || "?"} queries`,
    triage:
      run.status === "idle" || !d.order.length
        ? stateLabel(st("triage"))
        : vertical
          ? `${tri.length}/${d.order.length} · ${yoursT} ●`
          : `${tri.length}/${d.order.length} · ${yoursT} ${studentCopy(d.hosted).compact}`,
    reader:
      run.status === "idle" || (!d.readQueue.length && !d.triageDone)
        ? stateLabel(st("reader"))
        : vertical
          ? `${rd.length}/${d.readQueue.length}${d.triageDone ? "" : "+"} · ${yoursR} ●`
          : `${rd.length}/${d.readQueue.length}${d.triageDone ? "" : "+"} · ${yoursR} ${studentCopy(d.hosted).compact}`,
    writer: run.writer || run.done ? "answer ready" : stateLabel(writerState),
  };
  const labels: Label[] = (Object.keys(labelPos) as AgentRow["agent"][]).map((agent) => ({
    agent,
    ...labelPos[agent],
    state: st(agent),
    progress: progress[agent],
  }));

  return {
    w,
    h,
    vertical,
    planner: { ...plannerPt, state: plannerState, who: run.planner?.served_by ?? null },
    writer: { ...writerPt, state: writerState, who: run.writer?.served_by ?? (run.done ? "teacher" : null) },
    queries,
    triage,
    reader,
    edges,
    labels,
  };
}

function curve(a: Pt, b: Pt, vertical: boolean) {
  if (vertical) {
    const dy = (b.y - a.y) * 0.55;
    return `M${a.x},${a.y} C${a.x},${a.y + dy} ${b.x},${b.y - dy} ${b.x},${b.y}`;
  }
  const dx = (b.x - a.x) * 0.55;
  return `M${a.x},${a.y} C${a.x + dx},${a.y} ${b.x - dx},${b.y} ${b.x},${b.y}`;
}

const EASE = [0.22, 1, 0.36, 1] as const;

function EdgePath({ e, vertical, reduce }: { e: Edge; vertical: boolean; reduce: boolean }) {
  const d = curve(e.a, e.b, vertical);
  return (
    <g>
      <motion.path
        d={d}
        initial={false}
        animate={{ d }}
        transition={{ duration: reduce ? 0 : 0.5, ease: EASE }}
        fill="none"
        stroke="var(--line-strong)"
        strokeWidth={1}
        strokeDasharray={e.state === "idle" ? "2 4" : undefined}
        opacity={e.state === "idle" ? 0.9 : 0.55}
      />
      {e.state === "active" && (
        <path d={d} fill="none" stroke="var(--ink-2)" strokeWidth={1.25} strokeDasharray="3 5" className="scout-flow" opacity={0.6} />
      )}
      {e.state === "lit" && (
        <motion.path
          key="lit"
          d={d}
          fill="none"
          stroke={whoColor(e.who)}
          strokeWidth={e.who === "tool" ? 1.25 : 1.75}
          strokeLinecap="round"
          opacity={e.faded ? 0.28 : e.who === "tool" ? 0.55 : 0.85}
          initial={reduce ? false : { pathLength: 0 }}
          animate={{ pathLength: 1, d }}
          transition={{ duration: reduce ? 0 : 0.55, ease: EASE }}
        />
      )}
    </g>
  );
}

function Pulse({ x, y, r }: { x: number; y: number; r: number }) {
  return <circle cx={x} cy={y} r={r} fill="none" stroke="var(--ink-2)" strokeWidth={1.25} className="scout-pulse" />;
}

function Dot({ n, reduce }: { n: CallNode; reduce: boolean }) {
  const t = { duration: reduce ? 0 : 0.5, ease: EASE };
  return (
    <g>
      <title>{n.title}</title>
      {n.state === "working" && <Pulse x={n.x} y={n.y} r={n.r + 1} />}
      {n.state === "done" ? (
        <>
          {n.escalated && (
            <motion.circle
              initial={false}
              animate={{ cx: n.x, cy: n.y }}
              transition={t}
              r={n.r + 3.5}
              fill="none"
              stroke="var(--owned)"
              strokeWidth={1.5}
              strokeDasharray="2.5 2"
            />
          )}
          <motion.circle
            key="done"
            initial={reduce ? false : { scale: 0.3, opacity: 0, cx: n.x, cy: n.y }}
            animate={{ scale: 1, opacity: n.faded ? 0.38 : 1, cx: n.x, cy: n.y }}
            transition={{ ...t, duration: reduce ? 0 : 0.35 }}
            r={n.r}
            fill={whoColor(n.who)}
            style={{ transformBox: "fill-box", transformOrigin: "center" }}
          />
        </>
      ) : (
        <motion.circle
          initial={false}
          animate={{ cx: n.x, cy: n.y }}
          transition={t}
          r={n.r - 0.5}
          fill="var(--surface)"
          stroke={n.state === "working" ? "var(--ink-2)" : n.state === "queued" ? "var(--muted)" : "var(--line-strong)"}
          strokeWidth={n.state === "working" ? 1.5 : 1.4}
          strokeDasharray={n.state === "idle" || n.state === "skipped" ? "2 2.5" : undefined}
        />
      )}
    </g>
  );
}

function AgentNode({
  x,
  y,
  state,
  who,
  icon,
  reduce,
}: {
  x: number;
  y: number;
  state: NodeState;
  who: Who;
  icon: (x: number, y: number) => ReactNode;
  reduce: boolean;
}) {
  const done = state === "done";
  const fill = done ? (who === "student" ? "var(--owned-soft)" : "var(--rented-soft)") : "var(--surface)";
  const stroke = done ? whoColor(who) : state === "working" ? "var(--ink-2)" : "var(--line-strong)";
  const iconColor = done ? (who === "student" ? "text-owned-ink" : "text-rented-ink") : state === "working" ? "text-ink" : "text-muted";
  return (
    <g>
      {state === "working" && <Pulse x={x} y={y} r={AGENT_R + 2} />}
      <motion.circle
        initial={false}
        animate={{ cx: x, cy: y }}
        transition={{ duration: reduce ? 0 : 0.5, ease: EASE }}
        r={AGENT_R}
        fill={fill}
        stroke={stroke}
        strokeWidth={done ? 2 : 1.5}
        strokeDasharray={state === "idle" || state === "skipped" ? "3 3" : undefined}
        style={{ transition: "fill .3s, stroke .3s" }}
      />
      <g className={cn("pointer-events-none transition-colors", iconColor)}>{icon(x - 9, y - 9)}</g>
    </g>
  );
}

function QueryPill({ q, reduce }: { q: QueryNode; reduce: boolean }) {
  const done = q.state === "done";
  return (
    <g>
      <title>{q.title}</title>
      {q.state === "working" && (
        <rect
          x={q.x - q.w / 2 - 2}
          y={q.y - q.h / 2 - 2}
          width={q.w + 4}
          height={q.h + 4}
          rx={(q.h + 4) / 2}
          fill="none"
          stroke="var(--ink-2)"
          strokeWidth={1.25}
          className="scout-pulse"
        />
      )}
      <motion.rect
        initial={false}
        animate={{ x: q.x - q.w / 2, y: q.y - q.h / 2, width: q.w }}
        transition={{ duration: reduce ? 0 : 0.5, ease: EASE }}
        height={q.h}
        rx={q.h / 2}
        fill={done ? "var(--surface-2)" : "var(--surface)"}
        stroke={q.state === "working" ? "var(--ink-2)" : "var(--line-strong)"}
        strokeWidth={1.25}
        strokeDasharray={q.state === "idle" || q.state === "skipped" ? "3 3" : undefined}
      />
      {!q.compact && <circle cx={q.x - q.w / 2 + 13} cy={q.y} r={3} fill={done ? "var(--muted)" : "none"} stroke="var(--muted)" strokeWidth={1.25} />}
      {q.text && (
        <text
          x={q.compact ? q.x : q.x - q.w / 2 + 22}
          y={q.y}
          textAnchor={q.compact ? "middle" : "start"}
          dominantBaseline="central"
          fontSize={q.compact ? 11 : 11.5}
          fill={done ? "var(--ink-2)" : "var(--muted)"}
          className={q.compact ? "font-mono" : "font-sans"}
        >
          {q.text}
        </text>
      )}
    </g>
  );
}

function GraphLabel({ l }: { l: Label }) {
  const meta = AGENTS[l.agent];
  const left = l.align === "left";
  const route =
    meta.route === "pinned" ? (
      <span className="text-rented-ink">pinned · Ultra</span>
    ) : meta.route === "distillable" ? (
      <span className="text-owned-ink">{left ? "learns · yours" : "learns · moves to yours"}</span>
    ) : (
      <span className="text-muted">{left ? "tool · search" : "tool · web search"}</span>
    );
  // "●" in compact progress stands for calls the student answered; render it as an owned dot.
  const parts = l.progress.split("●");
  const progress =
    parts.length > 1 ? (
      <>
        {parts[0]}
        <span className="inline-block size-[7px] -translate-y-px rounded-full bg-owned align-middle" aria-hidden />
      </>
    ) : (
      l.progress
    );
  return (
    <div
      className={cn("absolute flex flex-col gap-0.5", left ? "items-start text-left" : "w-[176px] -translate-x-1/2 items-center text-center")}
      style={{ left: l.x, top: l.y }}
    >
      <div className="flex items-center gap-2">
        <AgentDisc n={meta.n} state={l.state} className="size-[22px] text-[10.5px]" />
        <span className="text-[14px] font-semibold tracking-[-0.01em] text-ink">{meta.name}</span>
      </div>
      <div className={cn("whitespace-nowrap text-[11.5px] font-medium", left && "text-[11px]")}>{route}</div>
      <div className={cn("whitespace-nowrap font-mono text-muted tabular", left ? "text-[11px]" : "text-[11.5px]")}>{progress}</div>
    </div>
  );
}

function summary(run: RunState, d: Derived) {
  if (run.status === "idle") return "Agent graph, idle. Pick a question to start a run.";
  const tri = Object.values(run.triage);
  const rd = Object.values(run.reader);
  return [
    `Planner ${run.planner ? "done, answered by Ultra" : "working"}.`,
    `Searcher: ${d.groups.filter((g) => g.step).length} of ${d.groups.length || 3} queries.`,
    `Triage: ${tri.length} of ${d.order.length || "?"} calls, ${tri.filter((s) => s.served_by === "student").length} ${studentCopy(d.hosted).byShort}.`,
    `Reader: ${rd.length} of ${d.readQueue.length} calls, ${rd.filter((s) => s.served_by === "student").length} ${studentCopy(d.hosted).byShort}.`,
    `Writer ${run.writer || run.done ? "done, answered by Ultra" : "waiting"}.`,
  ].join(" ");
}

export function AgentGraph({ run, derived, className }: { run: RunState; derived: Derived; className?: string }) {
  const wrap = useRef<HTMLDivElement | null>(null);
  const [w, setW] = useState(0);
  const reduce = !!useReducedMotion();
  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    setW(Math.round(el.getBoundingClientRect().width));
    const ro = new ResizeObserver((es) => {
      const nw = Math.round(es[0]?.contentRect.width ?? 0);
      setW((old) => (Math.abs(old - nw) >= 1 ? nw : old));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const m = useMemo(() => (w > 0 ? buildModel(run, derived, w) : null), [run, derived, w]);

  return (
    <div ref={wrap} className={cn("relative w-full [contain:layout_paint]", className)}>
      {m && (
        <>
          <svg
            width={m.w}
            height={m.h}
            viewBox={`0 0 ${m.w} ${m.h}`}
            role="img"
            aria-label={summary(run, derived)}
            className="block overflow-visible"
          >
            <style>{`
              .scout-flow { animation: scout-flow 0.9s linear infinite; }
              @keyframes scout-flow { to { stroke-dashoffset: -16; } }
              .scout-pulse { transform-box: fill-box; transform-origin: center; animation: scout-pulse 1.5s cubic-bezier(.22,1,.36,1) infinite; }
              @keyframes scout-pulse { 0% { opacity: .55; transform: scale(1); } 100% { opacity: 0; transform: scale(1.7); } }
              @media (prefers-reduced-motion: reduce) { .scout-flow, .scout-pulse { animation: none; } .scout-pulse { opacity: .4; } }
            `}</style>
            <g>
              {m.edges.map((e) => (
                <EdgePath key={e.key} e={e} vertical={m.vertical} reduce={reduce} />
              ))}
            </g>
            <AgentNode
              x={m.planner.x}
              y={m.planner.y}
              state={m.planner.state}
              who={m.planner.who}
              icon={(ix, iy) => <ListTree x={ix} y={iy} width={18} height={18} aria-hidden />}
              reduce={reduce}
            />
            <AgentNode
              x={m.writer.x}
              y={m.writer.y}
              state={m.writer.state}
              who={m.writer.who}
              icon={(ix, iy) => <PenLine x={ix} y={iy} width={18} height={18} aria-hidden />}
              reduce={reduce}
            />
            {m.queries.map((q) => (
              <QueryPill key={q.key} q={q} reduce={reduce} />
            ))}
            {m.triage.map((n) => (
              <Dot key={n.key} n={n} reduce={reduce} />
            ))}
            {m.reader.map((n) => (
              <Dot key={n.key} n={n} reduce={reduce} />
            ))}
            {m.reader.map(
              (n) =>
                n.cite != null && (
                  <text
                    key={`c-${n.key}`}
                    x={m.vertical ? n.x + n.r + 2 : n.x + n.r + 6}
                    y={m.vertical ? n.y - n.r - 6 : n.y}
                    textAnchor="start"
                    dominantBaseline="central"
                    fontSize={10.5}
                    fill="var(--muted)"
                    className="font-mono"
                  >
                    [{n.cite}]
                  </text>
                ),
            )}
          </svg>
          <div aria-hidden className="pointer-events-none absolute inset-0">
            {m.labels.map((l) => (
              <GraphLabel key={l.agent} l={l} />
            ))}
          </div>
        </>
      )}
      {!m && <div className="h-[380px]" />}
    </div>
  );
}

export function GraphLegend({ className, hosted = false }: { className?: string; hosted?: boolean }) {
  const item = "inline-flex items-center gap-2";
  const who = studentCopy(hosted);
  return (
    <ul className={cn("flex flex-wrap gap-x-5 gap-y-2 text-[12.5px] text-muted", className)} aria-label="Legend">
      <li className={item}>
        <svg width="14" height="14" aria-hidden>
          <circle cx="7" cy="7" r="6" fill="var(--owned)" />
        </svg>
        {who.label}
      </li>
      <li className={item}>
        <svg width="14" height="14" aria-hidden>
          <circle cx="7" cy="7" r="6" fill="var(--rented)" />
        </svg>
        Answered by Ultra
      </li>
      <li className={item}>
        <svg width="18" height="18" aria-hidden>
          <circle cx="9" cy="9" r="8" fill="none" stroke="var(--owned)" strokeWidth="1.5" strokeDasharray="2.5 2" />
          <circle cx="9" cy="9" r="5" fill="var(--rented)" />
        </svg>
        Escalated: {who.tried} tried first
      </li>
      <li className={item}>
        <svg width="14" height="14" aria-hidden>
          <circle cx="7" cy="7" r="6" fill="var(--owned)" opacity="0.38" />
        </svg>
        Faded: dropped (not relevant or no answer)
      </li>
      <li className={item}>
        <svg width="14" height="14" aria-hidden>
          <circle cx="7" cy="7" r="5.5" fill="none" stroke="var(--line-strong)" strokeWidth="1.25" strokeDasharray="2 2.5" />
        </svg>
        Not run yet
      </li>
    </ul>
  );
}
