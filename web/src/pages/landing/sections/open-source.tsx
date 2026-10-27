import { ArrowUpRight, BookOpenText } from "lucide-react";
import { ButtonA } from "@/components/ui/button";
import { CodeBlock, tok } from "@/components/ui/code-block";
import { Container } from "@/components/ui/section";
import { DOCS_URL, REPO_URL } from "@/lib/utils";
import { Chapter, ChapterHead, Lead, Reveal } from "../shared";
import { ClosingCta } from "./closing-cta";

const QUICKSTART = `pip install -e ".[dev]"

# the gateway on mock models, offline
RATCHET_MODE=mock \\
RATCHET_MOCK_WARMUP_DAYS=4 \\
  uvicorn ratchet.gateway:app --port 8000

pytest -q`;

/** What the test suite is: the count `pytest --collect-only` prints (tests/AGENTS.md), run offline, no GPU. */
const PROOF = [
  { value: "1,934", label: "tests, offline", sub: "no network, no GPU, no API key" },
  { value: "26", label: "invariants", sub: "numbered in AGENTS.md, traced to tests in CI" },
  { value: "Apache‑2.0", label: "the code", sub: "model weights keep their own licenses" },
];

export function OpenSource() {
  return (
    <Chapter id="run" className="border-t border-line bg-bg-sunken/60 pb-16 sm:pb-20 lg:pb-24">
      <Container>
        <div className="grid grid-cols-1 gap-10 lg:grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] lg:items-center lg:gap-14">
          <div>
            <ChapterHead
              id="run"
              title="Open source. Run it in a minute."
              lead={
                <>
                  <Lead>
                    Apache-2.0. No keys and no GPU needed to try the code: mock mode runs the gateway, Night School, the gates and Scout on
                    your laptop. Night School is built and tested, and has not run on live traffic.
                  </Lead>
                  <Lead>
                    Model weights keep their own licenses: Nemotron Nano under the NVIDIA Nemotron Open Model License, Nemotron 3 Ultra under
                    OpenMDW-1.1.
                  </Lead>
                </>
              }
            />
            <Reveal delay={0.1} className="mt-8 flex flex-wrap gap-3">
              <ButtonA href={REPO_URL} target="_blank" rel="noopener noreferrer" variant="outline">
                GitHub repository <ArrowUpRight />
              </ButtonA>
              <ButtonA href={DOCS_URL} target="_blank" rel="noopener noreferrer" variant="ghost">
                <BookOpenText /> Documentation
              </ButtonA>
            </Reveal>
            <Reveal delay={0.15} className="mt-8">
              <dl className="grid grid-cols-3 divide-x divide-line rounded-2xl border border-line bg-surface shadow-card">
                {PROOF.map((p) => (
                  <div key={p.label} className="min-w-0 px-3 py-4 sm:px-4">
                    <dd className="whitespace-nowrap text-[18px] font-semibold leading-none tracking-[-0.03em] text-ink tabular sm:text-[22px]">{p.value}</dd>
                    <dt className="mt-1.5 text-[12.5px] font-medium text-ink-2">{p.label}</dt>
                    <dd className="mt-0.5 text-[11.5px] leading-snug text-muted">{p.sub}</dd>
                  </div>
                ))}
              </dl>
            </Reveal>
          </div>
          <Reveal delay={0.05}>
            <CodeBlock title="quick start · terminal" copy={QUICKSTART} className="bg-surface shadow-card">
              {tok.k("pip")} install -e {tok.s('".[dev]"')}
              {"\n\n"}
              {tok.c("# the gateway on mock models, offline")}
              {"\n"}
              RATCHET_MODE=mock \{"\n"}
              RATCHET_MOCK_WARMUP_DAYS=4 \{"\n"}
              {"  "}
              {tok.k("uvicorn")} ratchet.gateway:app --port 8000{"\n\n"}
              {tok.k("pytest")} -q
            </CodeBlock>
          </Reveal>
        </div>

        <ClosingCta className="mt-20 sm:mt-28" />
      </Container>
    </Chapter>
  );
}
