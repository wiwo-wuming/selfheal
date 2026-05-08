# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test

```bash
pip install -e ".[dev,llm]"       # editable install with all deps
python -m pytest tests/ -q         # full suite (~10 min, 436 tests)
python -m pytest tests/ -x --tb=short  # stop on first failure
python -m pytest tests/test_template_patcher.py -v  # single test file
python -m pytest tests/test_llm_vcr.py -v -k "openai"  # filtered
python -m ruff check src/          # lint
python -m mypy src/                # type check (strict mode)
python -m selfheal --help          # CLI smoke test
```

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
