import { ArrowRight, ArrowUpRight, BookOpenText } from "lucide-react";
import { ButtonA, ButtonLink } from "@/components/ui/button";
import { CodeBlock, tok } from "@/components/ui/code-block";
import { Container } from "@/components/ui/section";
import { DOCS_URL, REPO_URL } from "@/lib/utils";
import { LogoMark } from "@/components/ui/logo";
import { Chapter, ChapterHead, Lead, Reveal } from "../shared";

const QUICKSTART = `pip install -e ".[dev]"

# the full loop in simulation (mock models)
python -m sim.simulate --days 10 \\
  --requests 2000 --bad-deploy-version 3

# the gateway, with a trained student
RATCHET_MODE=mock \\
RATCHET_MOCK_WARMUP_DAYS=4 \\
  uvicorn ratchet.gateway:app --port 8000

pytest -q`;

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
                    Apache-2.0. No keys and no GPU needed to see the whole loop: mock mode runs the gateway, Night School, the gates and Scout on
                    your laptop.
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
          </div>
          <Reveal delay={0.05}>
            <CodeBlock title="quick start · terminal" copy={QUICKSTART} className="bg-surface shadow-card">
              {tok.k("pip")} install -e {tok.s('".[dev]"')}
              {"\n\n"}
              {tok.c("# the full loop in simulation (mock models)")}
              {"\n"}
              {tok.k("python")} -m sim.simulate --days 10 \{"\n"}
              {"  "}--requests 2000 --bad-deploy-version 3{"\n\n"}
              {tok.c("# the gateway, with a trained student")}
              {"\n"}
              RATCHET_MODE=mock \{"\n"}
              RATCHET_MOCK_WARMUP_DAYS=4 \{"\n"}
              {"  "}
              {tok.k("uvicorn")} ratchet.gateway:app --port 8000{"\n\n"}
              {tok.k("pytest")} -q
            </CodeBlock>
          </Reveal>
        </div>

        <Reveal className="mt-20 sm:mt-28">
          <div className="relative overflow-hidden rounded-[28px] border border-line bg-surface px-5 py-12 text-center shadow-card sm:px-10 sm:py-16">
            <div aria-hidden className="pointer-events-none absolute inset-0 bg-grid [mask-image:radial-gradient(ellipse_60%_80%_at_50%_100%,black,transparent)]" />
            <div className="relative">
              <LogoMark className="mx-auto mb-7 size-11" />
              <h2 className="mx-auto max-w-[22ch] text-balance text-3xl font-semibold tracking-[-0.03em] text-ink sm:text-[2.75rem] sm:leading-[1.08]">
                Your traffic is already teaching. Keep what it learns.
              </h2>
              <p className="mx-auto mt-5 max-w-[52ch] text-pretty text-[17px] leading-relaxed text-ink-2">
                Run Scout and watch each call get routed. Then open the dashboard to see every version, gate and rollback.
              </p>
              <div className="mt-8 flex flex-wrap justify-center gap-3">
                <ButtonLink to="/scout" size="lg">
                  Try the Scout demo <ArrowRight />
                </ButtonLink>
                <ButtonLink to="/dashboard" variant="outline" size="lg">
                  Open the dashboard
                </ButtonLink>
              </div>
            </div>
          </div>
        </Reveal>
      </Container>
    </Chapter>
  );
}
