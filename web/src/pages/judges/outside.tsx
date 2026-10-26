import { useId, useMemo, useState } from "react";
import { Braces, Loader2, Send, TriangleAlert, Wrench, Tags } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { CodeBlock, tok } from "@/components/ui/code-block";
import { RealBadge } from "@/components/ui/real-badge";
import { WhoBadge } from "@/components/ui/who";
import type { ServedBy } from "@/lib/api";
import { cn, fmtMs, fmtUsd } from "@/lib/utils";
import surfaceText from "@/data/api-surface.json?raw";
import { LiveRequest } from "@/pages/landing/sections/live-request";
import { getJson, useGateway, useJudges } from "./data";
import { baseUrl, routeWords } from "./judges-model";
import { REAL } from "./real";
import { Chapter, Ext, Panel, SimLink, Unavailable } from "./ui";

interface Surface {
  levels: { level: string; who: string }[];
  groups: { name: string; routes: { method: string; path: string; level: string; what: string }[] }[];
  error_example: string;
}
const SURFACE: Surface = JSON.parse(surfaceText);

const DOCS = REAL.links.docs.replace(/\/?$/, "/");
const DEFAULT_TEXT = "I was charged twice for my Pro plan this month. Order #48213. Can you fix it?";

interface ChatResult {
  status: number;
  body: Record<string, unknown> | null;
  route?: string | null;
}

function ratchetBlock(body: Record<string, unknown> | null): Record<string, unknown> | null {
  const r = body?.ratchet;
  return r && typeof r === "object" ? (r as Record<string, unknown>) : null;
}

function answerText(body: Record<string, unknown> | null): string {
  const choices = body?.choices;
  if (!Array.isArray(choices) || !choices.length) return "";
  const m = (choices[0] as { message?: { content?: unknown } }).message;
  return typeof m?.content === "string" ? m.content : "";
}

/**
 * Send one chat request to this gateway's /v1/chat/completions and show what came back. On the mock demo: any task, and a
 * bad request. On a live gateway: the landing page's one keyless request (`store: false`, sent once, never retried).
 */
function SendBox() {
  const { stats, mock, addSent, status } = useJudges();
  const [text, setText] = useState(DEFAULT_TEXT);
  const [task, setTask] = useState("ticket-triage");
  const [busy, setBusy] = useState<"ok" | "bad" | null>(null);
  const [res, setRes] = useState<ChatResult | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const textId = useId();
  const taskId = useId();
  const tasks = useMemo(() => {
    const names = new Set<string>(["ticket-triage"]);
    for (const t of stats?.tasks ?? []) if (!t.pinned) names.add(t.task);
    return [...names];
  }, [stats]);

  const send = async (bad: boolean) => {
    setBusy(bad ? "bad" : "ok");
    setFailed(null);
    try {
      const body: Record<string, unknown> = { model: "ratchet", messages: [{ role: "user", content: text.trim() || DEFAULT_TEXT }] };
      if (bad) body.temperature = 3;
      const r = await fetch("/v1/chat/completions", {
        method: "POST",
        headers: { "content-type": "application/json", "X-Ratchet-Task": task },
        body: JSON.stringify(body),
      });
      let json: Record<string, unknown> | null = null;
      try {
        json = (await r.json()) as Record<string, unknown>;
      } catch {
        json = null;
      }
      const rb = ratchetBlock(json);
      let route: string | null = null;
      const rid = typeof rb?.request_id === "string" ? rb.request_id : null;
      if (r.ok && rid) {
        const look = await getJson<{ request?: { route?: string } }>(`/ratchet/requests/${encodeURIComponent(rid)}`);
        if (look.state === "ok") route = look.data.request?.route ?? null;
        addSent({
          requestId: rid,
          task,
          servedBy: String(rb?.served_by ?? ""),
          model: String(json?.model ?? ""),
          route,
          at: Date.now(),
        });
      }
      setRes({ status: r.status, body: json, route });
    } catch {
      setFailed("The gateway did not answer.");
      setRes(null);
    } finally {
      setBusy(null);
    }
  };

  if (!stats) return <Unavailable path="/ratchet/stats" read={{ state: "error", status: 0, message: "" }} />;
  if (!mock)
    return (
      <div className="min-w-0">
        <LiveRequest mode={stats.mode} className="mt-0" />
        <p className="mt-2.5 text-[13px] leading-relaxed text-muted">
          Choosing a task and sending a bad request (temperature 3) run on the <SimLink hash="client">simulation demo</SimLink>, with mock
          models.
        </p>
      </div>
    );

  const rb = ratchetBlock(res?.body ?? null);
  const err = res && res.status >= 400 ? res.body : null;
  return (
    <Panel
      title="Send a request"
      aside={status && <RealBadge status={status} label="Simulation: mock models answer" />}
    >
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_200px]">
        <div className="min-w-0">
          <label htmlFor={textId} className="text-[12.5px] font-medium text-ink-2">
            Message
          </label>
          <textarea
            id={textId}
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={3}
            maxLength={2000}
            className="mt-1.5 block w-full resize-y rounded-xl border border-line-strong bg-bg px-3 py-2 text-[14px] leading-relaxed text-ink placeholder:text-muted focus:border-ink"
          />
        </div>
        <div className="min-w-0">
          <label htmlFor={taskId} className="text-[12.5px] font-medium text-ink-2">
            Task (<code className="font-mono text-[12px]">X-Ratchet-Task</code>)
          </label>
          <select
            id={taskId}
            value={task}
            onChange={(e) => setTask(e.target.value)}
            className="mt-1.5 block h-10 w-full rounded-xl border border-line-strong bg-bg px-3 font-mono text-[13px] text-ink"
          >
            {tasks.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button onClick={() => void send(false)} disabled={busy != null}>
          {busy === "ok" ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <Send aria-hidden />} Send a request
        </Button>
        <Button variant="outline" onClick={() => void send(true)} disabled={busy != null}>
          {busy === "bad" ? <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden /> : <TriangleAlert aria-hidden />} Send a bad one
          (temperature 3)
        </Button>
      </div>

      <div aria-live="polite" className="mt-4 min-w-0">
        {failed && <p className="text-[13.5px] text-bad">{failed}</p>}
        {res && !err && rb && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <WhoBadge servedBy={(String(rb.served_by) as ServedBy) || "teacher"} model={String(res.body?.model ?? "")} />
              <Badge tone="outline">route: {res.route ? routeWords(res.route) : "not recorded"}</Badge>
              {rb.escalated === true && <Badge tone="rented">escalated</Badge>}
            </div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-[13px] sm:grid-cols-4">
              <Field k="served_by" v={String(rb.served_by)} />
              <Field k="route" v={res.route ?? "–"} />
              <Field k="confidence" v={typeof rb.confidence === "number" ? rb.confidence.toFixed(2) : "–"} />
              <Field k="latency" v={typeof rb.latency_ms === "number" ? fmtMs(rb.latency_ms) : "–"} />
              <Field k="cost_usd" v={typeof rb.cost_usd === "number" ? fmtUsd(rb.cost_usd) : "–"} />
              <Field k="all-teacher cost" v={typeof rb.all_teacher_cost_usd === "number" ? fmtUsd(rb.all_teacher_cost_usd) : "–"} />
              <Field k="stored" v={rb.stored === true ? "yes" : "no"} />
              <Field k="task" v={String(rb.task ?? task)} />
            </dl>
            <p className="min-w-0 text-[12.5px] text-muted">
              request_id <code className="font-mono text-ink-2 [overflow-wrap:anywhere]">{String(rb.request_id)}</code>
            </p>
            <div className="rounded-xl border border-line bg-bg-sunken px-3.5 py-3">
              <p className="text-[12px] text-muted">Answer</p>
              <p className="mt-1 whitespace-pre-wrap text-[14px] leading-relaxed text-ink [overflow-wrap:anywhere]">{answerText(res.body)}</p>
            </div>
            <details className="group rounded-xl border border-line">
              <summary className="cursor-pointer px-3.5 py-2.5 text-[13px] font-medium text-ink-2">The response's ratchet block</summary>
              <pre className="max-h-72 overflow-auto border-t border-line px-3.5 py-3 font-mono text-[12px] leading-5 text-ink-2">{JSON.stringify(rb, null, 2)}</pre>
            </details>
          </div>
        )}
        {err && (
          <div className="space-y-2">
            <p className="text-[13.5px] text-ink-2">
              <Badge tone="bad">{res?.status}</Badge> The error comes back in OpenAI's envelope, so an OpenAI SDK raises its usual typed exception. It
              cost nothing.
            </p>
            <pre className="overflow-x-auto rounded-xl border border-line bg-bg-sunken px-3.5 py-3 font-mono text-[12px] leading-5 text-ink-2">
              {JSON.stringify(err, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </Panel>
  );
}

function Field({ k, v }: { k: string; v: string }) {
  return (
    <div className="min-w-0">
      <dt className="font-mono text-[11.5px] text-muted">{k}</dt>
      <dd className="mt-0.5 font-medium tabular-nums text-ink [overflow-wrap:anywhere]">{v}</dd>
    </div>
  );
}

export function ClientChapter() {
  const base = baseUrl(typeof window !== "undefined" ? window.location.origin : "");
  const curl = `curl -s ${base}/chat/completions \\\n  -H 'content-type: application/json' \\\n  -H 'X-Ratchet-Task: ticket-triage' \\\n  -d '{"model":"ratchet","messages":[{"role":"user","content":"I was charged twice this month."}]}'`;
  const py = `from openai import OpenAI\n\nclient = OpenAI(base_url="${base}", api_key="unused")  # the one line that changes\nresp = client.chat.completions.create(\n    model="ratchet",\n    messages=[{"role": "user", "content": "I was charged twice this month."}],\n    extra_headers={"X-Ratchet-Task": "ticket-triage"},\n)\nprint(resp.choices[0].message.content)\nprint(resp.model_extra["ratchet"])  # served_by, confidence, cost_usd, request_id`;
  return (
    <Chapter
      id="client"
      title="Use it from any OpenAI client"
      lead={
        <p>
          Ratchet speaks the OpenAI chat API. An app keeps its client and points the base URL at the gateway; every answer carries a{" "}
          <code className="font-mono text-[14px]">ratchet</code> block that says who answered, at what confidence and cost, with a request id.
        </p>
      }
    >
      <div className="grid min-w-0 gap-4">
        <CodeBlock title="curl" copy={curl} className="min-w-0">
          {curl}
        </CodeBlock>
        <CodeBlock title="Python (OpenAI SDK)" copy={py} className="min-w-0">
          {py.split("\n").map((line, i) => (
            <span key={i}>
              {line.includes("base_url") ? (
                <>
                  {line.slice(0, line.indexOf("base_url"))}
                  {tok.h(`base_url="${base}"`)}
                  {line.slice(line.indexOf("base_url") + `base_url="${base}"`.length)}
                </>
              ) : (
                line
              )}
              {"\n"}
            </span>
          ))}
        </CodeBlock>
      </div>
      <div className="mt-4">
        <SendBox />
      </div>
    </Chapter>
  );
}

/** Access levels are not states or who answered, so they wear no status or owned/rented colour. */
const LEVEL_TONE: Record<string, "neutral" | "outline"> = {
  public: "outline",
  api: "neutral",
  dashboard: "neutral",
  bom: "neutral",
  metrics: "neutral",
  admin: "neutral",
  mock: "outline",
};

export function ApiChapter() {
  const { stats, mock } = useJudges();
  return (
    <Chapter
      id="api"
      title="The API surface"
      lead={
        <p>
          A selection of the routes, each with its one access level (the full table is in the{" "}
          <Ext href={`${DOCS}api/endpoints/`}>API reference</Ext>). Errors on <code className="font-mono text-[14px]">/v1</code> use OpenAI's
          envelope, with the request id.
        </p>
      }
    >
      <div className="grid min-w-0 gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="min-w-0 space-y-4">
          {SURFACE.groups.map((g) => (
            <Panel key={g.name} title={g.name} flush>
              <ul className="divide-y divide-line border-t border-line">
                {g.routes.map((r) => (
                  <li key={`${r.method} ${r.path}`} className="grid min-w-0 gap-x-3 gap-y-1 px-4 py-2.5 sm:grid-cols-[64px_minmax(0,1fr)_88px] sm:items-baseline sm:px-5">
                    <span className="font-mono text-[11.5px] font-semibold text-muted">{r.method}</span>
                    <span className="min-w-0">
                      <code className="font-mono text-[12.5px] text-ink [overflow-wrap:anywhere]">{r.path}</code>
                      <span className="block text-[12.5px] leading-snug text-muted">{r.what}</span>
                    </span>
                    <span className="sm:text-right">
                      <Badge tone={LEVEL_TONE[r.level] ?? "neutral"}>{r.level}</Badge>
                    </span>
                  </li>
                ))}
              </ul>
            </Panel>
          ))}
        </div>
        <div className="min-w-0 space-y-4">
          <Panel title="Access levels">
            <dl className="space-y-2.5">
              {SURFACE.levels.map((l) => (
                <div key={l.level} className="flex min-w-0 items-start gap-2.5">
                  <dt className="w-[78px] shrink-0">
                    <Badge tone={LEVEL_TONE[l.level] ?? "neutral"}>{l.level}</Badge>
                  </dt>
                  <dd className="min-w-0 text-[12.5px] leading-snug text-ink-2">{l.who}</dd>
                </div>
              ))}
            </dl>
          </Panel>
          <Panel title="The error envelope">
            <pre className="overflow-x-auto rounded-lg bg-bg-sunken p-3 font-mono text-[11.5px] leading-5 text-ink-2">{SURFACE.error_example}</pre>
            <p className="mt-2 text-[12.5px] leading-snug text-muted">
              {stats && !mock ? (
                <>
                  Try it on the <SimLink hash="client">simulation demo</SimLink>: "Send a bad one" sends <code className="font-mono">temperature: 3</code>.
                </>
              ) : (
                <>
                  Try it: "Send a bad one" above sends <code className="font-mono">temperature: 3</code>.
                </>
              )}
            </p>
          </Panel>
        </div>
      </div>
    </Chapter>
  );
}

interface TaskEntryLite {
  name: string;
  source: string;
  pinned?: boolean;
  format?: string;
}

export function FeaturesChapter() {
  const { status } = useJudges();
  const reg = useGateway<{ tasks: TaskEntryLite[] }>("/ratchet/tasks");
  const items = [
    {
      icon: Tags,
      title: "Task selection",
      text: "A request names its task in a header, in metadata, or as the model name ratchet/<task>, so clients that can set only a model name work too.",
      href: `${DOCS}api/python/`,
    },
    {
      icon: Braces,
      title: "Structured outputs",
      text: "A response_format is a contract on every answer: a student answer that breaks the JSON schema gets no vote, and a teacher answer that breaks it is never stored.",
      href: `${DOCS}guides/request-journey/`,
    },
    {
      icon: Wrench,
      title: "Tool calls",
      text: "A request that offers tools is answered by the teacher alone, with its tools, and is never stored or used for training.",
      href: `${DOCS}guides/request-journey/`,
    },
  ];
  return (
    <Chapter id="features" title="Tasks, structured outputs and tool calls" lead={<p>Three things a real app sends, one sentence each.</p>}>
      <div className="grid min-w-0 gap-3 md:grid-cols-3">
        {items.map((it) => (
          <div key={it.title} className="min-w-0 rounded-2xl border border-line bg-surface p-4 shadow-card">
            <it.icon className="size-4 text-muted" aria-hidden />
            <h3 className="mt-2.5 text-[15px] font-semibold text-ink">{it.title}</h3>
            <p className="mt-1.5 text-[13.5px] leading-relaxed text-ink-2">{it.text}</p>
            <p className="mt-2.5 text-[13px]">
              <Ext href={it.href}>Read more</Ext>
            </p>
          </div>
        ))}
      </div>
      <div className="mt-4">
        {reg.state === "ok" ? (
          <Panel title="Tasks this gateway knows" aside={status && <RealBadge status={status} />}>
            <ul className="flex flex-wrap gap-1.5">
              {(reg.data.tasks ?? []).map((t) => (
                <li
                  key={t.name}
                  className={cn("inline-flex items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-[12px]", t.pinned ? "border-rented/30 bg-rented-soft text-rented-ink" : "border-line bg-bg-sunken text-ink-2")}
                >
                  {t.name}
                  <span className="font-sans text-[11px] text-muted">{t.pinned ? "pinned to the teacher" : t.source}</span>
                </li>
              ))}
            </ul>
          </Panel>
        ) : reg.state === "error" ? (
          <Unavailable path="/ratchet/tasks" read={reg} />
        ) : null}
      </div>
    </Chapter>
  );
}
