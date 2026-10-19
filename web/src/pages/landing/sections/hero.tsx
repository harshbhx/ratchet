import { motion } from "motion/react";
import { ArrowRight, ArrowUpRight, Code2, ShieldCheck, Undo2 } from "lucide-react";
import { ButtonA, ButtonLink } from "@/components/ui/button";
import { CodeBlock, tok } from "@/components/ui/code-block";
import { Container, Eyebrow } from "@/components/ui/section";
import { REPO_URL } from "@/lib/utils";
import { EASE } from "../shared";
import { HeroFlow } from "./hero-flow";

const SNIPPET = `from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="unused",
)
r = client.chat.completions.create(
    model="ratchet",
    messages=messages,
    extra_headers={
        "X-Ratchet-Task": "ticket-triage",
    },
)
print(r.ratchet)  # served_by, cost_usd, ...`;

const POINTS = [
  { icon: Code2, title: "One-line switch", body: "Change base_url and name the task." },
  { icon: ShieldCheck, title: "License-clean", body: "Closed models are refused as teachers." },
  { icon: Undo2, title: "Gated releases", body: "Shadow, canary, automatic rollback." },
];

const rise = (delay: number) => ({
  initial: { opacity: 0, y: 16 },
  animate: { opacity: 1, y: 0 },
  transition: { duration: 0.7, ease: EASE, delay },
});

export function Hero() {
  return (
    <section aria-labelledby="hero-title" className="relative overflow-hidden">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 -top-16 bottom-0 bg-grid [mask-image:radial-gradient(ellipse_75%_65%_at_50%_30%,black_35%,transparent_100%)]"
      />
      <Container className="relative pb-14 pt-10 sm:pb-20 sm:pt-16 lg:pb-24 lg:pt-20">
        <motion.div {...rise(0)}>
          <Eyebrow className="text-balance text-[11px] leading-5 tracking-[0.12em] sm:text-[12px]">
            Open source · Apache-2.0 · Built on NVIDIA Nemotron and Nebius
          </Eyebrow>
        </motion.div>
        <motion.h1
          id="hero-title"
          {...rise(0.06)}
          className="mt-6 text-balance text-[2.75rem] font-semibold leading-[1.04] tracking-[-0.04em] text-ink min-[400px]:text-[3rem] sm:text-[4.25rem] lg:text-[4.9rem] xl:text-[5.35rem]"
        >
          <span className="block text-muted">Rent intelligence today.</span>
          <span className="block">Own it tomorrow.</span>
        </motion.h1>
        <div className="mt-10 grid grid-cols-1 items-start gap-10 sm:mt-12 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:grid-rows-[auto_1fr] lg:gap-x-14 lg:gap-y-0">
          <div className="lg:col-start-1 lg:row-start-1 lg:pt-1">
            <motion.p {...rise(0.12)} className="max-w-[34rem] text-pretty text-[17px] leading-relaxed text-ink-2 sm:text-lg">
              Ratchet is an OpenAI-compatible gateway. Your own traffic trains a small model that you own, so more of it is answered
              cheaply every night. It only learns from open teachers whose licenses allow it.
            </motion.p>
            <motion.div {...rise(0.18)} className="mt-8 flex flex-wrap items-center gap-3">
              <ButtonLink to="/scout" size="lg" className="w-full min-[440px]:w-auto">
                Try the Scout demo <ArrowRight />
              </ButtonLink>
              <ButtonA href="#learn" variant="outline" size="lg" className="w-full min-[440px]:w-auto">
                Watch it learn
              </ButtonA>
            </motion.div>
            <motion.div {...rise(0.22)} className="mt-5">
              <a
                href={REPO_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-[14px] text-muted transition-colors hover:text-ink"
              >
                View on GitHub <ArrowUpRight className="size-3.5" />
              </a>
            </motion.div>
          </div>

          <motion.div
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, ease: EASE, delay: 0.15 }}
            className="lg:col-start-2 lg:row-span-2 lg:row-start-1"
          >
            <HeroFlow />
            <p className="mt-3 px-1 text-[12px] leading-relaxed text-muted">
              Simulation, mock models: seed 7, 2,000 support tickets a day. It shows how the loop behaves, not real model quality.
            </p>
          </motion.div>
          <motion.ul {...rise(0.26)} className="grid gap-4 border-t border-line pt-6 text-[14px] sm:grid-cols-3 lg:col-start-1 lg:row-start-2 lg:mt-10 lg:grid-cols-1 lg:self-start">
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

export function OneLineChange() {
  return (
    <section aria-labelledby="one-line-title">
      <Container className="pb-4 pt-14 sm:pt-20">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "0px 0px -8% 0px" }}
          transition={{ duration: 0.7, ease: EASE }}
          className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-14"
        >
          <div className="lg:pt-2">
            <p className="font-mono text-[12px] uppercase tracking-[0.14em] text-muted">The only change in your app</p>
            <h2 id="one-line-title" className="mt-3 text-balance text-2xl font-semibold tracking-[-0.025em] text-ink sm:text-[1.75rem]">
              Point your OpenAI SDK at Ratchet. Name the task.
            </h2>
            <p className="mt-4 max-w-[46ch] text-pretty text-[16px] leading-relaxed text-ink-2">
              Same request, same response shape. Each response also says who answered it, how confident it was, what it cost, and what it
              would have cost on Ultra alone.
            </p>
            <ul className="mt-5 space-y-2 text-[14.5px] text-ink-2">
              <li className="flex gap-2.5">
                <span className="mt-[9px] size-1.5 shrink-0 rounded-full bg-owned" aria-hidden />
                <span>
                  <code className="font-mono text-[13px] text-ink">base_url</code> points at your Ratchet gateway
                </span>
              </li>
              <li className="flex gap-2.5">
                <span className="mt-[9px] size-1.5 shrink-0 rounded-full bg-owned" aria-hidden />
                <span>
                  <code className="font-mono text-[13px] text-ink">X-Ratchet-Task</code> groups requests the student can learn
                </span>
              </li>
            </ul>
          </div>
          <CodeBlock title="your_app.py" copy={SNIPPET} className="shadow-card">
            {tok.k("from")} openai {tok.k("import")} OpenAI{"\n\n"}
            client = OpenAI({"\n"}
            {"    "}
            {tok.h('base_url="http://localhost:8000/v1"')},{"\n"}
            {"    "}api_key={tok.s('"unused"')},{"\n"}){"\n"}
            r = client.chat.completions.create({"\n"}
            {"    "}model={tok.s('"ratchet"')},{"\n"}
            {"    "}messages=messages,{"\n"}
            {"    "}extra_headers={"{"}
            {"\n"}
            {"        "}
            {tok.h('"X-Ratchet-Task": "ticket-triage"')},{"\n"}
            {"    "}
            {"},"}
            {"\n"}){"\n"}
            {tok.k("print")}(r.ratchet)  {tok.c("# served_by, cost_usd, ...")}
          </CodeBlock>
        </motion.div>
      </Container>
    </section>
  );
}
