# ruff: noqa
"""Repro script from the 22 Oct 2026 skills review (see ../evaluation.md); quick and unpolished on purpose."""

import random
import sys

sys.path.insert(0, "/home/user/lanes2/main")
from ratchet.stats import wilson

FLOOR = 0.92
MIN = 20
CAP = 3000


def stage(p, rng):
    n = s = 0
    while n < CAP:
        n += 1
        s += rng.random() < p
        if n >= MIN:
            lo, hi = wilson(s, n)
            if hi < FLOOR:
                return "rollback", n
            if s / n >= FLOOR:
                return "advance", n
    return "hold", n


def run(p, trials=4000, seed=1):
    rng = random.Random(seed)
    out = {"live": 0, "rollback": 0, "hold": 0}
    aud = []
    for _ in range(trials):
        tot = 0
        for st in range(3):
            r, n = stage(p, rng)
            tot += n
            if r != "advance":
                out[r] += 1
                break
        else:
            out["live"] += 1
        aud.append(tot)
    return {k: v / trials for k, v in out.items()}, sorted(aud)[len(aud) // 2]


for p in (0.80, 0.85, 0.88, 0.90, 0.92, 0.95, 0.97):
    print(p, run(p))
