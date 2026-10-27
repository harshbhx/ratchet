"""Time the endpoint start-up. Usage:
  python startup.py --phase cold   --create          (runs create_endpoint.sh, then times everything)
  python startup.py --phase cached --endpoint-id ID  (runs `nebius ai endpoint start`, then times everything)
Records seconds from the command being issued to: RUNNING, first GET /v1/models, each adapter loaded, first completion.
Writes startup.json (merged by phase) and STATUS.md. Gives up after --max-wait seconds without RUNNING."""
import argparse, json, os, re, subprocess, sys, time, httpx
from common import *

ADAPTERS = {"sql-r2": "/data/adapters/sql-r2", "toolcall": "/data/adapters/toolcall"}
ap = argparse.ArgumentParser()
ap.add_argument("--phase", required=True)
ap.add_argument("--create", action="store_true")
ap.add_argument("--attach-created-at", help="epoch secs: endpoint already created at this time")
ap.add_argument("--endpoint-id")
ap.add_argument("--max-wait", type=int, default=1800)
a = ap.parse_args()
tok = read_token()
H = {"Authorization": f"Bearer {tok}"}
res = {"phase": a.phase, "command_issued_at": stamp(), "states": [], "seconds": {}}
t0 = time.time()
def el(): return round(time.time() - t0, 1)
def save(): merge_json(os.path.join(HERE, "startup.json"), a.phase, res)

status(f"[{a.phase}] issuing the {'create' if a.create else 'start'} command now")
if a.attach_created_at:
    eid = a.endpoint_id; t0 = float(a.attach_created_at); res["note"] = "attached to already-created endpoint"
elif a.create:
    r = subprocess.run(["wsl", "-d", "Ubuntu", "-e", "bash", "<private path>/create_endpoint.sh"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    res["create_rc"] = r.returncode
    open(os.path.join(HERE, f"log_{a.phase}_create.txt"), "w", encoding="utf-8").write(
        r.stdout[-4000:].replace(tok, "<hidden>") + "\n--stderr--\n" + r.stderr[-3000:].replace(tok, "<hidden>"))
    if r.returncode != 0:
        save(); sys.exit("create failed, see log_cold_create.txt")
    m = re.search(r"aiendpoint-[a-z0-9]+", r.stdout)
    eid = m.group(0) if m else None
    if not eid:
        save(); sys.exit("no endpoint id in create output")
else:
    eid = a.endpoint_id
    rc, out, err = nb("ai", "endpoint", "start", eid, "--async")
    res["start_rc"] = rc
    if rc != 0:
        save(); sys.exit("start failed: " + err[-300:])
res["endpoint_id"] = eid
res["seconds"]["command_returned"] = el()
status(f"[{a.phase}] command returned after {el()} s, endpoint {eid}")

last = None; url = ""
while True:
    if (el() > a.max_wait and last in (None, "PROVISIONING", "UNKNOWN")) or el() > 1800:
        res["timeout"] = True; save()
        status(f"[{a.phase}] NOT RUNNING after {a.max_wait} s, giving up (caller must read logs and stop it)"); sys.exit(3)
    try:
        d = endpoint_get(eid); s = endpoint_state(d)
    except Exception:
        time.sleep(5); continue
    if s != last:
        res["states"].append({"state": s, "at_seconds": el()}); last = s
        status(f"[{a.phase}] endpoint state {s} at {el()} s"); save()
    if s == "RUNNING":
        url = endpoint_url(d); break
    if s in ("ERROR", "FAILED"):
        res["failed_state"] = s; save(); sys.exit("endpoint " + s)
    time.sleep(5)
res["seconds"]["state_running"] = el(); res["url_host"] = url.split("//")[-1].split("/")[0]
open(os.path.join(HERE, "endpoint_url.txt"), "w").write(url)  # the URL is not a secret; the token is
save()

c = httpx.Client(timeout=60)
codes = set()
while True:
    if el() > a.max_wait + 900:
        res["models_never_ok"] = True; res["models_codes_seen"] = sorted(codes); save(); sys.exit("GET /v1/models never succeeded")
    try:
        r = c.get(url + "/v1/models", headers=H)
        codes.add(r.status_code)
        if r.status_code == 200: break
    except Exception as e:
        codes.add(type(e).__name__)
    time.sleep(5)
res["models_codes_seen_before_ok"] = sorted(map(str, codes))
res["seconds"]["first_models_ok"] = el(); status(f"[{a.phase}] first GET /v1/models OK at {el()} s"); save()

for name, path in ADAPTERS.items():
    t1 = time.time()
    r = c.post(url + "/v1/load_lora_adapter", headers=H, json={"lora_name": name, "lora_path": path}, timeout=300)
    res.setdefault("adapter_load", {})[name] = {"status": r.status_code, "seconds": round(time.time() - t1, 2), "text": r.text[:200]}
    res["seconds"][f"adapter_{name}_loaded"] = el()
    status(f"[{a.phase}] adapter {name} load HTTP {r.status_code} in {round(time.time()-t1,1)} s"); save()
res["seconds"]["both_adapters_loaded"] = el()

for name in ADAPTERS:
    t1 = time.time()
    r = c.post(url + "/v1/chat/completions", headers=H, timeout=300, json={"model": name, "temperature": 0, "max_tokens": 32,
        "messages": [{"role": "user", "content": "Say hello."}]})
    res.setdefault("first_completion", {})[name] = {"status": r.status_code, "seconds": round(time.time() - t1, 2), "at_seconds": el()}
    if "first_completion" not in res["seconds"] and r.status_code == 200:
        res["seconds"]["first_completion"] = el()
    save()
res["seconds"]["total_until_ready"] = el()
status(f"[{a.phase}] ready: RUNNING {res['seconds']['state_running']} s, models {res['seconds']['first_models_ok']} s, adapters {res['seconds']['both_adapters_loaded']} s, first completion {res['seconds'].get('first_completion')} s")
save()
