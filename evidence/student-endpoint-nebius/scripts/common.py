"""Shared helpers. Secrets are read from the existing private key files and never printed or written elsewhere."""
import datetime, json, os, re, subprocess, time
HERE = os.path.dirname(os.path.abspath(__file__))
PRIV = os.path.dirname(HERE)
GPU_USD_PER_HOUR = 0.74
NB_SH = "<private path>/nb.sh"

def now(): return datetime.datetime.now().astimezone()
def stamp(): return now().strftime("%Y-%m-%d %H:%M:%S %Z")

def read_token():
    txt = open(os.path.join(PRIV, "student-endpoint-key.txt")).read().strip()
    m = re.search(r"RATCHET_STUDENT_KEY\s*=\s*['\"]?([^'\"\s]+)", txt)
    return m.group(1) if m else txt.split()[0]

def status(line):
    """Append a plain-words line to STATUS.md (no secrets)."""
    p = os.path.join(HERE, "STATUS.md")
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"- {stamp()}  {line}\n")
    print(line, flush=True)

def nb(*args, timeout=600):
    """Run the nebius CLI inside WSL via nb.sh. Returns (returncode, stdout, stderr)."""
    r = subprocess.run(["wsl", "-d", "Ubuntu", "-e", "bash", NB_SH, *args],
                       capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout, r.stderr

def endpoint_get(eid):
    rc, out, err = nb("ai", "endpoint", "get", eid, "--format", "json")
    if rc != 0: raise RuntimeError("endpoint get failed: " + err[-300:])
    return json.loads(out)

def endpoint_state(d): return str((d.get("status") or {}).get("state", "UNKNOWN")).upper()

def endpoint_url(d):
    pe = (d.get("status") or {}).get("public_endpoints") or []
    https = [u for u in pe if str(u).startswith("https://")]
    return (https[0] if https else "").rstrip("/")

def merge_json(path, key, value):
    data = {}
    if os.path.exists(path):
        try: data = json.load(open(path, encoding="utf-8"))
        except Exception: data = {}
    data[key] = value
    json.dump(data, open(path, "w", encoding="utf-8"), indent=2)
