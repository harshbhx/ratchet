import { LayoutDashboard, Search } from "lucide-react";
import { ButtonLink } from "@/components/ui/button";
import { CodeBlock, tok } from "@/components/ui/code-block";
import { Container, Eyebrow, Lead } from "@/components/ui/section";
import { useStats } from "@/lib/hooks";
import { LIVE_URL } from "@/lib/utils";
import { LiveStrip } from "../landing/sections/live-strip";
import { LiveRequest } from "../landing/sections/live-request";

const CURL = `curl ${LIVE_URL}/v1/chat/completions \\
  -H "Content-Type: application/json" \\
  -d '{"model":"ratchet","messages":[{"role":"user","content":"My order never arrived"}]}'`;

/**
 * The live gateway, inside this site: the same pages, header and footer. "Try a real request" sends its call to this site's own
 * /v1 path, which the Pages proxy forwards to the live gateway (no cross-origin call); Scout and the dashboard read the same gateway.
 * The absolute address appears only as the API base URL in the curl line.
 */
export default function Live() {
  const { stats } = useStats(15000);
  return (
    <div className="pb-16 sm:pb-24">
      <Container className="pt-10 sm:pt-14">
        <Eyebrow>Live gateway</Eyebrow>
        <h1 className="mt-4 max-w-3xl text-balance text-4xl font-semibold tracking-[-0.03em] text-ink sm:text-5xl">Send a real request.</h1>
        <Lead className="mt-4 max-w-[62ch]">
          This gateway makes real Nebius Token Factory calls under daily spend limits, and needs no key. The numbers below are its own traffic.
        </Lead>
      </Container>
      <div className="mt-8">
        <LiveStrip stats={stats} />
      </div>
      <Container className="mt-10">
        <LiveRequest mode={stats?.mode ?? null} className="mt-0" />
        <div className="mt-8 grid gap-4 md:grid-cols-2">
          <div className="flex flex-col rounded-2xl border border-line bg-surface p-5 shadow-card sm:p-6">
            <h2 className="text-[16px] font-semibold tracking-[-0.01em] text-ink">Keep going</h2>
            <p className="mt-2 text-[14px] leading-relaxed text-ink-2">
              Run Scout to watch each call get routed, or open the dashboard for the gateway's traffic, costs and releases.
            </p>
            <div className="mt-auto flex flex-wrap gap-2.5 pt-5">
              <ButtonLink to="/scout" size="lg">
                <Search aria-hidden /> Scout
              </ButtonLink>
              <ButtonLink to="/dashboard" variant="outline" size="lg">
                <LayoutDashboard aria-hidden /> Dashboard
              </ButtonLink>
            </div>
          </div>
          <div className="flex min-w-0 flex-col rounded-2xl border border-line bg-surface p-5 shadow-card sm:p-6">
            <h2 className="text-[16px] font-semibold tracking-[-0.01em] text-ink">From your terminal</h2>
            <p className="mt-2 text-[14px] leading-relaxed text-ink-2">It is the OpenAI API. This is a real call, so please keep it to a few.</p>
            <CodeBlock title="terminal" copy={CURL} className="mt-4">
              {tok.k("curl")} {LIVE_URL.replace("https://", "")}/v1/chat/completions \
              {"\n  "}-H {tok.s('"Content-Type: application/json"')} \
              {"\n  "}-d {tok.s(`'{"model":"ratchet","messages":[{"role":"user","content":"My order never arrived"}]}'`)}
            </CodeBlock>
          </div>
        </div>
      </Container>
    </div>
  );
}
