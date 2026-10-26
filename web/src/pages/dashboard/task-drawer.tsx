import { useEffect, useId, useState, type ReactNode } from "react";
import { CheckCircle2, Copy, Cpu, Database, FilePenLine, GitBranchPlus, KeyRound, Loader2, Lock, RefreshCw, Save, ShieldCheck, Trash2, TriangleAlert, XCircle } from "lucide-react";
import { ApiError, api, type FieldDoc, type Mode, type TaskEntry, type TaskValidation } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn, fmtInt } from "@/lib/utils";
import { fmtShare } from "./model";
import { Drawer, DrawerBody, DrawerFooter } from "./drawer";
import {
  NAME_RE,
  TYPE_HINT,
  adminMessage,
  errorsOf,
  extendDraft,
  fieldOptions,
  fmtDateTime,
  isEditable,
  parseSpec,
  presence,
  sourceMeta,
  specDocument,
  specText,
  storage,
} from "./registry-model";

export type DrawerState =
  | { mode: "view"; task: TaskEntry }
  | { mode: "edit"; name: string; text: string; isNew: boolean }
  | null;

export function SourceBadge({ source }: { source: string }) {
  const meta = sourceMeta(source);
  return (
    <Badge tone={source === "api" ? "neutral" : "outline"} title={meta.hint}>
      {meta.label}
    </Badge>
  );
}

export function RouteBadge({ pinned }: { pinned: boolean }) {
  return pinned ? (
    <Badge tone="rented">
      <Lock aria-hidden /> Pinned to teacher
    </Badge>
  ) : (
    <Badge tone="owned">
      <Cpu aria-hidden /> Distilled
    </Badge>
  );
}

export function StorageText({ task }: { task: Pick<TaskEntry, "pinned" | "store"> }) {
  const s = storage(task);
  if (s === "pinned") return <span className="text-muted">Never (pinned)</span>;
  if (s === "not_stored") return <span className="text-ink-2">Not stored</span>;
  return (
    <span className="inline-flex items-center gap-1 text-ink-2">
      <Database className="size-3.5 text-muted" aria-hidden /> Stored
    </span>
  );
}

export function RecalibratingBadge() {
  return (
    <Badge tone="warn" title="The live version was scored under another spec of this task; the teacher answers it until a night re-scores the version.">
      <RefreshCw aria-hidden /> Recalibrating
    </Badge>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          window.setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard blocked */
        }
      }}
      aria-label={label}
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] text-muted transition-colors hover:bg-surface-2 hover:text-ink"
    >
      {done ? <CheckCircle2 className="size-3.5 text-good" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
      {done ? "Copied" : "Copy"}
    </button>
  );
}

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="border-b border-line py-4 first:pt-0 last:border-b-0 last:pb-0">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="font-mono text-[11px] uppercase tracking-[0.12em] text-muted">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-[13px]">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-muted">{k}</dt>
          <dd className="min-w-0 text-ink-2 [overflow-wrap:anywhere]">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

/** One field spec and, for records, its sub-fields, nested. */
function FieldTree({ fields, depth = 0 }: { fields: Record<string, FieldDoc>; depth?: number }) {
  const entries = Object.entries(fields);
  return (
    <ul className={cn("space-y-2", depth > 0 && "mt-2 border-l-2 border-line pl-3")}>
      {entries.map(([path, f]) => {
        const type = typeof f?.type === "string" ? f.type : "exact";
        const options = fieldOptions(f ?? { type });
        return (
          <li key={path} className="rounded-lg border border-line bg-bg-sunken/50 px-3 py-2.5">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="font-mono text-[12.5px] font-semibold text-ink [overflow-wrap:anywhere]">{path}</span>
              <Badge tone="neutral" className="font-mono text-[11.5px]">
                {type}
              </Badge>
              <span className="text-[12px] text-muted">{presence(f ?? { type })}</span>
            </div>
            {TYPE_HINT[type] && <p className="mt-1 text-[12px] text-muted">{TYPE_HINT[type]}</p>}
            {options.length > 0 && (
              <dl className="mt-2 grid grid-cols-[minmax(0,8.5rem)_minmax(0,1fr)] gap-x-3 gap-y-1 text-[12.5px]">
                {options.map((o) => (
                  <div key={o.key} className="contents">
                    <dt className="text-muted">{o.label}</dt>
                    <dd className="min-w-0 font-mono text-[12px] text-ink-2 [overflow-wrap:anywhere]">{o.text}</dd>
                  </div>
                ))}
              </dl>
            )}
            {f?.fields && Object.keys(f.fields).length > 0 && <FieldTree fields={f.fields} depth={depth + 1} />}
          </li>
        );
      })}
    </ul>
  );
}

function Json({ value }: { value: unknown }) {
  return (
    <pre className="max-h-60 overflow-auto rounded-lg border border-line bg-bg-sunken px-3 py-2 font-mono text-[12px] leading-5 text-ink-2">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

const nonEmpty = (o: Record<string, unknown> | undefined) => o != null && Object.keys(o).length > 0;

function TaskView({ task, detail, loading, missing }: { task: TaskEntry; detail: TaskEntry | null; loading: boolean; missing: boolean }) {
  const t = detail ?? task;
  const fields = t.fields ?? {};
  const meta = sourceMeta(t.source);
  return (
    <div>
      {missing && (
        <p role="alert" className="mb-4 flex items-start gap-2 rounded-lg border border-warn/30 bg-warn-soft/70 px-3 py-2 text-[13px] text-ink-2">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden /> This task is no longer registered. Requests that name it follow the
          unknown-task policy.
        </p>
      )}
      {t.description && <p className="mb-4 text-pretty text-[14px] leading-6 text-ink-2">{t.description}</p>}

      <Section title="How answers are compared">
        {Object.keys(fields).length === 0 ? (
          <p className="text-[13px] leading-5 text-ink-2">
            No fields: whole answers are compared ({t.format === "text" ? "the text, without trailing punctuation" : t.format}). Fields that can be checked
            make agreement mean more.
          </p>
        ) : (
          <>
            <p className="mb-2 text-[12.5px] text-muted">
              Format <span className="font-mono text-ink-2">{t.format}</span>. A student answer agrees with the teacher's when every field agrees.
            </p>
            <FieldTree fields={fields} />
          </>
        )}
      </Section>

      <Section title="Routing and data">
        <Facts
          rows={[
            ["Route", <RouteBadge key="r" pinned={t.pinned} />],
            ["Training data", <StorageText key="s" task={t} />],
            ["Tool calls", t.tools === "distill" ? "Distilled (opt-in)" : "Answered by the teacher"],
            ["Who may use it", t.clients.length ? <span className="font-mono text-[12.5px]">{t.clients.join(", ")}</span> : "Every API key"],
            ["Recalibrating", t.recalibrating ? "Yes: the teacher answers it until a night re-scores the live version" : "No"],
          ]}
        />
      </Section>

      <Section title="Traffic, all days">
        <Facts
          rows={[
            ["Requests", <span key="n" className="tabular">{fmtInt(t.requests)}</span>],
            ["Answered by your adapter", t.pinned ? <span className="text-muted">– (pinned)</span> : <span className="tabular">{fmtShare(t.student_share)}</span>],
          ]}
        />
      </Section>

      <Section title="Where it is defined">
        <Facts
          rows={[
            ["Source", <span key="s">{meta.label}</span>],
            ["Spec hash", <span key="h" className="inline-flex items-center gap-1 font-mono text-[12.5px] text-ink">{t.spec_hash} <CopyButton text={t.spec_hash} label="Copy the spec hash" /></span>],
            ...(t.updated_ts != null
              ? ([
                  ["Written", t.created_ts != null ? fmtDateTime(t.created_ts) : "–"],
                  ["Last changed", `${fmtDateTime(t.updated_ts)}${t.updated_by ? ` by ${t.updated_by}` : ""}`],
                ] as [string, ReactNode][])
              : []),
          ]}
        />
        <p className="mt-2 text-[12.5px] leading-5 text-muted">
          {meta.hint} The spec hash covers how answers compare (format, fields, pinned, store, policy, routing, tools), not the description, clients or match
          rules.
          {loading && <Loader2 className="ml-1.5 inline size-3.5 animate-spin" aria-label="Loading the task" />}
        </p>
      </Section>

      {(nonEmpty(t.match) || nonEmpty(t.policy) || nonEmpty(t.routing)) && (
        <Section title="More of the spec">
          <div className="space-y-2.5">
            {nonEmpty(t.match) && (
              <div>
                <p className="mb-1 text-[12.5px] text-muted">Match rules (how a request without a task name may find it)</p>
                <Json value={t.match} />
              </div>
            )}
            {nonEmpty(t.policy) && (
              <div>
                <p className="mb-1 text-[12.5px] text-muted">Policy</p>
                <Json value={t.policy} />
              </div>
            )}
            {nonEmpty(t.routing) && (
              <div>
                <p className="mb-1 text-[12.5px] text-muted">Routing</p>
                <Json value={t.routing} />
              </div>
            )}
          </div>
        </Section>
      )}

      <Section title="Spec as JSON" aside={<CopyButton text={specText(specDocument(t, true))} label="Copy the spec as JSON" />}>
        <Json value={specDocument(t, true)} />
        <p className="mt-1.5 text-[12px] text-muted">What a tasks file holds and PUT /ratchet/tasks/{"{name}"} takes back.</p>
      </Section>
    </div>
  );
}

function AdminKey({ value, onChange, mode }: { value: string; onChange: (v: string) => void; mode: Mode }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="flex items-center gap-1.5 text-[13px] font-medium text-ink">
        <KeyRound className="size-3.5 text-muted" aria-hidden /> Admin key
      </label>
      <div className="mt-1.5 flex gap-2">
        <input
          id={id}
          type="password"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete="off"
          spellCheck={false}
          placeholder={mode === "mock" ? "Optional" : "A key from RATCHET_ADMIN_KEYS"}
          className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-surface px-3 font-mono text-[13px] text-ink placeholder:font-sans placeholder:text-muted focus:border-line-strong"
        />
        {value && (
          <Button size="sm" variant="ghost" className="h-9" onClick={() => onChange("")}>
            Forget
          </Button>
        )}
      </div>
      <p className="mt-1 text-[12px] leading-5 text-muted">
        {mode === "mock" ? "Not needed in mock mode unless RATCHET_ADMIN_KEYS is set. " : ""}Kept in this page's memory only and sent as a Bearer header;
        never stored. Reloading the page forgets it.
      </p>
    </div>
  );
}

type EditResult = { tone: "good" | "bad" | "info"; title: string; errors?: string[] } | null;

function ResultBox({ result }: { result: EditResult }) {
  return (
    <div role="status" aria-live="polite">
      {result && (
        <div
          className={cn(
            "rounded-lg border px-3 py-2.5 text-[13px] leading-5",
            result.tone === "good" ? "border-good/30 bg-good-soft/60" : result.tone === "bad" ? "border-bad/30 bg-bad-soft/70" : "border-line bg-surface-2",
          )}
        >
          <p className="flex items-start gap-2 font-medium text-ink">
            {result.tone === "good" ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-good" aria-hidden />
            ) : result.tone === "bad" ? (
              <XCircle className="mt-0.5 size-4 shrink-0 text-bad" aria-hidden />
            ) : (
              <ShieldCheck className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden />
            )}
            {result.title}
          </p>
          {result.errors && result.errors.length > 0 && (
            <ul className="mt-1.5 space-y-1 pl-6">
              {result.errors.map((err, i) => (
                <li key={i} className="list-disc font-mono text-[12px] text-ink-2 [overflow-wrap:anywhere]">
                  {err}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

interface Shared {
  mode: Mode;
  adminKey: string;
  setAdminKey: (v: string) => void;
  setState: (s: DrawerState) => void;
  /** Registered names and their sources (the editor warns before it would replace or collide with one). */
  taken: Map<string, string>;
  onChanged: () => void;
  onDeleted: (message: string) => void;
  /** While a write is in flight the drawer does not close. */
  setLocked: (v: boolean) => void;
  /** The editor has changes that closing would discard. */
  setDirty: (v: boolean) => void;
}

/** A registered task, read again from GET /ratchet/tasks/{name}; edit and delete for an API task. */
function ViewContent({ task, shared }: { task: TaskEntry; shared: Shared }) {
  const { mode, adminKey, setAdminKey, setState, taken, onChanged, onDeleted, setLocked } = shared;
  const [detail, setDetail] = useState<TaskEntry | null>(null);
  const [loading, setLoading] = useState(true);
  const [missing, setMissing] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .task(task.name)
      .then((t) => !cancelled && setDetail(t))
      .catch((e) => {
        if (!cancelled && e instanceof ApiError && e.status === 404) setMissing(true);
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [task.name]);

  const t = detail ?? task;
  const remove = async () => {
    setDeleting(true);
    setLocked(true);
    setError(null);
    try {
      const r = await api.deleteTask(t.name, adminKey);
      setLocked(false);
      onChanged();
      setState(null);
      onDeleted(`Deleted ${r.deleted}. Its ${fmtInt(r.examples_kept)} stored example${r.examples_kept === 1 ? "" : "s"} stay, for provenance.`);
    } catch (e) {
      setLocked(false);
      setDeleting(false);
      setError(e instanceof ApiError ? adminMessage(e) : "The gateway did not answer.");
    }
  };

  let footer: ReactNode = null;
  if (isEditable(t) && !missing) {
    footer = confirming ? (
      <div className="flex flex-col gap-2.5">
        <p className="text-[13px] text-ink-2">
          Delete <span className="font-mono font-medium text-ink">{t.name}</span>? Its stored examples stay; requests that name it then follow the unknown-task
          policy.
        </p>
        <AdminKey value={adminKey} onChange={setAdminKey} mode={mode} />
        {error && (
          <p role="alert" className="text-[13px] text-bad">
            {error}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="danger" onClick={() => void remove()} disabled={deleting} autoFocus>
            {deleting ? <Loader2 className="animate-spin" aria-hidden /> : <Trash2 aria-hidden />} Yes, delete
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirming(false)} disabled={deleting}>
            Cancel
          </Button>
        </div>
      </div>
    ) : (
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={() => setState({ mode: "edit", name: t.name, text: specText(specDocument(t)), isNew: false })}>
          <FilePenLine aria-hidden /> Edit spec
        </Button>
        <Button size="sm" variant="danger" onClick={() => setConfirming(true)}>
          <Trash2 aria-hidden /> Delete
        </Button>
      </div>
    );
  } else if (!missing) {
    footer = (
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          onClick={() => {
            const draft = extendDraft(t, taken);
            setState({ mode: "edit", name: draft.name, text: specText(draft.spec), isNew: true });
          }}
        >
          <GitBranchPlus aria-hidden /> New task based on this
        </Button>
        <span className="text-[12px] text-muted">{t.source === "file" ? "Edit the tasks file to change this one." : "A built-in task cannot be changed."}</span>
      </div>
    );
  }

  return (
    <>
      <DrawerBody>
        <TaskView task={task} detail={detail} loading={loading} missing={missing} />
      </DrawerBody>
      {footer && <DrawerFooter>{footer}</DrawerFooter>}
    </>
  );
}

/** Create or replace an API task: check it (POST /ratchet/tasks/validate), then save it (PUT /ratchet/tasks/{name}). */
function EditContent({ initial, shared }: { initial: { name: string; text: string; isNew: boolean }; shared: Shared }) {
  const { mode, adminKey, setAdminKey, setState, taken, onChanged, setLocked, setDirty } = shared;
  const [name, setName] = useState(initial.name);
  const [text, setText] = useState(initial.text);
  const dirty = name !== initial.name || text !== initial.text;
  useEffect(() => {
    setDirty(dirty);
  }, [dirty, setDirty]);
  useEffect(() => () => setDirty(false), [setDirty]);
  const [busy, setBusy] = useState<"check" | "save" | null>(null);
  const [result, setResult] = useState<EditResult>(null);
  const nameId = useId();
  const specId = useId();
  const badName = name.length > 0 && !NAME_RE.test(name);
  const owner = initial.isNew ? taken.get(name) : undefined;

  const parsed = (): Record<string, unknown> | null => {
    if (!NAME_RE.test(name)) {
      setResult({ tone: "bad", title: "The task name is not valid.", errors: ["1 to 64 letters, digits, '_', '.' or '-'."] });
      return null;
    }
    const p = parseSpec(text);
    if (!p.ok) {
      setResult({ tone: "bad", title: "The spec does not parse.", errors: [p.error] });
      return null;
    }
    return p.value;
  };

  const run = async (what: "check" | "save") => {
    const spec = parsed();
    if (!spec) return;
    setBusy(what);
    setLocked(true);
    setResult(null);
    try {
      if (what === "check") {
        const r: TaskValidation = await api.validateTask({ ...spec, name }, adminKey);
        if (r.valid) {
          const t = r.tasks[0];
          const replaces = t?.registered ? ` Saving replaces the ${t.registered} task of that name.` : "";
          setResult({ tone: "good", title: `Valid. Spec hash ${t?.spec_hash ?? "–"}.${replaces}` });
        } else {
          setResult({ tone: "bad", title: `${r.errors.length} problem${r.errors.length === 1 ? "" : "s"}; nothing was changed.`, errors: r.errors });
        }
      } else {
        const r = await api.putTask(name, spec, adminKey);
        onChanged();
        if (!r.changed) setResult({ tone: "info", title: "No change: the stored spec is identical." });
        else {
          setLocked(false);
          setDirty(false);
          setState({ mode: "view", task: r.task });
          return;
        }
      }
    } catch (e) {
      const refused = e instanceof ApiError && e.status === 422;
      const title = refused ? "The gateway refused the spec; nothing was changed." : what === "check" ? "Could not check the spec." : "Could not save the task.";
      setResult({ tone: "bad", title, errors: errorsOf(e) });
    } finally {
      setBusy(null);
      setLocked(false);
    }
  };

  return (
    <>
      <DrawerBody>
        <div className="space-y-4">
          <div>
            <label htmlFor={nameId} className="text-[13px] font-medium text-ink">
              Task name
            </label>
            <input
              id={nameId}
              value={name}
              onChange={(e) => setName(e.target.value)}
              readOnly={!initial.isNew}
              data-autofocus={initial.isNew ? "" : undefined}
              spellCheck={false}
              autoComplete="off"
              maxLength={64}
              aria-invalid={badName}
              aria-describedby={`${nameId}-hint`}
              className={cn(
                "mt-1.5 h-9 w-full rounded-lg border border-line bg-surface px-3 font-mono text-[13px] text-ink focus:border-line-strong",
                !initial.isNew && "bg-bg-sunken text-ink-2",
                badName && "border-bad/60",
              )}
            />
            <p id={`${nameId}-hint`} className={cn("mt-1 text-[12px]", badName ? "text-bad" : owner ? "text-warn" : "text-muted")}>
              {badName
                ? "Use 1 to 64 letters, digits, '_', '.' or '-'."
                : owner === "api"
                  ? "An API task has this name: saving replaces its spec."
                  : owner
                    ? `A ${sourceMeta(owner).label.toLowerCase()} task has this name, so the gateway will refuse it. Pick another name.`
                    : initial.isNew
                      ? "Clients send it in the X-Ratchet-Task header."
                      : "Saving replaces this API task's spec."}
            </p>
          </div>

          <div>
            <div className="flex flex-wrap items-baseline justify-between gap-x-3">
              <label htmlFor={specId} className="text-[13px] font-medium text-ink">
                Spec (JSON)
              </label>
              <span className="text-[12px] text-muted">Keys and field types: docs/CONFIGURATION.md#tasks</span>
            </div>
            <textarea
              id={specId}
              value={text}
              onChange={(e) => setText(e.target.value)}
              data-autofocus={initial.isNew ? undefined : ""}
              rows={16}
              spellCheck={false}
              autoCapitalize="off"
              autoCorrect="off"
              className="mt-1.5 block w-full resize-y rounded-lg border border-line bg-bg-sunken px-3 py-2 font-mono text-[12.5px] leading-5 text-ink focus:border-line-strong"
            />
            <p className="mt-1 text-[12px] leading-5 text-muted">
              A changed spec keeps the task on the teacher until a night scores the live version under it. Stored examples are kept either way.
            </p>
          </div>

          <AdminKey value={adminKey} onChange={setAdminKey} mode={mode} />
          <ResultBox result={result} />
        </div>
      </DrawerBody>
      <DrawerFooter>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => void run("save")} disabled={busy != null}>
            {busy === "save" ? <Loader2 className="animate-spin" aria-hidden /> : <Save aria-hidden />} Save task
          </Button>
          <Button size="sm" variant="outline" onClick={() => void run("check")} disabled={busy != null}>
            {busy === "check" ? <Loader2 className="animate-spin" aria-hidden /> : <ShieldCheck aria-hidden />} Check
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setState(null)} disabled={busy != null}>
            Cancel
          </Button>
        </div>
      </DrawerFooter>
    </>
  );
}

/**
 * The task drawer: a registered task's spec and traffic (GET /ratchet/tasks/{name}) and, for API tasks, editing and
 * deleting with an admin key the operator types in (PUT and DELETE /ratchet/tasks/{name}, POST /ratchet/tasks/validate).
 * The key lives in the registry panel's state only.
 */
export function TaskDrawer({ state, ...rest }: { state: DrawerState } & Omit<Shared, "setLocked" | "setDirty">) {
  const [locked, setLocked] = useState(false);
  const [dirty, setDirty] = useState(false);
  const shared: Shared = { ...rest, setLocked, setDirty };
  // Escape, the close button and a click outside all come here: an edit in progress is not thrown away silently.
  const close = () => {
    if (locked) return;
    if (dirty && !window.confirm("Discard the changes to this task spec?")) return;
    rest.setState(null);
  };

  let title: ReactNode = "";
  let subtitle: ReactNode = null;
  let content: ReactNode = null;
  if (state?.mode === "view") {
    const t = state.task;
    title = <span className="font-mono">{t.name}</span>;
    subtitle = (
      <>
        <SourceBadge source={t.source} />
        <RouteBadge pinned={t.pinned} />
        {t.recalibrating && <RecalibratingBadge />}
      </>
    );
    content = <ViewContent key={`view:${t.name}:${t.spec_hash}`} task={t} shared={shared} />;
  } else if (state?.mode === "edit") {
    title = state.isNew ? "Register a task" : <span className="font-mono">Edit {state.name}</span>;
    subtitle = <span>An API task: stored in the ledger and registered at once.</span>;
    content = <EditContent key={`edit:${state.name}:${state.isNew}`} initial={state} shared={shared} />;
  }

  return (
    <Drawer open={state != null} onClose={close} title={title} subtitle={subtitle} wide={state?.mode === "edit"}>
      {content}
    </Drawer>
  );
}
