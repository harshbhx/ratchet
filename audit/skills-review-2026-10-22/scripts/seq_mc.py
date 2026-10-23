# ruff: noqa
"""Repro script from the 22 Oct 2026 skills review (see ../evaluation.md); quick and unpolished on purpose."""

import math
import random
import sys

sys.path.insert(0, "/home/user/lanes2/main")
from ratchet.stats import log_e_bernoulli

FLOOR = 0.92
MIN = 20
CAP = 400
A = math.log(20)


def run(p, trials=3000, seed=2):
    rng = random.Random(seed)
    out = {"live": 0, "rollback": 0, "inconclusive": 0}
    for _ in range(trials):
        N = S = 0
        res = None
        for st in range(3):
            n = s = 0
            while True:
                x = rng.random() < p
                n += 1
                s += x
                N += 1
                S += x
                if log_e_bernoulli(S, N, FLOOR, "below") >= A:
                    res = "rollback"
                    break
                if n >= MIN and s / n >= FLOOR:
                    res = "adv"
                    break
                if n >= CAP:
                    res = "inconclusive"
                    break
            if res != "adv":
                break
        out["live" if res == "adv" else res] += 1
    return {k: v / trials for k, v in out.items()}


for p in (0.85, 0.88, 0.90, 0.92, 0.95):
    print(p, run(p))
