import { ArrowUpRight, BookOpen, Code, Film, Radio, Server } from "lucide-react";
import { RealBadge } from "@/components/ui/real-badge";
import { entryById, type RealStatus } from "@/lib/real-model";
import { LIVE_URL } from "@/lib/utils";
import { Chapter, Command, Panel } from "./ui";
import { JUDGES_VIDEO, REAL } from "./real";

const COMMANDS: { title: string; why: string; cmd: string }[] = [
  { title: "Get the code", why: "Apache-2.0, public.", cmd: "git clone https://github.com/harshbhx/ratchet && cd ratchet" },
  { title: "Install", why: "Python 3.10 or later; no GPU and no API key needed.", cmd: 'pip install -e ".[dev]"' },
  { title: "Run the tests", why: "No network, no GPU; about two minutes.", cmd: "make test" },
  { title: "Run the gateway locally", why: "A gateway on mock models with this page at /judges.", cmd: "RATCHET_MODE=mock uvicorn ratchet.gateway:app --port 8000" },
  { title: "Verify a bill of materials", why: "Fetch one from a gateway first (Certificate chapter).", cmd: "python -m ratchet.bom verify bom.json --public-key <published key>" },
  { title: "Recompute the exam numbers", why: "The raw answers are in evidence/text2sql-exam2 (and round1, round2).", cmd: "python scripts/measure_adapter.py verify --raw evidence/text2sql-exam2/answers/raw_v2.jsonl --results evidence/text2sql-exam2/results/results_v2.json --golden evidence/text2sql-exam2/data/golden.jsonl --dbs evidence/text2sql-exam2/data/dbs.json" },
  { title: "Check every requirement maps to code and tests", why: "The traceability check CI runs.", cmd: "python scripts/reqtrace.py --check" },
];

export function CheckChapter() {
  return (
    <Chapter
      id="check"
      title="Check it yourself"
      lead={
        <p>
          Most of what is on this page can be checked from a fresh clone, without a GPU or an API key. The live gateway and Hugging Face links check themselves. Each command has a copy button. The full list, with the output to expect and
          what none of it proves, is{" "}
          <a
            href="https://docs.ratchet.dualmindlab.tech/reference/verify/"
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink"
          >
            Check it yourself<span className="sr-only"> (opens in a new tab)</span>
          </a>
          .
        </p>
      }
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
        { text: "A text-to-SQL adapter trained on a borrowed GPU, and the round-2 recipe retrained on a Nebius L40S (Serverless Job)", status: statusOf("adapter-training") },
        { text: "Its exam result", status: statusOf("exam-result") },
        { text: "The adapter and its data on Hugging Face", status: statusOf("hf-publish") },
        { text: "Served once on a Nebius endpoint for a speed test", status: statusOf("student-endpoint-test") },
        { text: "Serving it on a Nebius endpoint through the gateway", status: statusOf("gateway-live") },
      ],
    },
    {
      name: "NVIDIA Nemotron 3 Ultra, through Nebius Token Factory",
      role: "The teacher: it answers what the student cannot, and its answers become training data. Its license allows that.",
      parts: [
        { text: "Wrote the text-to-SQL training data on 22 Oct 2026", status: statusOf("teacher-labels") },
        { text: "Called by the gateway in live mode", status: statusOf("live-gateway-smoke") },
      ],
    },
    {
      name: "Tavily",
      role: "Search for Scout, the multi-agent research app on top of the gateway.",
      parts: [{ text: "Scout with real Tavily searches", status: statusOf("scout-tavily") }],
    },
  ];
  return (
    <Chapter
      id="sponsors"
      title="What we used from NVIDIA, Nebius and Tavily"
      lead={<p>Stated as it stands today: what ran for real, and what is built and ready to run.</p>}
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
  "The adapters are small (Nemotron 3 Nano 4B, LoRA) and the published ones were trained on a borrowed GPU; the round-2 recipe was also retrained once on Nebius AI Cloud. At a 95% precision bar the text-to-SQL student answers 1 of 86 questions on exam 1 and none on exam 2, so it does not replace the teacher.",
  "The main site's dashboard and chapters read the live gateway: its numbers come from real Token Factory calls, its teacher answers every request, and its ledger lives in the container's /tmp, so a restart empties it.",
  "The gateway's live mode runs on Nebius Token Factory (a smoke run and the live gateway). The live gateway has no student endpoint yet, so the teacher answers every request; the student was served once, by hand, on a Nebius endpoint for a speed test (then deleted), the nightly loop's jobs are built and tested offline, ready to run with make nebius-plan, and the nightly loop has not run on live traffic. The web front end and the live gateway run on Cloudflare.",
  "Scout's live runs were all answered by the teacher; no trained student has served a Scout call. The Scout page runs on a mock research world unless the gateway is live.",
  "The ledger is a single SQLite file, written by one gateway process.",
];

export function LimitsChapter() {
  const links = [
    { href: REAL.links.repo, label: "GitHub repository", icon: Code },
    { href: REAL.links.docs, label: "Documentation", icon: BookOpen },
    { href: LIVE_URL, label: "Live gateway (real Token Factory calls)", icon: Radio },
    ...(JUDGES_VIDEO ? [{ href: JUDGES_VIDEO, label: "The video (2:59)", icon: Film }] : []),
    // The one-command Nebius path (deploy/nebius/nebius.sh): scripted and tested offline, ready to run with make nebius-plan.
    {
      href: `${REAL.links.docs.replace(/\/?$/, "/")}reference/where-it-runs/`,
      label: "Run on Nebius: scripted, ready to run",
      icon: Server,
    },
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
                  <span className="min-w-0">{l.label}</span>
                  <span className="hidden min-w-0 flex-1 truncate text-right font-mono text-[11.5px] text-muted sm:inline">{l.href.replace(/^https:\/\//, "").replace(/\?from=[\w-]+$/, "").replace(/\/$/, "")}</span>
                  <ArrowUpRight className="size-3.5 shrink-0 text-muted" aria-hidden />
                </a>
              </li>
            ))}
            {!JUDGES_VIDEO && (
              <li className="flex min-w-0 items-center gap-2.5 px-2 py-2 text-[14px]">
                <Film className="size-4 shrink-0 text-muted" aria-hidden />
                <span className="text-muted">The video: not published yet</span>
              </li>
            )}
          </ul>
        </Panel>
      </div>
    </Chapter>
  );
}
