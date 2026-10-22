import { useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { GlowOrbs } from "@/components/fx/glow-orbs";
import { Highlight } from "@/components/fx/highlight";
import { CodeBlock, tok } from "@/components/ui/code-block";
import { Container, Eyebrow, H2, Lead } from "@/components/ui/section";
import { cn } from "@/lib/utils";
import { EASE } from "@/lib/motion";
import { Reveal } from "../shared";

type Lang = "python" | "node" | "curl";

const TABS: { id: Lang; label: string; file: string; copy: string; code: ReactNode }[] = [
  {
    id: "python",
    label: "Python",
    file: "your_app.py",
    copy: `from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="unused",
)
r = client.chat.completions.create(
    model="ratchet",
    messages=messages,
    extra_headers={"X-Ratchet-Task": "ticket-triage"},
)
print(r.ratchet)  # served_by, confidence, cost_usd, ...`,
    code: (
      <>
        {tok.k("from")} openai {tok.k("import")} OpenAI{"\n\n"}
        client = OpenAI({"\n"}
        {"    "}
        {tok.h('base_url="http://localhost:8000/v1"')},{"\n"}
        {"    "}api_key={tok.s('"unused"')},{"\n"}){"\n"}
        r = client.chat.completions.create({"\n"}
        {"    "}model={tok.s('"ratchet"')},{"\n"}
        {"    "}messages=messages,{"\n"}
        {"    "}extra_headers={"{"}
        {tok.h('"X-Ratchet-Task": "ticket-triage"')}
        {"},"}
        {"\n"}){"\n"}
        {tok.k("print")}(r.ratchet)  {tok.c("# served_by, confidence, cost_usd, ...")}
      </>
    ),
  },
  {
    id: "node",
    label: "Node",
    file: "app.mjs",
    copy: `import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://localhost:8000/v1",
  apiKey: "unused",
});
const r = await client.chat.completions.create(
  { model: "ratchet/ticket-triage", messages },
);
console.log(r.ratchet); // served_by, confidence, cost_usd, ...`,
    code: (
      <>
        {tok.k("import")} OpenAI {tok.k("from")} {tok.s('"openai"')};{"\n\n"}
        {tok.k("const")} client = {tok.k("new")} OpenAI({"{"}
        {"\n"}
        {"  "}
        {tok.h('baseURL: "http://localhost:8000/v1"')},{"\n"}
        {"  "}apiKey: {tok.s('"unused"')},{"\n"}
        {"}"});{"\n"}
        {tok.k("const")} r = {tok.k("await")} client.chat.completions.create({"\n"}
        {"  "}
        {"{"} model: {tok.h('"ratchet/ticket-triage"')}, messages {"}"},{"\n"}
        );{"\n"}
        console.log(r.ratchet); {tok.c("// served_by, confidence, cost_usd, ...")}
      </>
    ),
  },
  {
    id: "curl",
    label: "curl",
    file: "terminal",
    copy: `curl http://localhost:8000/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -H "X-Ratchet-Task: ticket-triage" \\
  -d '{"model": "ratchet", "messages": [{"role": "user", "content": "I was charged twice."}]}'`,
    code: (
      <>
        {tok.k("curl")} {tok.h("http://localhost:8000/v1/chat/completions")} \{"\n"}
        {"  "}-H {tok.s('"Content-Type: application/json"')} \{"\n"}
        {"  "}-H {tok.h('"X-Ratchet-Task: ticket-triage"')} \{"\n"}
        {"  "}-d {tok.s(`'{"model": "ratchet", "messages": [{"role": "user", "content": "I was charged twice."}]}'`)}
      </>
    ),
  },
];

/** The `ratchet` block every response carries (docs/API.md), the fields a team reads first. */
const RESPONSE = [
  ["served_by", '"student"', "or teacher"],
  ["student_version", "5", "the adapter that answered"],
  ["confidence", "0.951", "against this version's threshold"],
  ["cost_usd", "0.0000312", "what this answer cost"],
  ["all_teacher_cost_usd", "0.00131", "what Ultra alone would have cost"],
  ["request_id", '"req_3f1c…"', "for feedback and lookups"],
];

export function Integrate() {
  const [lang, setLang] = useState<Lang>("python");
  const base = useId();
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const next = e.key === "ArrowRight" ? (i + 1) % TABS.length : e.key === "ArrowLeft" ? (i - 1 + TABS.length) % TABS.length : e.key === "Home" ? 0 : e.key === "End" ? TABS.length - 1 : -1;
    if (next < 0) return;
    e.preventDefault();
    setLang(TABS[next].id);
    tabs.current[next]?.focus();
  };
  const cur = TABS.find((t) => t.id === lang)!;
  return (
    <section id="integrate" aria-labelledby="integrate-title" className="relative scroll-mt-16 border-t border-line bg-bg-sunken/40 py-16 sm:py-20 lg:py-24">
      <GlowOrbs tones={["owned"]} className="[mask-image:linear-gradient(to_right,transparent,black_60%)]" />
      <Container className="relative">
        <div className="grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,0.8fr)_minmax(0,1.2fr)] lg:gap-14">
          <Reveal>
            <Eyebrow>Integration</Eyebrow>
            <H2 id="integrate-title" className="mt-5">
              Point your OpenAI SDK at Ratchet. <Highlight>Name the task.</Highlight>
            </H2>
            <Lead className="mt-5 max-w-[46ch]">
              Same request, same response shape, in any language with an OpenAI client. Each response also says who answered it, how
              confident it was, what it cost, and what it would have cost on Ultra alone.
            </Lead>
            <ul className="mt-6 space-y-2.5 text-[14.5px] text-ink-2">
              <li className="flex gap-2.5">
                <span className="mt-[9px] size-1.5 shrink-0 rounded-full bg-owned" aria-hidden />
                <span>
                  <code className="font-mono text-[13px] text-ink">base_url</code> points at your Ratchet gateway
                </span>
              </li>
              <li className="flex gap-2.5">
                <span className="mt-[9px] size-1.5 shrink-0 rounded-full bg-owned" aria-hidden />
                <span>
                  <code className="font-mono text-[13px] text-ink">X-Ratchet-Task</code>, or a model of the form{" "}
                  <code className="font-mono text-[13px] text-ink">ratchet/&lt;task&gt;</code>, groups the requests the student can learn
                </span>
              </li>
              <li className="flex gap-2.5">
                <span className="mt-[9px] size-1.5 shrink-0 rounded-full bg-owned" aria-hidden />
                <span>Streaming, tools, structured outputs and the Azure-style route work as in the OpenAI API</span>
              </li>
            </ul>
            <dl className="mt-7 overflow-hidden rounded-xl border border-line bg-surface text-[12.5px] shadow-card">
              <div className="border-b border-line bg-surface-2/60 px-3.5 py-2 font-mono text-[11.5px] text-muted">
                response.ratchet <span className="text-ink-2">· every answer carries it</span>
              </div>
              {RESPONSE.map(([k, v, why], i) => (
                <motion.div
                  key={k}
                  initial={{ opacity: 0, x: -8 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true, margin: "0px 0px -8% 0px" }}
                  transition={{ duration: 0.45, ease: EASE, delay: 0.1 + i * 0.07 }}
                  className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 border-b border-line px-3.5 py-1.5 last:border-b-0 sm:grid-cols-[10.5rem_auto_minmax(0,1fr)]"
                >
                  <dt className="font-mono text-ink">{k}</dt>
                  <dd className="font-mono text-ink-2 tabular">{v}</dd>
                  <dd className="col-span-2 text-[12px] text-muted sm:col-span-1 sm:text-right">{why}</dd>
                </motion.div>
              ))}
            </dl>
          </Reveal>
          <Reveal delay={0.08}>
            <div className="overflow-hidden rounded-[22px] border border-line bg-surface shadow-card">
              <div role="tablist" aria-label="Language" className="flex gap-1 border-b border-line bg-surface-2/60 p-1.5">
                {TABS.map((t, i) => (
                  <button
                    key={t.id}
                    ref={(el) => {
                      tabs.current[i] = el;
                    }}
                    id={`${base}-tab-${t.id}`}
                    role="tab"
                    type="button"
                    aria-selected={lang === t.id}
                    aria-controls={`${base}-panel`}
                    tabIndex={lang === t.id ? 0 : -1}
                    onClick={() => setLang(t.id)}
                    onKeyDown={(e) => onKey(e, i)}
                    className={cn(
                      "relative rounded-lg px-3.5 py-1.5 text-[13px] font-medium transition-colors",
                      lang === t.id ? "text-ink" : "text-muted hover:text-ink",
                    )}
                  >
                    {lang === t.id && (
                      <motion.span layoutId="integrate-tab" aria-hidden className="absolute inset-0 rounded-lg bg-surface shadow-card" transition={{ duration: 0.3, ease: EASE }} />
                    )}
                    <span className="relative">{t.label}</span>
                  </button>
                ))}
              </div>
              <div id={`${base}-panel`} role="tabpanel" aria-labelledby={`${base}-tab-${cur.id}`}>
                <AnimatePresence mode="wait" initial={false}>
                  <motion.div
                    key={cur.id}
                    initial={{ opacity: 0, y: 6 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={{ duration: 0.22, ease: EASE }}
                  >
                    <CodeBlock title={cur.file} copy={cur.copy} className="rounded-none border-0 bg-surface [&_pre]:whitespace-pre-wrap [&_pre]:[overflow-wrap:anywhere]">
                      {cur.code}
                    </CodeBlock>
                  </motion.div>
                </AnimatePresence>
              </div>
            </div>
          </Reveal>
        </div>
      </Container>
    </section>
  );
}
