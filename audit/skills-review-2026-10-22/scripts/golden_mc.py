# ruff: noqa
"""Repro script from the 22 Oct 2026 skills review (see ../evaluation.md); quick and unpolished on purpose."""

import random
import sys

sys.path.insert(0, "/home/user/lanes2/main")
from ratchet.confidence import calibrate_threshold

# confidence levels as with 3 samples: 0.4p+0.6a ; model: discrete levels with true accuracy
levels = [
    (0.55, 0.10, 0.55),
    (0.70, 0.15, 0.80),
    (0.80, 0.20, 0.90),
    (0.88, 0.25, 0.95),
    (0.95, 0.30, 0.985),
]  # (conf, share, acc)


def true_prec(thr):
    w = [(s, a) for c, s, a in levels if c >= thr]
    if not w:
        return None
    return sum(s * a for s, a in w) / sum(s for s, _ in w)


for c, _, _ in levels:
    print("thr", c, "true precision", round(true_prec(c), 4))


def draw(n, rng):
    out = []
    for _ in range(n):
        r = rng.random()
        acc = 0
        for c, s, a in levels:
            acc += s
            if r < acc:
                out.append((c, rng.random() < a))
                break
    return out


for N in (100, 150, 300, 1000):
    rng = random.Random(1)
    passes = 0
    tp = []
    served_true = []
    for _ in range(4000):
        cal = draw(N // 2, rng)
        test = draw(N // 2, rng)
        thr = max(0.5, min(1.01, calibrate_threshold(cal, 0.97)))
        srv = [ok for c, ok in test if c >= thr]
        prec = sum(srv) / len(srv) if srv else 0
        cov = len(srv) / len(test)
        p = true_prec(thr)
        if prec >= 0.95 and cov >= 0.25:
            passes += 1
            served_true.append(p)
    served_true.sort()
    below = sum(1 for p in served_true if p < 0.95) / max(1, len(served_true))
    print(
        f"golden n={N}: pass rate {passes / 4000:.2f}; among passes, true served precision <0.95 in {below:.0%}; median true {served_true[len(served_true) // 2]:.3f}"
    )
