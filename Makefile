# Discoverability wrapper; every target is a plain `uv run` command (Windows: see tasks.ps1).
.PHONY: setup lint typecheck test check preflight provision cheatcode status capacity corpus bicep integration

P ?=
N ?= 1
PARTICIPANT := $(if $(P),--participant $(P),)

setup:            ## install Python deps into .venv
	uv sync --all-groups
lint:
	uv run ruff check src tests corpus/generator
typecheck:
	uv run mypy
test:             ## offline unit tests (no Azure)
	uv run pytest -q
check: lint typecheck test
bicep:
	az bicep build --file infra/main.bicep --stdout > /dev/null
	az bicep build --file infra/reuse-existing.bicep --stdout > /dev/null
preflight:        ## read-only readiness check: make preflight N=40
	uv run contoso preflight --participants $(N)
provision:        ## plan only; add --apply yourself after reading the plan
	uv run contoso provision $(PARTICIPANT)
cheatcode:        ## plan only
	uv run contoso cheatcode $(PARTICIPANT)
status:
	uv run contoso status $(PARTICIPANT)
capacity:
	uv run contoso capacity --participants $(N)
corpus:           ## regenerate the synthetic corpus deterministically
	uv run python -m corpus.generator
integration:      ## live, read-mostly tests; needs CONTOSO_LIVE=1 and CONTOSO_TEST_PARTICIPANT
	uv run pytest -m integration -q
