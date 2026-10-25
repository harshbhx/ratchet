import { useId, useMemo, useState } from "react";
import { ChevronRight, CircleHelp, ListTree, Plus, Search, TriangleAlert, X } from "lucide-react";
import type { Mode, TaskEntry } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn, fmtInt } from "@/lib/utils";
import { fmtShare } from "./model";
import { NEW_TASK, countFields, matches, policyText, sourceCounts, sourceMeta, specText, typeSummary } from "./registry-model";
import { RecalibratingBadge, RouteBadge, SourceBadge, StorageText, TaskDrawer, type DrawerState } from "./task-drawer";
import { DataTable, Empty, Panel, td, tdNum, th, thNum } from "./ui";
import type { RegistryState } from "./use-dashboard";

/** Rows shown before "Show all": the registry holds up to 500 tasks. */
const PAGE = 30;
/** A search box appears once there are more tasks than this. */
const SEARCH_FROM = 8;

function Compares({ t }: { t: TaskEntry }) {
  const types = typeSummary(t.fields ?? {});
  const n = countFields(t.fields);
  if (types.length === 0) {
    return (
      <span className="text-[12.5px] text-muted">
        <span className="font-mono text-ink-2">{t.format}</span> · whole answer
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1" title={`${n} field${n === 1 ? "" : "s"}, counting the fields inside records`}>
      <span className="mr-0.5 font-mono text-[12px] text-muted">{t.format}</span>
      {types.slice(0, 3).map(({ type, n: k }) => (
        <Badge key={type} tone="neutral" className="font-mono text-[11.5px]">
          {type}
          {k > 1 && <span className="text-muted">×{k}</span>}
        </Badge>
      ))}
      {types.length > 3 && <span className="text-[12px] text-muted">+{types.length - 3}</span>}
    </span>
  );
}

function FilterChip({ on, label, n, onClick, title }: { on: boolean; label: string; n: number; onClick: () => void; title?: string }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      title={title}
      className={cn(
        "inline-flex h-7 items-center gap-1.5 whitespace-nowrap rounded-full border px-2.5 text-[12px] font-medium transition-colors",
        on ? "border-ink bg-ink text-bg" : "border-line text-ink-2 hover:border-line-strong hover:bg-surface-2",
      )}
    >
      {label}
      <span className={cn("tabular", on ? "text-bg/70" : "text-muted")}>{n}</span>
    </button>
  );
}

function Skeleton() {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading the task registry">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="h-10 animate-pulse rounded-lg bg-surface-2" />
      ))}
    </div>
  );
}

/**
 * The task registry (GET /ratchet/tasks): every registered task's source, format, comparators, route, storage, spec hash
 * and traffic, filterable by source, with a drawer for one task's field specs. API tasks can be registered, changed and
 * deleted with an admin key held in this component's state only. Hidden on a gateway without the endpoint.
 */
export function TaskRegistryPanel({ state, mode, onChanged }: { state: RegistryState; mode: Mode; onChanged: () => void }) {
  const [source, setSource] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [all, setAll] = useState(false);
  const [drawer, setDrawer] = useState<DrawerState>(null);
  // Memory only: never written to storage, a cookie or a URL (AGENTS.md invariant 18).
  const [adminKey, setAdminKey] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const searchId = useId();

  const registry = state.registry;
  const tasks = useMemo(() => registry?.tasks ?? [], [registry]);
  const taken = useMemo(() => new Map(tasks.map((t) => [t.name, t.source] as [string, string])), [tasks]);
  const counts = sourceCounts(tasks);
  const active = source && counts.some((c) => c.source === source) ? source : null;
  const filtered = tasks.filter((t) => (!active || t.source === active) && matches(t, query));
  const shown = all ? filtered : filtered.slice(0, PAGE);
  const unregistered = registry?.unregistered ?? [];

  if (state.missing) return null;

  const openNew = (name = NEW_TASK.name, spec: Record<string, unknown> = NEW_TASK.spec) => {
    setNotice(null);
    setDrawer({ mode: "edit", name, text: specText(spec), isNew: true });
  };

  return (
    <Panel
      id="registry"
      title="Task registry"
      description="Every task the gateway knows: where its spec comes from, how answers are compared, and whether it is distilled into your model or pinned to the teacher."
      actions={
        registry && (
          <>
            <span className="hidden text-[12px] text-muted tabular sm:inline">
              {fmtInt(tasks.length)} of {fmtInt(registry.max_tasks)}
            </span>
            <Button size="sm" variant="outline" className="h-8" onClick={() => openNew()}>
              <Plus aria-hidden /> <span className="hidden min-[420px]:inline">Register a task</span>
              <span className="min-[420px]:hidden">New</span>
            </Button>
          </>
        )
      }
    >
      {!registry && state.loading && <Skeleton />}
      {!registry && !state.loading && state.error && (
        <p role="alert" className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn-soft/70 px-3 py-2 text-[13px] text-ink-2">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
          Could not load the task registry ({state.error}). It is read again with the next poll.
        </p>
      )}

      {registry && (
        <>
          {state.error && (
            <p role="status" className="mb-3 flex items-start gap-2 rounded-lg border border-warn/30 bg-warn-soft/70 px-3 py-2 text-[12.5px] text-ink-2">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0 text-warn" aria-hidden />
              Could not refresh the registry ({state.error}). Showing the last one that loaded.
            </p>
          )}
          {notice && (
            <p role="status" className="mb-3 flex items-start justify-between gap-2 rounded-lg border border-line bg-surface-2 px-3 py-2 text-[13px] text-ink-2">
              <span>{notice}</span>
              <button type="button" onClick={() => setNotice(null)} aria-label="Dismiss message" className="-m-1 rounded-md p-1 text-muted hover:bg-surface hover:text-ink">
                <X className="size-4" aria-hidden />
              </button>
            </p>
          )}

          <div className="mb-3 flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="-mx-4 flex gap-1.5 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0 sm:pb-0" role="group" aria-label="Filter tasks by source">
              <FilterChip on={active == null} label="All" n={tasks.length} onClick={() => setSource(null)} />
              {counts.map((c) => (
                <FilterChip key={c.source} on={active === c.source} label={sourceMeta(c.source).label} n={c.n} title={sourceMeta(c.source).hint} onClick={() => setSource(c.source)} />
              ))}
            </div>
            {tasks.length > SEARCH_FROM && (
              <div className="relative sm:w-56">
                <label htmlFor={searchId} className="sr-only">
                  Search tasks
                </label>
                <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted" aria-hidden />
                <input
                  id={searchId}
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search by name or description"
                  className="h-8 w-full rounded-full border border-line bg-surface pl-8 pr-3 text-[12.5px] text-ink placeholder:text-muted focus:border-line-strong"
                />
              </div>
            )}
          </div>

          {filtered.length === 0 ? (
            <Empty icon={ListTree} title="No task matches">
              {query ? "Try another search." : "No task has this source."}
            </Empty>
          ) : (
            <DataTable caption="Registered tasks" minWidth={900}>
              <thead>
                <tr>
                  <th className={th}>Task</th>
                  <th className={th}>Source</th>
                  <th className={th}>Compares</th>
                  <th className={th}>Route</th>
                  <th className={th}>Training data</th>
                  <th className={th}>Spec hash</th>
                  <th className={thNum}>Requests</th>
                  <th className={th}>
                    <span className="sr-only">Details</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {shown.map((t) => (
                  <tr key={t.name} className="cursor-pointer transition-colors hover:bg-surface-2/70" onClick={() => setDrawer({ mode: "view", task: t })}>
                    <td className={cn(td, "max-w-[260px]")}>
                      <div className="flex min-w-0 items-center gap-2">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setDrawer({ mode: "view", task: t });
                          }}
                          className="min-w-0 truncate rounded font-mono text-[12.5px] font-medium text-ink underline-offset-2 hover:underline"
                        >
                          {t.name}
                        </button>
                        {t.recalibrating && <RecalibratingBadge />}
                      </div>
                      {t.description && <p className="mt-0.5 truncate text-[12px] text-muted">{t.description}</p>}
                    </td>
                    <td className={td}>
                      <SourceBadge source={t.source} />
                    </td>
                    <td className={td}>
                      <Compares t={t} />
                    </td>
                    <td className={td}>
                      <RouteBadge pinned={t.pinned} />
                    </td>
                    <td className={cn(td, "text-[12.5px]")}>
                      <StorageText task={t} />
                    </td>
                    <td className={cn(td, "font-mono text-[12px] text-ink-2")}>{t.spec_hash}</td>
                    <td className={tdNum}>
                      <span className="text-ink-2">{fmtInt(t.requests)}</span>
                      {!t.pinned && t.requests > 0 && (
                        <span className="ml-1.5 text-[11.5px] text-muted" title="Answered by your model, all days">
                          {fmtShare(t.student_share)}
                        </span>
                      )}
                    </td>
                    <td className={cn(td, "w-8 text-right")}>
                      <ChevronRight className="inline size-4 text-muted" aria-hidden />
                    </td>
                  </tr>
                ))}
              </tbody>
            </DataTable>
          )}
          {filtered.length > PAGE && (
            <div className="mt-2 text-center">
              <Button size="sm" variant="ghost" onClick={() => setAll(!all)}>
                {all ? `Show the first ${PAGE}` : `Show all ${fmtInt(filtered.length)}`}
              </Button>
            </div>
          )}

          {unregistered.length > 0 && (
            <div className="mt-4 rounded-xl border border-line p-3.5">
              <p className="flex items-center gap-2 text-[13px] font-medium text-ink">
                <CircleHelp className="size-4 text-muted" aria-hidden /> Names requests used that are not registered
              </p>
              <p className="mt-1 text-pretty text-[12.5px] leading-5 text-muted">{policyText(registry.unknown_tasks).long} A typo, or a task to register or pin.</p>
              <ul className="mt-2.5 flex flex-wrap gap-1.5">
                {unregistered.slice(0, 24).map((u) => (
                  <li key={u.name}>
                    <button
                      type="button"
                      onClick={() => openNew(u.name, { description: "", pinned: false })}
                      title={`Register ${u.name} as an API task`}
                      className="inline-flex h-7 items-center gap-1.5 rounded-full border border-line px-2.5 font-mono text-[12px] text-ink-2 transition-colors hover:border-line-strong hover:bg-surface-2"
                    >
                      {u.name}
                      <span className="font-sans text-muted tabular">{fmtInt(u.requests)}</span>
                      <Plus className="size-3.5 text-muted" aria-hidden />
                    </button>
                  </li>
                ))}
                {unregistered.length > 24 && <li className="self-center text-[12px] text-muted">and {fmtInt(unregistered.length - 24)} more</li>}
              </ul>
            </div>
          )}

          <p className="mt-3 text-pretty text-[12.5px] leading-5 text-muted">
            Unregistered task names are <span className="text-ink-2">{policyText(registry.unknown_tasks).short}</span> (
            <span className="font-mono">RATCHET_UNKNOWN_TASKS={registry.unknown_tasks}</span>). Pinned tasks always go to the teacher and never become training
            data. Click a task for its field specs; only Admin API tasks can be changed here.
          </p>
        </>
      )}

      <TaskDrawer
        state={drawer}
        setState={setDrawer}
        mode={mode}
        adminKey={adminKey}
        setAdminKey={setAdminKey}
        taken={taken}
        onChanged={onChanged}
        onDeleted={(m) => setNotice(m)}
      />
    </Panel>
  );
}
