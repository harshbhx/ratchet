.PHONY: install test lint fmt trace reference sim demo day1 docker clean web web-dev live-plan live-smoke live-run

install:
	pip install -e ".[dev]" ruff

test:
	pytest -q

lint:
	ruff check .
	ruff format --check .
	python scripts/reqtrace.py --check

# Regenerate docs/REQUIREMENTS.md (PRD rows and invariants -> code and tests) after changing PRD rows or test markers.
trace:
	python scripts/reqtrace.py

# Three pinned workloads (tickets, mixed, research) must still reproduce v0.4.0's behaviour (about 25 s; CI runs it).
reference:
	python scripts/reference_check.py

fmt:
	ruff format .
	ruff check --fix .

sim:
	python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3

demo:
	RATCHET_MODE=mock RATCHET_MOCK_WARMUP_DAYS=4 uvicorn ratchet.gateway:app --port 8000

# React app (web/) -> ratchet/web/dist, which the gateway serves at /, /scout and /dashboard.
web:
	cd web && npm ci && npm run build

# Hot-reload UI on :5173 against a gateway on :8000 (run `make demo` in another terminal).
web-dev:
	cd web && npm ci && npm run dev

# Live day-1 probes (never in test, lint or CI). Default: the Token Factory teacher check.
# make day1 DAY1_CHECKS="all --json evidence/day1/day1.json" runs every probe whose key is set.
DAY1_CHECKS ?=
day1:
	python scripts/day1_check.py $(DAY1_CHECKS)

# The live-run harness (ratchet/liverun.py, docs/RUNBOOK.md#live-measurement-run): on the gateway's host, with its .env and
# RATCHET_LIVERUN_KEY exported. Never part of test, lint or CI.
ROUNDS ?= 4
REQUESTS ?= 1500
MAX_USD ?= 30
WORKLOAD ?= tickets
BASE_URL ?= http://localhost:8000/v1

live-plan:   # free: prints the plan, no paid call
	python -m ratchet.liverun plan --rounds $(ROUNDS) --requests $(REQUESTS) --max-usd $(MAX_USD) --workload $(WORKLOAD)

live-smoke:  # 20 requests, about 5 control calls, no nights: cents
	python -m ratchet.liverun smoke --base-url $(BASE_URL)

live-run:
	python -m ratchet.liverun run --base-url $(BASE_URL) --rounds $(ROUNDS) --requests $(REQUESTS) --max-usd $(MAX_USD) --workload $(WORKLOAD)

docker:
	docker build -t ratchet-gateway .

clean:
	rm -rf .pytest_cache .ruff_cache .coverage build dist *.egg-info sim_results.json ratchet.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
