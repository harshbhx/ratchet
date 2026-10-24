import { ArrowUpRight, LayoutDashboard, Play, Search, Terminal } from "lucide-react";
import { Link } from "react-router";
import { ButtonA, ButtonLink } from "@/components/ui/button";
import { CodeBlock, tok } from "@/components/ui/code-block";
import { Container, Eyebrow, H2 } from "@/components/ui/section";
import { DEMO_URL, LIVE_URL, REPO_URL } from "@/lib/utils";
import { Reveal } from "../shared";

/**
 * "Try it in a minute": the shortest path from this page to a working request, for someone judging many projects. Three ways in
 * (click, paste one command, run it locally), each with what they will see. The live demo makes real Token Factory calls under
 * spend limits (the teacher answers every request for now); the simulation next to it runs mock models and is labelled as such.
 */
const CURL = `curl ${LIVE_URL}/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{"model":"ratchet","messages":[{"role":"user","content":"My order never arrived"}]}'`;

const LOCAL = `git clone ${REPO_URL}.git
cd ratchet && make install
make demo   # then open http://localhost:8000`;

function Step({ n, title, children }: { n: string; title: string; children: React.ReactNode }) {
  return (
    <li className="flex min-w-0 flex-col rounded-2xl border border-line bg-surface p-5 shadow-card sm:p-6">
      <div className="flex items-center gap-3">
        <span className="grid size-7 place-items-center rounded-full bg-ink font-mono text-[12px] font-semibold text-bg">{n}</span>
        <h3 className="text-[16px] font-semibold tracking-[-0.01em] text-ink">{title}</h3>
      </div>
      <div className="mt-4 flex flex-1 flex-col gap-3">{children}</div>
    </li>
  );
}

export function TryIt() {
  return (
    <section id="try" aria-labelledby="try-title" className="relative scroll-mt-16 border-t border-line bg-bg-sunken/50 py-14 sm:py-16">
      <Container>
        <Reveal>
          <Eyebrow>Try it</Eyebrow>
          <H2 id="try-title" className="mt-4 max-w-3xl">
            Send a request in under a minute.
          </H2>
          <p className="mt-3 max-w-[62ch] text-[15px] leading-relaxed text-ink-2">
            Three ways in, from nothing to a real request. The live demo calls Nebius Token Factory for real, under spend limits, and needs no key. The simulation and the local setup run mock models and cost nothing.
          </p>
          <ol className="mt-7 grid gap-4 lg:grid-cols-3">
            <Step n="1" title="Click">
              <p className="text-[14px] leading-relaxed text-ink-2">Run a simulated day, break a release on purpose, and watch the gate roll it back.</p>
              <div className="mt-auto flex flex-wrap gap-2.5 pt-2">
                <ButtonA href={LIVE_URL} target="_blank" rel="noopener noreferrer" size="lg">
                  <Play aria-hidden /> Open the live demo <ArrowUpRight aria-hidden />
                  <span className="sr-only"> (opens in a new tab)</span>
                </ButtonA>
                <ButtonA href={DEMO_URL} target="_blank" rel="noopener noreferrer" size="lg" variant="outline">
                  Simulation <ArrowUpRight aria-hidden />
                  <span className="sr-only"> (opens in a new tab)</span>
                </ButtonA>
                <ButtonLink to="/scout" variant="outline" size="lg">
                  <Search aria-hidden /> Scout
                </ButtonLink>
                <ButtonLink to="/dashboard" variant="outline" size="lg">
                  <LayoutDashboard aria-hidden /> Dashboard
                </ButtonLink>
              </div>
            </Step>
            <Step n="2" title="Paste one command">
              <p className="text-[14px] leading-relaxed text-ink-2">It is the OpenAI API. The reply carries a `ratchet` block that says who answered and what it cost. This one is a real call, so please keep it to a few.</p>
              <CodeBlock title="terminal" copy={CURL} className="mt-auto">
                {tok.k("curl")} {LIVE_URL.replace("https://", "")}/v1/chat/completions \
                {"\n  "}-H {tok.s('"Content-Type: application/json"')} \
                {"\n  "}-d {tok.s(`'{"model":"ratchet","messages":[{"role":"user","content":"My order never arrived"}]}'`)}
              </CodeBlock>
            </Step>
            <Step n="3" title="Run it yourself">
              <p className="text-[14px] leading-relaxed text-ink-2">The whole gateway, dashboard and Scout, on your machine. No GPU and no key; the tests run offline.</p>
              <CodeBlock title="terminal" copy={LOCAL} className="mt-auto">
                {tok.k("git clone")} {REPO_URL.replace("https://", "")}.git
                {"\n"}cd ratchet && {tok.k("make install")}
                {"\n"}
                {tok.k("make demo")} {tok.c("# open http://localhost:8000")}
              </CodeBlock>
            </Step>
          </ol>
          <p className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2 text-[13px] text-muted">
            <Terminal className="size-3.5" aria-hidden />
            <span>The simulation and a local install use mock models: their numbers are simulation. The measured results are above.</span>
            <Link to="/judges" className="font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink">
              For judges: where to look
            </Link>
          </p>
        </Reveal>
      </Container>
    </section>
  );
}
