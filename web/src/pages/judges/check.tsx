import { ArrowUpRight, BookOpen, Code, Film, MonitorPlay } from "lucide-react";
import { RealBadge } from "@/components/ui/real-badge";
import { entryById, type RealStatus } from "@/lib/real-model";
import { Chapter, Command, Ext, Panel } from "./ui";
import { REAL } from "./real";

const COMMANDS: { title: string; why: string; cmd: string }[] = [
  { title: "Get the code", why: "Apache-2.0, public.", cmd: "git clone https://github.com/harshbhx/ratchet && cd ratchet" },
  { title: "Install", why: "Python 3.10 or later; no GPU and no API key needed.", cmd: 'pip install -e ".[dev]"' },
  { title: "Run the tests", why: "No network, no GPU; about two minutes.", cmd: "make test" },
  { title: "Run the demo locally", why: "The mock gateway with this page at /judges.", cmd: "make demo" },
  { title: "Replay the reference simulation", why: "The story page's numbers come from this run.", cmd: "python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3" },
  { title: "Verify a bill of materials", why: "Fetch one from a gateway first (Certificate chapter).", cmd: "python -m ratchet.bom verify bom.json --public-key <published key>" },
  { title: "Recompute the exam numbers", why: "Once raw.jsonl and results.json are published.", cmd: "python scripts/measure_adapter.py verify --raw raw.jsonl --results results.json" },
  { title: "Check every requirement maps to code and tests", why: "The traceability check CI runs.", cmd: "python scripts/reqtrace.py --check" },
];

export function CheckChapter() {
  return (
    <Chapter
      id="check"
      title="Check it yourself"
      lead={<p>Everything on this page can be checked from a fresh clone. Each command has a copy button.</p>}
    >
      <ol className="space-y-4">
        {COMMANDS.map((c, i) => (
          <li key={c.cmd} className="grid min-w-0 gap-2 sm:grid-cols-[28px_minmax(0,1fr)] sm:gap-3">
            <span className="hidden size-7 items-center justify-center rounded-full border border-line-strong font-mono text-[12px] text-muted tabular-nums sm:inline-flex">
              {i + 1}
            </span>
            <div className="min-w-0">
              <p className="text-[14px] text-ink">
                <span className="font-semibold">{c.title}.</span> <span className="text-ink-2">{c.why}</span>
              </p>
              <Command cmd={c.cmd} label={c.title} className="mt-1.5" />
            </div>
          </li>
        ))}
      </ol>
    </Chapter>
  );
}

function statusOf(id: string): RealStatus | null {
  return entryById(REAL, id)?.status ?? null;
}

export function SponsorsChapter() {
  const rows: { name: string; role: string; parts: { text: string; status: RealStatus | null }[] }[] = [
    {
      name: "NVIDIA Nemotron 3 Nano 4B",
      role: "The student's base model. Ratchet trains a LoRA adapter on it; the base keeps NVIDIA's Nemotron Open Model License.",
      parts: [
        { text: "A text-to-SQL adapter trained on a free Colab T4", status: statusOf("adapter-training") },
        { text: "Its exam result", status: statusOf("exam-result") },
        { text: "The adapter and its data on Hugging Face", status: statusOf("hf-publish") },
        { text: "Serving it on a Nebius endpoint through the gateway", status: statusOf("gateway-live") },
      ],
    },
    {
      name: "NVIDIA Nemotron 3 Ultra, through Nebius Token Factory",
      role: "The teacher: it answers what the student cannot, and its answers become training data. Its license allows that.",
      parts: [
        { text: "Wrote the text-to-SQL training data on 22 Oct 2026", status: statusOf("teacher-labels") },
        { text: "Called by the gateway in live mode", status: statusOf("gateway-live") },
      ],
    },
    {
      name: "Tavily",
      role: "Search for Scout, the multi-agent research demo on top of the gateway.",
      parts: [{ text: "Scout with real Tavily searches", status: statusOf("scout-tavily") }],
    },
  ];
  return (
    <Chapter
      id="sponsors"
      title="What we used from NVIDIA, Nebius and Tavily"
      lead={<p>Stated as it stands today: what ran for real, and what is built but has not run live yet.</p>}
    >
      <div className="grid min-w-0 gap-4 lg:grid-cols-3">
        {rows.map((r) => (
          <Panel key={r.name} title={r.name}>
            <p className="text-[13.5px] leading-relaxed text-ink-2">{r.role}</p>
            <ul className="mt-3 space-y-2 border-t border-line pt-3">
              {r.parts.map((p) => (
                <li key={p.text} className="flex min-w-0 items-start justify-between gap-3 text-[13px]">
                  <span className="min-w-0 text-ink-2">{p.text}</span>
                  {p.status && <RealBadge status={p.status} className="shrink-0" />}
                </li>
              ))}
            </ul>
          </Panel>
        ))}
      </div>
      <Panel title="License and open source" className="mt-4">
        <ul className="space-y-2 text-[13.5px] leading-relaxed text-ink-2">
          <li>
            <span className="font-medium text-ink">Apache-2.0</span> for all of Ratchet's code. Model weights keep their own licenses.
          </li>
          <li>
            <span className="font-medium text-ink">Open teachers only.</span> Closed models (GPT, Claude, Gemini) are refused as teachers in code,
            because their terms forbid training competing models on their outputs. <RealBadge status={statusOf("license-rule") ?? "real"} />
          </li>
          <li>Every adapter's bill of materials names its teacher, the teacher's license and the base model's license, and a NOTICE file travels with it.</li>
        </ul>
      </Panel>
    </Chapter>
  );
}

const LIMITS = [
  "The one real adapter is small and was trained on a free Colab T4, not on Nebius GPUs; its exam result is still being measured.",
  "Every number in the demo and on the dashboard comes from mock models (simulation).",
  "Nothing has run live on Nebius GPUs yet: not the gateway's live mode, the student endpoint or Night School's jobs.",
  "Scout's real Tavily searches have not been run yet; the demo uses a mock research world.",
  "The ledger is a single SQLite file, written by one gateway process.",
  "The demo is shared: every visitor sees and changes the same simulated state.",
];

export function LimitsChapter() {
  const links = [
    { href: REAL.links.repo, label: "GitHub repository", icon: Code },
    { href: REAL.links.docs, label: "Documentation", icon: BookOpen },
    { href: REAL.links.demo, label: "The demo", icon: MonitorPlay },
  ];
  return (
    <Chapter id="limits" title="Honest limits, and where to go next">
      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <Panel title="What this is not yet">
          <ul className="list-disc space-y-2 pl-5 text-[14px] leading-relaxed text-ink-2 marker:text-muted">
            {LIMITS.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </Panel>
        <Panel title="Links">
          <ul className="space-y-1">
            {links.map((l) => (
              <li key={l.href}>
                <a
                  href={l.href}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex min-w-0 items-center gap-2.5 rounded-lg px-2 py-2 text-[14px] text-ink transition-colors hover:bg-surface-2"
                >
                  <l.icon className="size-4 shrink-0 text-muted" aria-hidden />
                  <span className="min-w-0 flex-1">{l.label}</span>
                  <span className="hidden truncate font-mono text-[11.5px] text-muted sm:inline">{l.href.replace(/^https:\/\//, "").replace(/\/$/, "")}</span>
                  <ArrowUpRight className="size-3.5 shrink-0 text-muted" aria-hidden />
                </a>
              </li>
            ))}
            <li className="flex min-w-0 items-center gap-2.5 px-2 py-2 text-[14px]">
              <Film className="size-4 shrink-0 text-muted" aria-hidden />
              {REAL.links.video ? (
                <Ext href={REAL.links.video}>The demo video</Ext>
              ) : (
                <span className="text-muted">The demo video: not published yet</span>
              )}
            </li>
          </ul>
        </Panel>
      </div>
    </Chapter>
  );
}
