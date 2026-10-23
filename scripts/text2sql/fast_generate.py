"""Answer the held-out rows with the base model and with the adapter, for measure_adapter.py report.

Same output records as `measure_adapter.py generate`, but:
  - the decoding cache is passed the way this model's forward expects it (`cache_params`). The model's own
    prepare_inputs_for_generation hands it over as `past_key_values`, which its forward ignores, so transformers' generate()
    recomputed the whole prompt for every token;
  - decoding stops when the SQL ends (a ';' followed by a newline, a run of blank lines, or an end token). The trained model
    does not emit its end token, so generate() ran to the token limit with blank lines;
  - every row is written as soon as it is done, and rows already in --out are skipped (resume after an interruption).
Greedy decoding, like the original. Needs the GPU; run it in its own process (not inside the training process).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--golden", required=True)
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16")
    ap.add_argument("--max-new", type=int, default=200)
    ap.add_argument("--limit", type=int, default=0, help="only the first N rows (a test)")
    ap.add_argument("--suffix", default="", help="text added to the end of the last user message (a prompt variant)")
    a = ap.parse_args()
    a.suffix = a.suffix.replace(chr(92) + "n", chr(10))  # a shell passes the two characters backslash and n

    import torch
    from peft import PeftModel
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    rows = [json.loads(line) for line in open(a.golden, encoding="utf-8") if line.strip()]
    if a.limit:
        rows = rows[: a.limit]
    done: set[int] = set()
    if os.path.exists(a.out):
        for line in open(a.out, encoding="utf-8"):
            if line.strip():
                done.add(json.loads(line)["i"])
    print(f"{len(rows)} rows, {len(done)} already done", flush=True)

    tok = AutoTokenizer.from_pretrained(a.base, trust_remote_code=True)
    quant = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=torch.float16,
        bnb_4bit_use_double_quant=True,
        llm_int8_skip_modules=["in_proj", "out_proj", "lm_head"],  # as in training
    )
    base = AutoModelForCausalLM.from_pretrained(
        a.base, trust_remote_code=True, quantization_config=quant, device_map="auto", dtype=torch.float16
    )
    model = PeftModel.from_pretrained(base, a.adapter)
    model.eval()
    dev = next(base.parameters()).device
    shipped_cache = sys.modules[type(base).__module__].HybridMambaAttentionDynamicCache

    class FixedCache(shipped_cache):
        """The shipped cache keeps per-layer lists, but its update methods and the layers expect `conv_kernel_size` and call
        `.device` on the whole list. Same states, with update methods that work on the lists."""

        def __init__(self, config, batch_size, dtype, device):
            super().__init__(config, batch_size, dtype=dtype, device=device)
            self.conv_kernel_size = config.conv_kernel
            self._device = device

        def update_conv_state(self, layer_idx, new_conv_state, cache_init=False):
            if cache_init:
                self.conv_states[layer_idx] = new_conv_state.to(self._device)
            else:
                self.conv_states[layer_idx] = self.conv_states[layer_idx].roll(shifts=-1, dims=-1)
                self.conv_states[layer_idx][:, :, -1] = new_conv_state[:, 0, :].to(self._device)
            return self.conv_states[layer_idx]

        def update_ssm_state(self, layer_idx, new_ssm_state):
            self.ssm_states[layer_idx] = new_ssm_state.to(self._device)
            return self.ssm_states[layer_idx]

    cache_cls = FixedCache

    # The shipped block calls its attention mixer as mixer(hidden_states, cache_position=...), without the cache, so every cached
    # decoding step attended to the newest token alone. Hand the attention layers the current cache.
    live: dict = {"cache": None}

    def with_cache(orig):
        def forward(hidden_states, *args, **kwargs):
            kwargs["past_key_value"] = live["cache"]
            return orig(hidden_states, *args, **kwargs)

        return forward

    n_attn = 0
    for blk in base.backbone.layers:
        if blk.block_type == "attention":
            blk.mixer.forward = with_cache(blk.mixer.forward)
            n_attn += 1
    print(f"{n_attn} attention layers get the cache", flush=True)

    eos = {tok.eos_token_id}
    ge = getattr(base.generation_config, "eos_token_id", None)
    for e in ge if isinstance(ge, (list, tuple)) else [ge]:
        if e is not None:
            eos.add(e)
    eos.discard(None)

    @torch.no_grad()
    def decode(prompt_ids: torch.Tensor) -> dict:
        n = prompt_ids.shape[1]
        cache = cache_cls(base.config, 1, dtype=torch.float16, device=dev)
        live["cache"] = cache
        out = model(input_ids=prompt_ids, cache_params=cache, use_cache=True, cache_position=torch.arange(n, device=dev))
        logits = out.logits[:, -1, :]
        gen: list[int] = []
        probs: list[float] = []
        for t in range(a.max_new):
            p = torch.softmax(logits[0].float(), -1)
            nxt = int(p.argmax())
            probs.append(float(p[nxt]))
            gen.append(nxt)
            if nxt in eos:
                break
            tail = tok.decode(gen[-40:], skip_special_tokens=True)
            if ";\n" in tail or "\n\n\n" in tail:
                break
            out = model(
                input_ids=torch.tensor([[nxt]], device=dev),
                cache_params=cache,
                use_cache=True,
                cache_position=torch.tensor([n + t], device=dev),
            )
            logits = out.logits[:, -1, :]
        return {
            "text": tok.decode(gen, skip_special_tokens=True).rstrip(),
            "mean_prob": sum(probs) / max(len(probs), 1),
            "min_prob": min(probs) if probs else 0.0,
            "tokens_in": int(n),
            "tokens_out": len(gen),
        }

    t0 = time.time()
    n_new = 0
    with open(a.out, "a", encoding="utf-8") as f:
        for i, r in enumerate(rows):
            if i in done:
                continue
            msgs = [dict(m) for m in r["prompt"]]
            if a.suffix:
                for m in reversed(msgs):
                    if m.get("role") == "user":
                        m["content"] = str(m["content"]) + a.suffix
                        break
            ids = tok.apply_chat_template(
                msgs, add_generation_prompt=True, return_tensors="pt", return_dict=True, enable_thinking=False
            )["input_ids"].to(dev)
            with model.disable_adapter():
                b = decode(ids)
            ad = decode(ids)
            f.write(json.dumps({"i": i, "teacher": r["completion"][0]["content"], "base": b, "adapter": ad}) + "\n")
            f.flush()
            n_new += 1
            print(
                f"row {i + 1}/{len(rows)}  {time.time() - t0:.0f}s  adapter {ad['tokens_out']} tok, base {b['tokens_out']} tok",
                flush=True,
            )
    print(f"done: {n_new} rows in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
