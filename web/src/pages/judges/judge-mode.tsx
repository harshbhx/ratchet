import { useState } from "react";
import { Link } from "react-router";
import { KeyRound, LoaderCircle } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { WhoBadge } from "@/components/ui/who";
import { JudgeKey } from "@/components/judge-key";
import { errorText } from "@/pages/landing/try-request-model";
import { keyErrorText, keyHeaders, remainingFromHeaders } from "@/lib/judge-key-model";
import { clearJudgeKey, useJudgeKey } from "@/lib/use-judge-key";
import { gatewayFetch } from "@/lib/waking-store";
import { DOSSIER_LINE, MODE_BADGE, NO_KEY_LINE, TESTS, budgetLine, costLine, panelState, parseJudgeResult, type JudgeResult, type JudgeTest } from "./judge-mode-model";

type Run = { kind: "sending" } | { kind: "done"; result: JudgeResult } | { kind: "error"; message: string };

/**
 * Judge mode: with a judges' key stored, a panel near the top of /judges with what the key allows and three real, one-click tests. Without a key,
 * one line that opens the same key control. The page itself is public either way; the key stays in this browser (see judge-key-model.ts).
 */
export function JudgeMode({ mode }: { mode: string | null }) {
  const { key } = useJudgeKey();
  const [left, setLeft] = useState<number | null>(null);
  const [runs, setRuns] = useState<Partial<Record<JudgeTest["id"], Run>>>({});
  const state = panelState(mode, key);
  if (state === "none") return null;
  if (state === "invite") return <JudgeKey left={null} label={NO_KEY_LINE} className="mt-4 text-[13px]" />;

  const busy = Object.values(runs).some((r) => r.kind === "sending");
  const run = async (t: JudgeTest) => {
    if (busy) return;
    setRuns((r) => ({ ...r, [t.id]: { kind: "sending" } }));
    try {
      const r = await gatewayFetch("/v1/chat/completions", {
        method: "POST",
        headers: { "content-type": "application/json", ...keyHeaders(key) },
        body: JSON.stringify(t.body),
      });
      setLeft(remainingFromHeaders(r.headers));
      const body = await r.json().catch(() => null);
      if (!r.ok) {
        setRuns((s) => ({ ...s, [t.id]: { kind: "error", message: keyErrorText(r.status, body) ?? errorText(r.status) } }));
        return;
      }
      const result = parseJudgeResult(body);
      setRuns((s) => ({ ...s, [t.id]: result ? { kind: "done", result } : { kind: "error", message: "The gateway returned no answer this time." } }));
    } catch {
      setRuns((s) => ({ ...s, [t.id]: { kind: "error", message: errorText(0) } }));
    }
  };

  return (
    <section data-judge-mode aria-label="Judge mode" className="mt-6 rounded-2xl border border-line bg-surface p-5 shadow-card sm:p-6">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <Badge tone="good">
          <KeyRound aria-hidden /> {MODE_BADGE}
        </Badge>
        <button
          type="button"
          onClick={() => {
            clearJudgeKey();
            setRuns({});
            setLeft(null);
          }}
          className="rounded text-[12.5px] text-muted underline decoration-line-strong underline-offset-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-owned"
        >
          Forget key
        </button>
      </div>
      <p className="mt-2 text-[13.5px] leading-relaxed text-ink-2" role="status">
        {budgetLine(left)}
      </p>
      <div className="mt-4 flex flex-wrap gap-2.5">
        {TESTS.map((t) => (
          <Button key={t.id} type="button" variant="outline" size="md" disabled={busy} onClick={() => void run(t)}>
            {runs[t.id]?.kind === "sending" && <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden />}
            {t.label}
          </Button>
        ))}
        <Link
          to="/scout"
          className="inline-flex h-10 items-center rounded-full border border-line-strong bg-surface px-4 text-sm font-medium text-ink hover:bg-surface-2"
        >
          Run Scout
        </Link>
      </div>
      <div aria-live="polite" className="mt-1">
        {TESTS.map((t) => {
          const r = runs[t.id];
          if (!r || r.kind === "sending") return null;
          return (
            <div key={t.id} data-judge-result={t.id} className="mt-4 border-t border-line pt-3.5">
              <p className="text-[12.5px] font-medium text-muted">{t.label}</p>
              {r.kind === "error" ? (
                <p className="mt-1 text-[14px] text-ink-2">{r.message}</p>
              ) : (
                <>
                  <p className="mt-1 whitespace-pre-wrap break-words text-[14.5px] leading-relaxed text-ink">{r.result.text}</p>
                  <p className="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1.5 text-[12.5px] text-muted">
                    {r.result.servedBy ? (
                      <>
                        Answered by <WhoBadge servedBy={r.result.servedBy} model={r.result.model} route={r.result.route} />
                      </>
                    ) : (
                      "The reply did not say which model answered."
                    )}
                    <span>· {costLine(r.result.costUsd)}</span>
                  </p>
                </>
              )}
            </div>
          );
        })}
      </div>
      <p className="mt-4 text-[12.5px] text-muted">{DOSSIER_LINE}</p>
    </section>
  );
}
