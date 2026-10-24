import type { ReactNode } from "react";

/** The dashboard's sections, in page order. The jump bar and each heading read this list, so a section is added in one place. */
export const SECTIONS = [
  { id: "overview", label: "Overview" },
  { id: "money", label: "Money" },
  { id: "versions", label: "Versions and tasks" },
  { id: "reliability", label: "Reliability" },
  { id: "trust", label: "Trust" },
  { id: "activity", label: "Activity" },
  { id: "measured", label: "Measured" },
] as const;

export type SectionId = (typeof SECTIONS)[number]["id"];

/** A jump bar that stays under the site header, so a long page can be crossed in one click. It scrolls sideways on a phone. */
export function JumpBar({ show }: { show: readonly SectionId[] }) {
  const items = SECTIONS.filter((s) => show.includes(s.id));
  return (
    <nav
      aria-label="Dashboard sections"
      className="sticky top-14 z-20 -mx-4 mt-5 border-y border-line bg-bg/85 px-4 backdrop-blur supports-[backdrop-filter]:bg-bg/70 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8"
    >
      <ul className="flex gap-1 overflow-x-auto py-2 [scrollbar-width:none]">
        {items.map((s) => (
          <li key={s.id} className="shrink-0">
            <a
              href={`#${s.id}`}
              className="inline-flex h-8 items-center rounded-lg px-3 text-[13px] font-medium text-muted transition-colors hover:bg-surface-2 hover:text-ink"
            >
              {s.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** A named group of panels with an anchor the jump bar points at. */
export function Group({ id, title, hint, children }: { id: SectionId; title: string; hint?: string; children: ReactNode }) {
  return (
    <section id={id} aria-labelledby={`${id}-heading`} className="scroll-mt-28 space-y-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 pt-4">
        <h2 id={`${id}-heading`} className="text-[13px] font-semibold uppercase tracking-[0.12em] text-ink">
          {title}
        </h2>
        {hint && <p className="text-[12.5px] text-muted">{hint}</p>}
        <span aria-hidden className="h-px min-w-8 flex-1 bg-line" />
      </div>
      {children}
    </section>
  );
}
