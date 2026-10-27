import type { ReactNode } from "react";
import { HoverGrid } from "@/components/fx/hover-grid";
import { Container } from "@/components/ui/section";
import { RealBadge } from "@/components/ui/real-badge";
import { WhoBadge } from "@/components/ui/who";
import { Chapter, ChapterHead, Lead, Reveal } from "../shared";

/** `RATCHET_FALLBACK_STUDENT`'s default; its badge is the hosted one the pages show for its answers. */
const FALLBACK_ID = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B";

/** Token Factory's list of the models it can fine-tune (read 26 Oct 2026: DeepSeek, Gemma, GPT-OSS, Qwen and Llama; no Nemotron). */
const TF_FINETUNE_MODELS = "https://docs.tokenfactory.nebius.com/post-training/models";

interface Role {
  role: string;
  service: ReactNode;
  id?: string;
  why: string;
  badge?: ReactNode;
}

const ROLES: Role[] = [
  {
    role: "Teacher",
    service: "Nemotron 3 Ultra on Nebius Token Factory",
    id: "nvidia/Nemotron-3-Ultra-550b-a55b",
    why: "The strongest model in the loop. OpenMDW-1.1 allows training on its outputs.",
    badge: <WhoBadge servedBy="teacher" />,
  },
  {
    role: "Student",
    service: "Nemotron 3 Nano 4B + LoRA, vLLM (built for a Nebius Serverless Endpoint)",
    id: "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16",
    why: "Cheap and fast. The LoRA adapter is yours; the Nano base stays under NVIDIA's Nemotron Open Model License. Scripted for Nebius and ready to run.",
    badge: (
      <span className="flex flex-wrap items-center justify-end gap-1.5">
        <RealBadge status="not-run" />
        <WhoBadge servedBy="student" />
      </span>
    ),
  },
  {
    role: "Night School",
    service: "Nebius Serverless Jobs (L40S) with Unsloth (one hand-run job on 27 Oct 2026; the nightly job is scripted, ready to run)",
    why: "Fine-tunes a new adapter each night. A job is billed only while it runs. The round-2 adapter was retrained once by hand on a Nebius L40S (27 Oct 2026); the original adapters were trained on a borrowed GPU.",
    badge: <RealBadge status="not-run" />,
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
    id: FALLBACK_ID,
    why: "Answers in the student's place when your endpoint is stopped.",
    badge: <WhoBadge servedBy="student" model={FALLBACK_ID} fallback={FALLBACK_ID} simulated={false} />,
  },
  {
    role: "Storage",
    service: "Nebius Object Storage (scripted, ready to run)",
    why: "Datasets and adapters, in one bucket mounted in jobs and endpoints.",
    badge: <RealBadge status="not-run" />,
  },
  {
    role: "Search",
    service: "Tavily Search API",
    why: "Scout's search tool only. Not an NVIDIA or Nebius service.",
  },
  {
    role: "Why two places",
    service: (
      <>
        Token Factory's list of models it can fine-tune has no Nemotron model (
        <a
          href={TF_FINETUNE_MODELS}
          target="_blank"
          rel="noopener noreferrer"
          className="font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink"
        >
          its docs<span className="sr-only"> (opens in a new tab)</span>
        </a>
        , read 26 Oct 2026), so the student is designed to be trained and served on Nebius AI Cloud. The round-2 adapter was retrained once on Nebius AI Cloud by hand (27 Oct 2026); the student endpoint is tested offline and ready to
        run with make nebius-plan. The original adapters were trained on a borrowed GPU.
      </>
    ),
    why: "",
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
              Open Nemotron models for every model role. Every model call on the live site runs on Nebius Token Factory, the adapter was retrained on Nebius AI Cloud (one L40S, Serverless Job), and the student was served from a Nebius Serverless Endpoint in a measured test; the demo is not connected to it. The gateway itself does no inference, so it fits on a
              small CPU machine.
            </Lead>
          }
        />
        <Reveal className="mt-12">
          <HoverGrid
            items={ROLES}
            keyOf={(r) => r.role}
            className="rounded-[22px] border border-line bg-surface p-1.5 shadow-card"
            cellClassName="min-w-0"
            render={(r) => (
              <div className="flex h-full min-w-0 flex-col rounded-2xl p-4 lg:p-5">
                <div className="flex min-h-6 flex-wrap items-center justify-between gap-x-2 gap-y-1.5">
                  <span className="font-mono text-[11.5px] uppercase tracking-[0.12em] text-muted">{r.role}</span>
                  {r.badge}
                </div>
                <p className={r.why ? "mt-3 text-[15px] font-semibold leading-snug tracking-[-0.01em] text-ink" : "mt-3 text-[13.5px] leading-relaxed text-ink-2"}>
                  {r.service}
                </p>
                {r.id && <code className="mt-2 block font-mono text-[12px] leading-relaxed text-ink-2 [overflow-wrap:anywhere]">{r.id}</code>}
                {r.why && <p className="mt-3 text-[13.5px] leading-relaxed text-muted">{r.why}</p>}
              </div>
            )}
          />
        </Reveal>
      </Container>
    </Chapter>
  );
}
