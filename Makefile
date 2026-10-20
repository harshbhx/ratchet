.PHONY: install test lint fmt trace reference sim demo day1 docker clean web web-dev

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

# Three pinned workloads (tickets, mixed, research) must still reproduce their pinned behaviour (about 25 s; CI runs it).
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

day1:
	python scripts/day1_check.py

docker:
	docker build -t ratchet-gateway .

clean:
	rm -rf .pytest_cache .ruff_cache .coverage build dist *.egg-info sim_results.json ratchet.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
