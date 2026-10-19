import type { ReactNode } from "react";
import { Container } from "@/components/ui/section";
import { WhoBadge } from "@/components/ui/who";
import { Chapter, ChapterHead, Lead, Reveal } from "../shared";

const ROLES: { role: string; service: string; id?: string; why: string; badge?: ReactNode }[] = [
  {
    role: "Teacher",
    service: "Nemotron 3 Ultra on Nebius Token Factory",
    id: "nvidia/Nemotron-3-Ultra-550b-a55b",
    why: "Best reasoning in the loop. OpenMDW-1.1 allows training on its outputs.",
    badge: <WhoBadge servedBy="teacher" />,
  },
  {
    role: "Student",
    service: "Nemotron 3 Nano 4B + LoRA, vLLM on a Nebius Serverless Endpoint",
    id: "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16",
    why: "Cheap and fast. The adapter weights are yours.",
    badge: <WhoBadge servedBy="student" />,
  },
  {
    role: "Night School",
    service: "Nebius Serverless Jobs (L40S) with Unsloth",
    why: "Fine-tunes a new adapter each night. You pay only while it trains.",
  },
  {
    role: "Judge",
    service: "Nemotron 3 Super on Token Factory",
    id: "nvidia/nemotron-3-super-120b-a12b",
    why: "Scores free-text answers during evaluation. Fast, with JSON mode.",
  },
  {
    role: "Fallback student",
    service: "Nemotron 3 Nano 30B on Token Factory",
    id: "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    why: "Answers in the student's place when your endpoint is stopped.",
  },
  {
    role: "Storage",
    service: "Nebius Object Storage",
    why: "Datasets and adapters, in one bucket mounted in jobs and endpoints.",
  },
  {
    role: "Search",
    service: "Tavily Search API",
    why: "Scout's search tool only. Not an NVIDIA or Nebius service.",
  },
];

export function Stack() {
  return (
    <Chapter id="stack" className="border-t border-line">
      <Container>
        <ChapterHead
          id="stack"
          title="Built on Nebius and NVIDIA."
          lead={
            <Lead>
              Open Nemotron models for every model role, Nebius for every place they run. The gateway itself does no inference, so it fits on a
              small CPU machine.
            </Lead>
          }
        />
        <ul className="mt-12 grid gap-px overflow-hidden rounded-[22px] border border-line bg-line shadow-card sm:grid-cols-2 lg:grid-cols-4">
          {ROLES.map((r, i) => (
            <Reveal as="li" key={r.role} delay={(i % 4) * 0.06} className="flex flex-col bg-surface p-5">
              <div className="flex min-h-6 items-center justify-between gap-2">
                <span className="font-mono text-[11.5px] uppercase tracking-[0.12em] text-muted">{r.role}</span>
                {r.badge}
              </div>
              <p className="mt-3 text-[15px] font-semibold leading-snug tracking-[-0.01em] text-ink">{r.service}</p>
              {r.id && <code className="mt-2 block font-mono text-[12px] leading-relaxed text-ink-2 [overflow-wrap:anywhere]">{r.id}</code>}
              <p className="mt-3 text-[13.5px] leading-relaxed text-muted">{r.why}</p>
            </Reveal>
          ))}
          <Reveal as="li" delay={0.18} className="flex flex-col justify-center bg-surface-2 p-5">
            <span className="font-mono text-[11.5px] uppercase tracking-[0.12em] text-muted">Why two places</span>
            <p className="mt-3 text-[13.5px] leading-relaxed text-ink-2">
              Token Factory does not offer fine-tuning or LoRA serving for Nemotron yet (Oct 2026), so the student is trained and served on
              Nebius AI Cloud.
            </p>
          </Reveal>
        </ul>
      </Container>
    </Chapter>
  );
}
