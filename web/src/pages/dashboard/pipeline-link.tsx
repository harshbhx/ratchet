import { ArrowUpRight } from "lucide-react";
import { DEMO_URL } from "@/lib/utils";
import { PIPELINE_LINK } from "./pipeline-link-model";

/** One calm pointer to the demo gateway, shown only in the live dashboard's empty state. */
export function PipelineLink() {
  return (
    <a
      href={`${DEMO_URL}/dashboard`}
      target="_blank"
      rel="noopener noreferrer"
      className="group mt-3 flex items-start justify-between gap-3 rounded-xl border border-line bg-bg-sunken/70 px-3.5 py-3 text-ink transition-colors hover:border-line-strong"
    >
      <span className="min-w-0">
        <span className="block text-[14px] font-medium">{PIPELINE_LINK.title}</span>
        <span className="mt-0.5 block text-[13px] leading-5 text-ink-2">{PIPELINE_LINK.line}</span>
      </span>
      <ArrowUpRight aria-hidden className="mt-0.5 size-4 shrink-0 text-muted transition-colors group-hover:text-ink" />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
}
