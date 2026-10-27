import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router";
import { motion, useScroll } from "motion/react";
import { ArrowUpRight, Menu, Moon, Sun } from "lucide-react";
import { Logo } from "@/components/ui/logo";
import { ButtonLink } from "@/components/ui/button";
import { useTheme } from "@/lib/hooks";
import { cn, LIVE_PATH } from "@/lib/utils";
import { MobileMenu } from "./mobile-menu";
import { isCurrentRoute, pillItems, sectionIds, type NavItem } from "./nav-model";

/** Sun and moon stacked: the one that is not showing turns away and shrinks (a rotate and a scale, motion-safe). */
export function ThemeIcon({ theme, className }: { theme: "light" | "dark"; className?: string }) {
  const swap = "col-start-1 row-start-1 size-4 motion-safe:transition-[transform,opacity] motion-safe:duration-500 motion-safe:ease-[cubic-bezier(0.22,1,0.36,1)]";
  return (
    <span aria-hidden className={cn("inline-grid place-items-center", className)}>
      <Sun className={cn(swap, theme === "dark" ? "rotate-0 scale-100 opacity-100" : "rotate-90 scale-0 opacity-0")} />
      <Moon className={cn(swap, theme === "dark" ? "-rotate-90 scale-0 opacity-0" : "rotate-0 scale-100 opacity-100")} />
    </span>
  );
}

export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggle } = useTheme();
  return (
    <button
      type="button"
      onClick={(e) => toggle(e.currentTarget)}
      className={cn(
        "inline-flex size-9 items-center justify-center rounded-full text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink focus-visible:rounded-full",
        className,
      )}
      aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
    >
      <ThemeIcon theme={theme} />
    </button>
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

/** What the page knows of the live gateway: up (stats came back), waking (the container is starting), down (no answer), unknown (not asked yet). */
export type GatewayState = "up" | "waking" | "down" | "unknown";

const STATE_WORD: Record<GatewayState, string> = {
  up: "gateway is up",
  waking: "gateway is waking up",
  down: "gateway not reachable from this page",
  unknown: "checking the gateway",
};

/**
 * The small status dot: green and pulsing while the gateway answers, amber while it wakes, grey otherwise. The pulse is a CSS
 * animation that stops under reduced motion; the state is always also in text (the button's accessible name), never colour alone.
 */
export function StatusDot({ state, className }: { state: GatewayState; className?: string }) {
  const tone = state === "up" ? "bg-[#4ade80] dark:bg-[#17722e]" : state === "waking" ? "bg-[#fbbf24] dark:bg-[#8a5200]" : "bg-[#a1a1aa] dark:bg-[#52525b]";
  return (
    <span aria-hidden className={cn("relative inline-flex size-2 shrink-0", className)}>
      {state !== "down" && state !== "unknown" && (
        <span className={cn("absolute inset-0 rounded-full opacity-60 motion-safe:animate-ping", tone)} />
      )}
      <span className={cn("relative inline-flex size-2 rounded-full", tone)} />
    </span>
  );
}

/**
 * The centred nav. On the story page it lists chapters and, elsewhere, the pages. One highlight is moved with a transform
 * behind the items: it follows the pointer or keyboard focus, and rests on the current item (aria-current) when nothing is
 * hovered. It fades out where no item is current, and comes back in place (no slide from the last spot). Reduced motion: it jumps.
 */
function NavPill({ items, pathname, activeSection }: { items: readonly NavItem[]; pathname: string; activeSection: string | null }) {
  const list = useRef<HTMLUListElement | null>(null);
  const refs = useRef<(HTMLElement | null)[]>([]);
  const [mark, setMark] = useState<Mark>({ x: 0, w: 0, on: false, glide: false });
  const [hover, setHover] = useState<number | null>(null);

  const current = items.findIndex((i) => (i.kind === "section" ? i.href.slice(1) === activeSection : isCurrentRoute(i, pathname)));
  const target = hover ?? current;
  const targetRef = useRef(target);
  targetRef.current = target;

  // Move the highlight when the item it should sit behind changes.
  useLayoutEffect(() => {
    const el = target >= 0 ? refs.current[target] : null;
    setMark((prev) =>
      el ? { x: el.offsetLeft, w: el.offsetWidth, on: true, glide: prev.on } : prev.on ? { ...prev, on: false, glide: false } : prev,
    );
  }, [target, items]);

  // Keep it on its item when widths change (fonts loading, a resize): in place, without a glide.
  useEffect(() => {
    if (!list.current || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      const i = targetRef.current;
      const el = i >= 0 ? refs.current[i] : null;
      if (!el) return;
      setMark((prev) => (prev.x === el.offsetLeft && prev.w === el.offsetWidth ? prev : { ...prev, x: el.offsetLeft, w: el.offsetWidth, glide: false }));
    });
    ro.observe(list.current);
    return () => ro.disconnect();
  }, [items]);

  const itemClass = (i: number) =>
    cn(
      "relative z-10 inline-flex h-8 items-center gap-1 whitespace-nowrap rounded-full px-2.5 text-[13px] font-medium transition-colors duration-150 focus-visible:rounded-full lg:px-3.5 lg:text-[13.5px]",
      i === current || i === hover ? "text-ink" : "text-muted",
    );
  const track = (i: number) => ({
    onMouseEnter: () => setHover(i),
    onFocus: () => setHover(i),
  });

  return (
    <nav aria-label="Main" className="col-start-2 hidden justify-self-center md:block">
      <ul ref={list} className="relative flex items-center" onMouseLeave={() => setHover(null)} onBlur={() => setHover(null)}>
        <li
          aria-hidden
          className={cn(
            "pointer-events-none absolute left-0 top-0 bottom-0 rounded-full bg-ink/[0.07] ring-1 ring-inset ring-ink/[0.07] dark:bg-ink/[0.1]",
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
                <a ref={setRef} href={item.href} aria-current={on ? "location" : undefined} className={itemClass(i)} {...track(i)}>
                  {item.label}
                </a>
              </li>
            );
          }
          if (item.kind === "route") {
            const on = i === current;
            return (
              <li key={item.href}>
                <Link ref={setRef} to={item.href} aria-current={on ? "page" : undefined} className={itemClass(i)} {...track(i)}>
                  {item.label}
                </Link>
              </li>
            );
          }
          return (
            <li key={item.href}>
              <a ref={setRef} href={item.href} target="_blank" rel="noopener noreferrer" className={cn(itemClass(i), "group")} {...track(i)}>
                {item.label}
                <ArrowUpRight aria-hidden className="size-3 text-muted/80 transition-colors group-hover:text-ink" />
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** The live button: stays on this site (same tab), with the gateway's state as a dot and in its accessible name. */
export function LiveButton({ state, pathname, className, size = "sm" }: { state: GatewayState; pathname: string; className?: string; size?: "sm" | "lg" }) {
  const here = pathname === LIVE_PATH;
  return (
    <ButtonLink
      to={LIVE_PATH}
      size={size}
      aria-current={here ? "page" : undefined}
      aria-label={`Live, ${STATE_WORD[state]}`}
      className={cn("gap-2 pl-3.5", here && "ring-2 ring-owned/40 ring-offset-2 ring-offset-surface", className)}
    >
      <StatusDot state={state} />
      Live
    </ButtonLink>
  );
}

export function SiteHeader({ gateway = "unknown" }: { gateway?: GatewayState }) {
  const { pathname, hash } = useLocation();
  const [open, setOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement | null>(null);
  const scrolled = useScrolled();
  const items = pillItems(pathname);
  const ids = useMemo(() => sectionIds(items), [items]);
  const activeSection = useScrollSpy(ids);
  const story = pathname === "/";
  const { scrollYProgress } = useScroll();

  useEffect(() => setOpen(false), [pathname]);
  // The sheet is for phones: close it if the window grows past the breakpoint, so the page is never left locked.
  useEffect(() => {
    if (!open) return;
    const mq = window.matchMedia("(min-width: 768px)");
    const on = () => mq.matches && setOpen(false);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [open]);

  return (
    <>
      {/* A floating bar: the header itself takes no background, the rounded bar inside it does. */}
      <header className={cn("pointer-events-none sticky top-0 z-40 px-3 transition-[padding] duration-300 sm:px-4", scrolled ? "pt-2" : "pt-3")}>
        <a
          href="#main"
          className="pointer-events-auto sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-3 focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2"
        >
          Skip to content
        </a>
        <div
          className={cn(
            "pointer-events-auto relative mx-auto grid grid-cols-[1fr_auto_1fr] items-center gap-2 rounded-full border pl-4 pr-2 sm:pl-5",
            "transition-[max-width,height,background-color,border-color,box-shadow] duration-300 ease-[cubic-bezier(0.22,1,0.36,1)] motion-reduce:transition-none",
            "bg-surface/85 supports-[backdrop-filter]:bg-surface/60 supports-[backdrop-filter]:backdrop-blur-xl supports-[backdrop-filter]:backdrop-saturate-150",
            "[@media(prefers-reduced-transparency:reduce)]:bg-surface [@media(prefers-reduced-transparency:reduce)]:backdrop-blur-none",
            scrolled
              ? "h-12 max-w-4xl border-line-strong/80 shadow-[inset_0_1px_0_rgb(255_255_255/0.6),0_1px_2px_rgb(18_18_18/0.06),0_14px_34px_-14px_rgb(18_18_18/0.32)] dark:shadow-[inset_0_1px_0_rgb(255_255_255/0.05),0_1px_2px_rgb(0_0_0/0.5),0_16px_38px_-16px_rgb(0_0_0/0.9)]"
              : "h-14 max-w-6xl border-line shadow-[inset_0_1px_0_rgb(255_255_255/0.6),0_1px_2px_rgb(18_18_18/0.04)] dark:shadow-[inset_0_1px_0_rgb(255_255_255/0.05),0_1px_2px_rgb(0_0_0/0.4)]",
          )}
        >
          <Link
            to="/"
            aria-label="Ratchet home"
            className="col-start-1 flex items-center justify-self-start rounded-full"
            onClick={() => {
              // Already at the top of the story: the mark takes you back up instead of doing nothing.
              if (pathname === "/" && !hash) window.scrollTo({ top: 0 });
            }}
          >
            {/* The header stays mounted across pages, so the mark clicks forward once per page load. */}
            <Logo animate />
          </Link>

          <NavPill items={items} pathname={pathname} activeSection={activeSection} />

          <div className="col-start-3 flex items-center justify-end gap-1">
            <ThemeToggle className="hidden md:inline-flex" />
            <LiveButton state={gateway} pathname={pathname} />
            <button
              ref={menuButton}
              type="button"
              className="inline-flex size-10 items-center justify-center rounded-full text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink focus-visible:rounded-full md:hidden"
              aria-label="Open menu"
              aria-haspopup="dialog"
              aria-expanded={open}
              onClick={() => setOpen(true)}
            >
              <Menu className="size-5" aria-hidden />
            </button>
          </div>
          {story && (
            // How far down the story the reader is: a hairline along the bar's lower edge, from rented (teacher) to owned (student).
            <motion.span
              aria-hidden
              className="pointer-events-none absolute inset-x-8 -bottom-px h-px origin-left bg-[linear-gradient(to_right,var(--rented),var(--owned))]"
              style={{ scaleX: scrollYProgress }}
            />
          )}
        </div>
      </header>
      <MobileMenu open={open} onClose={() => setOpen(false)} pathname={pathname} activeSection={activeSection} gateway={gateway} returnFocus={menuButton} />
    </>
  );
}
