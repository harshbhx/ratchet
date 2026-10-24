import { useEffect, useRef, useState, type FormEvent } from "react";
import { KeyRound, LoaderCircle, ShieldCheck } from "lucide-react";
import { api, type ScoutStatus } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { JUDGE_OFF_ANNOUNCE, JUDGE_ON_ANNOUNCE, parseJudge, runsLeftText, signOutError, unlockError } from "./judge-model";

/**
 * "I am a judge": a small disclosure under the ask bar that unlocks the judges' reserve (ADR 0120). Shown only when
 * /scout/status says judge access is on. The code lives in the input's own state, goes out once in the body of
 * POST /scout/judge and is cleared right after; it is never stored, logged or put in a URL (invariant 18's rule).
 */
export function JudgeCode({ status, running, onChange }: { status: ScoutStatus | null; running: boolean; onChange: () => Promise<void> | void }) {
  const judge = parseJudge(status);
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);
  const toggleRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  if (!judge.enabled) return null;

  const close = () => {
    setOpen(false);
    setCode("");
    setError(null);
    toggleRef.current?.focus();
  };

  const unlock = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const sent = code.trim();
    if (!sent) {
      setError(unlockError(400));
      inputRef.current?.focus();
      return;
    }
    setBusy(true);
    setError(null);
    const http = await api.judgeUnlock(sent);
    setCode("");
    if (http === 200) {
      setOpen(false);
      setNote(JUDGE_ON_ANNOUNCE);
      await onChange();
    } else {
      setError(unlockError(http));
      inputRef.current?.focus();
    }
    setBusy(false);
  };

  const signOut = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    const http = await api.judgeSignOut();
    if (http >= 200 && http < 300) {
      setNote(JUDGE_OFF_ANNOUNCE);
      await onChange();
    } else {
      setError(signOutError(http));
    }
    setBusy(false);
  };

  return (
    <div className="mt-3 text-[13.5px] sm:text-center" data-testid="judge-code">
      {/* The one live region: results of unlock and sign-out, and errors. */}
      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {error ?? note}
      </p>

      {judge.session ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 sm:justify-center">
          <span className="inline-flex items-center gap-1.5 font-medium text-good">
            <ShieldCheck className="size-4" aria-hidden /> Judge access on
          </span>
          {judge.runsLeft !== null && <span className="text-muted tabular">{runsLeftText(judge.runsLeft)}</span>}
          <Button type="button" variant="ghost" size="sm" onClick={() => void signOut()} disabled={busy || running}>
            {busy && <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden />} Sign out
          </Button>
        </div>
      ) : (
        <>
          <button
            ref={toggleRef}
            type="button"
            aria-expanded={open}
            aria-controls="judge-code-form"
            onClick={() => (open ? close() : setOpen(true))}
            className="inline-flex min-h-8 items-center gap-1.5 rounded-md px-1 text-muted underline decoration-line-strong underline-offset-4 transition-colors hover:text-ink"
          >
            <KeyRound className="size-3.5" aria-hidden /> I am a judge
          </button>
          {open && (
            <form id="judge-code-form" onSubmit={(e) => void unlock(e)} className="mt-2 flex flex-wrap items-center gap-2 sm:justify-center" noValidate>
              <label htmlFor="judge-code-input" className="text-ink-2">
                Judge code
              </label>
              <input
                ref={inputRef}
                id="judge-code-input"
                name="judge-code"
                type="password"
                value={code}
                onChange={(e) => setCode(e.target.value.slice(0, 200))}
                onKeyDown={(e) => {
                  if (e.key === "Escape") close();
                }}
                autoComplete="off"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                disabled={busy}
                aria-invalid={error ? true : undefined}
                aria-describedby={error ? "judge-code-error" : undefined}
                className="h-10 min-w-0 flex-1 rounded-full border border-line-strong bg-surface px-4 text-[16px] text-ink outline-none placeholder:text-muted focus-visible:border-owned focus-visible:shadow-[0_0_0_4px_var(--owned-soft)] sm:w-56 sm:flex-none"
              />
              <Button type="submit" size="md" variant="outline" disabled={busy}>
                {busy && <LoaderCircle className="animate-spin motion-reduce:animate-none" aria-hidden />} Unlock
              </Button>
            </form>
          )}
        </>
      )}
      {error && (
        <p id="judge-code-error" className="mt-2 font-medium text-bad">
          {error}
        </p>
      )}
    </div>
  );
}
