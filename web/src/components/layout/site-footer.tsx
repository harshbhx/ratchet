import { Link } from "react-router";
import { ArrowUpRight } from "lucide-react";
import { Logo } from "@/components/ui/logo";
import type { Stats } from "@/lib/api";
import { cn } from "@/lib/utils";
import { FOOTER_COLUMNS, HF_MODEL, REACT_BITS_PERMISSION, type NavItem } from "./nav-model";
import { REPO_URL } from "@/lib/utils";
import { ThemeToggle } from "./site-header";
import { useWaking } from "@/lib/waking-store";
import { FOOTER_WAKING, isWaking } from "@/lib/waking-model";

function FooterLink({ item }: { item: NavItem }) {
  const cls = "group inline-flex items-center gap-1 whitespace-nowrap rounded-md text-[14px] text-muted transition-colors hover:text-ink";
  if (item.kind === "route") {
    return (
      <Link to={item.href} className={cls}>
        {item.label}
      </Link>
    );
  }
  if (item.kind === "section") {
    return (
      <a href={item.href} className={cls}>
        {item.label}
      </a>
    );
  }
  return (
    <a href={item.href} target="_blank" rel="noopener noreferrer" className={cls}>
      {item.label}
      <ArrowUpRight aria-hidden className="size-3 opacity-60 transition-opacity group-hover:opacity-100" />
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
      : "Gateway up · simulation (mock models)";
  return (
    <p className="inline-flex items-center gap-2 text-[13px] text-muted">
      <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", up ? "bg-good" : waking ? "bg-warn" : "bg-muted")} />
      {text}
    </p>
  );
}

const CTA = "inline-flex h-10 items-center gap-1.5 rounded-lg px-4 text-[14px] font-medium transition-colors";
const UL = "underline decoration-line-strong underline-offset-2 hover:text-ink";

export function SiteFooter({ stats, error }: { stats: Stats | null; error: string | null }) {
  return (
    <footer className="border-t border-line bg-bg-sunken/60">
      <div className="mx-auto max-w-6xl px-4 sm:px-6 lg:px-8">
        <div className="relative mt-12 overflow-hidden rounded-2xl border border-line bg-surface p-6 shadow-card sm:p-8">
          <div aria-hidden className="bg-grid pointer-events-none absolute inset-0 opacity-50 [mask-image:linear-gradient(to_left,black,transparent_65%)]" />
          <div className="relative flex flex-col gap-5 md:flex-row md:items-center md:justify-between">
            <div className="max-w-xl">
              <h2 className="text-balance text-[20px] font-semibold tracking-[-0.02em] text-ink sm:text-[22px]">Don't take our word for it. Check the numbers.</h2>
              <p className="mt-1.5 text-pretty text-[14px] leading-relaxed text-ink-2">
                The adapter, the training data and both exams, with every answer, are public. The scores recompute from the saved answers.
              </p>
            </div>
            <div className="flex flex-wrap gap-2.5">
              <Link to="/judges" className={cn(CTA, "bg-ink text-bg hover:opacity-90")}>
                See the results
              </Link>
              <a href={HF_MODEL} target="_blank" rel="noopener noreferrer" className={cn(CTA, "border border-line-strong bg-surface text-ink hover:bg-surface-2")}>
                Adapter on Hugging Face <ArrowUpRight aria-hidden className="size-3.5" />
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
              <a href={REPO_URL} target="_blank" rel="noopener noreferrer" className={cn(CTA, "border border-line-strong bg-surface text-ink hover:bg-surface-2")}>
                Source on GitHub <ArrowUpRight aria-hidden className="size-3.5" />
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            </div>
          </div>
        </div>

        <div className="grid gap-12 py-12 sm:py-14 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,3fr)] xl:gap-14">
          <div className="max-w-sm">
            <Link to="/" aria-label="Ratchet home" className="inline-flex items-center rounded-lg">
              <Logo />
            </Link>
            <p className="mt-4 text-pretty text-[14.5px] leading-relaxed text-ink-2">
              An open-source gateway that moves work from a large rented model to a small one with our LoRA adapter, and checks each release before it serves.
            </p>
            <p className="mt-3 text-[13px] leading-relaxed text-muted">
              {stats?.mode === "mock"
                ? "Numbers on this page are simulation unless labelled Measured. "
                : "Numbers from the gateway are its own traffic; reference runs are labelled Simulation and exam results Measured. "}
              The adapter was trained on rented T4 GPUs, not on Nebius GPUs.
            </p>
            <div className="mt-4">
              <GatewayStatus stats={stats} error={error} />
            </div>
          </div>
          <nav aria-label="Footer" className="grid grid-cols-2 gap-x-6 gap-y-10 sm:grid-cols-3 lg:grid-cols-5">
            {FOOTER_COLUMNS.map((col) => (
              <div key={col.title}>
                <h2 className="text-[13px] font-semibold text-ink">{col.title}</h2>
                <ul className="mt-4 space-y-3">
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
          <div className="max-w-2xl space-y-1.5 text-[12.5px] leading-relaxed text-muted">
            <p>© {new Date().getFullYear()} Harsh Bhanushali · DualMind Labs. Code under Apache-2.0; model weights and data keep their own licenses.</p>
            <p>
              Built for the Nebius x NVIDIA Global AI Hackathon; not an official product of either company. The documentation site uses adapted{" "}
              <a href="https://reactbits.dev" target="_blank" rel="noopener noreferrer" className={UL}>
                React Bits
              </a>{" "}
              components, with the maintainer's{" "}
              <a href={REACT_BITS_PERMISSION} target="_blank" rel="noopener noreferrer" className={UL}>
                confirmation
              </a>
              .
            </p>
          </div>
          <ThemeToggle className="-my-1 shrink-0 self-start border border-line" />
        </div>
      </div>
    </footer>
  );
}
