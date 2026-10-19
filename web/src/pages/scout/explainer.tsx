import { ArrowLeft, ArrowRight, Pin, Sprout } from "lucide-react";
import { ButtonLink } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Eyebrow } from "@/components/ui/section";
import { WhoBadge } from "@/components/ui/who";

/** Why some calls move to the owned model and others never do. */
export function Explainer() {
  return (
    <section aria-labelledby="h-explain" className="pt-10 sm:pt-16">
      <Eyebrow>How Scout routes</Eyebrow>
      <h2
        id="h-explain"
        className="mt-4 max-w-[24ch] text-balance text-3xl font-semibold tracking-[-0.025em] text-ink sm:text-[2.25rem] sm:leading-[1.1]"
      >
        What moves to your model, and why
      </h2>
      <p className="mt-4 max-w-[68ch] text-pretty text-[17px] leading-relaxed text-ink-2">
        Scout's agents are plain OpenAI clients pointed at Ratchet. Each call carries a task name, and Ratchet routes by task. NVIDIA Research
        estimates that 40–70% of the model calls in agent systems could be handled by small models (
        <a
          className="text-owned-ink underline underline-offset-2"
          href="https://arxiv.org/html/2506.02153v1"
          target="_blank"
          rel="noopener noreferrer"
        >
          arXiv 2506.02153
        </a>
        ). Scout shows where that line falls.
      </p>

      <div className="mt-10 grid gap-5 md:grid-cols-2">
        <Card className="p-6 sm:p-7">
          <div className="flex items-center gap-2.5">
            <span className="inline-flex size-8 items-center justify-center rounded-full bg-owned-soft text-owned-ink">
              <Sprout className="size-4" aria-hidden />
            </span>
            <h3 className="text-[17px] font-semibold tracking-[-0.01em] text-ink">Triage and Reader move</h3>
          </div>
          <p className="mt-4 text-[15px] leading-relaxed text-ink-2">
            They make many short calls with a fixed answer shape: a few labels and one fact. Ratchet keeps Ultra's answers to them as training data,
            trains a small Nemotron Nano model on those answers each night, and lets it answer when it is confident.
          </p>
          <p className="mt-3 text-[15px] leading-relaxed text-ink-2">
            When it is not confident, the call is <strong className="font-semibold text-rented-ink">escalated</strong> to Ultra, and that answer
            becomes tomorrow's training data.
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            <WhoBadge servedBy="student" />
            <span className="self-center text-[13px] text-muted">most of the time, once trained</span>
          </div>
        </Card>
        <Card className="p-6 sm:p-7">
          <div className="flex items-center gap-2.5">
            <span className="inline-flex size-8 items-center justify-center rounded-full bg-rented-soft text-rented-ink">
              <Pin className="size-4" aria-hidden />
            </span>
            <h3 className="text-[17px] font-semibold tracking-[-0.01em] text-ink">Planner and Writer stay</h3>
          </div>
          <p className="mt-4 text-[15px] leading-relaxed text-ink-2">
            Planning searches and writing a cited answer are open-ended. There is no single right output to learn and check, so these tasks are pinned
            to Ultra. They are never used as training data and never move.
          </p>
          <p className="mt-3 text-[15px] leading-relaxed text-ink-2">
            That is where the big model earns its price: two calls per question, out of up to twenty.
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            <WhoBadge servedBy="teacher" />
            <span className="self-center text-[13px] text-muted">always</span>
          </div>
        </Card>
      </div>

      <p className="mt-6 max-w-[68ch] text-[14px] leading-relaxed text-muted">
        The cost on each step compares what the call cost with what it would have cost on Ultra alone. A new student version only takes traffic after
        it passes the release gates: a golden-set check, then shadow, then canary at 10%, 50% and 100%, with automatic rollback.
      </p>

      <div className="mt-8 flex flex-wrap gap-3">
        <ButtonLink to="/dashboard" size="lg" className="h-auto min-h-12 whitespace-normal py-3 text-left">
          Watch Night School train and release new versions <ArrowRight aria-hidden />
        </ButtonLink>
        <ButtonLink to="/" variant="ghost" size="lg">
          <ArrowLeft aria-hidden /> Back to the story
        </ButtonLink>
      </div>
    </section>
  );
}
