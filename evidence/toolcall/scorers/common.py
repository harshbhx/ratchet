"""Shared pieces copied from tools/pilot/pilot.py (prompt builder, strict scorer, Wilson). Keys are read inside the process only."""
import json, os, re, subprocess, math, datetime, threading, time, urllib.request, urllib.error
HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = "2026-10-24"
SYS = ('You are a tool-calling assistant. Today is %s. Given the tools below and a request, reply with one JSON object '
       '{"name": ..., "arguments": {...}} and nothing else. If no tool fits, reply {"name": "none", "arguments": {}}.' % TODAY)

def userkey(name):
    k = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetEnvironmentVariable('%s','User')" % name],
                       capture_output=True, text=True).stdout.strip()
    assert k, "no key " + name
    return k

def tool_json(t):
    return json.dumps({"name": t["name"], "description": t["description"], "parameters": t["parameters"]}, separators=(",", ":"))
def prompt_user(tools, shown, req):
    return "Tools:\n" + "\n".join(tool_json(tools[n]) for n in shown) + "\n\nRequest: " + req

def wilson(s, n, z=1.96):
    p = s / n; d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return c - h, c + h
def nstr(s): return re.sub(r"\s+", " ", s.strip()).lower()
def nval(v):
    if isinstance(v, bool): return ("b", v)
    if isinstance(v, (int, float)): return ("n", float(v))
    if isinstance(v, str): return ("s", nstr(v))
    if isinstance(v, list) and all(isinstance(x, str) for x in v): return ("set", frozenset(nstr(x) for x in v))
    return ("raw", json.dumps(v, sort_keys=True))
def score(ref, ans):
    if not isinstance(ans, dict) or "name" not in ans or not isinstance(ans.get("arguments"), dict):
        return False, "bad_shape"
    if ref["name"] == "none":
        good = ans["name"] == "none" and not ans["arguments"]
        return good, ("ok" if good else "none_missed")
    if ans["name"] == "none": return False, "none_wrongly_answered"
    if ans["name"] != ref["name"]: return False, "wrong_tool"
    ra, aa = ref["arguments"], ans["arguments"]
    if set(ra) != set(aa): return False, "wrong_arguments"
    for k in ra:
        if nval(ra[k]) != nval(aa[k]): return False, "wrong_arguments"
    return True, "ok"

def parse_obj(txt):
    if not txt: return None
    t = txt.strip()
    try: return json.loads(t)
    except Exception: pass
    m = re.search(r"\{.*\}", t, re.S)
    try: return json.loads(m.group(0)) if m else None
    except Exception: return None
def parse_arr(txt):
    if not txt: return None
    m = re.search(r"\[.*\]", txt, re.S)
    try: return json.loads(m.group(0)) if m else None
    except Exception: return None

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
def valid_date(v):
    if not isinstance(v, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v): return False
    try: datetime.date.fromisoformat(v); return True
    except Exception: return False
def check_value(spec, v):
    t = spec["type"]
    if t == "string": ok = isinstance(v, str) and v.strip() != ""
    elif t == "integer": ok = isinstance(v, int) and not isinstance(v, bool)
    elif t == "number": ok = isinstance(v, (int, float)) and not isinstance(v, bool)
    elif t == "boolean": ok = isinstance(v, bool)
    elif t.startswith("array"): ok = isinstance(v, list) and len(v) > 0 and all(isinstance(x, str) and x.strip() for x in v)
    else: ok = False
    if not ok: return "wrong_type"
    if "enum" in spec:
        vals = v if isinstance(v, list) else [v]
        if not all(x in spec["enum"] for x in vals): return "bad_enum"
    fmt = spec.get("format", "")
    if fmt == "YYYY-MM-DD" and not valid_date(v): return "bad_date"
    if fmt.startswith("ISO 8601"):
        if not (isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", v)): return "bad_datetime"
        try: datetime.datetime.fromisoformat(v)
        except Exception: return "bad_datetime"
    if fmt == "email":
        for x in (v if isinstance(v, list) else [v]):
            if not EMAIL.match(x): return "bad_email"
    return None
def validate_call(tools, shown, call, kind):
    if not isinstance(call, dict) or not isinstance(call.get("name"), str) or not isinstance(call.get("arguments"), dict):
        return "bad_call_shape"
    name, args = call["name"], call["arguments"]
    if name == "none":
        if kind != "none": return "none_unexpected"
        return "none_with_arguments" if args else None
    if kind == "none": return "tool_called_for_none_row"
    if name not in shown: return "tool_not_in_shown"
    params = tools[name]["parameters"]
    for p, s in params.items():
        if p not in args and not s.get("optional"): return "missing_required_arg"
    for p, v in args.items():
        if p not in params: return "invented_arg"
        r = check_value(params[p], v)
        if r: return r
    return None

class Meter:
    def __init__(self, pin, pout, cap):
        self.pin, self.pout, self.cap = pin, pout, cap
        self.tin = self.tout = self.calls = self.failed = 0
        self.lock = threading.Lock()
    def usd(self): return (self.tin * self.pin + self.tout * self.pout) / 1e6
    def snap(self): return {"calls": self.calls, "failed": self.failed, "tin": self.tin, "tout": self.tout, "usd": round(self.usd(), 4)}

def claude(key, model, system, user, meter, max_tokens=16000):
    if meter.usd() >= meter.cap: return None
    body = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": user}]}  # no temperature
    if system: body["system"] = system
    data = json.dumps(body).encode()
    for a in range(5):
        try:
            req = urllib.request.Request("https://api.anthropic.com/v1/messages", data,
                {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as r: d = json.load(r)
            with meter.lock:
                meter.tin += d.get("usage", {}).get("input_tokens", 0); meter.tout += d.get("usage", {}).get("output_tokens", 0); meter.calls += 1
            return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
        except urllib.error.HTTPError as e:
            with meter.lock: meter.failed += 1
            if e.code in (429, 500, 502, 503, 529) and a < 4: time.sleep(5 * (a + 1)); continue
            try: print("API error", e.code, json.loads(e.read().decode())["error"]["message"][:200], flush=True)
            except Exception: print("API error", e.code, flush=True)
            return None
        except Exception as e:
            with meter.lock: meter.failed += 1
            if a == 4: print("request failed", type(e).__name__, flush=True); return None
            time.sleep(5 * (a + 1))
    return None
