# ruff: noqa
"""Repro script from the 22 Oct 2026 skills review (see ../evaluation.md); quick and unpolished on purpose."""

import math
import random
import sys

sys.path.insert(0, "/home/user/lanes2/main")
from ratchet import tasks
from ratchet.nightschool.evaluate import cascade_eval
from ratchet.providers import Completion

print(tasks.get("foo").name)
rng = random.Random(3)
rows = []
plan = {}
# task A ('default'): easy, conf 0.98 and right. Task B ('foo'): conf p uniform; correct with prob depending on conf.
for i in range(800):
    task = "default" if rng.random() < 0.5 else "foo"
    msgs = [{"role": "user", "content": f"q{i}"}]
    ref = '{"a": %d}' % i
    if task == "default":
        p = rng.choice([0.6, 0.7, 0.8, 0.9])
        ok = rng.random() < {0.6: 0.5, 0.7: 0.7, 0.8: 0.9, 0.9: 0.995}[p]
    else:
        p = rng.choice([0.6, 0.7, 0.8, 0.9])
        ok = rng.random() < {0.6: 0.96, 0.7: 0.97, 0.8: 0.98, 0.9: 0.99}[p]
    plan[f"q{i}"] = (p, ok, ref)
    rows.append({"task": task, "messages": msgs + [{"role": "assistant", "content": ref}]})


def sample(prompt, n, params):
    p, ok, ref = plan[prompt[0]["content"]]
    text = ref if ok else '{"a": -1}'
    return [
        Completion(
            text=text, model="m", in_tokens=1, out_tokens=1, latency_ms=1, token_logprobs=[math.log(p)], finish_reason="stop"
        )
        for _ in range(n)
    ]


ev = cascade_eval(sample, rows, samples=3, target_precision=0.97)
print(
    "global thr",
    ev["threshold"],
    "precision",
    round(ev["precision"], 4),
    "cert",
    ev["certificate"]["served"],
    ev["certificate"]["errors"],
    round(ev["certificate"]["lcb"], 4),
    ev["certificate"]["certified"],
)
for name, rec in ev["tasks"].items():
    print(
        name, "thr", rec["threshold"], "prec", round(rec["precision"], 4), "cov", rec["coverage"], "calibrated", rec["calibrated"]
    )
# precision as served at per-task thresholds on test half:
# Recompute what the router would serve on the test half at per-task thresholds
test = []
j = 0
for r in rows:
    p, ok, ref = plan[r["messages"][0]["content"]]
    conf = 0.4 * p + 0.6
    if j % 2 == 1:
        test.append((r["task"], conf, ok))
    j += 1
served = [ok for t, c, ok in test if c >= ev["tasks"][t]["threshold"]]
print(
    "as served at per-task thresholds: served",
    len(served),
    "errors",
    len(served) - sum(served),
    "precision",
    round(sum(served) / len(served), 4),
)
from ratchet.stats import cp_lower

print("lcb as served", round(cp_lower(sum(served), len(served), 0.05), 4))
