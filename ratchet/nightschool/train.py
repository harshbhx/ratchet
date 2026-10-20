"""LoRA fine-tune of the student. Runs inside a Nebius Serverless Job (see deploy/train_job.sh).

Usage (inside the job container):
    python -m ratchet.nightschool.train --data /data/night/train.jsonl \
        --base nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16 --out /data/adapters/student-v3

Uses Unsloth when available (fast, low VRAM), else plain transformers + peft.
Nemotron 3 Nano is a hybrid Mamba-Transformer model: pin library versions that support it
and verify on day 1 (see docs/DAY1.md).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def manifest(base: str, rows: list[dict]) -> dict:
    """`ratchet_manifest.json`, saved next to the adapter: its base model and the teachers and licenses it learned from."""
    return {
        "base_model": base,
        "examples": len(rows),
        "provenance": sorted({(r["provenance"]["teacher"], r["provenance"]["license"]) for r in rows}),
    }


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
        default="q_proj,k_proj,v_proj,o_proj,up_proj,down_proj,in_proj,out_proj",
        help="comma-separated LoRA target modules, or 'all-linear'. Nemotron-H mixes attention, "
        "Mamba (in_proj/out_proj) and MLP layers: confirm module names on day 1 with "
        "print(model) and drop names that do not exist.",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - needs a GPU
    a = parse_args(argv)
    rows = load_rows(a.data)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    try:
        from unsloth import FastLanguageModel  # type: ignore

        model, tok = FastLanguageModel.from_pretrained(a.base, max_seq_length=a.max_seq, load_in_4bit=False)
        targets = a.targets if a.targets == "all-linear" else a.targets.split(",")
        model = FastLanguageModel.get_peft_model(
            model,
            r=a.rank,
            lora_alpha=a.rank * 2,
            lora_dropout=0.0,
            target_modules=targets,
        )
    except ImportError:
        from peft import LoraConfig, get_peft_model  # type: ignore
        from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore

        tok = AutoTokenizer.from_pretrained(a.base, trust_remote_code=True)
        model = AutoModelForCausalLM.from_pretrained(a.base, trust_remote_code=True, torch_dtype="auto", device_map="auto")
        targets = a.targets if a.targets == "all-linear" else a.targets.split(",")
        model = get_peft_model(model, LoraConfig(r=a.rank, lora_alpha=a.rank * 2, task_type="CAUSAL_LM", target_modules=targets))

    from datasets import Dataset  # type: ignore
    from trl import SFTConfig, SFTTrainer  # type: ignore

    ds = Dataset.from_list([{"messages": r["messages"]} for r in rows])
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
            assistant_only_loss=True,
            report_to=[],
        ),
    )
    trainer.train()
    model.save_pretrained(str(out))
    tok.save_pretrained(str(out))
    (out / "ratchet_manifest.json").write_text(json.dumps(manifest(a.base, rows), indent=2, default=list))
    print(f"saved adapter to {out}")


if __name__ == "__main__":  # pragma: no cover
    main()
