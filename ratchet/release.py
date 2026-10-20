"""Release gate: candidate -> shadow -> canary 10% -> canary 50% -> live, with automatic rollback.

What matters for a cascade is not raw accuracy but the quality of what the student
chooses to serve, so every gate looks at two numbers:
  - precision: how often the answers it would serve agree with the teacher
  - coverage:  how much traffic it would serve (= how much money it saves)

Rules, in order:
  1. Golden gate (after training): precision >= min_precision and coverage >= min_coverage
     on the held-out golden set, using the version's own calibrated threshold, and no
     regression against the live version on either number.
  2. Shadow: it scores real traffic silently. Disagreements with the live student are
     arbitrated by the teacher; one the teacher could not arbitrate counts as a disagreement.
     Promote when precision and coverage hold on real traffic.
  3. Canary 10% -> 50% -> 100%: the teacher audits a sample of what it serves.
  4. Rollback: if the 95% upper confidence bound of audited precision falls below the
     floor (strong evidence it is worse), traffic returns to the previous live version.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, fields

from .ledger import Ledger, Version

CANARY_STEPS = [10, 50]


def wilson(successes: float, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a proportion."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass
class ReleasePolicy:
    min_precision: float = 0.95
    min_coverage: float = 0.25
    tolerance: float = 0.02
    min_shadow: int = 100  # shadow answers it would have served
    min_audits: int = 20
    live_floor: float = 0.92  # audited precision floor for canary/live

    @classmethod
    def from_env(cls) -> ReleasePolicy:
        """Override any field with RATCHET_<FIELD> (e.g. RATCHET_MIN_AUDITS=30)."""
        kw = {}
        for f in fields(cls):
            raw = os.environ.get(f"RATCHET_{f.name.upper()}")
            if raw is not None:
                kw[f.name] = int(raw) if f.type in (int, "int") else float(raw)
        return cls(**kw)


class ReleaseGate:
    def __init__(self, ledger: Ledger, policy: ReleasePolicy | None = None):
        self.ledger = ledger
        self.policy = policy or ReleasePolicy()
        self._alerted: set[int] = set()  # live versions already alerted on (one alert per version)

    # ---- step 1: after training ----
    def admit(self, vid: int) -> bool:
        p, v = self.policy, self.ledger.version(vid)
        assert v is not None
        prec, cov = v.eval.get("precision", 0.0), v.eval.get("coverage", 0.0)
        live = self.ledger.live()
        if prec < p.min_precision:
            return self._reject(v, f"golden precision {prec:.1%} < {p.min_precision:.0%}")
        if cov < p.min_coverage:
            return self._reject(v, f"golden coverage {cov:.1%} < {p.min_coverage:.0%}")
        if live:
            lp, lc = live.eval.get("precision", 0.0), live.eval.get("coverage", 0.0)
            if prec < lp - p.tolerance or cov < lc - p.tolerance:
                return self._reject(
                    v, f"regresses vs live v{live.id} (precision {prec:.1%} vs {lp:.1%}, coverage {cov:.1%} vs {lc:.1%})"
                )
        for old in self.ledger.versions(["shadow", "canary"]):
            self.ledger.set_version(old.id, "retired", 0)
            self.ledger.event("retired", f"v{old.id} replaced by newer candidate v{v.id}")
        if live is None:
            # Cold start: nothing to shadow against, so a small canary audited by the teacher.
            self.ledger.set_version(v.id, "canary", CANARY_STEPS[0])
            self.ledger.event(
                "canary", f"v{v.id} first student: golden precision {prec:.1%}, coverage {cov:.1%}; canary {CANARY_STEPS[0]}%"
            )
        else:
            self.ledger.set_version(v.id, "shadow", 0)
            self.ledger.event(
                "shadow", f"v{v.id} passed golden gate (precision {prec:.1%}, coverage {cov:.1%}); shadowing live v{live.id}"
            )
        return True

    def _reject(self, v: Version, why: str) -> bool:
        self.ledger.set_version(v.id, "rejected", 0)
        self.ledger.event("rejected", f"v{v.id}: {why}")
        return False

    # ---- steps 2-4: call periodically (e.g. every few minutes in production) ----
    def tick(self) -> None:
        p = self.policy
        live = self.ledger.live()

        if live:
            n, prec = self.ledger.version_live_quality(live.id)
            if live.id not in self._alerted and n >= p.min_audits and prec is not None and wilson(prec * n, n)[1] < p.live_floor:
                self._alerted.add(live.id)
                self.ledger.event(
                    "alert",
                    f"live v{live.id} audited precision {prec:.1%} (n={n}) below floor; "
                    "investigate or roll back manually (see docs/RUNBOOK.md)",
                )

        shadow = self.ledger.by_state("shadow")
        if shadow:
            s = self.ledger.shadow_quality(shadow.id)
            if s["served"] >= p.min_shadow:
                live_cov = s["live_coverage"] if s["live_coverage"] is not None else 0.0
                # Disagreements the teacher could not arbitrate already count against the candidate; say so.
                unarbitrated = f" ({s['unarbitrated']} unarbitrated counted as disagreements)" if s["unarbitrated"] else ""
                if s["precision"] < p.min_precision:
                    self._reject(shadow, f"shadow precision {s['precision']:.1%} < {p.min_precision:.0%}{unarbitrated}")
                elif s["coverage"] < live_cov - p.tolerance:
                    self._reject(shadow, f"shadow coverage {s['coverage']:.1%} < live {live_cov:.1%}")
                else:
                    self.ledger.set_version(shadow.id, "canary", CANARY_STEPS[0])
                    self.ledger.event(
                        "canary",
                        f"v{shadow.id} shadow precision {s['precision']:.1%}{unarbitrated}, coverage "
                        f"{s['coverage']:.1%} (live {live_cov:.1%}); canary {CANARY_STEPS[0]}%",
                    )

        canary = self.ledger.by_state("canary")
        if canary:
            n, prec = self.ledger.version_live_quality(canary.id)
            if n >= p.min_audits and prec is not None:
                lo, hi = wilson(prec * n, n)
                if hi < p.live_floor:
                    self.ledger.set_version(canary.id, "rolled_back", 0)
                    back = f"live v{live.id}" if live else "the teacher"
                    self.ledger.event(
                        "rollback",
                        f"v{canary.id} audited precision {prec:.1%} (n={n}, upper bound "
                        f"{hi:.1%} < {p.live_floor:.0%}); traffic back to {back}",
                    )
                    return
                if prec < p.live_floor:
                    return  # not enough evidence either way yet: hold this stage, keep auditing
                nxt = [s for s in CANARY_STEPS if s > canary.traffic_pct]
                if nxt:
                    self.ledger.set_version(canary.id, "canary", nxt[0])
                    self.ledger.event("canary", f"v{canary.id} audited precision {prec:.1%} (n={n}); canary {nxt[0]}%")
                else:
                    if live:
                        self.ledger.set_version(live.id, "retired", 0)
                    self.ledger.set_version(canary.id, "live", 100)
                    self.ledger.event("live", f"v{canary.id} promoted to 100% (audited precision {prec:.1%}, n={n})")
