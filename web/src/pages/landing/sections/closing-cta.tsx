import { useRef, type PointerEvent } from "react";
import { ArrowRight, LayoutDashboard, Search } from "lucide-react";
import { ButtonLink } from "@/components/ui/button";
import { LogoMark } from "@/components/ui/logo";
import examsText from "@/data/exams.json?raw";
import { parseExams, pct } from "@/lib/exams-model";
import { cn, LIVE_PATH } from "@/lib/utils";
import { Reveal } from "../shared";

const EXAMS = parseExams(examsText);
const r2 = EXAMS.scores;

/** The proof under the buttons: every number comes from exams.json (the same file the charts read); the other two are the site's own wording. */
const PROOF = [
  {
    value: `${pct(r2.exam1.r2.rate)} and ${pct(r2.exam2.r2.rate)}`,
    label: "on two text-to-SQL exams",
    sub: `the untrained base scores ${pct(r2.exam1.base.rate)} and ${pct(r2.exam2.base.rate)}`,
  },
  { value: "Token Factory", label: "every model call on the live site", sub: "Nebius, under daily spend limits" },
  { value: "Nebius AI Cloud", label: "the adapter was retrained there", sub: "and the result reproduced" },
];

/**
 * The closing call to action: a wide card with a light travelling around its border, a grid and a few meteors behind the type,
 * and a spotlight that follows the pointer. One primary button with a sheen; the other two are quiet. All motion stops under
 * reduced motion (the border and the meteors are CSS, the spotlight needs a pointer).
 */
export function ClosingCta({ className }: { className?: string }) {
  const root = useRef<HTMLDivElement | null>(null);
  const move = (e: PointerEvent<HTMLDivElement>) => {
    const el = root.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty("--mx", `${e.clientX - r.left}px`);
    el.style.setProperty("--my", `${e.clientY - r.top}px`);
  };
  return (
    <Reveal className={className}>
      <div
        ref={root}
        onPointerMove={move}
        className="beam-border group/cta relative isolate overflow-hidden rounded-[28px] border border-line bg-surface shadow-card"
      >
        <div aria-hidden className="bg-grid pointer-events-none absolute inset-0 -z-10 opacity-70 [mask-image:radial-gradient(ellipse_90%_90%_at_50%_30%,black,transparent)]" />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 -z-10 [background:radial-gradient(45%_60%_at_15%_0%,color-mix(in_srgb,var(--owned)_16%,transparent),transparent),radial-gradient(45%_60%_at_90%_100%,color-mix(in_srgb,var(--rented)_16%,transparent),transparent)]"
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 -z-10 opacity-0 transition-opacity duration-500 group-hover/cta:opacity-100 motion-reduce:hidden [background:radial-gradient(380px_circle_at_var(--mx,50%)_var(--my,40%),color-mix(in_srgb,var(--owned)_14%,transparent),transparent_70%)]"
        />
        <div aria-hidden className="pointer-events-none absolute inset-0 -z-10 overflow-hidden">
          <span className="meteor left-[78%]" style={{ animationDelay: "0s" }} />
          <span className="meteor left-[92%]" style={{ animationDelay: "3.2s" }} />
          <span className="meteor left-[60%]" style={{ animationDelay: "6s" }} />
        </div>

        <div className="relative px-5 py-14 text-center sm:px-10 sm:py-20 lg:px-16 lg:py-24 xl:py-32">
          <LogoMark className="mx-auto mb-7 size-12" />
          <h2 className="mx-auto max-w-[18ch] text-balance xl:max-w-[20ch] text-[2.6rem] font-semibold leading-[1.02] tracking-[-0.04em] text-ink sm:text-6xl lg:text-7xl xl:text-8xl">
            Your traffic can teach.{" "}
            <span className="bg-[linear-gradient(100deg,var(--owned-ink),var(--rented-ink))] bg-clip-text text-transparent">Keep what it learns.</span>
          </h2>
          <p className="mx-auto mt-6 max-w-[56ch] text-pretty text-[17px] leading-relaxed text-ink-2 xl:text-xl">
            Run Scout and watch each call get routed. Then open the dashboard to see the gateway's own traffic, costs and releases.
          </p>
          <div className="mt-9 flex flex-col items-stretch justify-center gap-3 min-[480px]:flex-row min-[480px]:items-center xl:mt-11 xl:gap-4">
            <ButtonLink
              to={LIVE_PATH}
              size="lg"
              className="btn-shine h-14 px-8 text-base shadow-[0_10px_34px_-10px_color-mix(in_srgb,var(--owned)_70%,transparent)] hover:shadow-[0_14px_44px_-8px_color-mix(in_srgb,var(--owned)_85%,transparent)]"
            >
              Try the live gateway <ArrowRight aria-hidden />
            </ButtonLink>
            <ButtonLink to="/scout" variant="outline" size="lg" className="h-14 bg-surface/80 backdrop-blur-md">
              <Search aria-hidden /> Try Scout live
            </ButtonLink>
            <ButtonLink to="/dashboard" variant="outline" size="lg" className="h-14 bg-surface/80 backdrop-blur-md">
              <LayoutDashboard aria-hidden /> Open the dashboard
            </ButtonLink>
          </div>

          <ul className="mx-auto mt-12 grid max-w-6xl gap-px overflow-hidden rounded-2xl border border-line bg-line text-left sm:grid-cols-3">
            {PROOF.map((p) => (
              <li key={p.value} className={cn("bg-surface/90 px-5 py-4 backdrop-blur-md")}>
                <p className="text-[20px] font-semibold tracking-[-0.02em] text-ink tabular">{p.value}</p>
                <p className="mt-0.5 text-[13px] font-medium text-ink-2">{p.label}</p>
                <p className="mt-0.5 text-[12.5px] text-muted">{p.sub}</p>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Reveal>
  );
}
