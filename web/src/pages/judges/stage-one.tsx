import { ArrowUpRight, ChevronRight } from "lucide-react";
import { RealBadge } from "@/components/ui/real-badge";
import { REAL_STATUSES, STATUS_TEXT, type RealStatus } from "@/lib/real-model";
import { LIVE_URL } from "@/lib/utils";
import { JUDGES_VIDEO, REAL } from "./real";

/** One hackathon requirement, the status its evidence earns today and where to check it. */
export interface StageOneItem {
  id: string;
  requirement: string;
  status: RealStatus;
  evidence: string;
  /** An external link (opened in a new tab) or a chapter on this page. */
  link: { label: string; href: string } | { label: string; chapter: string } | null;
}

const { repo, demo } = REAL.links;
/** The video's counting link (from=judges), null while there is no video. */
const video = JUDGES_VIDEO;

/**
 * The Stage One checklist. Statuses follow `src/data/real.json`'s rules: Real only for what ran and can be checked, Measured for
 * numbers recomputed from saved answers, Simulation for the mock demo, Pending for what is built or recorded later.
 */
export const STAGE_ONE: StageOneItem[] = [
  {
    id: "repo",
    requirement: "Public repository with an Apache-2.0 license",
    status: "real",
    evidence: "Code, tests and docs in one repository; the license file is the Apache License 2.0.",
    link: { label: "Repository", href: `${repo}/blob/main/LICENSE` },
  },
  {
    id: "demo",
    requirement: "A working demo URL",
    status: "simulation",
    evidence: "Runs the real gateway code with mock models, so its numbers are simulation.",
    link: { label: "Open demo", href: demo },
  },
  {
    id: "live-demo",
    requirement: "A live demo that calls Token Factory",
    status: "real",
    evidence: "Deployed on Cloudflare Containers, in live mode, with spending caps. A request to it is answered by Nemotron 3 Ultra on Token Factory. No trained student is served yet, so the teacher answers every request.",
    link: { label: "Open the live demo", href: LIVE_URL },
  },
  {
    id: "runtime-call",
    requirement: "A runtime call to Nebius Token Factory",
    status: "real",
    evidence: "Smoke run, 23 Oct 2026: 20 real requests, each answered by Nemotron 3 Ultra on Token Factory, about $0.01.",
    link: { label: "Records", href: `${repo}/tree/main/evidence/live-smoke-2026-10-23` },
  },
  {
    id: "nemotron",
    requirement: "NVIDIA Nemotron models in the product",
    status: "real",
    evidence:
      "Nemotron 3 Ultra is the teacher. Nemotron 3 Nano 4B is the student, with a LoRA adapter trained on a borrowed GPU.",
    link: { label: "Adapter", href: "https://huggingface.co/harshbhx/ratchet-nemotron-nano-4b-text2sql" },
  },
  {
    id: "results",
    requirement: "Real, measured results",
    status: "measured",
    evidence: "Two held-out text-to-SQL exams, scored with 95% ranges and recomputed from the saved answers.",
    link: { label: "Exams", chapter: "exams" },
  },
  {
    id: "video",
    requirement: "A demo video",
    status: video ? "real" : "pending",
    evidence: video ? "Public on YouTube since 26 Oct 2026, 2 minutes 59 seconds." : "Not published yet. This row changes when the link exists.",
    link: video ? { label: "Watch the video", href: video } : null,
  },
];

const linkClass =
  "inline-flex items-center gap-0.5 whitespace-nowrap font-medium text-ink underline decoration-line-strong underline-offset-[3px] transition-colors hover:decoration-ink";

/** The hackathon's pass/fail items on one card, each with an honest status and where to check it. */
export function StageOneCard({ onChapter }: { onChapter: (id: string) => void }) {
  const counts = REAL_STATUSES.map((status) => ({ status, n: STAGE_ONE.filter((i) => i.status === status).length })).filter((c) => c.n > 0);
  return (
    <section aria-labelledby="stage-one-title" className="min-w-0 rounded-3xl border border-line-strong bg-surface shadow-card">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-line px-5 py-4 sm:px-6">
        <h2 id="stage-one-title" className="text-[17px] font-semibold tracking-[-0.02em] text-ink">
          Stage One checklist
        </h2>
        <p className="text-[12.5px] text-muted">
          {counts.map((c, i) => (
            <span key={c.status}>
              {i > 0 && " · "}
              {c.n} {STATUS_TEXT[c.status].label.toLowerCase()}
            </span>
          ))}
        </p>
      </header>
      <ul className="divide-y divide-line">
        {STAGE_ONE.map((it) => (
          <li key={it.id} className="grid min-w-0 gap-x-4 gap-y-1.5 px-5 py-3 sm:grid-cols-[100px_minmax(0,1fr)] sm:px-6">
            <div className="flex items-start sm:pt-px">
              <RealBadge status={it.status} />
            </div>
            <div className="min-w-0">
              <p className="text-[14.5px] font-semibold leading-snug tracking-[-0.01em] text-ink">{it.requirement}</p>
              <p className="mt-0.5 text-pretty text-[13px] leading-relaxed text-ink-2">
                {it.evidence}
                {it.link && " "}
                {it.link &&
                  ("href" in it.link ? (
                    <a href={it.link.href} target="_blank" rel="noopener noreferrer" className={linkClass}>
                      {it.link.label}
                      <ArrowUpRight className="size-3 shrink-0 self-center text-muted" aria-hidden />
                    </a>
                  ) : (
                    <button type="button" onClick={() => onChapter((it.link as { chapter: string }).chapter)} className={linkClass}>
                      {it.link.label}
                      <ChevronRight className="size-3 shrink-0 self-center text-muted" aria-hidden />
                    </button>
                  ))}
              </p>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
