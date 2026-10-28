import { motion } from "motion/react";
import { arrowHead, EASE } from "../shared";

/**
 * One system diagram for "How it turns". The active step lights its part; the rest recedes.
 * Clockwise loop: app → student → teacher → ledger → Night School → golden set → release gate → student.
 */

type NodeId = "app" | "student" | "teacher" | "ledger" | "school" | "golden" | "gate";
type EdgeId = "app-student" | "student-teacher" | "teacher-ledger" | "ledger-school" | "school-golden" | "golden-gate" | "gate-student";

const ACTIVE: { nodes: NodeId[]; edges: EdgeId[] }[] = [
  { nodes: ["app", "student"], edges: ["app-student"] },
  { nodes: ["student", "teacher", "ledger"], edges: ["student-teacher", "teacher-ledger"] },
  { nodes: ["ledger", "school", "golden"], edges: ["ledger-school", "school-golden"] },
  { nodes: ["golden", "gate", "student"], edges: ["golden-gate", "gate-student"] },
];

const W = 430;
const H = 400;
const BW = 108;
const BH = 58;
const COL = [62, 215, 368];
const ROW = [72, 204];

interface NodeDef {
  id: NodeId;
  x: number;
  y: number;
  title: string;
  sub: string;
  short: string;
  tone?: "owned" | "rented";
}

const NODES: NodeDef[] = [
  { id: "app", x: COL[0], y: ROW[0], title: "Your app", sub: "request + task", short: "request" },
  { id: "student", x: COL[1], y: ROW[0], title: "Student", sub: "Nano 4B + LoRA", short: "Nano + LoRA", tone: "owned" },
  { id: "teacher", x: COL[2], y: ROW[0], title: "Teacher", sub: "Nemotron 3 Ultra", short: "Ultra", tone: "rented" },
  { id: "ledger", x: COL[2], y: ROW[1], title: "Ledger", sub: "answer + license", short: "+ license" },
  { id: "school", x: COL[1], y: ROW[1], title: "Night School", sub: "ready to run", short: "nightly job" },
  { id: "golden", x: COL[0], y: ROW[1], title: "Golden set", sub: "held out", short: "held out" },
];

const top = (y: number) => y - BH / 2;
const bot = (y: number) => y + BH / 2;
const left = (x: number) => x - BW / 2;
const right = (x: number) => x + BW / 2;

const GATE = { x: 10, y: 300, w: W - 20, h: 82 };
const LOOP_X = (right(COL[0]) + left(COL[1])) / 2;
const LOOP_Y = (bot(ROW[0]) + top(ROW[1])) / 2;
const LOOP2_X = (right(COL[1]) + left(COL[2])) / 2 - 6;

const EDGES: {
  id: EdgeId;
  d: string;
  head: string;
  /** `compactY`: where the label sits on a phone, where the type is larger and the gap between boxes too narrow for it. */
  label?: { x: number; y: number; compactY?: number; text: string; anchor?: "start" | "middle" | "end"; rotate?: number };
}[] = [
  {
    id: "app-student",
    d: `M${right(COL[0])} ${ROW[0]} H${left(COL[1]) - 2}`,
    head: arrowHead(left(COL[1]) - 2, ROW[0], "r"),
  },
  {
    id: "student-teacher",
    d: `M${right(COL[1])} ${ROW[0]} H${left(COL[2]) - 2}`,
    head: arrowHead(left(COL[2]) - 2, ROW[0], "r"),
    label: { x: (right(COL[1]) + left(COL[2])) / 2, y: ROW[0] - 9, compactY: top(ROW[0]) - 6, text: "unsure" },
  },
  {
    id: "teacher-ledger",
    d: `M${COL[2]} ${bot(ROW[0])} V${top(ROW[1]) - 2}`,
    head: arrowHead(COL[2], top(ROW[1]) - 2, "d"),
    label: { x: COL[2] + 8, y: LOOP_Y + 4, text: "saved", anchor: "start" },
  },
  {
    id: "ledger-school",
    d: `M${left(COL[2])} ${ROW[1]} H${right(COL[1]) + 2}`,
    head: arrowHead(right(COL[1]) + 2, ROW[1], "l"),
  },
  {
    id: "school-golden",
    d: `M${left(COL[1])} ${ROW[1]} H${right(COL[0]) + 2}`,
    head: arrowHead(right(COL[0]) + 2, ROW[1], "l"),
    label: { x: LOOP_X, y: ROW[1] - 9, text: "test" },
  },
  {
    id: "golden-gate",
    d: `M${COL[0]} ${bot(ROW[1])} V${GATE.y - 2}`,
    head: arrowHead(COL[0], GATE.y - 2, "d"),
    label: { x: COL[0] + 8, y: (bot(ROW[1]) + GATE.y) / 2 + 4, text: "pass", anchor: "start" },
  },
  {
    id: "gate-student",
    d: `M${LOOP2_X} ${GATE.y} V${LOOP_Y + 10} Q${LOOP2_X} ${LOOP_Y} ${LOOP2_X - 10} ${LOOP_Y} H${COL[1] + 22} Q${COL[1] + 12} ${LOOP_Y} ${COL[1] + 12} ${LOOP_Y - 10} V${bot(ROW[0]) + 2}`,
    head: arrowHead(COL[1] + 12, bot(ROW[0]) + 2, "u"),
    label: { x: LOOP2_X + 6, y: GATE.y - 10, text: "new version", anchor: "start" },
  },
];

const STAGES = ["shadow", "10%", "50%", "100%"];

export function HowDiagram({ active, className, compact = false }: { active: number; className?: string; compact?: boolean }) {
  // Phones draw the diagram ~20% smaller, so type is set larger and labels shorter.
  const f = compact ? 1.24 : 1;
  const on = ACTIVE[active] ?? ACTIVE[0];
  const nodeOn = (id: NodeId) => on.nodes.includes(id);
  const edgeOn = (id: EdgeId) => on.edges.includes(id);
  const t = { duration: 0.45, ease: EASE };
  const dim = 0.32;

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className={className} role="img" aria-label={`System diagram, step ${active + 1} of 4 highlighted.`}>
      {/* edges */}
      {EDGES.map((e) => {
        const lit = edgeOn(e.id);
        const color = e.id === "student-teacher" || e.id === "teacher-ledger" ? "var(--rented)" : e.id === "gate-student" ? "var(--owned)" : "var(--ink-2)";
        return (
          <motion.g key={e.id} initial={false} animate={{ opacity: lit ? 1 : dim }} transition={t}>
            <path d={e.d} fill="none" stroke={lit ? color : "var(--line-strong)"} strokeWidth={lit ? 1.8 : 1.4} strokeLinejoin="round" style={{ transition: "stroke .4s" }} />
            {lit && (
              <motion.path
                key={`flow-${active}`}
                d={e.d}
                fill="none"
                stroke={color}
                strokeWidth={2.6}
                strokeLinecap="round"
                initial={{ pathLength: 0, opacity: 0.9 }}
                animate={{ pathLength: 1, opacity: 0 }}
                transition={{ duration: 1.1, ease: EASE, delay: 0.15 }}
              />
            )}
            <path d={e.head} fill="none" stroke={lit ? color : "var(--line-strong)"} strokeWidth={1.6} strokeLinecap="round" strokeLinejoin="round" />
            {e.label && (
              <text
                x={e.label.x}
                y={compact && e.label.compactY != null ? e.label.compactY : e.label.y}
                textAnchor={e.label.anchor ?? "middle"}
                className="fill-muted font-mono"
                style={{ fontSize: 10.5 * f }}
              >
                {e.label.text}
              </text>
            )}
          </motion.g>
        );
      })}

      {/* nodes */}
      {NODES.map((n) => {
        const lit = nodeOn(n.id);
        const stroke = n.tone === "owned" ? "var(--owned)" : n.tone === "rented" ? "var(--rented)" : "var(--ink-2)";
        const fill = lit ? (n.tone === "owned" ? "var(--owned-soft)" : n.tone === "rented" ? "var(--rented-soft)" : "var(--surface)") : "var(--surface)";
        return (
          <motion.g key={n.id} initial={false} animate={{ opacity: lit ? 1 : dim }} transition={t}>
            <rect
              x={left(n.x)}
              y={top(n.y)}
              width={BW}
              height={BH}
              rx={12}
              fill={fill}
              stroke={lit ? stroke : "var(--line-strong)"}
              strokeWidth={lit ? 1.6 : 1}
              style={{ transition: "fill .4s, stroke .4s" }}
            />
            <text x={n.x} y={n.y - (compact ? 4 : 3)} textAnchor="middle" className="fill-ink font-semibold" style={{ fontSize: 13.5 * f }}>
              {n.title}
            </text>
            <text x={n.x} y={n.y + (compact ? 16 : 14)} textAnchor="middle" className="fill-muted font-mono" style={{ fontSize: 10 * f }}>
              {compact ? n.short : n.sub}
            </text>
          </motion.g>
        );
      })}

      {/* step 1: three samples + confidence above the student */}
      <motion.g initial={false} animate={{ opacity: active === 0 ? 1 : 0 }} transition={t}>
        {[0, 1, 2].map((i) => (
          <motion.circle
            key={i}
            cx={COL[1] - 16 + i * 16}
            cy={top(ROW[0]) - 26}
            r={4.5}
            fill="var(--owned)"
            initial={false}
            animate={active === 0 ? { scale: [0.4, 1], opacity: [0, 1] } : { opacity: 0 }}
            transition={{ duration: 0.4, delay: 0.25 + i * 0.12 }}
          />
        ))}
        <text x={COL[1] + 32} y={top(ROW[0]) - 22} className="fill-owned-ink font-mono" style={{ fontSize: 10.5 * f }}>
          3 samples
        </text>
        <text x={COL[1] - 32} y={top(ROW[0]) - 22} textAnchor="end" className="fill-muted font-mono" style={{ fontSize: 10.5 * f }}>
          conf 0.94
        </text>
      </motion.g>

      {/* step 2: license tag under the ledger */}
      <motion.g initial={false} animate={{ opacity: active === 1 ? 1 : 0, y: active === 1 ? 0 : -4 }} transition={t}>
        <rect x={COL[2] - 46} y={bot(ROW[1]) + 10} width={92} height={22} rx={11} fill="var(--good-soft)" stroke="var(--good)" strokeOpacity={0.4} />
        <text x={COL[2] - 7} y={bot(ROW[1]) + 25} textAnchor="middle" className="fill-good font-mono" style={{ fontSize: 10.5 * f }}>
          OpenMDW-1.1
        </text>
        <path
          d={`M${COL[2] + 30} ${bot(ROW[1]) + 21}l3 3 6-6`}
          fill="none"
          stroke="var(--good)"
          strokeWidth={1.75}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </motion.g>

      {/* step 3: base-model note above Night School */}
      <motion.g initial={false} animate={{ opacity: active === 2 ? 1 : 0 }} transition={t}>
        <text x={COL[1]} y={top(ROW[1]) - 10} textAnchor="middle" className="fill-ink-2 font-mono" style={{ fontSize: 10.5 * f }}>
          from base, all answers
        </text>
      </motion.g>

      {/* release gate band */}
      <motion.g initial={false} animate={{ opacity: active === 3 ? 1 : dim }} transition={t}>
        <rect
          x={GATE.x}
          y={GATE.y}
          width={GATE.w}
          height={GATE.h}
          rx={14}
          fill="var(--surface)"
          stroke={active === 3 ? "var(--ink-2)" : "var(--line-strong)"}
          strokeWidth={active === 3 ? 1.6 : 1}
        />
        <text x={GATE.x + 14} y={GATE.y + 22} className="fill-ink font-semibold" style={{ fontSize: 13.5 * f }}>
          Release gate
        </text>
        <text x={GATE.x + GATE.w - 14} y={GATE.y + 22} textAnchor="end" className="fill-muted font-mono" style={{ fontSize: 10.5 * f }}>
          rollback if it slips
        </text>
        {STAGES.map((s, i) => {
          const pw = (GATE.w - 28 - 3 * 18) / 4;
          const x = GATE.x + 14 + i * (pw + 18);
          const y = GATE.y + 36;
          return (
            <g key={s}>
              <motion.rect
                x={x}
                y={y}
                width={pw}
                height={30}
                rx={8}
                initial={false}
                animate={{
                  fill: active === 3 ? (i === 0 ? "var(--surface-2)" : "var(--owned-soft)") : "var(--surface-2)",
                  stroke: active === 3 ? (i === 0 ? "var(--line-strong)" : "var(--owned)") : "var(--line)",
                }}
                transition={{ duration: 0.4, delay: active === 3 ? 0.2 + i * 0.25 : 0 }}
                strokeWidth={1}
              />
              <text x={x + pw / 2} y={y + 19.5} textAnchor="middle" className="fill-ink-2 font-mono" style={{ fontSize: 11.5 * f }}>
                {s}
              </text>
              {i < 3 && <path d={arrowHead(x + pw + 11, y + 15, "r", 4)} fill="none" stroke="var(--muted)" strokeWidth={1.4} strokeLinecap="round" />}
            </g>
          );
        })}
      </motion.g>
    </svg>
  );
}
