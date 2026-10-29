import type { ReactNode } from "react";
import { Link } from "react-router";
import { ArrowUpRight, BookOpen, CalendarDays, CircleCheck, Hourglass, Radio, ShieldCheck, ChevronRight } from "lucide-react";
import { Button, ButtonA, ButtonLink } from "@/components/ui/button";
import { GithubIcon, HuggingFaceIcon, YoutubeIcon } from "@/components/ui/brand-icons";
import { Container } from "@/components/ui/section";
import { RealBadge } from "@/components/ui/real-badge";
import { cn } from "@/lib/utils";
import packText from "@/data/pack.json?raw";
import { hrefKind, parsePack, type LinkGroup, type PackIcon, type PackLink, type ProofCard } from "./pack-model";
import { Command } from "./ui";

/** The pack's data (src/data/pack.json), parsed once; null renders nothing, never half a pack. */
export const PACK = parsePack(packText);

const rowClass =
  "group/row flex min-w-0 items-baseline justify-between gap-3 rounded-lg px-2.5 py-1.5 text-[14px] leading-snug text-ink transition-colors hover:bg-surface-2 focus-visible:bg-surface-2";

/** One link: a page of this site goes through the router, an anchor stays on the page, anything else opens in a new tab. */
function PackAnchor({ link, className, children, avoid }: { link: PackLink; className?: string; children: ReactNode; avoid?: boolean }) {
  const kind = hrefKind(link.href);
  const mark = avoid ? { "data-companion-avoid": "" } : {};
  if (kind === "page")
    return (
      <Link to={link.href} className={className} {...mark}>
        {children}
      </Link>
    );
  if (kind === "anchor")
    return (
      <a href={link.href} className={className} {...mark}>
        {children}
      </a>
    );
  return (
    <a href={link.href} target="_blank" rel="noopener noreferrer" className={className} {...mark}>
      {children}
    </a>
  );
}

function LinkRow({ link }: { link: PackLink }) {
  const external = hrefKind(link.href) === "external";
  return (
    <PackAnchor link={link} className={rowClass}>
      <span className="min-w-0 font-medium [overflow-wrap:anywhere]">{link.label}</span>
      <span className="flex shrink-0 items-center gap-1.5 text-[12.5px] text-muted">
        {link.note && <span className="hidden text-right sm:inline">{link.note}</span>}
        {external ? <ArrowUpRight className="size-3.5" aria-hidden /> : <ChevronRight className="size-3.5" aria-hidden />}
      </span>
    </PackAnchor>
  );
}

function GroupIcon({ icon, className }: { icon: PackIcon; className?: string }) {
  const c = cn("size-[18px]", className);
  switch (icon) {
    case "github":
      return <GithubIcon className={c} />;
    case "hf":
      return <HuggingFaceIcon className={c} />;
    case "youtube":
      return <YoutubeIcon className={c} />;
    case "book":
      return <BookOpen className={c} aria-hidden />;
    case "shield":
      return <ShieldCheck className={c} aria-hidden />;
    default:
      return <Radio className={c} aria-hidden />;
  }
}

function SectionHead({ id, title, lead }: { id: string; title: string; lead: ReactNode }) {
  return (
    <div className="mb-5 max-w-[64ch]">
      <h2 id={`${id}-title`} tabIndex={-1} className="text-[26px] font-semibold leading-tight tracking-[-0.025em] text-ink outline-none sm:text-[32px]">
        {title}
      </h2>
      <p className="mt-1.5 text-pretty text-[15.5px] leading-relaxed text-ink-2">{lead}</p>
    </div>
  );
}

// ---------------------------------------------------------------- hero

/** The hero: the approved headline, the pitch and the three primary buttons, with the five-minute path beside them. */
export function PackHero({ children, video }: { children?: ReactNode; video: string }) {
  if (!PACK) return null;
  return (
    <Container className="pb-10 pt-8 sm:pt-12">
      <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)] lg:gap-12">
        <div className="min-w-0">
          <p className="font-mono text-[12.5px] text-muted">For judges</p>
          <h1 className="mt-3 text-balance text-[34px] font-semibold leading-[1.06] tracking-[-0.03em] text-ink sm:text-[46px]">
            A small open model that learns from a big one, with the receipts.
          </h1>
          <p className="mt-5 border-l-2 border-owned pl-4 text-pretty text-[17px] font-medium leading-relaxed text-ink">{PACK.headline}</p>
          <p className="mt-4 text-pretty text-[15.5px] leading-relaxed text-ink-2">{PACK.pitch}</p>
          <div data-companion-avoid className="mt-6 flex flex-wrap items-center gap-2.5">
            <ButtonLink to="/live" size="lg">
              Try it live
            </ButtonLink>
            <Button size="lg" variant="outline" onClick={() => document.getElementById("proof")?.scrollIntoView({ behavior: "smooth", block: "start" })}>
              Check the evidence
            </Button>
            <ButtonA href={video} target="_blank" rel="noopener noreferrer" size="lg" variant="outline">
              <YoutubeIcon className="size-4" /> Watch the video
            </ButtonA>
          </div>
          <div className="mt-6 space-y-3">{children}</div>
        </div>
        <PathCard />
      </div>
    </Container>
  );
}

/** "Check it in 5 minutes": four steps, each with the one thing to click or run. */
function PathCard() {
  if (!PACK) return null;
  return (
    <section aria-labelledby="path-title" className="min-w-0 rounded-3xl border border-line-strong bg-surface shadow-card">
      <header className="border-b border-line px-5 py-4 sm:px-6">
        <h2 id="path-title" className="text-[17px] font-semibold tracking-[-0.02em] text-ink">
          Check it in 5 minutes
        </h2>
      </header>
      <ol className="divide-y divide-line">
        {PACK.path.map((s, i) => (
          <li key={s.id} className="grid min-w-0 grid-cols-[28px_minmax(0,1fr)] gap-x-3 px-5 py-4 sm:px-6">
            <span className="mt-0.5 inline-flex size-7 items-center justify-center rounded-full bg-ink font-mono text-[13px] text-bg tabular-nums">{i + 1}</span>
            <div className="min-w-0">
              <p className="text-[15.5px] font-semibold leading-snug tracking-[-0.01em] text-ink">{s.title}</p>
              <p className="mt-1 text-pretty text-[13.5px] leading-relaxed text-ink-2">{s.body}</p>
              {s.command && <Command cmd={s.command} label={s.title} className="mt-2.5" />}
              <PackAnchor
                avoid
                link={s.cta}
                className="mt-2.5 inline-flex h-8 items-center gap-1.5 rounded-full border border-line-strong px-3 text-[13px] font-medium text-ink transition-colors hover:bg-surface-2"
              >
                {s.cta.label}
                {hrefKind(s.cta.href) === "external" ? <ArrowUpRight className="size-3.5" aria-hidden /> : <ChevronRight className="size-3.5" aria-hidden />}
              </PackAnchor>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

// ---------------------------------------------------------------- proof

function ProofCardView({ c, wide }: { c: ProofCard; wide?: boolean }) {
  const long = c.number.length > 14;
  return (
    <article className={cn("flex min-w-0 flex-col rounded-2xl border border-line bg-surface p-5 shadow-card", wide && "sm:col-span-2")}>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
        <RealBadge status={c.status} />
        <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-bg-sunken px-2.5 py-0.5 font-mono text-[11.5px] text-muted">
          <CalendarDays className="size-3" aria-hidden />
          {c.date}
        </span>
      </div>
      <h3 className="mt-3 text-[14px] font-medium text-ink-2">{c.title}</h3>
      <p className={cn("mt-1 font-semibold tracking-[-0.03em] text-ink tabular-nums [overflow-wrap:anywhere]", long ? "font-mono text-[17px] leading-8" : "text-[28px] leading-9")}>{c.number}</p>
      <p className="mt-2 flex-1 text-pretty text-[13.5px] leading-relaxed text-ink-2">{c.note}</p>
      <ul className="mt-3 flex flex-wrap gap-x-3 gap-y-1" aria-label={`Evidence for ${c.title}`}>
        {c.evidence.map((e) => (
          <li key={e.href}>
            <PackAnchor
              link={e}
              className="inline-flex items-center gap-0.5 font-mono text-[12px] text-owned-ink underline decoration-owned/30 underline-offset-2 hover:decoration-owned [overflow-wrap:anywhere]"
            >
              {e.label}
              <ArrowUpRight className="size-3 shrink-0" aria-hidden />
            </PackAnchor>
          </li>
        ))}
      </ul>
      {c.command && <Command cmd={c.command} label={c.title} wrap className="mt-3" />}
    </article>
  );
}

export function ProofSection() {
  if (!PACK) return null;
  return (
    <section id="proof" aria-labelledby="proof-title" className="scroll-mt-28 pt-12 sm:pt-16">
      <SectionHead
        id="proof"
        title="The proof"
        lead="What was run, with its date, the evidence folder it is read from and, where there is one, the command that checks it on a clone. No GPU and no key are needed."
      />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {PACK.proof.map((c) => (
          <ProofCardView key={c.id} c={c} wide={c.id === "bom"} />
        ))}
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- links

function GroupCard({ g, className, cols }: { g: LinkGroup; className?: string; cols?: string }) {
  return (
    <section aria-label={g.title} className={cn("min-w-0 rounded-2xl border border-line bg-surface p-3 shadow-card sm:p-4", className)}>
      <h3 className="mb-1.5 flex items-center gap-2 px-2.5 pt-1 text-[15px] font-semibold tracking-[-0.01em] text-ink">
        <span className="inline-flex size-7 items-center justify-center rounded-lg bg-surface-2 text-ink-2">
          <GroupIcon icon={g.icon} />
        </span>
        {g.title}
      </h3>
      <ul className={cn("grid gap-x-3", cols)}>
        {g.items.map((it) => (
          <li key={it.href} className="min-w-0">
            <LinkRow link={it} />
          </li>
        ))}
      </ul>
    </section>
  );
}

export function LinksSection() {
  if (!PACK) return null;
  const g = (id: string) => PACK.groups.find((x) => x.id === id);
  const live = g("live");
  const docs = g("docs");
  const code = g("code");
  const evidence = g("evidence");
  const video = g("video");
  const hf = g("hf");
  return (
    <section id="links" aria-labelledby="links-title" className="scroll-mt-28 pt-12 sm:pt-16">
      <SectionHead id="links" title="Every link, in one place" lead="The live site, the docs, the code, every evidence folder, Hugging Face and the video. External links open in a new tab." />
      <div className="grid gap-4 lg:grid-cols-3">
        {live && <GroupCard g={live} />}
        {docs && <GroupCard g={docs} />}
        {code && <GroupCard g={code} />}
        {evidence && <GroupCard g={evidence} className="lg:col-span-2" cols="sm:grid-cols-2" />}
        {video && <GroupCard g={video} />}
        {hf && <GroupCard g={hf} className="lg:col-span-3" cols="md:grid-cols-2 xl:grid-cols-3" />}
      </div>
      <div className="mt-4 overflow-hidden rounded-2xl border border-line bg-surface shadow-card">
        <h3 className="border-b border-line px-5 py-3 text-[15px] font-semibold tracking-[-0.01em] text-ink">Nebius and NVIDIA: what runs where</h3>
        <dl className="divide-y divide-line">
          {PACK.where.map((w) => (
            <div key={w.where} className="grid gap-x-6 gap-y-1 px-5 py-3 sm:grid-cols-[190px_minmax(0,1fr)]">
              <dt className="text-[14px] font-semibold text-ink">{w.where}</dt>
              <dd className="text-pretty text-[14px] leading-relaxed text-ink-2">{w.what}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- checklist, what is real, costs

export function ChecklistSection() {
  if (!PACK) return null;
  return (
    <section id="checklist" aria-labelledby="checklist-title" className="scroll-mt-28 pt-12 sm:pt-16">
      <SectionHead id="checklist" title="Hackathon checklist" lead="Each requirement, whether it is met, and where to see it." />
      <ul className="divide-y divide-line overflow-hidden rounded-2xl border border-line bg-surface shadow-card">
        {PACK.checklist.map((c) => (
          <li key={c.id} className="grid min-w-0 gap-x-4 gap-y-1.5 px-5 py-3.5 sm:grid-cols-[28px_minmax(0,1fr)_auto] sm:items-center">
            <CircleCheck className="size-5 text-good" aria-label="Met" />
            <div className="min-w-0">
              <p className="text-[15px] font-semibold leading-snug tracking-[-0.01em] text-ink">{c.item}</p>
              <p className="mt-0.5 text-[13.5px] leading-relaxed text-ink-2">{c.detail}</p>
            </div>
            <ul className="flex flex-wrap gap-x-4 gap-y-1 sm:justify-end">
              {c.links.map((l) => (
                <li key={l.href}>
                  <PackAnchor
                    link={l}
                    className="inline-flex items-center gap-0.5 text-[13.5px] font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink"
                  >
                    {l.label}
                    {hrefKind(l.href) === "external" && <ArrowUpRight className="size-3 self-center text-muted" aria-hidden />}
                  </PackAnchor>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      <p className="mt-3 text-[13.5px] text-muted">{PACK.trackNote}</p>

      <div className="mt-8 grid gap-4 lg:grid-cols-2">
        <section aria-labelledby="honest-title" className="min-w-0 rounded-2xl border border-line bg-bg-sunken/60 p-5">
          <h3 id="honest-title" className="text-[17px] font-semibold tracking-[-0.02em] text-ink">
            What is real, and what is not yet
          </h3>
          <p className="mt-3 flex items-center gap-2 text-[13px] font-medium text-ink">
            <CircleCheck className="size-4 text-good" aria-hidden /> Ran, with the evidence above
          </p>
          <ul className="mt-1.5 space-y-1 pl-6 text-[14px] leading-relaxed text-ink-2 [list-style:disc]">
            {PACK.ran.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
          <p className="mt-4 flex items-center gap-2 text-[13px] font-medium text-ink">
            <Hourglass className="size-4 text-muted" aria-hidden /> Not yet
          </p>
          <ul className="mt-1.5 space-y-1 pl-6 text-[14px] leading-relaxed text-ink-2 [list-style:disc]">
            {PACK.notYet.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        </section>
        <section aria-labelledby="cost-title" className="min-w-0 rounded-2xl border border-line bg-surface p-5 shadow-card">
          <h3 id="cost-title" className="text-[17px] font-semibold tracking-[-0.02em] text-ink">
            What it cost
          </h3>
          <p className="mt-1 text-[13.5px] text-ink-2">Estimates recorded by the scripts, not invoices.</p>
          <dl className="mt-3 divide-y divide-line">
            {PACK.costs.map((c) => (
              <div key={c.item} className="grid gap-x-4 gap-y-0.5 py-2.5 sm:grid-cols-[minmax(0,1fr)_auto]">
                <dt className="min-w-0 text-[14px] text-ink">
                  {c.item}
                  <span className="block text-[12.5px] text-muted">{c.note}</span>
                </dt>
                <dd className="font-mono text-[13.5px] text-ink tabular-nums sm:text-right">{c.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      </div>
    </section>
  );
}
