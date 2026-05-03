# SelfHeal

[![CI](https://github.com/wiwo-wuming/selfheal/actions/workflows/selfheal.yml/badge.svg)](https://github.com/wiwo-wuming/selfheal/actions/workflows/selfheal.yml)

Intelligent test self-healing framework. Automatically detects test failures, classifies errors, generates fix patches, validates repairs, and reports results.

智能测试自愈框架 — 自动检测测试失败、分类错误、生成修复补丁、验证修复、输出报告。

## Pipeline

```
Watcher -> Classifier -> Patcher -> Validator -> Reporter
                            |
                          Store
```

## Features

- **Multi-source watchers**: pytest executor, raw log parser, plugin hot-reload monitor
- **Intelligent classification**: rule-based, LLM-powered, or hybrid (rule with LLM fallback)
- **Automatic patching**: Jinja2 template engine, LLM-based code generation, experience reuse
- **Multi-environment validation**: local pytest, Docker sandbox (isolated temp-copy)
- **Flexible reporting**: terminal, GitHub Issues, webhook (Slack/Discord) with HMAC signing
- **Persistent storage**: memory (dev) or SQLite (production)
- **Plugin system**: hot-reload custom components, SHA256 integrity verification
- **Safety-first**: backup/rollback, dry-run preview, auto_apply disabled by default

## Installation

Requires Python 3.10+.

```bash
pip install -e .              # base install
pip install -e ".[dev]"       # test dependencies (pytest, vcrpy, ruff, mypy)
pip install -e ".[llm]"       # LLM support (OpenAI, Anthropic, DeepSeek)
pip install -e ".[docker]"    # Docker sandbox validation
pip install -e ".[github]"    # GitHub Issues integration
pip install -e ".[hotreload]" # watchdog for plugin hot-reload
```

## Quick Start

```bash
# Generate default config
python -m selfheal init

# Watch tests and auto-heal on failure
python -m selfheal watch -- pytest tests/

# Process a single failure
python -m selfheal classify --input failure.json
python -m selfheal patch --input classification.json

# Batch process failures
python -m selfheal batch --input failures.json --auto-apply

# Apply a generated patch to source
python -m selfheal apply --input patch.json --auto-apply

# Preview without modifying files
python -m selfheal apply --input patch.json --dry-run

# Rollback applied patches
python -m selfheal rollback
python -m selfheal rollback --all

# Generate HTML dashboard
python -m selfheal dashboard --output report.html

# View metrics
python -m selfheal metrics
python -m selfheal metrics --json

# Backup management
python -m selfheal backups
python -m selfheal cleanup --max-age 30
```

## Configuration

`selfheal.yaml`:

```yaml
watcher:
  type: pytest
  path: tests/

classifier:
  type: rule          # rule | llm | hybrid
  rules:
    - pattern: "AssertionError"
      category: assertion
      severity: medium

patcher:
  type: template      # template | llm

validator:
  type: local         # local | docker
  timeout: 300

reporter:
  type: terminal      # terminal | github | webhook

store:
  type: sqlite        # memory | sqlite
  db_path: .selfheal/selfheal.db

engine:
  auto_apply: false   # safety: patches are generated but not applied
  max_retries: 3
  max_concurrency: 1

pipeline:             # optional: customize pipeline stages
  stages:
    - type: classify
    - type: patch
      retry: 3
    - type: validate
    - type: report
    - type: store
```

### LLM Configuration

```yaml
classifier:
  type: llm
  llm:
    provider: openai                    # openai | anthropic | deepseek
    model: gpt-4
    api_key: ${OPENAI_API_KEY}          # env var resolution
    temperature: 0.1
```

## Architecture

| Component | Description |
|-----------|-------------|
| **Watcher** | Monitors test output (PytestWatcher, RawLogWatcher, PluginWatcher) |
| **Classifier** | Categorizes errors by type & severity (RuleClassifier, LLMClassifier, HybridClassifier) |
| **Patcher** | Generates fix patches (TemplatePatcher with Jinja2, LLMPatcher) |
| **Validator** | Runs tests against patches (LocalValidator, DockerValidator with sandbox) |
| **Reporter** | Reports results (TerminalReporter, GitHubReporter, WebhookReporter) |
| **Store** | Persists event chains (MemoryStore, SQLiteStore) |
| **Experience** | Reuses successful fixes from persistent SQLite store |
| **PatchApplier** | Applies patches with automatic backup, rollback, and dry-run |
| **Metrics** | Collects and reports pipeline statistics |
| **Dashboard** | HTML dashboard with Chart.js visualizations |

## Plugins

Custom components are auto-discovered from the `plugins/` directory. Implement any interface, set `name`, and the plugin loader will register it:

```python
from selfheal.interfaces.classifier import ClassifierInterface
from selfheal.events import TestFailureEvent, ClassificationEvent

class MyClassifier(ClassifierInterface):
    name = "my_classifier"

    def classify(self, event: TestFailureEvent) -> ClassificationEvent:
        ...
```

Plugins support hot-reloading with optional watchdog integration and SHA256 integrity verification.

## Running Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific module
python -m pytest tests/ -v -k "test_engine"

# Run VCR tests in replay mode (no API keys needed)
CI=1 python -m pytest tests/test_llm_vcr.py -v

# With coverage
pip install pytest-cov
python -m pytest tests/ --cov=src/selfheal --cov-report=html
```

## CI/CD

### Jenkins

See [`Jenkinsfile`](Jenkinsfile) — 5-stage pipeline: Setup, Run Tests, Self-Heal Repair, Retry Tests, Metrics Report.

### GitHub Actions

See [`.github/workflows/selfheal.yml`](.github/workflows/selfheal.yml).

## Project Structure

```
selfheal/
├── src/selfheal/
│   ├── cli.py                 # CLI (12 subcommands)
│   ├── config.py              # Pydantic v2 configuration
│   ├── engine.py              # Pipeline orchestrator
│   ├── events.py              # Event dataclasses
│   ├── registry.py            # Component registry (singleton)
│   ├── interfaces/            # Abstract interfaces
│   │   ├── classifier.py
│   │   ├── patcher.py
│   │   ├── validator.py
│   │   ├── reporter.py
│   │   ├── store.py
│   │   ├── watcher.py
│   │   └── pipeline_stage.py
│   └── core/                  # Concrete implementations
│       ├── watchers/          # Pytest, RawLog, Plugin
│       ├── classifiers/       # Rule, LLM, Hybrid
│       ├── patchers/          # Template, LLM
│       ├── validators/        # Local, Docker
│       ├── reporters/         # Terminal, GitHub, Webhook
│       ├── stores/            # Memory, SQLite
│       ├── pipeline_stages/   # Classify, Patch, Validate, Report, Store
│       ├── applier.py         # PatchApplier (backup, rollback, dry-run)
│       ├── experience.py      # ExperienceStore (successful fix reuse)
│       ├── cache.py           # LLM response cache
│       ├── metrics.py         # MetricsCollector
│       ├── hooks.py           # Hook system
│       └── dashboard.py       # HTML dashboard generator
├── tests/                     # 28 test files, 311+ tests
├── patches/                   # Jinja2 patch templates (.j2)
├── pyproject.toml
├── Jenkinsfile
└── selfheal.example.yaml
```

## License

MIT
