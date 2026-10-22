import type { ReactNode } from "react";
import { Link } from "react-router";
import { ArrowRight, EyeOff, FileSignature, FlaskConical, ListChecks, ShieldAlert, ShieldCheck, Sigma, type LucideIcon } from "lucide-react";
import type { Stats } from "@/lib/api";
import { GlowOrbs } from "@/components/fx/glow-orbs";
import { Highlight } from "@/components/fx/highlight";
import { SpotlightCard } from "@/components/fx/spotlight-card";
import { Badge } from "@/components/ui/badge";
import { CodeBlock, tok } from "@/components/ui/code-block";
import { Container } from "@/components/ui/section";
import { REPO_URL } from "@/lib/utils";
import {
  BOM_FETCH,
  BOM_OK,
  BOM_TAMPERED,
  BOM_VERIFY,
  GATE_TESTS,
  REQTRACE_CHECK,
  TRUST_GATE,
  TRUST_REDACTS,
  clip,
  listWords,
} from "../trust-model";
import { Chapter, ChapterHead, ExtLink, Lead, Reveal } from "../shared";

const pct = (v: number) => `${Math.round(v * 100)}%`;

function Claim({
  icon: Icon,
  title,
  status,
  children,
  check,
  code,
  delay,
}: {
  icon: LucideIcon;
  title: string;
  status?: ReactNode;
  children: ReactNode;
  check: ReactNode;
  code: string[];
  delay: number;
}) {
  return (
    <Reveal as="li" delay={delay} className="min-w-0">
      <SpotlightCard tone="owned" className="h-full">
        <div className="flex flex-1 flex-col p-5 sm:p-6">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="inline-flex size-9 items-center justify-center rounded-xl border border-line bg-surface-2 text-ink-2">
              <Icon className="size-[18px]" aria-hidden />
            </span>
            {status}
          </div>
          <h3 className="mt-5 text-balance text-[18px] font-semibold leading-snug tracking-[-0.015em] text-ink">{title}</h3>
          <div className="mt-3 text-pretty text-[14.5px] leading-relaxed text-ink-2">{children}</div>
          <div className="mt-5 flex-1">{check}</div>
        </div>
        <p className="border-t border-line px-5 py-3 font-mono text-[11.5px] leading-relaxed text-muted [overflow-wrap:anywhere] sm:px-6">
          {code.map((c, i) => (
            <span key={c}>
              {i > 0 && <span aria-hidden> · </span>}
              {c}
            </span>
          ))}
        </p>
      </SpotlightCard>
    </Reveal>
  );
}

function Here({ good, children }: { good: boolean; children: ReactNode }) {
  return good ? (
    <Badge tone="good">
      <ShieldCheck aria-hidden /> {children}
    </Badge>
  ) : (
    <Badge tone="outline">
      <ShieldAlert aria-hidden /> {children}
    </Badge>
  );
}

function Terminal({ title, lines }: { title: string; lines: Array<{ text: string; kind: "cmd" | "comment" | "out" }> }) {
  return (
    <CodeBlock
      title={title}
      copy={lines.filter((l) => l.kind === "cmd").map((l) => l.text).join("\n")}
      className="bg-bg-sunken [&_pre]:whitespace-pre-wrap [&_pre]:[overflow-wrap:anywhere]"
    >
      {lines.map((l, i) => (
        <span key={i}>
          {i > 0 && "\n"}
          {l.kind === "comment" ? tok.c(l.text) : l.kind === "out" ? <span className="text-ink">{l.text}</span> : l.text}
        </span>
      ))}
    </CodeBlock>
  );
}

export function Checked({ stats }: { stats: Stats | null }) {
  const privacy = stats?.privacy;
  const security = stats?.security;
  const rollback = stats ? [...stats.events].reverse().find((e) => e.kind === "rollback") : undefined;
  const mock = stats?.mode === "mock";

  return (
    <Chapter id="trust" className="border-t border-line bg-bg-sunken/60">
      <GlowOrbs tones={["good", "owned"]} className="[mask-image:linear-gradient(to_bottom,black_30%,transparent)]" />
      <Container className="relative">
        <ChapterHead
          id="trust"
          title={
            <>
              Built to be <Highlight>checked.</Highlight>
            </>
          }
          lead={
            <Lead>
              A page like this should not ask for trust. Each item below is in the code today, with a way to check it yourself: a page to try,
              a command to run, or a file to read.
            </Lead>
          }
        />

        <ul className="mt-12 grid grid-cols-1 gap-5 lg:grid-cols-2">
          <Claim
            icon={EyeOff}
            title="Personal data is redacted before it becomes training data."
            status={privacy && <Here good={privacy.redaction}>{privacy.redaction ? "On here" : "Off here"}</Here>}
            code={["ratchet/redact.py", "POST /ratchet/redact", "invariant 12"]}
            delay={0}
            check={
              <div className="rounded-xl border border-line bg-bg-sunken/70 p-4">
                <p className="text-[13px] text-ink-2">Paste a support message and see what would be stored. The preview stores nothing.</p>
                <Link
                  to="/dashboard#redactor"
                  className="mt-3 inline-flex items-center gap-1.5 text-[14px] font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink"
                >
                  Try the redactor on the dashboard <ArrowRight className="size-3.5" aria-hidden />
                </Link>
              </div>
            }
          >
            With redaction on, as it is by default, {listWords(TRUST_REDACTS)} are replaced with placeholders such as{" "}
            <code className="font-mono text-[13px] text-ink">[EMAIL]</code> before a request and its teacher answer are stored as a training
            example. Names and street addresses are not detected. The teacher still sees the original request, and the request log keeps
            metadata only, never the text.
          </Claim>

          <Claim
            icon={FileSignature}
            title="Each adapter carries a signed bill of materials."
            status={
              security && (
                <Here good={security.bom_signing}>{security.bom_signing ? `Signed here · key ${security.bom_key_id ?? "–"}` : "Unsigned here"}</Here>
              )
            }
            code={["ratchet/bom.py", "GET /ratchet/versions/{id}/bom", "invariant 14"]}
            delay={0.08}
            check={
              <Terminal
                title="check a BOM · terminal"
                lines={[
                  { text: BOM_FETCH, kind: "cmd" },
                  { text: "# offline from here: no gateway, no network", kind: "comment" },
                  { text: BOM_VERIFY, kind: "cmd" },
                  { text: BOM_OK, kind: "out" },
                  { text: "# after any edit to the manifest:", kind: "comment" },
                  { text: BOM_TAMPERED, kind: "out" },
                ]}
              />
            }
          >
            Written once, when the adapter is trained, from the exact rows it was trained and graded on: each teacher and the license that
            allows training on its answers, SHA-256 hashes of the training and golden sets, the redaction rules the text went through, and
            the evaluation it passed. With a signing key set it is signed with Ed25519, and it is never rewritten.
          </Claim>

          <Claim
            icon={ListChecks}
            title="Every requirement is traced to its code and its checks, in CI."
            code={["scripts/reqtrace.py", "docs/REQUIREMENTS.md", ".github/workflows/ci.yml"]}
            delay={0}
            check={
              <div className="space-y-3">
                <Terminal title="traceability · terminal" lines={[{ text: REQTRACE_CHECK, kind: "cmd" }]} />
                <ExtLink href={`${REPO_URL}/blob/main/docs/REQUIREMENTS.md`} className="text-[14px]">
                  Read the requirements matrix
                </ExtLink>
              </div>
            }
          >
            Each requirement in the PRD and each invariant is mapped, in a generated matrix, to the code that implements it and the tests
            that prove it, or to the live or manual check that covers it. CI fails when a requirement marked done has neither, a code
            reference breaks, an endpoint or setting is undocumented, or the matrix is out of date.
          </Claim>

          <Claim
            icon={Sigma}
            title="Releases are gated on held-out data and a confidence bound."
            code={["ratchet/release.py", "tests/test_release.py", "invariants 4 and 6"]}
            delay={0.08}
            check={
              <div className="space-y-3">
                <Terminal title="release gate tests · terminal" lines={[{ text: GATE_TESTS, kind: "cmd" }]} />
                {rollback && (
                  <p className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-[12.5px] leading-relaxed text-muted">
                    {mock && (
                      <Badge tone="outline">
                        <FlaskConical aria-hidden /> Simulation
                      </Badge>
                    )}
                    <span>
                      Latest rollback on this gateway, day {rollback.day}: <span className="text-ink-2">{clip(rollback.detail)}</span>
                    </span>
                  </p>
                )}
              </div>
            }
          >
            A new version needs golden precision of at least {pct(TRUST_GATE.min_precision)}, measured on rows its threshold was not
            calibrated on, and its precision and coverage may fall no more than {Math.round(TRUST_GATE.tolerance * 100)} points below the
            live model's, re-scored on the same rows the same night. As a canary it moves up a stage only after at
            least {TRUST_GATE.min_audits} audits there at {pct(TRUST_GATE.live_floor)} or better, and it is rolled back when the 95% Wilson
            upper bound of its audited precision is below {pct(TRUST_GATE.live_floor)}: even the most generous reading of its audits is too
            low. In between, it holds and keeps auditing.
          </Claim>
        </ul>

        <Reveal className="mt-5">
          <div className="flex flex-col gap-3 rounded-2xl border border-dashed border-line-strong px-5 py-4 sm:flex-row sm:items-start sm:gap-4">
            <Badge tone="outline" className="self-start">
              In v0.5
            </Badge>
            <p className="text-pretty text-[13.5px] leading-relaxed text-ink-2">
              Each candidate's signed bill of materials now also carries a certified lower bound on its precision on the held-out golden rows,
              which <code className="font-mono text-[12.5px]">python -m ratchet.stats check</code> recomputes from the signed counts. Being built
              on foundations already in the code, with their tests (<code className="font-mono text-[12.5px]">ratchet/stats.py</code>,{" "}
              <code className="font-mono text-[12.5px]">ratchet/merkle.py</code>): each adapter's training and golden sets committed with Merkle
              proofs, and signed savings receipts per API client.
            </p>
          </div>
        </Reveal>
      </Container>
    </Chapter>
  );
}
