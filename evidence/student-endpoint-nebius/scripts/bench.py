"""Latency/throughput benchmark against the endpoint (streaming). No accuracy scoring of any kind.
  python bench.py --phase cold --levels 1,4,16 --n 1:30,4:60,16:96
Writes/merges bench.json under the phase key."""
import argparse, asyncio, json, os, time, httpx
from common import *
ap = argparse.ArgumentParser()
ap.add_argument("--phase", required=True)
ap.add_argument("--levels", default="1,4,16")
ap.add_argument("--n", default="1:30,4:60,16:96", help="requests per adapter at each level, level:count")
ap.add_argument("--url"); ap.add_argument("--max-tokens", type=int, default=256)
ap.add_argument("--adapters", default="sql-r2,toolcall,base")
a = ap.parse_args()
ADAPT = a.adapters.split(",")
BASE = "nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16"
url = a.url or open(os.path.join(HERE, "endpoint_url.txt")).read().strip()
H = {"Authorization": f"Bearer {read_token()}"}
N = {int(k): int(v) for k, v in (x.split(":") for x in a.n.split(","))}
prompts = [json.loads(l) for l in open(os.path.join(HERE, "prompts.jsonl"), encoding="utf-8")]

def pct(v, p):
    if not v: return None
    v = sorted(v); k = (len(v) - 1) * p / 100; f = int(k); c = min(f + 1, len(v) - 1)
    return round(v[f] + (v[c] - v[f]) * (k - f), 4)

async def one(client, row):
    body = {"model": row["adapter"] if not row.get("_base") else BASE, "messages": row["messages"], "temperature": 0, "max_tokens": a.max_tokens,
            "stream": True, "stream_options": {"include_usage": True}}
    t = time.perf_counter(); ttft = None; toks = None; chunks = 0
    try:
        async with client.stream("POST", url + "/v1/chat/completions", headers=H, json=body) as r:
            if r.status_code != 200:
                txt = (await r.aread()).decode("utf-8", "replace")
                return {"err": f"HTTP {r.status_code}: {txt[:120]}"}
            async for line in r.aiter_lines():
                if not line.startswith("data:"): continue
                p = line[5:].strip()
                if p == "[DONE]": break
                d = json.loads(p)
                if d.get("usage"): toks = d["usage"].get("completion_tokens")
                for ch in d.get("choices") or []:
                    dl = ch.get("delta") or {}
                    if ttft is None and (dl.get("content") or dl.get("reasoning_content") or dl.get("reasoning")):
                        ttft = time.perf_counter() - t
                    chunks += 1
        return {"lat": time.perf_counter() - t, "ttft": ttft, "toks": toks if toks is not None else chunks}
    except Exception as e:
        return {"err": type(e).__name__ + ": " + str(e)[:120]}

async def level(adapter, conc, n):
    rows = prompts if adapter == "base" else [p for p in prompts if p["adapter"] == adapter]
    work = [rows[i % len(rows)] for i in range(n)]
    if adapter == "base": work = [dict(w, _base=True) for w in work]
    sem = asyncio.Semaphore(conc); out = []
    async with httpx.AsyncClient(timeout=300, limits=httpx.Limits(max_connections=conc + 2)) as client:
        async def run(r):
            async with sem: out.append(await one(client, r))
        t = time.perf_counter()
        await asyncio.gather(*[run(r) for r in work])
        wall = time.perf_counter() - t
    ok = [o for o in out if "lat" in o]; errs = [o["err"] for o in out if "err" in o]
    lat = [o["lat"] for o in ok]; tt = [o["ttft"] for o in ok if o["ttft"] is not None]
    toks = sum(o["toks"] or 0 for o in ok)
    rps = len(ok) / wall if wall else 0
    return {"adapter": adapter, "concurrency": conc, "requests": n, "ok": len(ok), "errors": len(errs), "error_samples": errs[:3],
            "wall_s": round(wall, 2), "lat_p50_s": pct(lat, 50), "lat_p95_s": pct(lat, 95),
            "ttft_p50_s": pct(tt, 50), "ttft_p95_s": pct(tt, 95),
            "tokens_out": toks, "tokens_per_s_total": round(toks / wall, 1) if wall else 0,
            "tokens_per_s_per_request_p50": pct([o["toks"] / o["lat"] for o in ok if o["toks"] and o["lat"]], 50),
            "requests_per_s": round(rps, 3),
            "cost_usd_per_1000_requests": round(1000 / rps * GPU_USD_PER_HOUR / 3600, 4) if rps else None,
            "cost_basis": f"whole-GPU time at ${GPU_USD_PER_HOUR}/h, wall {round(wall,1)} s for {len(ok)} ok requests"}

async def main():
    results = []
    for adapter in ADAPT:
        await level(adapter, 1, 2)  # warm-up, not recorded
        for lv in [int(x) for x in a.levels.split(",")]:
            r = await level(adapter, lv, N[lv]); results.append(r)
            status(f"[bench {a.phase}] {adapter} c={lv}: p50 {r['lat_p50_s']} s p95 {r['lat_p95_s']} s, {r['tokens_per_s_total']} tok/s, {r['requests_per_s']} req/s, ${r['cost_usd_per_1000_requests']}/1k, errors {r['errors']}")
    merge_json(os.path.join(HERE, "bench.json"), a.phase, {"at": stamp(), "gpu_usd_per_hour": GPU_USD_PER_HOUR, "max_tokens": a.max_tokens, "temperature": 0, "results": results})
asyncio.run(main())
