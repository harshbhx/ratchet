"""LoRA fine-tune of the student. Built to run inside a Nebius Serverless Job (see deploy/train_job.sh); scripted and
tested offline.

Usage (inside the job container):
    python -m ratchet.nightschool.train --data /data/night/train.jsonl \
        --base nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 --out /data/adapters/student-v3

On a Nebius job the bucket mount refuses the in-place rename safetensors uses (EPERM): deploy/train_job.sh passes a local
--out (/tmp/adapter-out) and copies the finished folder into /data/adapters/<version>. Do not point --out at the mount.

Transformers + PEFT + TRL, at the exact versions deploy/Dockerfile.train installs (deploy/train-versions.txt). Unsloth
is not used: its release of Oct 19, 2026 requires `trl<=1.15.0`, and the image pins TRL 1.16.0 (ADR 0145).

The rows are TRL's conversational prompt-completion format with `chat_template_kwargs` (reasoning off), written by
`dataset.write_training` (FR-8.42). TRL renders the prompt with `add_generation_prompt=True` and prompt+answer with the
same arguments, and computes the loss on the answer only. When the first is not a prefix of the second it only logs a
warning and trains on a context the student is never served. So this script checks every row before the model loads
(`check_rows`) and every rendering with the real tokenizer before training (`check_prefix`), and stops on a mismatch.

It runs in the training image, which has no httpx: from the rest of the package it imports only `notices` (standard
library and `ratchet.licenses`, for NOTICE.md), and the GPU libraries only inside `main`.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .notices import label_sources, sources, write_notices

# The chat-template arguments the student is served with: equal to `dataset.TRAIN_TEMPLATE_KWARGS`, which every row
# carries, and to the endpoint's `--default-chat-template-kwargs` (deploy/student_endpoint.sh). A copy, because this
# file imports nothing from the package but `notices`; tests/test_nightschool.py keeps the three equal.
TEMPLATE_KWARGS: Mapping[str, Any] = MappingProxyType({"enable_thinking": False})
# What Nemotron's chat template looks for in an assistant turn (`dataset.THINK_TAGS`): with either in the answer it does
# not add the empty think block, so the answer would not follow the served generation prompt.
THINK_TAGS = ("<think>", "</think>")
# The packages versions.json records: every one deploy/train-versions.txt pins (a test keeps the two equal).
LIBRARIES = (
    "torch",
    "transformers",
    "trl",
    "peft",
    "accelerate",
    "datasets",
    "causal-conv1d",
    "mamba-ssm",
    "einops",
    "ninja",
    "packaging",
    "setuptools",
    "wheel",
)
# LoRA targets for Nemotron-H (attention, Mamba in_proj/out_proj, MLP). The default is attention and MLP only: what the
# measured adapters used (evidence/text2sql-round*/adapter/adapter_config.json). TARGETS_WITH_MAMBA adds the Mamba
# projections (`--targets`), which vLLM's runtime LoRA must be checked to load and apply before an adapter with them
# is served (scripts/day1_check.py student).
TARGETS_WITH_MAMBA = "q_proj,k_proj,v_proj,o_proj,up_proj,down_proj,in_proj,out_proj"
TARGETS_ATTENTION_MLP = "q_proj,k_proj,v_proj,o_proj,up_proj,down_proj"

# render(messages, add_generation_prompt, chat_template_kwargs) -> text: the tokenizer's apply_chat_template in `main`,
# the vendored template in tests.
Render = Callable[[list[dict[str, Any]], bool, Mapping[str, Any]], str]


class TemplateMismatch(ValueError):
    """A row whose rendered prompt is not where its answer starts, or not the prompt the student is served: training on
    it would teach the student a context it never sees."""


def load_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _row_problem(row: Any) -> str | None:
    """Why a row cannot be trained on the prompt the student is served, naming the field and never the text; or None."""
    if not isinstance(row, dict):
        return "not an object"
    prompt, completion = row.get("prompt"), row.get("completion")
    if prompt is None and "messages" in row:
        return "has `messages` instead of `prompt` and `completion` (an older Night School wrote it: rebuild train.jsonl)"
    if not (isinstance(prompt, list) and prompt and all(isinstance(m, dict) and isinstance(m.get("role"), str) for m in prompt)):
        return "`prompt` is not a non-empty list of messages"
    if not (isinstance(completion, list) and len(completion) == 1 and isinstance(completion[0], dict)):
        return "`completion` is not one message"
    content = completion[0].get("content")
    if completion[0].get("role") != "assistant" or not isinstance(content, str) or not content.strip():
        return "`completion` is not an assistant message with text"
    if any(tag in content for tag in THINK_TAGS):
        return "the answer holds a literal think tag"
    if row.get("chat_template_kwargs") != dict(TEMPLATE_KWARGS):
        return f"`chat_template_kwargs` is not {dict(TEMPLATE_KWARGS)}"
    prov = row.get("provenance")
    if not (isinstance(prov, dict) and prov.get("teacher") and prov.get("license")):
        return "`provenance` lacks the teacher or the license"
    return None


def check_rows(rows: Sequence[Any]) -> None:
    """Stop before the model loads on the first row that would not train on the served prompt: ValueError naming the row
    and the field, never its text (the job's logs keep what it prints)."""
    if not rows:
        raise ValueError("no training rows")
    for i, row in enumerate(rows):
        problem = _row_problem(row)
        if problem is not None:
            raise ValueError(f"row {i}: {problem}")


def check_prefix(rows: Sequence[Mapping[str, Any]], render: Render, served: Sequence[Mapping[str, Any]] | None = None) -> None:
    """Render each row as TRL will (the prompt with the generation prompt, then prompt and answer together, both with the
    row's `chat_template_kwargs`) and raise TemplateMismatch on the first whose prompt is not a prefix of the whole.

    `served`, when given, are the same rows as written: each prompt must then also equal the one rendered from the
    written messages with TEMPLATE_KWARGS, which is what vLLM renders for the request. `main` passes the rows as the
    datasets library hands them to TRL (Arrow gives every message the keys any message has, as None) against the file."""
    if served is not None and len(served) != len(rows):
        raise TemplateMismatch(f"{len(rows)} rows to train against {len(served)} written")
    for i, row in enumerate(rows):
        kwargs = row.get("chat_template_kwargs") or {}
        prompt = render(list(row["prompt"]), True, kwargs)
        full = render(list(row["prompt"]) + list(row["completion"]), False, kwargs)
        if not full.startswith(prompt):
            raise TemplateMismatch(
                f"row {i}: the prompt rendered with add_generation_prompt=True is not a prefix of the prompt and answer "
                "rendered together, so the student would learn the answer after a prompt it is never served"
            )
        if served is not None and prompt != render(list(served[i]["prompt"]), True, TEMPLATE_KWARGS):
            raise TemplateMismatch(f"row {i}: the prompt TRL renders differs from the one the student endpoint renders")


def columns(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """What TRL gets: each row's `prompt`, `completion` and `chat_template_kwargs`, so no stored condition (`params`)
    reaches the template, and TRL's completion-only loss covers the teacher's answer and nothing before it."""
    return [
        {"prompt": r["prompt"], "completion": r["completion"], "chat_template_kwargs": r["chat_template_kwargs"]} for r in rows
    ]


def manifest(base: str, rows: list[dict]) -> dict:
    """`ratchet_manifest.json`, saved next to the adapter: its base model, the teachers and licenses it learned from, and
    the chat-template arguments it was trained under."""
    return {
        "base_model": base,
        "examples": len(rows),
        "provenance": sorted({(r["provenance"]["teacher"], r["provenance"]["license"]) for r in rows}),
        "chat_template_kwargs": dict(TEMPLATE_KWARGS),
    }


def write_metadata(out: str | Path, base: str, rows: list[dict]) -> list[Path]:
    """The files saved next to the adapter weights: `ratchet_manifest.json` and NOTICE.md, which carries the base model's
    license and each teacher's (NFR-9). The job runs with the built-in license registry only, so a teacher added in
    RATCHET_TEACHER_LICENSES shows its URL as not recorded here; live Night School writes NOTICE.md again from the
    adapter's BOM, with every URL, and syncs it into the same folder."""
    folder = Path(out)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "ratchet_manifest.json"
    path.write_text(json.dumps(manifest(base, rows), indent=2, default=list))
    return [path, write_notices(folder, base, sources(rows), label_sources=label_sources(rows))]


def library_versions(lookup: Callable[[str], str] = importlib.metadata.version) -> dict[str, str | None]:
    """The installed version of each package in LIBRARIES, None when it is missing."""
    out: dict[str, str | None] = {}
    for name in LIBRARIES:
        try:
            out[name] = lookup(name)
        except importlib.metadata.PackageNotFoundError:
            out[name] = None
    return out


def versions(
    lookup: Callable[[str], str] = importlib.metadata.version,
    cuda: str | None = None,
    python: str | None = None,
) -> dict[str, Any]:
    """`versions.json`, saved next to the adapter: Python, the CUDA version torch was built for and the libraries, so a
    copy of the adapter says what trained it. Compare it with deploy/train-versions.txt. It is not part of the signed
    BOM, which takes only the sections in `bom.EXTRA_KEYS` and is never rewritten (INV-14)."""
    return {"python": python or platform.python_version(), "cuda": cuda, "libraries": library_versions(lookup)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Ratchet Night School: LoRA fine-tune")
    ap.add_argument("--data", required=True)
    ap.add_argument("--base", default="nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16")
    ap.add_argument("--out", required=True)
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--max-seq", type=int, default=2048)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument(
        "--targets",
        default=TARGETS_ATTENTION_MLP,
        help="comma-separated LoRA target modules, or 'all-linear'. Nemotron-H mixes attention, Mamba (in_proj/out_proj) "
        "and MLP layers; the default, attention and MLP, is what the measured adapters used. To add the Mamba "
        f"projections: {TARGETS_WITH_MAMBA}.",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - needs a GPU
    a = parse_args(argv)
    rows = load_rows(a.data)
    check_rows(rows)  # seconds after the job starts, not after the model has loaded
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    import torch  # type: ignore
    from datasets import Dataset  # type: ignore
    from peft import LoraConfig, get_peft_model  # type: ignore
    from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore
    from trl import SFTConfig, SFTTrainer  # type: ignore

    # Written first, so a job that fails still says what it ran with.
    (out / "versions.json").write_text(json.dumps(versions(cuda=torch.version.cuda), indent=2))
    tok = AutoTokenizer.from_pretrained(a.base, trust_remote_code=True)

    def render(messages: list[dict[str, Any]], add_generation_prompt: bool, kwargs: Mapping[str, Any]) -> str:
        return tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=add_generation_prompt, **kwargs)

    ds = Dataset.from_list(columns(rows))
    check_prefix(list(ds), render, served=rows)  # what TRL will tokenize, against what vLLM renders for the request

    model = AutoModelForCausalLM.from_pretrained(a.base, trust_remote_code=True, dtype="auto", device_map="auto")
    targets = a.targets if a.targets == "all-linear" else a.targets.split(",")
    model = get_peft_model(model, LoraConfig(r=a.rank, lora_alpha=a.rank * 2, task_type="CAUSAL_LM", target_modules=targets))
    trainer = SFTTrainer(
        model=model,
        processing_class=tok,
        train_dataset=ds,
        args=SFTConfig(
            output_dir=str(out / "ckpt"),
            num_train_epochs=a.epochs,
            learning_rate=a.lr,
            per_device_train_batch_size=a.batch,
            max_length=a.max_seq,
            logging_steps=10,
            save_strategy="no",
            bf16=True,
            # The loss covers the completion (the teacher's answer and its end-of-turn token) only: TRL's default for
            # prompt-completion rows, set so a change of default cannot train the prompt. The template has no
            # {% generation %} markers, so assistant_only_loss stays off.
            completion_only_loss=True,
            report_to=[],
        ),
    )
    trainer.train()
    model.save_pretrained(str(out))
    tok.save_pretrained(str(out))
    write_metadata(out, a.base, rows)  # ratchet_manifest.json and NOTICE.md next to the weights
    print(f"saved adapter to {out}")


if __name__ == "__main__":  # pragma: no cover
    main()
