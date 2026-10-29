"""The one-command Nebius path (deploy/nebius/nebius.sh), run offline.

A fake `nebius` (fakes.FakeNebius), a fake `ssh` and `jq`, and a stub smoke check come first on PATH, so nothing reaches
Nebius or a VM and nothing costs money. The tests assert what a dry run prints and that it changes and calls nothing, the
exact sequence of `nebius` and `ssh` calls of a (fake) real run, and every refusal. They check the commands against the CLI
reference as read; a real account is the first real run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from fakes import FakeNebius

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
SCRIPT = ROOT / "deploy" / "nebius" / "nebius.sh"
STUDENT_KEY = "student-token-0123456789abcdef"
NEBIUS_KEY = "tf-nebius-secret-0123456789"
API_KEYS = "judge:api-secret-0123456789"
CONFIRM = {"RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY": "1"}

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


class Rig:
    """The fakes for one test, and a way to run the script with only these variables set."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.nebius = FakeNebius(tmp / "nebius")
        bin_dir = tmp / "extra-bin"
        bin_dir.mkdir()
        self.ssh_log = tmp / "ssh-calls.jsonl"
        self.smoke_log = tmp / "smoke-calls.jsonl"
        ssh_py = bin_dir / "ssh.py"
        ssh_py.write_text(
            "import json, sys\n"
            f"with open({str(self.ssh_log)!r}, 'a', encoding='utf-8') as f:\n"
            "    data = '' if sys.stdin.isatty() else sys.stdin.read()\n"
            "    f.write(json.dumps({'argv': sys.argv[1:], 'stdin': data}) + '\\n')\n",
            encoding="utf-8",
        )
        self._launcher(bin_dir / "ssh", f'exec "{sys.executable}" -I "{ssh_py}" "$@"')
        self._launcher(bin_dir / "jq", "echo 198.51.100.9")  # create_vm.sh reads the public IP with jq
        smoke_py = bin_dir / "smoke.py"
        smoke_py.write_text(
            f"import json, sys\nwith open({str(self.smoke_log)!r}, 'a', encoding='utf-8') as f:\n"
            "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n",
            encoding="utf-8",
        )
        self._launcher(bin_dir / "smoke", f'exec "{sys.executable}" -I "{smoke_py}" "$@"')
        self.smoke_cmd = (bin_dir / "smoke").as_posix()
        key = tmp / "id_ed25519.pub"
        key.write_text("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyForTests test@example\n", encoding="utf-8")
        self.key = key
        self.bin = bin_dir
        # The compute, VPC and storage calls a real run makes, answered the way the CLI reference says they answer.
        n = self.nebius
        n.respond(["iam", "whoami"], "{}")
        n.respond(["vpc", "subnet", "list"], "vpcsubnet-e00test")
        n.respond(["compute", "instance", "get-by-name"], "", 1)  # no VM yet
        n.respond(["compute", "disk", "create"], "computedisk-e00test")
        n.respond(["compute", "instance", "create"], "computeinstance-e00test")
        n.respond(["compute", "instance", "get"], "{}")
        n.respond(["storage", "bucket", "create"], "storagebucket-e00test")
        n.respond(["ai", "endpoint", "get"], "ratchet-student.fixture.invalid")

    @staticmethod
    def _launcher(path: Path, body: str) -> None:
        path.write_text(f"#!/usr/bin/env bash\n{body}\n", encoding="utf-8")
        path.chmod(0o755)

    def env(self, **extra: str) -> dict[str, str]:
        base = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}",
            "HOME": str(self.tmp),
            "RATCHET_PYTHON": sys.executable,
            "RATCHET_DB": str(self.tmp / "ledger.db"),
            "RATCHET_SMOKE_CMD": self.smoke_cmd,
            "SSH_PUBLIC_KEY_PATH": str(self.key),
        }
        return self.nebius.env({**base, **extra})

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        assert BASH is not None
        return subprocess.run(
            [BASH, str(SCRIPT), *args],
            cwd=self.tmp,
            env=self.env(**extra),
            capture_output=True,
            text=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
        )

    @property
    def ssh(self) -> list[dict[str, object]]:
        if not self.ssh_log.exists():
            return []
        return [json.loads(line) for line in self.ssh_log.read_text(encoding="utf-8").splitlines() if line]

    @property
    def smoke(self) -> list[list[str]]:
        if not self.smoke_log.exists():
            return []
        return [json.loads(line) for line in self.smoke_log.read_text(encoding="utf-8").splitlines() if line]


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def _shown(r: subprocess.CompletedProcess[str]) -> list[str]:
    return [ln[2:] for ln in r.stderr.splitlines() if ln.startswith("+ ")]


def _head(calls: list[list[str]], n: int = 3) -> list[list[str]]:
    return [c[:n] for c in calls]


@pytest.mark.req("FR-11.1")
def test_plan_prints_every_command_and_changes_nothing(rig: Rig):
    """`plan` works with no variables set, prints every command of the whole path in order, says it is scripted and
    tested offline, never calls the fake (or any) nebius, ssh or smoke check, and shows no secret."""
    r = rig.run("plan", "--with-student", "--with-training", "--live", RATCHET_TRAIN_IMAGE="cr.example/ratchet-train:1")
    assert r.returncode == 0, r.stderr
    assert rig.nebius.calls == [] and rig.ssh == [] and rig.smoke == []
    shown = _shown(r)
    heads = [" ".join(c.split()[:3]) for c in shown]
    order = [
        "nebius vpc subnet",
        "nebius compute disk",
        "nebius compute instance",
        "nebius storage bucket",
        "nebius ai endpoint",  # the lookup
        "nebius ai endpoint",  # create --dry-run
        "nebius ai job",
        "nebius ai endpoint",  # get the address
        "ssh -o StrictHostKeyChecking=accept-new",
    ]
    at = -1
    for head in order:
        at = heads.index(head, at + 1)
    assert any(c.startswith("nebius ai endpoint create") and "--dry-run" in c for c in shown)
    assert any("set_env.sh RATCHET_STUDENT_BASE" in c for c in shown) and any("update.sh" in c for c in shown)
    assert any(c.endswith("--mode live") for c in shown)
    assert "SCRIPTED AND TESTED OFFLINE" in r.stderr and "DRY RUN" in r.stderr
    assert "estimate" in r.stderr and "$1.35 an hour" in r.stderr and "stop" in r.stderr.lower()
    assert "bash deploy/student_endpoint.sh stop" in r.stderr and "nebius.sh down --yes" in r.stderr
    assert "Needed before a real run" in r.stderr and "NEBIUS_PROJECT_ID" in r.stderr
    secrets = (STUDENT_KEY, NEBIUS_KEY, API_KEYS)
    r2 = rig.run(
        "plan", "--with-student", "--live", RATCHET_STUDENT_KEY=STUDENT_KEY, NEBIUS_API_KEY=NEBIUS_KEY, RATCHET_API_KEYS=API_KEYS
    )
    assert r2.returncode == 0 and not any(s in r2.stdout + r2.stderr for s in secrets)
    assert "ExampleKeyForTests" not in r.stdout + r.stderr  # a dry run never prints your key


@pytest.mark.req("FR-11.1")
def test_up_without_yes_is_a_dry_run_even_when_confirmed(rig: Rig):
    """`up` is `plan` until it has both --yes and the confirmation variable: neither alone creates anything."""
    for args, extra in (((), {}), ((), CONFIRM), (("--with-student",), CONFIRM), (("--yes",), {"DRY_RUN": "1", **CONFIRM})):
        r = rig.run("up", *args, NEBIUS_PROJECT_ID="project-e00test", RATCHET_STUDENT_KEY=STUDENT_KEY, **extra)
        assert r.returncode == 0 and "DRY RUN" in r.stderr, (args, extra, r.stderr)
        assert rig.nebius.calls == [] and rig.ssh == [] and rig.smoke == []
    r = rig.run("down", NEBIUS_PROJECT_ID="project-e00test", **CONFIRM)
    assert r.returncode == 0 and "DRY RUN" in r.stderr and rig.nebius.calls == []
    assert "nebius compute instance delete --id <vm-id>" in r.stderr


@pytest.mark.req("FR-11.1")
def test_every_refusal_happens_before_any_call(rig: Rig):
    base = {"NEBIUS_PROJECT_ID": "project-e00test", **CONFIRM}
    cases = [
        (("up", "--yes"), {"NEBIUS_PROJECT_ID": "project-e00test"}, 2, "RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1"),
        (("down", "--yes"), {"NEBIUS_PROJECT_ID": "project-e00test"}, 2, "RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1"),
        (("up", "--yes", "--with-student"), {**base, "RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY": "yes"}, 2, "COSTS_MONEY=1"),
        (("up", "--yes", "--with-training"), base, 2, "--with-training needs --with-student"),
        (("up", "--bogus"), base, 2, "unknown option"),
        (("explode",), base, 2, "usage:"),
        ((), base, 2, "usage:"),
        (("up", "--yes"), {**base, "DRY_RUN": "true"}, 2, "DRY_RUN must be 0 or 1"),
        (("up", "--yes"), {**base, "NEBIUS_PROJECT_ID": ""}, 1, "Set NEBIUS_PROJECT_ID"),
        (("up", "--yes", "--with-student"), base, 1, "Set RATCHET_STUDENT_KEY"),
        (
            ("up", "--yes", "--with-student", "--with-training"),
            {**base, "RATCHET_STUDENT_KEY": STUDENT_KEY},
            1,
            "Set RATCHET_TRAIN_IMAGE",
        ),
        (("up", "--yes", "--live"), base, 1, "Set NEBIUS_API_KEY"),
        (("up", "--yes", "--live"), {**base, "NEBIUS_API_KEY": NEBIUS_KEY}, 1, "Set RATCHET_API_KEYS"),
        (("up", "--yes"), {**base, "NEBIUS_REGION": "eu-west1"}, 1, "exists only in eu-north1"),
        (("up", "--yes"), {**base, "SSH_PUBLIC_KEY_PATH": str(rig.tmp / "missing.pub")}, 1, "SSH public key not found"),
    ]
    for args, extra, code, message in cases:
        r = rig.run(*args, **extra)
        assert r.returncode == code and message in r.stderr, (args, extra, r.returncode, r.stderr)
        assert rig.nebius.calls == [] and rig.ssh == [], (args, extra)
    # A CLI that is not signed in stops the run after the one check, before anything is created.
    rig.nebius.fail(["iam", "whoami"])
    r = rig.run("up", "--yes", **base)
    assert r.returncode == 1 and "not signed in" in r.stderr
    assert rig.nebius.calls == [["iam", "whoami", "--format", "json"]]


@pytest.mark.req("FR-11.1")
def test_a_real_run_makes_these_calls_in_this_order(rig: Rig):
    """`up --yes --with-student --live`: preflight, the VM (disk, then instance), the bucket, the endpoint (start, found
    by name), its address, then the VM's .env through ssh with every value on stdin, update.sh, and the smoke check.
    Neither a key nor the token appears in a command line, in the output or in an ssh argument."""
    r = rig.run(
        "up",
        "--yes",
        "--with-student",
        "--live",
        NEBIUS_PROJECT_ID="project-e00test",
        RATCHET_STUDENT_KEY=STUDENT_KEY,
        NEBIUS_API_KEY=NEBIUS_KEY,
        RATCHET_API_KEYS=API_KEYS,
        **CONFIRM,
    )
    assert r.returncode == 0, r.stderr
    calls = rig.nebius.calls
    assert _head(calls) == [
        ["iam", "whoami", "--format"],
        ["vpc", "subnet", "list"],  # preflight
        ["compute", "instance", "get-by-name"],  # create_vm.sh: no VM of that name yet
        ["vpc", "subnet", "list"],
        ["compute", "disk", "create"],
        ["compute", "instance", "create"],
        ["compute", "instance", "get"],
        ["storage", "bucket", "create"],
        ["ai", "endpoint", "list"],
        ["ai", "endpoint", "start"],
        ["ai", "endpoint", "list"],
        ["ai", "endpoint", "get"],
    ], calls
    disk = next(c for c in calls if c[:3] == ["compute", "disk", "create"])
    assert disk[disk.index("--name") + 1] == "ratchet-host-boot" and "--parent-id" in disk
    instance = next(c for c in calls if c[:3] == ["compute", "instance", "create"])
    assert instance[instance.index("--boot-disk-existing-disk-id") + 1] == "computedisk-e00test"
    start = next(c for c in calls if c[:3] == ["ai", "endpoint", "start"])
    assert start == ["ai", "endpoint", "start", "--id", "aiendpoint-e00fixturestudent00"]  # the STOPPED fixture endpoint
    assert not any(c[:2] == ["ai", "job"] for c in calls)  # no training without --with-training

    ssh = rig.ssh
    argvs = [" ".join(str(a) for a in s["argv"]) for s in ssh]
    assert all("ubuntu@198.51.100.9" in a for a in argvs)
    assert "cloud-init status --wait" in argvs[0]
    env_calls = [(a.rsplit(" ", 1)[1], s["stdin"]) for a, s in zip(argvs, ssh, strict=True) if "set_env.sh" in a]
    assert env_calls == [
        ("RATCHET_STUDENT_BASE", "https://ratchet-student.fixture.invalid/v1"),
        ("RATCHET_STUDENT_KEY", STUDENT_KEY),
        ("NEBIUS_API_KEY", NEBIUS_KEY),
        ("RATCHET_API_KEYS", API_KEYS),
        ("RATCHET_MODE", "live"),
    ]
    assert "/opt/ratchet/deploy/vm/update.sh --skip-backup --no-probe" in argvs[-1]
    assert rig.smoke == [["https://198.51.100.9.sslip.io", "--mode", "live"]]  # create_vm.sh's own domain reading
    everything = r.stdout + r.stderr + " ".join(" ".join(c) for c in calls) + " ".join(argvs)
    assert STUDENT_KEY not in everything
    for secret in (NEBIUS_KEY, API_KEYS):
        assert secret not in everything
    token_args = [c for c in calls if STUDENT_KEY in " ".join(c)]
    assert token_args == []  # `start` needs no token; `create` would pass it to --token and echo <RATCHET_STUDENT_KEY>
    assert "bash deploy/student_endpoint.sh stop" in r.stderr and "nebius.sh down --yes" in r.stderr
    assert "Student:     https://ratchet-student.fixture.invalid/v1" in r.stderr


@pytest.mark.req("FR-11.1")
def test_a_cpu_only_run_creates_no_gpu_and_wires_nothing(rig: Rig):
    r = rig.run("up", "--yes", NEBIUS_PROJECT_ID="project-e00test", **CONFIRM)
    assert r.returncode == 0, r.stderr
    assert _head(rig.nebius.calls, 2) == [
        ["iam", "whoami"],
        ["vpc", "subnet"],
        ["compute", "instance"],
        ["vpc", "subnet"],
        ["compute", "disk"],
        ["compute", "instance"],
        ["compute", "instance"],
    ]
    assert not any(c[0] in ("ai", "storage") for c in rig.nebius.calls)
    assert [" ".join(str(a) for a in s["argv"]) for s in rig.ssh][0].endswith("cloud-init status --wait") and len(rig.ssh) == 1
    assert rig.smoke == [["https://198.51.100.9.sslip.io", "--mode", "mock"]]
    assert "STOP THE GPU" not in r.stderr


@pytest.mark.req("FR-11.1")
def test_training_is_opt_in_and_uses_the_buckets_dataset(rig: Rig):
    r = rig.run(
        "up",
        "--yes",
        "--with-student",
        "--with-training",
        NEBIUS_PROJECT_ID="project-e00test",
        RATCHET_STUDENT_KEY=STUDENT_KEY,
        RATCHET_TRAIN_IMAGE="cr.example/ratchet-train:1",
        RATCHET_BUCKET_ID="storagebucket-given",
        RATCHET_TRAIN_VERSION="student-v7",
        **CONFIRM,
    )
    assert r.returncode == 0, r.stderr
    calls = rig.nebius.calls
    assert not any(c[:3] == ["storage", "bucket", "create"] for c in calls)  # a given bucket is reused
    (job,) = [c for c in calls if c[:3] == ["ai", "job", "create"]]
    assert job[job.index("--name") + 1] == "ratchet-student-v7"
    assert job[job.index("--volume") + 1] == "storagebucket-given:/data" and "--dry-run" not in job
    assert calls.index(next(c for c in calls if c[:3] == ["ai", "endpoint", "start"])) < calls.index(job)


@pytest.mark.req("FR-11.1")
def test_a_failed_step_stops_everything_after_it(rig: Rig):
    rig.nebius.fail(["compute", "disk", "create"])
    r = rig.run("up", "--yes", "--with-student", NEBIUS_PROJECT_ID="project-e00test", RATCHET_STUDENT_KEY=STUDENT_KEY, **CONFIRM)
    assert r.returncode == 1 and "create_vm.sh failed" in r.stderr
    assert not any(c[0] in ("ai", "storage") or c[:3] == ["compute", "instance", "create"] for c in rig.nebius.calls)
    assert rig.ssh == [] and rig.smoke == []


@pytest.mark.req("FR-11.1")
def test_down_stops_the_endpoint_first_then_deletes_the_vm_and_disk(rig: Rig):
    listing = rig.nebius.fixture()
    listing["items"][1]["status"]["state"] = "RUNNING"
    rig.nebius.listing(listing)
    rig.nebius.respond(["compute", "instance", "get-by-name"], "computeinstance-e00test")
    rig.nebius.respond(["compute", "disk", "get-by-name"], "computedisk-e00test")
    r = rig.run("down", "--yes", NEBIUS_PROJECT_ID="project-e00test", **CONFIRM)
    assert r.returncode == 0, r.stderr
    calls = rig.nebius.calls
    stop_at = next(i for i, c in enumerate(calls) if c[:3] == ["ai", "endpoint", "stop"])
    delete_at = next(i for i, c in enumerate(calls) if c[:3] == ["compute", "instance", "delete"])
    assert stop_at < delete_at
    assert ["ai", "endpoint", "stop", "--id", "aiendpoint-e00fixturestudent00"] in calls
    assert calls[-2:] == [
        ["compute", "instance", "delete", "--id", "computeinstance-e00test"],
        ["compute", "disk", "delete", "--id", "computedisk-e00test"],
    ]
    assert "Not deleted" in r.stderr and "bucket" in r.stderr


@pytest.mark.req("FR-11.1")
def test_down_stops_a_running_endpoint_and_refuses_to_delete_if_it_cannot(rig: Rig):
    listing = rig.nebius.fixture()
    listing["items"][1]["status"]["state"] = "RUNNING"
    rig.nebius.listing(listing)
    rig.nebius.respond(["compute", "instance", "get-by-name"], "computeinstance-e00test")
    rig.nebius.respond(["compute", "disk", "get-by-name"], "computedisk-e00test")
    rig.nebius.fail(["ai", "endpoint", "stop"])
    r = rig.run("down", "--yes", NEBIUS_PROJECT_ID="project-e00test", **CONFIRM)
    assert r.returncode == 1 and "nothing was deleted" in r.stderr
    assert not any(c[:3] in (["compute", "instance", "delete"], ["compute", "disk", "delete"]) for c in rig.nebius.calls)
    rig.nebius.fail()
    rig.nebius.clear()
    r = rig.run("down", "--yes", NEBIUS_PROJECT_ID="project-e00test", **CONFIRM)
    assert r.returncode == 0, r.stderr
    kinds = [c[:3] for c in rig.nebius.calls]
    assert (
        kinds.index(["ai", "endpoint", "stop"])
        < kinds.index(["compute", "instance", "delete"])
        < kinds.index(["compute", "disk", "delete"])
    )


@pytest.mark.req("FR-11.1")
def test_down_without_an_endpoint_or_vm_deletes_nothing(rig: Rig):
    rig.nebius.listing(None)
    r = rig.run("down", "--yes", NEBIUS_PROJECT_ID="project-e00test", **CONFIRM)
    assert r.returncode == 0, r.stderr
    assert not any(c[0] == "compute" and c[2] == "delete" for c in rig.nebius.calls)
    assert not any(c[:3] == ["ai", "endpoint", "stop"] for c in rig.nebius.calls)
    assert "no VM named" in r.stderr


@pytest.mark.req("FR-11.1")
def test_status_only_reads(rig: Rig):
    rig.nebius.respond(["compute", "instance", "get-by-name"], "computeinstance-e00test")
    rig.nebius.respond(["compute", "instance", "get"], "RUNNING")
    r = rig.run("status", NEBIUS_PROJECT_ID="project-e00test")
    assert r.returncode == 0, r.stderr
    assert "computeinstance-e00test" in r.stderr and "ratchet-student" in r.stderr
    writes = {"create", "start", "stop", "delete", "update"}
    assert not [c for c in rig.nebius.calls if writes & set(c[:4])], rig.nebius.calls
    assert rig.run("status").returncode == 1


@pytest.mark.req("FR-11.1")
def test_set_env_replaces_or_appends_and_keeps_the_value_off_the_command_line(tmp_path: Path):
    assert BASH is not None
    env_file = tmp_path / ".env"
    env_file.write_text("A=1\nRATCHET_MODE=mock\n# note\nRATCHET_STUDENT_KEY=\n", encoding="utf-8")
    script = str(ROOT / "deploy" / "vm" / "set_env.sh")
    env = {**os.environ, "ENV_FILE": str(env_file)}

    def run(name: str, value: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([BASH, script, name], input=value, env=env, capture_output=True, text=True, timeout=30)

    assert run("RATCHET_MODE", "live").returncode == 0
    r = run("RATCHET_STUDENT_KEY", "s3cret=with=equals")
    assert r.returncode == 0 and "s3cret" not in r.stdout + r.stderr
    assert run("NEW_ONE", "x").returncode == 0
    assert env_file.read_text(encoding="utf-8").splitlines() == [
        "A=1",
        "RATCHET_MODE=live",
        "# note",
        "RATCHET_STUDENT_KEY=s3cret=with=equals",
        "NEW_ONE=x",
    ]
    assert run("bad name", "x").returncode == 2 and run("lower", "x").returncode == 2
    assert run("A", "two\nlines").returncode == 2
    assert env_file.read_text(encoding="utf-8").startswith("A=1\n")
