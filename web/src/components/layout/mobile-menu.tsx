import { useEffect, useId, useRef, type MouseEvent, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router";
import { AnimatePresence, motion } from "motion/react";
import { ArrowUpRight, Sparkles, X } from "lucide-react";
import { startSiteTour } from "@/components/tour/site-tour";
import { Logo } from "@/components/ui/logo";
import { useTheme } from "@/lib/hooks";
import { DUR, EASE } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { LiveButton, ThemeIcon, type GatewayState } from "./site-header";
import { isCurrentRoute, MORE_NAV, PAGE_NAV, STORY_NAV, type NavItem } from "./nav-model";

const FOCUSABLE = 'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * The phone menu: a full-height sheet over the page with the same links as the header's pill, large enough to tap.
 * A modal dialog: focus stays inside, Escape closes it, the page under it does not scroll, and focus goes back to the
 * menu button. A section link closes the sheet first, then scrolls, so the page is unlocked when it moves.
 */
export function MobileMenu({
  open,
  onClose,
  pathname,
  activeSection,
  gateway,
  returnFocus,
}: {
  open: boolean;
  onClose: () => void;
  pathname: string;
  activeSection: string | null;
  gateway: GatewayState;
  returnFocus: RefObject<HTMLElement | null>;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement | null>(null);
  const close = useRef(onClose);
  close.current = onClose;
  /** False when a section link closed the sheet: the reader is going to that section, not back to the button. */
  const restore = useRef(true);

  useEffect(() => {
    if (!open) return;
    restore.current = true;
    const html = document.documentElement;
    const overflow = html.style.overflow;
    html.style.overflow = "hidden";
    const focusFirst = window.requestAnimationFrame(() => {
      panel.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    });
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close.current();
        return;
      }
      if (e.key !== "Tab" || !panel.current) return;
      const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.offsetParent !== null);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      window.cancelAnimationFrame(focusFirst);
      document.removeEventListener("keydown", onKey);
      html.style.overflow = overflow;
      if (restore.current) returnFocus.current?.focus();
    };
  }, [open, returnFocus]);

  const goToSection = (e: MouseEvent<HTMLAnchorElement>, id: string) => {
    e.preventDefault();
    restore.current = false;
    onClose();
    // Two frames: the sheet's cleanup has unlocked the page's scroll before the jump.
    window.requestAnimationFrame(() =>
      window.requestAnimationFrame(() => {
        window.location.hash = id;
      }),
    );
  };

  const onStory = pathname === "/";
  const row =
    "group flex min-h-14 items-center justify-between gap-4 border-b border-line py-3 text-[22px] font-semibold tracking-[-0.02em] transition-colors";

  const render = (item: NavItem, i: number): ReactNode => {
    const child = (body: ReactNode) => (
      <motion.li
        key={item.href}
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: DUR.base, ease: EASE, delay: 0.04 + i * 0.03 }}
      >
        {body}
      </motion.li>
    );
    if (item.kind === "section") {
      const id = item.href.slice(1);
      const on = activeSection === id;
      return child(
        <a href={item.href} onClick={(e) => goToSection(e, id)} aria-current={on ? "location" : undefined} className={cn(row, on ? "text-ink" : "text-ink-2 hover:text-ink")}>
          {item.label}
          {on && <span aria-hidden className="size-1.5 rounded-full bg-ink" />}
        </a>,
      );
    }
    if (item.kind === "route") {
      const on = isCurrentRoute(item, pathname);
      return child(
        <Link to={item.href} onClick={onClose} aria-current={on ? "page" : undefined} className={cn(row, on ? "text-ink" : "text-ink-2 hover:text-ink")}>
          {item.label}
          {on && <span aria-hidden className="size-1.5 rounded-full bg-ink" />}
        </Link>,
      );
    }
    return child(
      <a href={item.href} target="_blank" rel="noopener noreferrer" className={cn(row, "text-ink-2 hover:text-ink")}>
        <span>
          {item.label}
          <span className="sr-only"> (opens in a new tab)</span>
        </span>
        <ArrowUpRight aria-hidden className="size-5 text-muted transition-colors group-hover:text-ink" />
      </a>,
    );
  };

  const sections = onStory ? STORY_NAV.filter((i) => i.kind === "section") : [];
  const pages = [...PAGE_NAV, ...MORE_NAV];

  return createPortal(
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 md:hidden"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0, transition: { duration: DUR.fast * 0.75, ease: EASE } }}
          transition={{ duration: DUR.fast, ease: EASE }}
        >
          {/* The scrim: a tap outside the sheet closes it. Not a tab stop; Escape and the Close button are the keyboard ways. */}
          <div aria-hidden onClick={onClose} className="absolute inset-0 bg-ink/40 backdrop-blur-[2px]" />
          <motion.div
            ref={panel}
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            className="absolute inset-y-0 right-0 flex w-full max-w-sm flex-col border-l border-line bg-bg shadow-[-24px_0_48px_-24px_rgb(0_0_0/0.35)] sm:rounded-l-3xl"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%", transition: { duration: DUR.fast * 0.75, ease: EASE } }}
            transition={{ duration: DUR.base, ease: EASE }}
          >
          <h2 id={titleId} className="sr-only">
            Menu
          </h2>
          <div className="flex h-16 shrink-0 items-center justify-between gap-3 border-b border-line px-4 sm:px-6">
            <Link to="/" onClick={onClose} aria-label="Ratchet home" className="flex items-center rounded-lg">
              <Logo />
            </Link>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close menu"
              data-autofocus
              className="inline-flex size-10 items-center justify-center rounded-full text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink focus-visible:rounded-full"
            >
              <X className="size-5" aria-hidden />
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 pb-6 pt-2 sm:px-6">
            {sections.length > 0 && (
              <nav aria-label="On this page" className="mt-4">
                <p className="mb-1 font-mono text-[12px] text-muted">On this page</p>
                <ul>{sections.map((s, i) => render(s, i))}</ul>
              </nav>
            )}
            <nav aria-label="Pages" className="mt-6">
              <p className="mb-1 font-mono text-[12px] text-muted">Pages</p>
              <ul>{pages.map((p, i) => render(p, sections.length + i))}</ul>
            </nav>
          </div>
          <div className="shrink-0 border-t border-line bg-bg px-4 pt-4 pb-[max(1rem,env(safe-area-inset-bottom))] sm:px-6">
            <button
              type="button"
              onClick={() => {
                onClose();
                startSiteTour();
              }}
              className="mb-3 inline-flex h-11 w-full items-center justify-center gap-2 rounded-full border border-line-strong bg-surface text-[15px] font-medium text-ink transition-colors hover:bg-surface-2 focus-visible:rounded-full"
            >
              <Sparkles aria-hidden /> Take the 60-second tour
            </button>
            <div className="flex items-center gap-3">
              <LiveButton state={gateway} pathname={pathname} size="lg" className="flex-1" />
              <SheetThemeToggle />
            </div>
          </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>,
    document.body,
  );
}

function SheetThemeToggle() {
  const { theme, toggle } = useTheme();
  return (
    <button
      type="button"
      onClick={(e) => toggle(e.currentTarget)}
      className="inline-flex h-12 shrink-0 items-center justify-center gap-2 rounded-full border border-line-strong bg-surface px-4 text-[15px] font-medium text-ink transition-colors hover:bg-surface-2 focus-visible:rounded-full"
    >
      <ThemeIcon theme={theme} />
      {theme === "dark" ? "Light theme" : "Dark theme"}
    </button>
  );
}
