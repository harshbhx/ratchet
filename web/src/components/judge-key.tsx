import { useId, useState, type FormEvent } from "react";
import { Eye, EyeOff, KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { USING_KEY_TEXT, leftText } from "@/lib/judge-key-model";
import { clearJudgeKey, setJudgeKey, useJudgeKey } from "@/lib/use-judge-key";

/**
 * "Have a judges' key?": collapsed to one text link under a request box. It opens to a password field (autocomplete off, with a show/hide
 * button), "Use key", and a "Remember on this device" box (without it the key lasts for this tab only). With a key set it shows one line,
 * "Using your judges' key" with how many calls are left today when the gateway said, and "Forget key". The key is never shown again and never
 * goes in an address. The height animates with the grid-rows trick (instant under prefers-reduced-motion), so nothing jumps.
 */
export function JudgeKey({ left, className }: { left: number | null; className?: string }) {
  const { key, remembered } = useJudgeKey();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [shown, setShown] = useState(false);
  const [remember, setRemember] = useState(remembered);
  const inputId = useId();
  const using = key !== "";
  const count = leftText(left);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!draft.trim()) return;
    setJudgeKey(draft, remember);
    setDraft("");
    setShown(false);
    setOpen(false);
  };
  const forget = () => {
    clearJudgeKey();
    setDraft("");
    setShown(false);
    setRemember(false);
  };

  const link =
    "rounded text-muted underline decoration-line-strong underline-offset-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-owned";

  return (
    <div data-companion-avoid data-judge-key={using ? "set" : open ? "open" : "closed"} className={cn("mt-2 text-[12.5px] leading-snug", className)}>
      {using ? (
        <p role="status" className="flex flex-wrap items-center gap-x-2 gap-y-1 text-muted">
          <KeyRound className="size-3.5 shrink-0 text-good-ink" aria-hidden />
          <span className="text-ink-2">{USING_KEY_TEXT}</span>
          {count && <span>· {count}</span>}
          <button type="button" onClick={forget} className={link}>
            Forget key
          </button>
        </p>
      ) : (
        <>
          <button type="button" aria-expanded={open} aria-controls={`${inputId}-panel`} onClick={() => setOpen((o) => !o)} className={link}>
            Have a judges' key?
          </button>
          <div
            id={`${inputId}-panel`}
            className={cn("grid transition-[grid-template-rows,opacity] duration-300 ease-out motion-reduce:transition-none", open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0")}
            inert={!open}
          >
            <div className="min-h-0 overflow-hidden">
              <form onSubmit={submit} className="flex flex-col gap-2 pt-2.5">
                <div className="flex gap-2">
                  <div className="relative min-w-0 flex-1">
                    <label htmlFor={inputId} className="sr-only">
                      Judges' key
                    </label>
                    <input
                      id={inputId}
                      type={shown ? "text" : "password"}
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      autoComplete="off"
                      autoCapitalize="off"
                      spellCheck={false}
                      className="h-9 w-full rounded-xl border border-line-strong bg-bg pl-3 pr-10 text-[16px] text-ink outline-none focus-visible:border-owned focus-visible:shadow-[0_0_0_4px_var(--owned-soft)] sm:text-[14px]"
                    />
                    <button
                      type="button"
                      onClick={() => setShown((s) => !s)}
                      aria-label={shown ? "Hide key" : "Show key"}
                      aria-pressed={shown}
                      className="absolute inset-y-0 right-0 flex w-9 items-center justify-center rounded-r-xl text-muted hover:text-ink focus-visible:outline-2 focus-visible:outline-owned"
                    >
                      {shown ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
                    </button>
                  </div>
                  <Button type="submit" variant="outline" size="sm" className="h-9 px-3.5" disabled={!draft.trim()}>
                    Use key
                  </Button>
                </div>
                <label className="flex items-center gap-2 text-muted">
                  <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} className="size-3.5 accent-[var(--owned)]" />
                  Remember on this device
                </label>
              </form>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
