.PHONY: nebius-plan nebius-up nebius-down nebius-status demo-check install test lint fmt trace reference sim demo day1 docker clean web web-dev live-plan live-smoke live-run

install:
	python -m pip install -e ".[dev]"

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

# The public demo as a judge sees it, from outside (never part of test or lint): make demo-check URL=https://<domain> ARGS=--chat
demo-check:
	python scripts/demo_check.py $(URL) $(ARGS)

# One command to host Ratchet on Nebius (scripted and tested offline, ready to run; docs/WHERE_IT_RUNS.md).
# nebius-plan prints every command and changes nothing. nebius-up and nebius-down are dry runs unless you set
# RATCHET_I_UNDERSTAND_THIS_COSTS_MONEY=1 and pass YES=1; GPU parts need ARGS="--with-student" (and --with-training).
nebius-plan:
	bash deploy/nebius/nebius.sh plan $(ARGS)

nebius-up:
	bash deploy/nebius/nebius.sh up $(if $(YES),--yes) $(ARGS)

nebius-down:
	bash deploy/nebius/nebius.sh down $(if $(YES),--yes)

nebius-status:
	bash deploy/nebius/nebius.sh status

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
