import { Link } from "react-router";
import { ArrowUpRight } from "lucide-react";
import { Logo } from "@/components/ui/logo";
import type { Stats } from "@/lib/api";
import { cn } from "@/lib/utils";
import { FOOTER_COLUMNS, type NavItem } from "./nav-model";
import { ThemeToggle } from "./site-header";

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
  if (!stats && !error) return null;
  const up = !!stats;
  const text = !stats
    ? "Gateway not reachable from this page"
    : stats.mode === "live"
      ? stats.upstream?.token_factory
        ? "Gateway up · live on Nebius Token Factory"
        : "Gateway up · live"
      : "Gateway up · simulation (mock models)";
  return (
    <p className="inline-flex items-center gap-2 text-[13px] text-muted">
      <span aria-hidden className={cn("size-1.5 shrink-0 rounded-full", up ? "bg-good" : "bg-muted")} />
      {text}
    </p>
  );
}

export function SiteFooter({ stats, error }: { stats: Stats | null; error: string | null }) {
  return (
    <footer className="border-t border-line bg-bg-sunken/60">
      <div className="mx-auto max-w-6xl px-4 sm:px-6 lg:px-8">
        <div className="grid gap-12 py-14 sm:py-16 xl:grid-cols-[minmax(0,1fr)_minmax(0,2.4fr)] xl:gap-16">
          <div className="max-w-sm">
            <Link to="/" aria-label="Ratchet home" className="inline-flex items-center rounded-lg">
              <Logo />
            </Link>
            <p className="mt-4 text-pretty text-[14.5px] leading-relaxed text-ink-2">
              An open-source gateway built on NVIDIA Nemotron and Nebius. Numbers on the demo are simulation unless labelled.
            </p>
            <p className="mt-3 text-[13px] leading-relaxed text-muted">Code under Apache-2.0. Model weights keep their own licenses.</p>
          </div>
          <nav aria-label="Footer" className="grid grid-cols-2 gap-x-6 gap-y-10 md:grid-cols-4">
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
        <div className="flex flex-col gap-4 border-t border-line py-6 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-[13px] text-muted">
            © {new Date().getFullYear()} Harsh Bhanushali · DualMind Labs
          </p>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <GatewayStatus stats={stats} error={error} />
            <ThemeToggle className="-my-1 border border-line" />
          </div>
        </div>
      </div>
    </footer>
  );
}
