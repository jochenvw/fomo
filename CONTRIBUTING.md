# Contributing

## 1. Setup

```powershell
uv sync --all-groups
./tasks.ps1 check          # or: make check   (ruff + mypy + offline pytest)
```

## 2. Rules

| Area | Rule |
|---|---|
| Azure changes | every mutating command is **plan → confirm → apply**; add new actions to a `Plan`, never call Azure directly from `cli.py` |
| Auth | Entra ID only; no keys, SAS or connection strings |
| Results | never fake provisioning, inference or retrieval; fail with `reason` + `fix` (`errors.py`) |
| Names | derive every resource name from `naming.ParticipantNames`; cleanup relies on it |
| Corpus | edit `corpus/generator/`, then `uv run python -m corpus.generator`; commit regenerated files; `--check` must pass |
| Ground truth | `corpus/ground_truth/` is never read by classifier or agent code |
| Docs | update `docs/` when a command, role or default changes |

## 3. Tests

| Layer | Command | Needs |
|---|---|---|
| Unit (CI) | `uv run pytest -q` | nothing; all Azure calls mocked |
| Integration | `$env:FOMO_LIVE='1'; $env:FOMO_TEST_PARTICIPANT='<name>'; uv run pytest -m integration` | `az login`, `.workshop/environment.json`, a completed participant |
| End-to-end | `$env:FOMO_E2E='1'; uv run pytest -m e2e` | as above; creates and deletes two fresh participants (~10 min, small cost) |

## 4. Pull requests

- CI (`.github/workflows/ci.yml`) runs lint, types, unit tests, bandit and Bicep build without secrets.
- Mention which live tests you ran and against which topology.
- No tenant IDs, UPNs, subscription names or customer data in code, logs, examples or issues.
