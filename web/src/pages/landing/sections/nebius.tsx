import { ArrowUpRight, Server } from "lucide-react";
import { ButtonA } from "@/components/ui/button";
import { CodeBlock, tok } from "@/components/ui/code-block";
import { Container, Eyebrow, H2 } from "@/components/ui/section";
import { DOCS_URL } from "@/lib/utils";
import { Reveal } from "../shared";

/**
 * "Run it on Nebius": the one-command path (deploy/nebius/nebius.sh, `make nebius-plan`), with the honest label directly under
 * the command box. The script is written and tested offline against a fake CLI, ready to run, and
 * the page says so next to the button. Nebius is named only as the place it is hosted: no endorsement is claimed.
 */
const COMMANDS = `make nebius-plan   # a dry run: prints every command, changes nothing
make nebius-up     # also a dry run until you add YES=1 and RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1`;

const GUIDE_URL = `${DOCS_URL}reference/where-it-runs/#one-command-to-host-it-all-on-nebius`;
const HOST_GUIDE_URL = `${DOCS_URL}guides/host-on-nebius/`;
const MAP_URL = `${DOCS_URL}reference/where-it-runs/`;

export function RunOnNebius() {
  return (
    <section id="nebius" aria-labelledby="nebius-title" className="relative scroll-mt-16 border-t border-line py-14 sm:py-16">
      <Container>
        <Reveal>
          <div className="grid grid-cols-1 gap-8 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] lg:items-center lg:gap-12">
            <div>
              <Eyebrow>Host it</Eyebrow>
              <H2 id="nebius-title" className="mt-4 max-w-xl">
                Run it on Nebius.
              </H2>
              <p className="mt-3 max-w-[56ch] text-[15px] leading-relaxed text-ink-2">
                One command prints every step to host the gateway on a small CPU VM, with an optional GPU endpoint for the student. The router is plain CPU
                software and cheap to host; only training and the student endpoint need a GPU, and the GPU parts are opt-in flags.
              </p>
              <div className="mt-6 flex flex-wrap gap-3">
                <ButtonA href={GUIDE_URL} target="_blank" rel="noopener noreferrer" size="lg">
                  <Server aria-hidden /> Run it on Nebius <ArrowUpRight aria-hidden />
                  <span className="sr-only"> (opens in a new tab)</span>
                </ButtonA>
                <ButtonA href={MAP_URL} target="_blank" rel="noopener noreferrer" size="lg" variant="outline">
                  Where everything runs <ArrowUpRight aria-hidden />
                  <span className="sr-only"> (opens in a new tab)</span>
                </ButtonA>
              </div>
            </div>
            <div>
              <CodeBlock title="terminal · in the repository" copy={COMMANDS} className="bg-surface shadow-card">
                {tok.k("make")} nebius-plan {"  "}
                {tok.c("# a dry run: prints every command, changes nothing")}
                {"\n"}
                {tok.k("make")} nebius-up {"    "}
                {tok.c("# also a dry run until you add YES=1 and")}
                {"\n"}
                {"                 "}
                {tok.c("# RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1")}
              </CodeBlock>
              <p className="mt-3 text-[13px] font-medium leading-snug text-ink-2">Scripted and tested offline; ready to run with make nebius-plan.</p>
              <p className="mt-1 text-[13px] leading-snug text-muted">
                Step by step, with costs as estimates:{" "}
                <a className="underline underline-offset-2 hover:text-ink" href={HOST_GUIDE_URL} target="_blank" rel="noopener noreferrer">
                  the guide<span className="sr-only"> (opens in a new tab)</span>
                </a>
                .
              </p>
            </div>
          </div>
        </Reveal>
      </Container>
    </section>
  );
}
