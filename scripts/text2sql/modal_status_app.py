"""A tiny, read-only status endpoint for the training run: it returns the progress the pipeline wrote, nothing else.
Deploy once with `python -m modal deploy modal_status_app.py`; it holds no secrets and the page polls it."""

import time

import modal

app = APP = modal.App("ratchet-status")
STATUS = modal.Dict.from_name("ratchet-status", create_if_missing=True)
image = modal.Image.debian_slim().pip_install("fastapi[standard]")


@app.function(image=image, cpu=0.125, memory=256, max_containers=2)
@modal.fastapi_endpoint(method="GET")
def status():
    from fastapi.responses import JSONResponse

    try:
        st = STATUS.get("status", None) or {"state": "waiting", "note": "no run has reported yet"}
        spent = float(STATUS.get("spent_s", 0.0))
    except Exception as e:
        st, spent = {"state": "unknown", "note": f"could not read the status: {type(e).__name__}"}, 0.0
    st = dict(st)
    st["spent_s"] = spent
    st["now"] = time.time()
    return JSONResponse(st, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "no-store"})
