import {
  ArrowUpRight,
  BookOpen,
  Bot,
  Code,
  Cpu,
  Database,
  FileSignature,
  LayoutDashboard,
  ClipboardCheck,
  Route,
  Scale,
  Server,
  Undo2,
  type LucideIcon,
} from "lucide-react";
import { Link } from "react-router";
import { RealBadge } from "@/components/ui/real-badge";
import { Container } from "@/components/ui/section";
import { type RealStatus } from "@/lib/real-model";
import { DOCS_URL, REPO_URL } from "@/lib/utils";
import { REAL } from "@/pages/judges/real";
import { LINKS, Reveal } from "../shared";

/**
 * "Everything in the box": one tile per thing we built, each with its honest status. A tile that rests on an entry of
 * `src/data/real.json` takes its status from there, so this grid and the judges' "What's real" table cannot disagree.
 */
interface Item {
  icon: LucideIcon;
  title: string;
  line: string;
  /** The entry in real.json this tile's status comes from, or a fixed status for things that are plainly there. */
  entry?: string;
  status?: RealStatus;
  to?: string;
  href?: string;
}

const ITEMS: Item[] = [
  { icon: Cpu, title: "Trained adapter", line: "Round 2 and round 1, on Hugging Face", entry: "hf-publish", href: LINKS.hfModel },
  { icon: Database, title: "Training data", line: "4,322 teacher examples, every query run", entry: "teacher-labels", href: LINKS.hfTrain },
  { icon: ClipboardCheck, title: "Two exams", line: "Answers and scores, recomputable", entry: "exam-result", href: LINKS.hfExams },
  { icon: Route, title: "The gateway", line: "Small model first, teacher on doubt", entry: "machinery", to: "/judges#cascade" },
  { icon: Undo2, title: "Release gate", line: "Shadow, canary, automatic rollback", entry: "demo-numbers", to: "/judges#release" },
  { icon: FileSignature, title: "Signed lineage", line: "A bill of materials per adapter", entry: "machinery", to: "/judges#certificate" },
  { icon: Scale, title: "License rule", line: "Closed models are refused as teachers", entry: "license-rule", to: "/judges#ledger" },
  { icon: Bot, title: "Scout", line: "An agent demo on the gateway", entry: "scout-tavily", to: "/scout" },
  { icon: LayoutDashboard, title: "Dashboard", line: "Costs, routes, releases, receipts", entry: "live-demo", to: "/dashboard" },
  { icon: Server, title: "Student on Nebius GPUs", line: "Wiring the demo to an endpoint, and Night School jobs (scripted, ready to run)", entry: "gateway-live", to: "/judges#real" },
  { icon: BookOpen, title: "Docs", line: "Guides, API, evidence, how to verify", status: "real", href: DOCS_URL },
  { icon: Code, title: "Open source", line: "Apache-2.0, 1,900+ tests", status: "real", href: REPO_URL },
];

function statusOf(item: Item): RealStatus {
  if (item.status) return item.status;
  return REAL.entries.find((e) => e.id === item.entry)?.status ?? "pending";
}

function Tile({ item }: { item: Item }) {
  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <span className="inline-flex size-9 items-center justify-center rounded-xl border border-line bg-surface-2 text-ink-2">
          <item.icon className="size-[18px]" aria-hidden />
        </span>
        <RealBadge status={statusOf(item)} />
      </div>
      <p className="mt-3 text-[15px] font-semibold leading-snug tracking-[-0.01em] text-ink">
        {item.title}
        {item.href && <ArrowUpRight className="ml-1 inline size-3.5 text-muted" aria-hidden />}
      </p>
      <p className="mt-1 text-[13px] leading-snug text-ink-2">{item.line}</p>
    </>
  );
  const cls =
    "block h-full rounded-2xl border border-line bg-surface p-4 shadow-card transition-colors hover:border-line-strong focus-visible:outline-2 focus-visible:outline-offset-2";
  if (item.to) {
    return (
      <Link to={item.to} className={cls}>
        {body}
      </Link>
    );
  }
  return (
    <a href={item.href} target="_blank" rel="noopener noreferrer" className={cls}>
      {body}
    </a>
  );
}

export function WhatsIn() {
  return (
    <section id="whats-in" aria-labelledby="whats-in-title" className="relative scroll-mt-16 border-t border-line py-14 sm:py-16">
      <Container>
        <Reveal>
          <h2 id="whats-in-title" className="max-w-3xl text-balance text-2xl font-semibold tracking-[-0.02em] text-ink sm:text-3xl">
            Everything in the box, and what is real.
          </h2>
          <p className="mt-3 max-w-[60ch] text-[15px] leading-relaxed text-ink-2">
            Each tile says whether it is real, measured, simulation or ready to run. Click any one.
          </p>
          <ul className="mt-7 grid grid-cols-1 gap-3 min-[520px]:grid-cols-2 lg:grid-cols-4">
            {ITEMS.map((it) => (
              <li key={it.title} className="min-w-0">
                <Tile item={it} />
              </li>
            ))}
          </ul>
        </Reveal>
      </Container>
    </section>
  );
}
