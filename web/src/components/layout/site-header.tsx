import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router";
import { motion, useScroll } from "motion/react";
import { ArrowUpRight, FlaskConical, Menu, Moon, Sun } from "lucide-react";
import { Logo } from "@/components/ui/logo";
import { ButtonA, ButtonLink } from "@/components/ui/button";
import type { Stats } from "@/lib/api";
import { useTheme } from "@/lib/hooks";
import { surfaceSwitch, type SurfaceSwitch as Switch } from "@/lib/surface-model";
import { cn, DEMO_URL } from "@/lib/utils";
import { MobileMenu } from "./mobile-menu";
import { isCurrentRoute, pillItems, sectionIds, type NavItem } from "./nav-model";

export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggle } = useTheme();
  return (
    <button
      type="button"
      onClick={toggle}
      className={cn(
        "inline-flex size-9 items-center justify-center rounded-full text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink focus-visible:rounded-full",
        className,
      )}
      aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
    >
      {theme === "dark" ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
    </button>
  );
}

/** "Live on Nebius Token Factory | Simulation": the surface this page is on is a label, the other one a link when its URL is known. */
export function SurfaceSwitch({ sw }: { sw: Switch }) {
  const seg = "inline-flex min-w-0 items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[12px] [overflow-wrap:anywhere]";
  const here = cn(seg, "bg-surface-2 font-medium text-ink ring-1 ring-inset ring-line");
  const there = cn(seg, "text-ink-2 transition-colors hover:text-ink");
  const live = sw.current === "live";
  const other = live ? sw.simHref : sw.liveHref;
  const liveDot = <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-good" />;
  const simIcon = <FlaskConical aria-hidden className="size-3 shrink-0" />;
  return (
    <div className="flex flex-wrap items-center justify-center gap-1" role="group" aria-label="Which gateway this page shows">
      {live ? (
        <span className={here} aria-current="true">
          {liveDot}
          {sw.liveLabel}
        </span>
      ) : other ? (
        <a href={other} className={there}>
          {liveDot}
          {sw.liveLabel} <ArrowUpRight className="size-3" aria-hidden />
        </a>
      ) : null}
      {live ? (
        other ? (
          <a href={other} className={there}>
            {simIcon}
            Simulation <ArrowUpRight className="size-3" aria-hidden />
          </a>
        ) : null
      ) : (
        <span className={here} aria-current="true">
          {simIcon}
          Simulation
        </span>
      )}
    </div>
  );
}

/**
 * Which of `ids` the reader is in: the section that crosses a thin band just above the middle of the screen, or null
 * between them (the hero, the interludes, the chapters the pill does not list). The story page loads after the header,
 * so sections are picked up as they appear.
 */
function useScrollSpy(ids: string[]): string | null {
  const [active, setActive] = useState<string | null>(null);
  const key = ids.join(" ");
  useEffect(() => {
    const list = key ? key.split(" ") : [];
    setActive(null);
    if (!list.length || typeof IntersectionObserver === "undefined") return;
    const hits = new Map<string, boolean>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) hits.set(e.target.id, e.isIntersecting);
        setActive(list.find((id) => hits.get(id)) ?? null);
      },
      { rootMargin: "-38% 0px -60% 0px" },
    );
    const seen = new Set<string>();
    const mo = new MutationObserver(() => attach());
    function attach() {
      for (const id of list) {
        if (seen.has(id)) continue;
        const el = document.getElementById(id);
        if (!el) continue;
        seen.add(id);
        io.observe(el);
      }
      if (seen.size === list.length) mo.disconnect();
    }
    mo.observe(document.body, { childList: true, subtree: true });
    attach();
    return () => {
      io.disconnect();
      mo.disconnect();
    };
  }, [key]);
  return active;
}

/** True once the page has scrolled past the first few pixels. */
function useScrolled(threshold = 8): boolean {
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > threshold);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, [threshold]);
  return scrolled;
}

type Mark = { x: number; w: number; on: boolean; glide: boolean };

/**
 * The centred pill. On the story page it lists chapters and a highlight slides to the one being read; elsewhere it lists
 * the pages with the current one marked. The highlight is one element moved with a transform, so it glides between
 * items, fades out where no item is current, and comes back in place (no slide from the last spot). Reduced motion:
 * it jumps.
 */
function NavPill({ items, pathname, activeSection }: { items: readonly NavItem[]; pathname: string; activeSection: string | null }) {
  const list = useRef<HTMLUListElement | null>(null);
  const refs = useRef<(HTMLElement | null)[]>([]);
  const [mark, setMark] = useState<Mark>({ x: 0, w: 0, on: false, glide: false });
  const story = items.some((i) => i.kind === "section");
  const { scrollYProgress } = useScroll();

  const current = items.findIndex((i) => (i.kind === "section" ? i.href.slice(1) === activeSection : isCurrentRoute(i, pathname)));
  const currentRef = useRef(current);
  currentRef.current = current;

  // Move the highlight when the current item changes.
  useLayoutEffect(() => {
    const el = current >= 0 ? refs.current[current] : null;
    setMark((prev) =>
      el ? { x: el.offsetLeft, w: el.offsetWidth, on: true, glide: prev.on } : prev.on ? { ...prev, on: false, glide: false } : prev,
    );
  }, [current, items]);

  // Keep it on its item when widths change (fonts loading, a resize): in place, without a glide.
  useEffect(() => {
    if (!list.current || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      const i = currentRef.current;
      const el = i >= 0 ? refs.current[i] : null;
      if (!el) return;
      setMark((prev) => (prev.x === el.offsetLeft && prev.w === el.offsetWidth ? prev : { ...prev, x: el.offsetLeft, w: el.offsetWidth, glide: false }));
    });
    ro.observe(list.current);
    return () => ro.disconnect();
  }, [items]);

  const itemClass = (on: boolean) =>
    cn(
      "relative z-10 inline-flex h-8 items-center gap-1 whitespace-nowrap rounded-full px-2.5 text-[13px] font-medium transition-colors duration-150 focus-visible:rounded-full lg:px-3.5 lg:text-[13.5px]",
      on ? "text-ink" : "text-muted hover:text-ink",
    );

  return (
    <nav aria-label="Main" className="col-start-2 hidden justify-self-center md:block">
      <ul
        ref={list}
        className={cn(
          "relative flex items-center rounded-full border border-line p-1",
          "bg-surface/90 supports-[backdrop-filter]:bg-surface/60 supports-[backdrop-filter]:backdrop-blur-xl supports-[backdrop-filter]:backdrop-saturate-150",
          "[@media(prefers-reduced-transparency:reduce)]:bg-surface [@media(prefers-reduced-transparency:reduce)]:backdrop-blur-none",
          "shadow-[inset_0_1px_0_rgb(255_255_255/0.7),0_1px_2px_rgb(18_18_18/0.05),0_10px_28px_-14px_rgb(18_18_18/0.25)]",
          "dark:shadow-[inset_0_1px_0_rgb(255_255_255/0.05),0_1px_2px_rgb(0_0_0/0.5),0_12px_32px_-16px_rgb(0_0_0/0.8)]",
        )}
      >
        <li
          aria-hidden
          className={cn(
            "pointer-events-none absolute left-0 top-1 bottom-1 rounded-full bg-ink/[0.07] ring-1 ring-inset ring-ink/[0.06] dark:bg-ink/[0.1]",
            mark.glide
              ? "transition-[transform,width,opacity] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)]"
              : "transition-opacity duration-200",
            "motion-reduce:transition-none",
            mark.on ? "opacity-100" : "opacity-0",
          )}
          style={{ transform: `translateX(${mark.x}px)`, width: mark.w }}
        />
        {items.map((item, i) => {
          const setRef = (el: HTMLElement | null) => {
            refs.current[i] = el;
          };
          if (item.kind === "section") {
            const on = i === current;
            return (
              <li key={item.href}>
                <a ref={setRef} href={item.href} aria-current={on ? "location" : undefined} className={itemClass(on)}>
                  {item.label}
                </a>
              </li>
            );
          }
          if (item.kind === "route") {
            const on = i === current;
            return (
              <li key={item.href}>
                <Link ref={setRef} to={item.href} aria-current={on ? "page" : undefined} className={itemClass(on)}>
                  {item.label}
                </Link>
              </li>
            );
          }
          return (
            <li key={item.href}>
              <a ref={setRef} href={item.href} target="_blank" rel="noopener noreferrer" className={cn(itemClass(false), "group")}>
                {item.label}
                <ArrowUpRight aria-hidden className="size-3 text-muted/80 transition-colors group-hover:text-ink" />
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            </li>
          );
        })}
        {story && (
          // How far down the story the reader is: a hairline under the pill from rented (teacher) to owned (student).
          <motion.li
            aria-hidden
            className="pointer-events-none absolute inset-x-6 -bottom-px h-px origin-left bg-[linear-gradient(to_right,var(--rented),var(--owned))]"
            style={{ scaleX: scrollYProgress }}
          />
        )}
      </ul>
    </nav>
  );
}

export function SiteHeader({ stats }: { stats: Stats | null }) {
  const sw = surfaceSwitch(stats);
  const { pathname, hash } = useLocation();
  const [open, setOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement | null>(null);
  const scrolled = useScrolled();
  const items = pillItems(pathname);
  const ids = useMemo(() => sectionIds(items), [items]);
  const activeSection = useScrollSpy(ids);

  useEffect(() => setOpen(false), [pathname]);
  // The sheet is for phones: close it if the window grows past the breakpoint, so the page is never left locked.
  useEffect(() => {
    if (!open) return;
    const mq = window.matchMedia("(min-width: 768px)");
    const on = () => mq.matches && setOpen(false);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [open]);

  // The quieter button: the dashboard, or Scout when the dashboard is already open.
  const onDashboard = pathname.startsWith("/dashboard");

  return (
    <>
      <header
        className={cn(
          "sticky top-0 z-40 h-16 border-b transition-[background-color,border-color,box-shadow] duration-300",
          scrolled
            ? "border-line/70 bg-bg/90 shadow-[0_10px_30px_-24px_rgb(18_18_18/0.35)] supports-[backdrop-filter]:bg-bg/70 supports-[backdrop-filter]:backdrop-blur-xl dark:shadow-[0_10px_30px_-20px_rgb(0_0_0/0.9)]"
            : "border-transparent bg-transparent",
        )}
      >
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2"
        >
          Skip to content
        </a>
        <div className="mx-auto grid h-full max-w-6xl grid-cols-[1fr_auto_1fr] items-center gap-3 px-4 sm:px-6 lg:gap-4 lg:px-8">
          <Link
            to="/"
            aria-label="Ratchet home"
            className="col-start-1 flex items-center justify-self-start rounded-lg"
            onClick={() => {
              // Already at the top of the story: the mark takes you back up instead of doing nothing.
              if (pathname === "/" && !hash) window.scrollTo({ top: 0 });
            }}
          >
            {/* The header stays mounted across pages, so the mark clicks forward once per page load. */}
            <Logo animate />
          </Link>

          <NavPill items={items} pathname={pathname} activeSection={activeSection} />

          <div className="col-start-3 flex items-center justify-end gap-1.5">
            <ThemeToggle className="hidden md:inline-flex" />
            <ButtonLink
              to={onDashboard ? "/scout" : "/dashboard"}
              variant="outline"
              size="sm"
              className="hidden bg-surface/70 lg:inline-flex"
            >
              {onDashboard ? "Scout" : "Dashboard"}
            </ButtonLink>
            <ButtonA href={DEMO_URL} target="_blank" rel="noopener noreferrer" size="sm" aria-label="Live demo (simulation), opens in a new tab">
              Live demo <ArrowUpRight aria-hidden className="hidden lg:block" />
            </ButtonA>
            <button
              ref={menuButton}
              type="button"
              className="-mr-1.5 inline-flex size-10 items-center justify-center rounded-full text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink focus-visible:rounded-full md:hidden"
              aria-label="Open menu"
              aria-haspopup="dialog"
              aria-expanded={open}
              onClick={() => setOpen(true)}
            >
              <Menu className="size-5" aria-hidden />
            </button>
          </div>
        </div>
      </header>
      {sw && (
        // Not sticky, so the bar above stays 64 px tall: anchors (scroll-mt-16) and Scout's sticky totals line up under it.
        <div className="border-b border-line/60 px-4 py-1.5 sm:px-6 lg:px-8">
          <SurfaceSwitch sw={sw} />
        </div>
      )}
      <MobileMenu open={open} onClose={() => setOpen(false)} pathname={pathname} activeSection={activeSection} returnFocus={menuButton} />
    </>
  );
}
