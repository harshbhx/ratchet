"""One-off driver (VM-only, not committed): run tonight's evaluation + gate on the adapter that tonight's job already trained
and saved (adapters/student-v1 in the bucket), WITHOUT training again. Everything else is the stock `ratchet.live night`."""

import functools

from ratchet import live
from ratchet.nightschool import orchestrate


class ReuseTrainer(live.NebiusTrainer):
    def train(self, train_rows, base_model, name):
        # the adapter student-v1 is already in the bucket and loaded in the endpoint; load it again (no-op if loaded)
        live.load_adapter(self.s.student_base, self.s.student_api_key, "student-v1", f"{self.adapter_root}/student-v1", self.http)
        print("ReuseTrainer: no training; reusing adapter student-v1 (trained 2026-10-28, job 3)", flush=True)
        return "student-v1"


live.NebiusTrainer = ReuseTrainer
live.run_night = functools.partial(orchestrate.run_night, min_new_examples=0)
live.main(["night"])
