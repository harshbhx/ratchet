import { useRef, type PointerEvent } from "react";
import { Link } from "react-router";
import { ArrowUpRight, BookOpen } from "lucide-react";
import { signal, useCompanionsHidden } from "@/components/companions/companion-bus";
import { GithubIcon, HuggingFaceIcon, YoutubeIcon } from "@/components/ui/brand-icons";
import { Logo } from "@/components/ui/logo";
import type { Stats } from "@/lib/api";
import { videoHref } from "@/lib/real-model";
import { cn, DOCS_URL, HF_COLLECTION, REPO_URL } from "@/lib/utils";
import { REAL } from "@/pages/judges/real";
import { FOOTER_COLUMNS, HF_MODEL, REACT_BITS_PERMISSION, type NavItem } from "./nav-model";
import { ThemeToggle } from "./site-header";
import { useWaking } from "@/lib/waking-store";
import { FOOTER_WAKING, isWaking } from "@/lib/waking-model";

/** The site's own tagline (the story's title): the footer reuses it, it does not coin a new one. */
const TAGLINE = "Own the adapter your traffic trains";
/** The approved headline wording, as the story's first screen prints it. */
const BUILT_ON = "Built on NVIDIA Nemotron and Nebius";

const LINK =
  "group/link -mx-2 inline-flex max-w-full items-center gap-1 rounded-md px-2 py-1 text-[14px] text-muted transition-[color,background-color] duration-150 hover:bg-ink/[0.05] hover:text-ink";

function FooterLink({ item }: { item: NavItem }) {
  if (item.kind === "route") {
    return (
      <Link to={item.href} className={LINK}>
        {item.label}
      </Link>
    );
  }
  if (item.kind === "section") {
    return (
      <a href={item.href} className={LINK}>
        {item.label}
      </a>
    );
  }
  return (
    <a href={item.href} target="_blank" rel="noopener noreferrer" className={LINK}>
      {item.label}
      <ArrowUpRight aria-hidden className="size-4 shrink-0 opacity-0 transition-opacity group-hover/link:opacity-70 group-focus-visible/link:opacity-70" />
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

/** What this gateway said about itself on the one stats call the layout makes: a word and a dot, never colour alone. */
function GatewayStatus({ stats, error }: { stats: Stats | null; error: string | null }) {
  const waking = isWaking(useWaking());
  if (!stats && !error && !waking) return null;
  const up = !!stats && !waking;
  const text = waking
    ? FOOTER_WAKING
    : !stats
    ? "Gateway not reachable from this page"
    : stats.mode === "live"
      ? stats.upstream?.token_factory
        ? "Gateway up · live on Nebius Token Factory"
        : "Gateway up · live"
      : "Gateway up · mock models";
  return (
    <p className="inline-flex items-center gap-2 text-[13px] text-muted">
      <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", up ? "bg-good" : waking ? "bg-warn" : "bg-muted")} />
      {text}
    </p>
  );
}

const ICON =
  "inline-flex size-10 items-center justify-center rounded-full border border-line bg-surface/70 text-ink-2 transition-[color,background-color,border-color] hover:border-line-strong hover:bg-surface-2 hover:text-ink";
const CTA = "inline-flex h-10 items-center gap-1.5 rounded-full px-4 text-[14px] font-medium transition-colors";
const UL = "underline decoration-line-strong underline-offset-2 hover:text-ink";

/** Brings Nano and Ultra back, or hides them; remembered on this device. */
function HideCompanions() {
  const hidden = useCompanionsHidden();
  return (
    <button
      type="button"
      onClick={() => signal({ type: hidden ? "show" : "hide" })}
      className="inline-flex h-9 items-center rounded-full border border-line px-3.5 text-[13px] text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
    >
      {hidden ? "Show Nano & Ultra" : "Hide Nano & Ultra"}
    </button>
  );
}

export function SiteFooter({ stats, error }: { stats: Stats | null; error: string | null }) {
  const root = useRef<HTMLElement | null>(null);
  const video = videoHref(REAL.links, "site");
  // The spotlight follows the pointer through two CSS variables; nothing re-renders, and touch and keyboard users see it at rest.
  const move = (e: PointerEvent<HTMLElement>) => {
    const el = root.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${e.clientX - r.left}px`);
    el.style.setProperty("--my", `${e.clientY - r.top}px`);
  };

  return (
    <footer ref={root} onPointerMove={move} className="group/footer relative isolate mt-12 overflow-hidden border-t border-line bg-bg-sunken/60">
      {/* Background: the engineering grid fading out downward, a resting glow, and a spotlight that follows the pointer. */}
      <div aria-hidden className="bg-grid pointer-events-none absolute inset-0 -z-10 opacity-60 [mask-image:linear-gradient(to_bottom,black,transparent_85%)]" />
      <div
        aria-hidden
        className="pointer-events-none absolute -top-40 left-1/2 -z-10 h-80 w-[60rem] max-w-[140vw] -translate-x-1/2 rounded-full opacity-70 blur-3xl [background:radial-gradient(closest-side,color-mix(in_srgb,var(--owned)_16%,transparent),transparent)]"
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 -z-10 opacity-0 transition-opacity duration-500 group-hover/footer:opacity-100 motion-reduce:hidden [background:radial-gradient(420px_circle_at_var(--mx,50%)_var(--my,0%),color-mix(in_srgb,var(--rented)_11%,transparent),transparent_70%)]"
      />

      <div className="mx-auto w-full max-w-[1960px] px-4 sm:px-6 lg:px-10">
        <div data-tour="check-numbers" className="relative mt-12 overflow-hidden rounded-3xl border border-line bg-surface/90 p-6 shadow-card sm:p-8">
          <div aria-hidden className="bg-grid pointer-events-none absolute inset-0 opacity-50 [mask-image:linear-gradient(to_left,black,transparent_65%)]" />
          <div className="relative flex flex-col gap-5 md:flex-row md:items-center md:justify-between">
            <div className="max-w-xl">
              <h2 className="text-balance text-[20px] font-semibold tracking-[-0.02em] text-ink sm:text-[22px]">Don't take our word for it. Check the numbers.</h2>
              <p className="mt-1.5 text-pretty text-[14px] leading-relaxed text-ink-2">
                The adapter, the training data and both exams, with every answer, are public. The scores recompute from the saved answers.
              </p>
            </div>
            <div className="flex flex-wrap gap-2.5">
              <Link to="/judges" className={cn(CTA, "bg-ink text-bg hover:bg-ink-2")}>
                See the results
              </Link>
              <a href={HF_MODEL} target="_blank" rel="noopener noreferrer" className={cn(CTA, "border border-line-strong bg-surface text-ink hover:bg-surface-2")}>
                Adapter on Hugging Face <ArrowUpRight aria-hidden className="size-4" />
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            </div>
          </div>
        </div>

        <div className="grid gap-12 py-12 sm:py-16 lg:grid-cols-12 lg:gap-x-12">
          <div className="flex flex-col gap-8 lg:col-span-4 xl:col-span-3">
            <div className="max-w-xl">
            <Link to="/" aria-label="Ratchet home" className="inline-flex items-center rounded-lg">
              <Logo />
            </Link>
            <p className="mt-5 text-balance text-[22px] font-semibold leading-snug tracking-[-0.025em] text-ink">{TAGLINE}</p>
            <p data-tour="built-on" className="mt-3 inline-flex max-w-full items-center rounded-full border border-line bg-surface/80 px-3 py-1 text-[12.5px] font-medium text-ink-2">{BUILT_ON}</p>
            </div>
            <div className="max-w-md">
            <p className="text-pretty text-[13px] leading-relaxed text-muted">
              {stats?.mode === "mock"
                ? "This gateway runs mock models, so its numbers are not measured. Exam results are labelled Measured. "
                : "Numbers from the gateway are its own traffic and exam results are labelled Measured. "}
              The published adapters were trained on a borrowed GPU.
            </p>
            <div className="mt-4">
              <GatewayStatus stats={stats} error={error} />
            </div>
            <div className="mt-6 flex items-center gap-2">
              <a href={REPO_URL} target="_blank" rel="noopener noreferrer" className={ICON} aria-label="GitHub (opens in a new tab)">
                <GithubIcon />
              </a>
              <a href={HF_COLLECTION} target="_blank" rel="noopener noreferrer" className={ICON} aria-label="Hugging Face collection (opens in a new tab)">
                <HuggingFaceIcon />
              </a>
              <a href={DOCS_URL} target="_blank" rel="noopener noreferrer" className={ICON} aria-label="Documentation (opens in a new tab)">
                <BookOpen className="size-5" aria-hidden />
              </a>
              {video && (
                <a href={video} target="_blank" rel="noopener noreferrer" className={ICON} aria-label="Watch the video (opens in a new tab)">
                  <YoutubeIcon />
                </a>
              )}
            </div>
            </div>
          </div>
          <nav aria-label="Footer" className="grid grid-cols-1 gap-x-8 gap-y-10 border-t border-line pt-10 sm:grid-cols-2 lg:col-span-8 lg:grid-cols-4 xl:col-span-9 lg:border-t-0 lg:pt-1">
            {FOOTER_COLUMNS.map((col) => (
              <div key={col.title}>
                <h2 className="font-mono text-[12px] uppercase tracking-[0.14em] text-muted">{col.title}</h2>
                <ul className="mt-4 space-y-1.5">
                  {col.items.map((item) => (
                    <li key={item.href + item.label}>
                      <FooterLink item={item} />
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
        </div>

        <div className="flex flex-col gap-4 border-t border-line py-6 lg:flex-row lg:items-start lg:justify-between">
          <div className="min-w-0 flex-1 space-y-1.5 text-[12.5px] leading-relaxed text-muted">
            <p>© {new Date().getFullYear()} Harsh Bhanushali · DualMind Labs. Code under Apache-2.0; model weights and data keep their own licenses.</p>
            <p>
              Built for the Nebius x NVIDIA Global AI Hackathon; not an official product of either company, and not affiliated with or endorsed by NVIDIA or Nebius. The
              documentation site uses adapted{" "}
              <a href="https://reactbits.dev" target="_blank" rel="noopener noreferrer" className={UL}>
                React Bits
              </a>{" "}
              components, with the maintainer's{" "}
              <a href={REACT_BITS_PERMISSION} target="_blank" rel="noopener noreferrer" className={UL}>
                confirmation
              </a>
              . Icons: Lucide (ISC) and Simple Icons (CC0); the GitHub, Hugging Face and YouTube marks belong to their owners and only link to them.
            </p>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2 self-start">
            <HideCompanions />
            <ThemeToggle className="-my-1 border border-line" />
          </div>
        </div>
      </div>

      {/* A very large wordmark, cropped by the footer's edge and faded out: decoration only. */}
      <div aria-hidden className="pointer-events-none relative select-none overflow-hidden">
        <p className="mx-auto -mb-[0.2em] w-full max-w-[1960px] px-4 text-center text-[24vw] font-semibold leading-[0.85] tracking-[-0.06em] text-transparent sm:px-6 lg:px-10 lg:text-[16vw]"
          style={{
            backgroundImage: "linear-gradient(to bottom, color-mix(in srgb, var(--ink) 13%, transparent), transparent 80%)",
            WebkitBackgroundClip: "text",
            backgroundClip: "text",
          }}
        >
          Ratchet
        </p>
      </div>
    </footer>
  );
}
