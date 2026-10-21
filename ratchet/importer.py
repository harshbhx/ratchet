"""Import labelled data into the ledger (FR-13.31, ADR 0091).

    python -m ratchet.importer labels.jsonl --task ticket-triage --source human --attest "Written by our support leads"
    python -m ratchet.importer golden.jsonl --task ticket-triage --source human --attest "..." --golden-only
    python -m ratchet.importer train.jsonl --task ticket-triage --source teacher:nvidia/nemotron-3-super-120b-a12b
    python -m ratchet.importer labels.jsonl --task ticket-triage --source human --attest "..." --dry-run

One JSON object per line (blank lines are skipped), in either format:

- OpenAI's chat fine-tuning format: `{"messages": [...]}` with the answer as the last message (role `assistant`),
  optionally `tools` and `parallel_tool_calls`. Earlier assistant messages are context; only the last one is the label.
  A last message with `weight: 0`, or one that is a tool call, is refused.
- Ratchet's own rows (`nightschool.dataset.write_jsonl`, as in golden.jsonl): `{"task", "messages", "params", "seen",
  "provenance"}`, or the training file Night School writes (`dataset.write_training`, train.jsonl), where `prompt` and
  `completion` stand for `messages` and `chat_template_kwargs` is ignored (FR-8.42). The row's task must be `--task`, and
  its provenance's teacher must be the `--source`, so a teacher's outputs can never come back in as human labels.

`--source human` marks the labels as written or approved by the customer (`licenses.SOURCES`) and needs `--attest`, a
statement of where they came from, which goes into the `import` event with the counts and the file's SHA-256.
`--source teacher:<model>` imports an open teacher's outputs: the model must pass `licenses.require_distillable`, so
`gpt-*`, `o1`/`o3`/`o4`, `claude-*` and `gemini-*` (any id of a closed family) are refused with the clause of their terms
quoted. Every row is validated and normalised as a chat request is, its label is cleaned and checked against the task's
spec, everything is redacted as a teacher answer is (invariant 12), and duplicates are stored once. A pinned task, a
no-store task, an unregistered task, zero retention and a `--client` whose `RATCHET_KEY_LIMITS` entry says `store=0` are
refused before anything is read (invariant 10, FR-13.1).

`--golden-only` stores the rows in the golden split whatever their hash says, flagged `golden_only`, so they are never
trained on, even past FREQUENT (FR-4.50): a human-verified golden set. A row whose prompt may already have been trained
on (a variant in the train split, or seen FREQUENT times) is refused, because it would not be held-out evidence.

`--dry-run` runs the same import against an in-memory copy of the ledger and prints what would happen; the ledger file
is opened read-only, so it is never written, created (a mistyped `--db` is refused) or migrated. Like
`python -m ratchet.admin`, this opens the gateway's ledger file (RATCHET_DB) and may run while the gateway serves.

An imported label is no occurrence of its request: a new example starts at `seen` 0 and a stored one keeps its count,
so an import never pushes a golden prompt past FREQUENT (invariant 3). A label that replaces a teacher's on an existing
training row adds no row, so it does not count toward Night School's new-data gate (`run_night`'s `min_new_examples`);
see the runbook.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, TextIO

from . import feedback, licenses, privacy, tasks, taskspec
from .auth import KeyRing, parse_key_limits, valid_name
from .config import Settings
from .ledger import SCHEMA, STORED_PARAM_KEYS, Ledger
from .openai_compat import TEXT_MODALITY, APIError, Limits, normalize_messages, parse_body, validate_chat_request
from .providers import GenParams, GenPolicy
from .redact import redact

HUMAN_SOURCE = licenses.HUMAN
TEACHER_PREFIX = "teacher:"
MAX_ATTEST_CHARS = 1000
MAX_REPORTED = 20  # problem lines listed in the summary; every one is counted
OPENAI_KEYS = frozenset({"messages", "tools", "parallel_tool_calls"})
RATCHET_KEYS = frozenset({"task", "messages", "params", "seen", "provenance"})
# Night School's training file (`dataset.write_training`): `prompt` + `completion` instead of `messages` (FR-8.42).
TRAINING_KEYS = frozenset({"task", "prompt", "completion", "chat_template_kwargs", "params", "seen", "provenance"})
TEXT_ONLY = frozenset({TEXT_MODALITY})


class ImportRefused(Exception):
    """The whole import is refused before a row is stored: a closed or unknown teacher, a human source without an
    attestation, a task that may not be stored, an unreadable file. The message says why."""


class RowError(ValueError):
    """One row that cannot be imported: `reason` is the category counted in the summary, the message the detail."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason


@dataclass(frozen=True)
class Source:
    """Where the labels come from: `human` (customer-provided labels) or an open teacher's model id."""

    name: str
    license: licenses.TeacherLicense

    @property
    def human(self) -> bool:
        return self.name == HUMAN_SOURCE


@dataclass
class Report:
    """What an import did (or, in a dry run, would do)."""

    task: str
    source: str
    license: str
    golden_only: bool
    dry_run: bool
    file: str
    sha256: str = ""
    rows: int = 0
    inserted: int = 0
    replaced_teacher: int = 0
    replaced_human: int = 0
    duplicates: int = 0  # the ledger already had the example and kept its label
    in_file_duplicates: int = 0  # the same example earlier in this file, with the same label
    in_file_conflicts: int = 0  # the same example earlier in this file, with another label (the first one is kept)
    invalid: Counter[str] = field(default_factory=Counter)
    refused: Counter[str] = field(default_factory=Counter)  # valid rows a rule kept out (feedback.NOT_STORED)
    problems: list[dict[str, Any]] = field(default_factory=list)

    @property
    def stored(self) -> int:
        return self.inserted + self.replaced_teacher + self.replaced_human

    def problem(self, line: int, reason: str, detail: str, counter: Counter[str]) -> None:
        counter[reason] += 1
        if len(self.problems) < MAX_REPORTED:
            self.problems.append({"line": line, "reason": reason, "detail": detail[:300]})

    def as_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "source": self.source,
            "license": self.license,
            "golden_only": self.golden_only,
            "dry_run": self.dry_run,
            "file": self.file,
            "sha256": self.sha256,
            "rows": self.rows,
            "stored": self.stored,
            "inserted": self.inserted,
            "replaced_teacher": self.replaced_teacher,
            "replaced_human": self.replaced_human,
            "duplicates": self.duplicates,
            "in_file_duplicates": self.in_file_duplicates,
            "in_file_conflicts": self.in_file_conflicts,
            "invalid": sum(self.invalid.values()),
            "invalid_by_reason": dict(sorted(self.invalid.items())),
            "refused": sum(self.refused.values()),
            "refused_by_reason": dict(sorted(self.refused.items())),
            "problems": self.problems,
        }


def parse_source(text: str) -> Source:
    """`human`, or `teacher:<model>` for an open teacher in the registry (`licenses.require_distillable`; the registry
    in force, RATCHET_TEACHER_LICENSES included). A model id of a closed family is refused with the clause of its terms
    quoted (`licenses.closed_family`, stricter than the registry's own rule), and so is an unknown model."""
    text = text.strip()
    if text == HUMAN_SOURCE:
        return Source(HUMAN_SOURCE, licenses.SOURCES[HUMAN_SOURCE])
    if not text.startswith(TEACHER_PREFIX) or not text[len(TEACHER_PREFIX) :].strip():
        raise ImportRefused(f"--source must be '{HUMAN_SOURCE}' or '{TEACHER_PREFIX}<model>', not {text!r}")
    model = text[len(TEACHER_PREFIX) :].strip()
    closed = licenses.closed_family(model)
    if closed is licenses.ROUTER:
        raise ImportRefused(
            f"refused: {model!r} is a router alias. {closed.note} Import under the id of the open model that answered "
            "(invariant 1)."
        )
    if closed is not None:
        clause = closed.note or "its terms forbid using outputs to develop competing models"
        raise ImportRefused(
            f'refused: {model!r} belongs to a closed model family. {closed.name}: "{clause}" ({closed.url}). Its outputs '
            "may never train a model here (invariant 1)."
        )
    try:
        return Source(model, licenses.require_distillable(model))
    except licenses.LicenseError as e:
        raise ImportRefused(f"refused: {e}") from None


def check_task(name: str, settings: Settings) -> tasks.Task:
    """The registered task an import writes to, or ImportRefused: an unregistered name (a typo would make a new task),
    a pinned task (invariant 10), a no-store task and zero retention (FR-13.1)."""
    task = tasks.lookup(name) if tasks.valid_name(name) else None
    if task is None:
        listed = ", ".join(sorted(t.name for t in tasks.registered())[:20])
        raise ImportRefused(f"task {name!r} is not registered (registered: {listed}); register it first")
    if task.pinned:
        raise ImportRefused(f"task {name!r} is pinned: its answers are never training examples (invariant 10)")
    opt_out = privacy.store_opt_out(settings, task)
    if opt_out == "global":
        raise ImportRefused("RATCHET_STORE_EXAMPLES=0 (zero retention): no example is stored, imported ones included")
    if opt_out == "task":
        raise ImportRefused(f"task {name!r} is a no-store task (its spec's store: false, or RATCHET_NO_STORE_TASKS)")
    return task


def check_client(client: str | None, settings: Settings) -> None:
    """The API key name an import attributes its rows to (`--client`), or ImportRefused: a name that is no name, or a key whose
    RATCHET_KEY_LIMITS entry says `store=0` (FR-13.1: nothing that key sends is stored, so a row attributed to it, which
    erasure and export would list under it, may not be either). A name that is no configured key is only a label."""
    if client is None:
        return
    if not valid_name(client):
        raise ImportRefused("--client must be an API key name (letters, digits, '_', '.', '-', at most 64)")
    try:
        policy = settings.limit_policy()
        limits = parse_key_limits(policy.key_limits, KeyRing(settings.api_keys).names, policy.key_defaults).get(client)
    except ValueError as e:  # a bad key list or limits spec: the same text the gateway stops at startup with
        raise ImportRefused(f"cannot read the key limits to check --client: {e}") from None
    if limits is not None and not limits.store:
        raise ImportRefused(
            f"--client {client!r} is an API key with store=0 in RATCHET_KEY_LIMITS: nothing it sends is stored, so no row "
            "may be attributed to it either (FR-13.1)"
        )


def check_attest(source: Source, attest: str | None) -> str | None:
    """The attestation, stripped: required (non-empty) for human labels, optional for a teacher's, at most
    MAX_ATTEST_CHARS and on one line, because it goes into an event."""
    text = (attest or "").strip()
    if source.human and not text:
        raise ImportRefused(
            "--source human needs --attest: say where the labels come from and that you own them (it is recorded with the import)"
        )
    if len(text) > MAX_ATTEST_CHARS:
        raise ImportRefused(f"--attest is limited to {MAX_ATTEST_CHARS} characters")
    if any(ord(c) < 32 for c in text):
        raise ImportRefused("--attest must be one line of text")
    return text or None


def _label(last: Any, limits: Limits) -> str:
    """The text of the row's last message, which must be an assistant answer (not a tool call, not weight 0)."""
    if not isinstance(last, dict) or last.get("role") != "assistant":
        raise RowError("no answer", "the last message must be the assistant's answer")
    if last.get("tool_calls") or last.get("function_call"):
        raise RowError("tool call answer", "the last assistant message is a tool call; only text answers are imported")
    if last.get("weight", 1) == 0:
        raise RowError("weight 0", "the last assistant message has weight 0: it is not a training target")
    plain = {k: v for k, v in last.items() if k != "weight"}
    try:
        req = validate_chat_request({"messages": [plain]}, limits)
        (message,), _ = normalize_messages(req.messages, TEXT_ONLY)
    except APIError as e:
        raise RowError("invalid answer", e.message) from None
    content = message.get("content")
    if not isinstance(content, str):
        raise RowError("invalid answer", "the answer has no text")
    return content


def parse_row(obj: Any, task: str, source: Source, limits: Limits) -> tuple[dict[str, Any], str]:
    """(the request body to validate: the prompt messages and their conditions, the label) from one parsed line, or
    RowError. The format is Ratchet's when the row has any of its own keys (`task`, `params`, `seen`, `provenance`), and
    Night School's training file's when `prompt` and `completion` are both lists of messages (TRL's conversational
    prompt-completion rows). OpenAI's legacy `{"prompt": str, "completion": str}` rows are not imported: their keys are
    unknown keys, as before."""
    if not isinstance(obj, dict):
        raise RowError("not an object", "each line must be one JSON object")
    training_format = isinstance(obj.get("prompt"), list) and isinstance(obj.get("completion"), list)
    ratchet_format = training_format or bool(set(obj) & (RATCHET_KEYS - {"messages"}))
    allowed = TRAINING_KEYS if training_format else RATCHET_KEYS if ratchet_format else OPENAI_KEYS
    unknown = sorted(str(k) for k in obj if k not in allowed)
    if unknown:
        raise RowError("unknown keys", f"unknown keys {unknown[:5]}; use {sorted(allowed)}")
    if training_format:
        prompt, completion = obj["prompt"], obj["completion"]
        if len(completion) != 1:
            raise RowError("no answer", "'completion' must hold exactly one message: the assistant's answer")
        messages: Any = prompt + completion
    else:
        messages = obj.get("messages")
    if not isinstance(messages, list) or len(messages) < 2:
        raise RowError("no answer", "'messages' must hold the prompt and, last, the assistant's answer")
    label = _label(messages[-1], limits)
    body: dict[str, Any] = {
        "messages": [{k: v for k, v in m.items() if k != "weight"} if isinstance(m, dict) else m for m in messages[:-1]]
    }
    if ratchet_format:
        if obj.get("task", task) != task:
            raise RowError("task mismatch", f"the row's task is {str(obj.get('task'))[:64]!r}, not {task!r}")
        prov = obj.get("provenance")
        if prov is not None:
            teacher = prov.get("teacher") if isinstance(prov, dict) else None
            if teacher != source.name:
                raise RowError(
                    "provenance mismatch", f"the row's provenance names {str(teacher)[:80]!r}, not the --source {source.name!r}"
                )
        params = obj.get("params")
        if params is not None:
            if not isinstance(params, dict) or not set(params) <= set(STORED_PARAM_KEYS):
                raise RowError("invalid params", f"'params' takes only {list(STORED_PARAM_KEYS)}")
            body.update(params)
    else:
        for key in ("tools", "parallel_tool_calls"):
            if key in obj:
                body[key] = obj[key]
    return body, label


def _lines(stream: BinaryIO, max_bytes: int) -> Iterator[tuple[int, bytes | None]]:
    """(line number, raw line) for each non-blank line; None for a line longer than `max_bytes` (0 = no limit), which is
    read past without being kept."""
    number = 0
    while True:
        limit = max_bytes + 1 if max_bytes > 0 else -1  # the content and its newline
        raw = stream.readline(limit)
        if not raw:
            return
        number += 1
        if max_bytes > 0 and len(raw) > max_bytes and not raw.endswith(b"\n"):
            while raw and not raw.endswith(b"\n"):
                raw = stream.readline(1 << 20)
            yield number, None
            continue
        if raw.strip():
            yield number, raw


def run_import(
    ledger: Ledger,
    settings: Settings,
    stream: BinaryIO,
    *,
    task: str,
    source: str,
    attest: str | None = None,
    golden_only: bool = False,
    client: str | None = None,
    file_name: str = "-",
    dry_run: bool = False,
) -> Report:
    """Import the rows of `stream` into `ledger` (see the module docstring); ImportRefused before any row is read when
    the import as a whole may not run. Writes one `import` event, unless `dry_run`. The registries of tasks and of
    teacher licenses must be installed first (`main` does it)."""
    src = parse_source(source)
    statement = check_attest(src, attest)
    t = check_task(task, settings)
    check_client(client, settings)
    limits = settings.limits()
    policy: GenPolicy = settings.gen_policy()
    report = Report(t.name, src.name, src.license.name, golden_only, dry_run, file_name)
    digest = hashlib.sha256()
    in_file: dict[str, str] = {}  # example hash -> label, for this file
    for number, raw in _lines(_Hashing(stream, digest), settings.max_body_bytes):
        report.rows += 1
        if raw is None:
            report.problem(
                number, "too long", f"the line is longer than RATCHET_MAX_BODY_BYTES ({settings.max_body_bytes})", report.invalid
            )
            continue
        try:
            obj = parse_body(raw)
            body, label = parse_row(obj, t.name, src, limits)
            req = validate_chat_request(body, limits)
            messages, _ = normalize_messages(req.messages, TEXT_ONLY, settings.merge_system)
            prepared = feedback.prepare(
                t,
                messages,
                label,
                params=feedback.conditions(policy, GenParams.from_request(req)),
                redact_pii=settings.redact_pii,
            )
        except RowError as e:
            report.problem(number, e.reason, str(e), report.invalid)
            continue
        except APIError as e:
            reason = "not json" if e.code == "invalid_json" else "invalid messages"
            report.problem(number, reason, e.message, report.invalid)
            continue
        except feedback.LabelError as e:
            report.problem(number, "invalid label", f"not a valid answer for {t.name!r}: {e}", report.invalid)
            continue
        if isinstance(prepared, str):  # 'invalid after redaction'
            report.problem(number, prepared, "a redaction placeholder breaks the label", report.refused)
            continue
        if prepared.hash in in_file:
            if in_file[prepared.hash] == prepared.completion:
                report.in_file_duplicates += 1
            else:
                report.in_file_conflicts += 1
                report.problem(number, "conflict in file", "the same prompt has another label earlier in the file", Counter())
            continue
        in_file[prepared.hash] = prepared.completion
        outcome = feedback.store_label(
            ledger,
            prepared,
            source=src.name,
            settings=settings,
            origin=feedback.IMPORT_ORIGIN,
            client=client,
            golden_only=golden_only,
            bump=False,  # an import is not an occurrence of the request
            replace_same_rank=True,  # an operator's later import wins over an earlier one
        )
        if outcome.inserted:
            report.inserted += 1
        elif outcome.replaced == "teacher":
            report.replaced_teacher += 1
        elif outcome.replaced == "human":
            report.replaced_human += 1
        elif outcome.reason is None:
            report.duplicates += 1
        else:
            report.problem(number, outcome.reason, "kept out by a storage rule", report.refused)
    report.sha256 = digest.hexdigest()
    if not dry_run:
        ledger.event("import", _event(report, statement))
    return report


class _Hashing:
    """A binary stream that hashes what is read from it, so the import event names the exact file."""

    def __init__(self, stream: BinaryIO, digest: Any) -> None:
        self.stream, self.digest = stream, digest

    def readline(self, size: int = -1) -> bytes:
        data = self.stream.readline(size)
        self.digest.update(data)
        return data


def _event(report: Report, attest: str | None) -> str:
    """The `import` event: counts, the source and its license, the file's name and SHA-256 and the attestation (redacted:
    /ratchet/stats shows recent events). Never row text."""
    what = "human labels" if report.source == HUMAN_SOURCE else f"answers of {report.source} ({report.license})"
    held = " (golden only)" if report.golden_only else ""
    said = f"; attested: {redact(attest)!r}" if attest else ""
    return (
        f"{report.stored} of {report.rows} rows imported into {report.task} as {what}{held}: {report.inserted} new, "
        f"{report.replaced_teacher + report.replaced_human} replaced, {report.duplicates + report.in_file_duplicates} "
        f"duplicates, {sum(report.invalid.values())} invalid, {sum(report.refused.values())} refused; file "
        f"{Path(report.file).name} sha256 {report.sha256[:16]}{said}"
    )


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m ratchet.importer", description=__doc__.split("\n\n")[0])
    ap.add_argument("file", help="a JSONL file (OpenAI chat fine-tuning format, or Ratchet's train.jsonl); '-' reads stdin")
    ap.add_argument("--task", required=True, help="the registered task the rows belong to")
    ap.add_argument("--source", required=True, help="'human' (with --attest) or 'teacher:<model>' (an open teacher)")
    ap.add_argument("--attest", help="where the labels come from and that you own them (required with --source human)")
    ap.add_argument("--golden-only", action="store_true", help="hold every row out of training for good (golden split)")
    ap.add_argument("--client", help="the API key name to attribute the rows to (for erasure and export by client)")
    ap.add_argument("--db", help="ledger file (default RATCHET_DB)")
    ap.add_argument("--dry-run", action="store_true", help="import into an in-memory copy and report; write nothing")
    return ap


def _snapshot(path: str, settings: Settings) -> Ledger:
    """An in-memory copy of the ledger file for a dry run: the same rows, so every rule decides as it would. The file is
    opened read-only, so a dry run never creates a file at a mistyped path (ImportRefused) or migrates an older one; the
    copy is brought to the current schema instead, as opening the file would."""
    file = Path(path)
    if not file.is_file():
        raise ImportRefused(f"no ledger file at {path}: a dry run reads an existing ledger and writes nothing")
    copy = Ledger(":memory:", settings.golden_fraction)
    try:
        source = sqlite3.connect(f"{file.resolve().as_uri()}?mode=ro", uri=True)
        try:
            source.backup(copy.db)
        finally:
            source.close()
    except sqlite3.Error as e:
        copy.db.close()
        raise ImportRefused(f"cannot read {path}: {e}") from None
    copy.db.executescript(SCHEMA)
    copy._migrate()
    copy.day = int(
        copy.db.execute(
            "SELECT MAX(COALESCE((SELECT MAX(day) FROM requests), 0), COALESCE((SELECT MAX(day) FROM daily_rollup), 0))"
        ).fetchone()[0]
    )
    return copy


def main(argv: list[str] | None = None, stdin: BinaryIO | None = None, stdout: TextIO | None = None) -> int:
    args = parser().parse_args(argv)
    out = stdout if stdout is not None else sys.stdout
    s = Settings(mode="live")
    licenses.install(s.teacher_licenses_file)  # the gateway's teachers, RATCHET_TEACHER_LICENSES included
    path = args.db or s.db_path
    if args.dry_run:
        try:
            led = _snapshot(path, s)
        except ImportRefused as e:
            raise SystemExit(f"import: {e}") from None
    else:
        led = Ledger(path, s.golden_fraction, auto_day=True)
    try:
        for line in taskspec.install(led, s.tasks_file):
            print(f"task skipped: {line}", file=sys.stderr)
        try:
            if args.file == "-":
                stream = stdin if stdin is not None else sys.stdin.buffer
                report = run_import(led, s, stream, **_options(args), file_name="-")
            else:
                data = Path(args.file)
                try:
                    handle = data.open("rb")
                except OSError as e:
                    raise ImportRefused(f"cannot read {data}: {e.strerror or e}") from None
                with handle:
                    report = run_import(led, s, handle, **_options(args), file_name=str(data))
        except (ImportRefused, ValueError) as e:  # a bad RATCHET_* value is a ValueError too
            raise SystemExit(f"import: {e}") from None
        out.write(json.dumps(report.as_dict(), indent=2, ensure_ascii=False) + "\n")
        if args.dry_run:
            out.write("dry run: nothing was written\n")
        return 0
    finally:
        led.db.close()


def _options(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "task": args.task,
        "source": args.source,
        "attest": args.attest,
        "golden_only": args.golden_only,
        "client": args.client,
        "dry_run": args.dry_run,
    }


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
