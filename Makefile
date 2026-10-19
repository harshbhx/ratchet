.PHONY: install test lint fmt sim demo day1 docker clean

install:
	pip install -e ".[dev]" ruff

test:
	pytest -q

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff format .
	ruff check --fix .

sim:
	python -m sim.simulate --days 10 --requests 2000 --bad-deploy-version 3

demo:
	RATCHET_MODE=mock uvicorn ratchet.gateway:app --port 8000

day1:
	python scripts/day1_check.py

docker:
	docker build -t ratchet-gateway .

clean:
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info sim_results.json ratchet.db
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
