import { Suspense, lazy, useState } from "react";
import { motion } from "motion/react";
import { Link } from "react-router";
import { ArrowRight, ArrowUpRight, Check, Copy, FileSignature, ShieldCheck, Undo2 } from "lucide-react";
import { ShinyText } from "@/components/fx/shiny-text";
import { SplitText } from "@/components/fx/split-text";
import { TiltCard } from "@/components/fx/tilt-card";
import { ButtonA } from "@/components/ui/button";
import { tok } from "@/components/ui/code-block";
import { LogoMark } from "@/components/ui/logo";
import { Container } from "@/components/ui/section";
import { enter } from "@/lib/motion";
import { videoHref } from "@/lib/real-model";
import { REPO_URL } from "@/lib/utils";
import { REAL } from "@/pages/judges/real";
import { HeroFlow } from "./hero-flow";

/** "Watch the 3-minute demo": the video's counting link, tagged from=site; null hides the link. */
const VIDEO_HREF = videoHref(REAL.links, "site");

// The aurora is a WebGL shader: it loads after the hero's text has painted, and the page is complete without it.
const Aurora = lazy(() => import("@/components/fx/aurora"));

/** The line a team changes, as the hero's diff shows it. */
const BASE_URL_LINE = 'client = OpenAI(base_url="http://localhost:8000/v1")';

const POINTS = [
  { icon: ShieldCheck, title: "License-clean", body: "Closed models are refused as teachers." },
  { icon: Undo2, title: "Gated releases", body: "Shadow, canary, automatic rollback." },
  { icon: FileSignature, title: "Signed lineage", body: "Each adapter carries a signed record of its training data and licenses." },
];

/** The integration in one diff: the default OpenAI client, then the same client pointed at Ratchet. */
export function BaseUrlDiff() {
  const [done, setDone] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(BASE_URL_LINE);
      setDone(true);
      window.setTimeout(() => setDone(false), 1600);
    } catch {
      /* clipboard blocked */
    }
  };
  return (
    <figure className="overflow-hidden rounded-xl border border-line bg-surface/85 shadow-card backdrop-blur-md">
      <figcaption className="flex items-center justify-between gap-3 border-b border-line px-3.5 py-1.5">
        <span className="font-mono text-[11.5px] text-muted">
          your_app.py <span className="hidden text-ink-2 min-[400px]:inline">· the one line that changes</span>
        </span>
        <button
          type="button"
          onClick={onCopy}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md px-1.5 py-1 text-[12px] text-muted transition-colors hover:bg-surface-2 hover:text-ink"
          aria-label="Copy the changed line"
        >
          {done ? <Check className="size-3.5 text-good" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
          <span aria-live="polite">{done ? "Copied" : "Copy"}</span>
        </button>
      </figcaption>
      <pre className="whitespace-pre-wrap px-3.5 py-2.5 font-mono text-[12px] leading-[1.7] [overflow-wrap:anywhere]">
        <code>
          <span className="block text-muted">
            <span aria-hidden className="mr-2 select-none text-bad">−</span>
            <span className="sr-only">Before: </span>client = OpenAI()
          </span>
          <span className="block rounded bg-owned-soft/70 text-ink">
            <span aria-hidden className="mr-2 select-none text-owned-ink">+</span>
            <span className="sr-only">After: </span>
            {"client = OpenAI("}
            <wbr />
            {tok.k("base_url")}=<span className="font-medium">"http://localhost:8000/v1"</span>)
          </span>
        </code>
      </pre>
    </figure>
  );
}

export function Hero() {
  return (
    <section aria-labelledby="hero-title" className="relative overflow-hidden">
      {/* the ambient wash, then the grid through it */}
      <div aria-hidden className="pointer-events-none absolute inset-x-0 top-0 h-[560px] sm:h-[640px]">
        <Suspense fallback={null}>
          <Aurora className="[mask-image:linear-gradient(to_bottom,black_55%,transparent)]" />
        </Suspense>
      </div>
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 -top-16 bottom-0 bg-grid [mask-image:radial-gradient(ellipse_75%_65%_at_50%_30%,black_35%,transparent_100%)]"
      />
      <Container className="relative pb-14 pt-10 sm:pb-20 sm:pt-14 lg:pb-20 lg:pt-16">
        <motion.p
          {...enter(0)}
          className="inline-flex max-w-full items-center gap-2.5 rounded-full border border-line bg-surface/80 py-1 pl-1.5 pr-3.5 text-[12px] leading-5 text-ink-2 shadow-card backdrop-blur-md sm:text-[13px]"
        >
          <LogoMark className="size-5" />
          <span className="shrink-0 font-medium text-ink">
            Open source<span className="sr-only">.</span>
          </span>
          <span aria-hidden className="h-3 w-px shrink-0 bg-line-strong" />
          <ShinyText className="min-w-0">Built on NVIDIA Nemotron and Nebius</ShinyText>
        </motion.p>
        <SplitText
          id="hero-title"
          className="mt-6 max-w-[15ch] text-balance text-[2.6rem] font-semibold leading-[1.03] tracking-[-0.04em] text-ink min-[400px]:text-[2.9rem] sm:max-w-none sm:text-[4rem] lg:text-[4.6rem] xl:text-[5rem]"
          delay={0.08}
          segments={[
            { text: "Stop paying frontier prices ", className: "text-muted" },
            { text: "for repetitive work.", className: "text-ink" },
          ]}
        />
        <div className="mt-7 grid grid-cols-1 items-start gap-10 sm:mt-9 lg:grid-cols-[minmax(0,0.88fr)_minmax(0,1.12fr)] lg:grid-rows-[auto_1fr] lg:gap-x-12 lg:gap-y-0">
          <div className="lg:col-start-1 lg:row-start-1 lg:pt-1">
            {/* What it is, what the customer keeps, then the one line they change. */}
            <motion.p {...enter(0.5)} className="max-w-[34rem] text-pretty text-[17px] leading-relaxed text-ink-2 sm:text-lg">
              <strong className="font-semibold text-ink">Ratchet is an OpenAI-compatible gateway.</strong> A Nemotron adapter you own (LoRA on
              NVIDIA's open Nano base) answers what it is sure of; Nemotron 3 Ultra answers the rest and teaches the next adapter.
            </motion.p>
            <motion.div {...enter(0.58)} className="mt-6 max-w-[34rem]">
              <BaseUrlDiff />
            </motion.div>
            <motion.div {...enter(0.66)} className="mt-6 flex flex-wrap items-center gap-3">
              <ButtonA href="#try" size="lg" className="w-full min-[440px]:w-auto">
                Try it in a minute <ArrowRight aria-hidden />
              </ButtonA>
              <ButtonA href="#real-run" variant="outline" size="lg" className="w-full bg-surface/80 backdrop-blur-md min-[440px]:w-auto">
                See the real results
              </ButtonA>
            </motion.div>
            <motion.p {...enter(0.74)} className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-[14px]">
              <a
                href="#learn"
                className="rounded-md font-medium text-ink-2 underline decoration-line-strong underline-offset-4 transition-colors hover:text-ink hover:decoration-ink"
              >
                Watch it learn
              </a>
              <Link to="/scout" className="rounded-md font-medium text-ink-2 underline decoration-line-strong underline-offset-4 transition-colors hover:text-ink hover:decoration-ink">
                Scout demo
              </Link>
              <Link to="/dashboard" className="rounded-md font-medium text-ink-2 underline decoration-line-strong underline-offset-4 transition-colors hover:text-ink hover:decoration-ink">
                Dashboard
              </Link>
              {VIDEO_HREF && (
                <a
                  href={VIDEO_HREF}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 rounded-md font-medium text-ink-2 underline decoration-line-strong underline-offset-4 transition-colors hover:text-ink hover:decoration-ink"
                >
                  Watch the 3-minute demo <ArrowUpRight className="size-3.5 text-muted" aria-hidden />
                  <span className="sr-only"> (video, opens in a new tab)</span>
                </a>
              )}
              <a
                href={REPO_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 rounded-md font-medium text-ink-2 underline decoration-line-strong underline-offset-4 transition-colors hover:text-ink hover:decoration-ink"
              >
                Read the code on GitHub <ArrowUpRight className="size-3.5 text-muted" aria-hidden />
              </a>
            </motion.p>
          </div>

          <motion.div
            initial={{ opacity: 0, y: 24, scale: 0.985 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1], delay: 0.55 }}
            className="relative lg:col-start-2 lg:row-span-2 lg:row-start-1"
          >
            {/* a pool of the two colours under the card, so it sits in the light rather than on the page */}
            <div
              aria-hidden
              className="pointer-events-none absolute -inset-x-10 -top-10 bottom-2 rounded-[40px] bg-[radial-gradient(60%_60%_at_30%_30%,color-mix(in_srgb,var(--owned)_14%,transparent),transparent_70%),radial-gradient(50%_60%_at_80%_80%,color-mix(in_srgb,var(--rented)_14%,transparent),transparent_70%)]"
            />
            <TiltCard max={4} scale={1.008}>
              <HeroFlow />
            </TiltCard>
            <p className="mt-3 px-1 text-[12px] leading-relaxed text-muted">
              Simulation, mock models, seed 7: how the loop behaves, not real model quality.
            </p>
          </motion.div>
          <motion.ul
            {...enter(0.82)}
            className="grid gap-4 border-t border-line pt-6 text-[14px] sm:grid-cols-3 lg:col-start-1 lg:row-start-2 lg:mt-9 lg:grid-cols-1 lg:self-start"
          >
            {POINTS.map((pt) => (
              <li key={pt.title} className="flex gap-3">
                <pt.icon className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden />
                <span>
                  <span className="font-medium text-ink">{pt.title}.</span> <span className="text-ink-2">{pt.body}</span>
                </span>
              </li>
            ))}
          </motion.ul>
        </div>
      </Container>
    </section>
  );
}
