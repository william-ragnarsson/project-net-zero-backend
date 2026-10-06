# net-zero. `make setup` once, then `make run` (or `make dev`).
SHELL := /bin/bash
UV ?= uv
NPM ?= npm
PORT ?= 8000

.PHONY: help setup check-uv run dev serve web test test-e2e test-web lint types record-demo demo-cassettes doctor clean

help:
	@echo "make setup        install python + web deps, warm the demo cache, run doctor"
	@echo "make run          build the web ui and serve everything on http://127.0.0.1:$(PORT)"
	@echo "make dev          api with --reload + vite dev server (proxying /api)"
	@echo "make test         backend unit + integration tests"
	@echo "make test-e2e     full demo-repo run in cassette replay mode"
	@echo "make test-web     frontend typecheck + vitest"
	@echo "make types        regenerate web/src/gen/{schema.json,events.ts}"
	@echo "make record-demo  run the demo and save it as the bundled web replay"
	@echo "make demo-cassettes  rebuild the demo cassettes from examples/demo-stories"
	@echo "make doctor       check git, uv, node, power measurement"

check-uv:
	@command -v $(UV) >/dev/null 2>&1 || { \
	  echo "uv is required: curl -LsSf https://astral.sh/uv/install.sh | sh  (or: brew install uv)"; exit 1; }

setup: check-uv
	$(UV) sync
	$(UV) python install 3.12
	@if [ -f examples/demo-repo/requirements.lock ]; then \
	  $(UV) pip compile --quiet examples/demo-repo/requirements.lock -o /dev/null >/dev/null 2>&1 || true; \
	  $(UV) run python -m netzero.pipeline.env --warm-demo || true; fi
	@if [ -f web/package.json ]; then $(NPM) --prefix web ci || $(NPM) --prefix web install; fi
	-$(UV) run netzero doctor

web:
	@if [ -f web/package.json ]; then $(NPM) --prefix web run build; fi

run: web
	$(UV) run netzero serve --port $(PORT)

serve:
	$(UV) run netzero serve --port $(PORT)

dev:
	@trap 'kill 0' EXIT; \
	$(UV) run netzero serve --reload --port $(PORT) & \
	$(NPM) --prefix web run dev & \
	wait

test:
	$(UV) run pytest

test-e2e:
	$(UV) run pytest -m e2e

test-web:
	$(NPM) --prefix web run typecheck
	$(NPM) --prefix web test

lint:
	$(UV) run ruff check netzero tests
	$(UV) run ruff format --check netzero tests

types:
	$(UV) run netzero export-schema
	$(NPM) --prefix web run gen:types

record-demo:
	$(UV) run netzero record

demo-cassettes:
	$(UV) run netzero demo-cassettes

doctor:
	$(UV) run netzero doctor

clean:
	rm -rf runs/ .netzero-cache/ web/dist/
