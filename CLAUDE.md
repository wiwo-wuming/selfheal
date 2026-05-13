# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test

```bash
pip install -e ".[dev,llm,dashboard]"    # full install
make all                        # ruff + mypy + coverage (recommended pre-commit)
make lint                       # ruff check src/
make type                       # mypy src/
make test                       # pytest -x (no coverage, fast)
make cov                        # pytest with coverage report
python -m pytest tests/ -q      # full suite (~15 min, 462 tests)
python -m pytest tests/ -x --tb=short  # stop on first failure
python -m pytest tests/test_experience.py -v  # single test file
python -m pytest tests/ --cov=src/selfheal --cov-report=term-missing  # run with coverage
python -m ruff check src/       # lint
python -m mypy src/             # type check (strict mode)
python -m selfheal --help       # CLI smoke test
```

Windows: use `make.bat all` or `.\Makefile.ps1 all` instead of `make`

VCR tests require an API key; set `OPENAI_API_KEY` env var for recording, or `CI=1` for replay-only mode.

## Architecture

SelfHeal is a pluggable pipeline for **test failure → classify → patch → validate → report → store**. Components are registered in a global `Registry` singleton and instantiated by the `SelfHealEngine` based on YAML config.

### Pipeline

```
Watcher (pytest/log) → Classifier (rule/llm/hybrid) → Patcher (template/llm)
  → Validator (local/docker) → Reporter (terminal/github/webhook) → Store (memory/sqlite)
```

### Key module responsibilities

| Path | Role |
|------|------|
| `engine.py` | Orchestrates the pipeline, loads components from registry via config |
| `config.py` | Pydantic v2 models for all settings; loads `selfheal.yaml`; resolves `${ENV}` vars |
| `registry.py` | Singleton component registry — 7 categories (watcher, classifier, patcher, validator, reporter, store, stage) |
| `events.py` | `TestFailureEvent` → `ClassificationEvent` → `PatchEvent` → `ValidationEvent`; `ErrorSeverity` and `ErrorCategory` enums |
| `interfaces/` | ABCs for each component type (7 interfaces) |
| `cli/__init__.py` | Click group entry point; 13 subcommands each in `cli/<name>.py` |
| `core/llm_client.py` | Unified LLM client factory + `call_structured()` with tool use + retry + error classification |
| `core/diff_parser.py` | Shared unified-diff parsing used by both `applier.py` and `docker_validator.py` |
| `core/cache.py` | `LLMResponseCache` — in-memory cache keyed by error signature (SHA256 of type+message+traceback) |
| `core/experience.py` | SQLite-backed store of previously successful patches for reuse |

### Classifier & Patcher strategy dispatch

- **HybridClassifier** (`classifiers/hybrid_classifier.py`): rule-first → LLM-fallback; has its own cache layer for rule results
- **TemplatePatcher** (`patchers/template_patcher.py`): dispatches `classification.category` to registered strategies in `patchers/strategies/`
- Strategies inherit from `TemplateRenderStrategy` (template with 3-level fallback) or `PatchStrategy` ABC (custom logic like `ImportStrategy`)

### LLM support matrix

Three providers (OpenAI, DeepSeek, Anthropic) via two SDKs:
- `openai.OpenAI` for OpenAI + DeepSeek (OpenAI-compatible endpoint)
- `anthropic.Anthropic` for Anthropic + DeepSeek Anthropic-compatible (`api.deepseek.com/anthropic`)

`call_structured()` in `llm_client.py` handles tool use / function calling for both protocols, with automatic JSON-extraction fallback.

### ExperienceStore — fuzzy matching (v0.3.0)

`core/experience.py` stores successful patches in SQLite and provides two lookup methods:
- `find_similar()` — original 3-tier exact-match search (unchanged for backward compat)
- `find_similar_with_confidence()` — returns `ExperienceMatch` dataclasses with confidence scores: exact signature=0.95, same error_type=0.70*decay, same category=0.45*decay. decay = `1 - e^(-success_count/5)`

`_try_experience_patch()` in `template_patcher.py` uses confidence thresholds:
- `min_confidence` (default 0.40): matches below this are discarded
- `auto_apply_threshold` (default 0.80): matches below this get `require_validation=True` in metadata

### Plugin sandbox

`plugins/sandbox.py` — `PluginSandbox` class runs untrusted plugin code in a subprocess with JSON-based data exchange, timeout protection, and optional SHA256 integrity pre-check. Safe alternative to in-process `importlib` loading for untrusted plugins.

## CI / GitHub Actions

Workflow at `.github/workflows/selfheal.yml`. Runs on push to main/develop and PRs:
1. **Lint (ruff)** — `ruff check src/`
2. **Type check (mypy)** — `mypy src/ --python-version 3.11` with type stubs
3. **Tests with coverage** — `pytest --cov=src/selfheal --cov-branch`
4. VCR replay tests (no API key needed)

CI installs `.[dev,llm,dashboard]` — all extras needed for mypy to type-check optional dependencies.

## Background Agent permissions

`.claude/settings.json` must be at the **Claude Code working directory** (not the project subdirectory) with:
```json
{"permissions": {"allow": ["Edit", "Write", "Read", "Bash", "WebFetch", "WebSearch"]}}
```
Settings are only loaded at session startup. Without this, background agents (`run_in_background=true`) cannot write files because they can't show permission prompts.

## Config patterns

```yaml
# selfheal.yaml
classifier:
  type: hybrid          # rule | llm | hybrid
  confidence_threshold: 0.5
  rules: [...]          # RuleConfig items
  llm:                  # LLMConfig for fallback
    provider: deepseek
    model: deepseek-chat
    api_key: ${DEEPSEEK_API_KEY}
    base_url: https://api.deepseek.com

patcher:
  type: llm             # template | llm
  refine_rounds: 2
  quality_threshold: 4.0

engine:
  max_retries: 3
  auto_apply: false
  dry_run: false
  backup_dir: .selfheal/backups
```

`.env` auto-loaded via python-dotenv (try/except, optional dependency).

## Testing conventions

- `tests/conftest.py` has shared fixtures: `make_failure()`, `make_classification()`, `make_patch()`, `create_mock_engine()`
- VCR cassettes in `tests/vcr_cassettes/{openai,anthropic}/` — delete and re-record when API call patterns change
- Mock-based tests use `unittest.mock.patch()` on `call_structured()` — not on `_client` attributes (removed after refactor)
- Cache singleton must be reset in `setup_method()` for tests that check cache behavior (see `test_hybrid_classifier.py`)

## Key invariants

- **Patches are unified-diff format** — `--- a/file` / `+++ b/file` / `@@ hunk @@` headers; detection via `is_unified_diff()`
- **PatchApplier always backups before applying** — backup path stored in `.selfheal/backup_index.json`
- **`auto_apply` defaults to `False`** — safety first; no code modification without explicit opt-in
- **Tool use is preferred over regex JSON parsing** — classifier and quality scoring use structured output; regex is fallback only
- **All 15 ErrorCategory values must have a registered strategy** — `strategies/__init__.py` covers every enum value
