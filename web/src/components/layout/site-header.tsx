import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router";
import { Menu, Moon, Sun, X, ArrowUpRight } from "lucide-react";
import { Logo } from "@/components/ui/logo";
import { ButtonLink } from "@/components/ui/button";
import { useStats, useTheme } from "@/lib/hooks";
import { surfaceSwitch, type SurfaceSwitch as Switch } from "@/lib/surface-model";
import { cn, DOCS_URL, REPO_URL } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Story", end: true },
  { to: "/scout", label: "Scout demo" },
  { to: "/dashboard", label: "Dashboard" },
];

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  return (
    <button
      type="button"
      onClick={toggle}
      className="inline-flex size-9 items-center justify-center rounded-full text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
      aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
    >
      {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </button>
  );
}

/** "Live on Nebius Token Factory | Simulation": the surface this page is on is a label, the other one a link when its URL is known. */
export function SurfaceSwitch({ sw }: { sw: Switch }) {
  const seg = "inline-flex min-w-0 items-center rounded-full px-3 py-1 text-[12.5px] [overflow-wrap:anywhere]";
  const here = cn(seg, "bg-surface-2 font-medium text-ink");
  const there = cn(seg, "text-ink-2 transition-colors hover:text-ink");
  const live = sw.current === "live";
  const other = live ? sw.simHref : sw.liveHref;
  return (
    <div className="flex flex-wrap items-center justify-center gap-1" role="group" aria-label="Which gateway this page shows">
      {live ? (
        <span className={here} aria-current="true">
          {sw.liveLabel}
        </span>
      ) : other ? (
        <a href={other} className={there}>
          {sw.liveLabel} <ArrowUpRight className="ml-1 size-3" aria-hidden />
        </a>
      ) : null}
      {live ? (
        other ? (
          <a href={other} className={there}>
            Simulation <ArrowUpRight className="ml-1 size-3" aria-hidden />
          </a>
        ) : null
      ) : (
        <span className={here} aria-current="true">
          Simulation
        </span>
      )}
    </div>
  );
}

export function SiteHeader() {
  const { stats } = useStats(0); // one fetch per page load: the switch needs the links, not live numbers
  const sw = surfaceSwitch(stats);
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const loc = useLocation();
  useEffect(() => setOpen(false), [loc.pathname]);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 8);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  const link = ({ isActive }: { isActive: boolean }) =>
    cn("rounded-full px-3 py-1.5 text-[14px] transition-colors", isActive ? "bg-surface-2 text-ink" : "text-ink-2 hover:text-ink");
  return (
    <header
      className={cn(
        "sticky top-0 z-40 border-b transition-[background,border-color,backdrop-filter] duration-300",
        scrolled ? "border-line bg-bg/80 backdrop-blur-xl" : "border-transparent bg-transparent",
      )}
    >
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
        <NavLink to="/" aria-label="Ratchet home" className="rounded-lg">
          {/* The header stays mounted across pages, so the mark clicks forward once per page load. */}
          <Logo animate />
        </NavLink>
        <nav className="hidden items-center gap-1 md:flex" aria-label="Main">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={link}>
              {n.label}
            </NavLink>
          ))}
          <a href={DOCS_URL} target="_blank" rel="noopener noreferrer" className="rounded-full px-3 py-1.5 text-[14px] text-ink-2 hover:text-ink">
            Docs
          </a>
        </nav>
        <div className="flex items-center gap-1">
          <ThemeToggle />
          <a
            href={REPO_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="hidden items-center gap-1 rounded-full px-3 py-1.5 text-[14px] text-ink-2 hover:text-ink sm:inline-flex"
          >
            GitHub <ArrowUpRight className="size-3.5" />
          </a>
          {/* The call to action is the other demo: Scout from the story and the dashboard, the dashboard from Scout. */}
          {loc.pathname.startsWith("/scout") ? (
            <ButtonLink to="/dashboard" size="sm" className="ml-1 hidden sm:inline-flex">
              Open dashboard
            </ButtonLink>
          ) : (
            <ButtonLink to="/scout" size="sm" className="ml-1 hidden sm:inline-flex">
              Try Scout
            </ButtonLink>
          )}
          <button
            type="button"
            className="inline-flex size-9 items-center justify-center rounded-full text-ink-2 hover:bg-surface-2 md:hidden"
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
            onClick={() => setOpen((o) => !o)}
          >
            {open ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
        </div>
      </div>
      {sw && (
        <div className="border-t border-line/60 px-4 py-1.5 sm:px-6 lg:px-8">
          <SurfaceSwitch sw={sw} />
        </div>
      )}
      {open && (
        <nav className="border-t border-line bg-bg px-4 pb-4 pt-2 md:hidden" aria-label="Mobile">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => cn("block rounded-lg px-3 py-2.5 text-[15px]", isActive ? "bg-surface-2 text-ink" : "text-ink-2")}>
              {n.label}
            </NavLink>
          ))}
          <a href={DOCS_URL} target="_blank" rel="noopener noreferrer" className="block rounded-lg px-3 py-2.5 text-[15px] text-ink-2">
            Docs
          </a>
          <a href={REPO_URL} target="_blank" rel="noopener noreferrer" className="block rounded-lg px-3 py-2.5 text-[15px] text-ink-2">
            GitHub
          </a>
        </nav>
      )}
    </header>
  );
}
