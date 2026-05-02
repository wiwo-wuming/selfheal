# Changelog

All notable changes to the SelfHeal project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] — 2026-05-02

### Added

- **Core pipeline**: Watcher → Classifier → Patcher → Validator → Reporter architecture (`331c32f`)
- **Multi-source watchers**: pytest test execution monitor and raw log file monitor
- **Dual classifier engines**: Rule-based classifier (14 error categories) and LLM-based classifier (Anthropic/DeepSeek)
- **Dual patcher engines**: Jinja2 template-based patcher (15 templates) and LLM-based patcher
- **Smart daily report**: Automated test failure detection, classification, patching, and validation pipeline (`331c32f`)
- **Multi-environment validation**: Local pytest validation and Docker sandbox isolation
- **Flexible reporters**: Terminal output, JSON output, and GitHub Issues integration
- **Persistent experience store**: SQLite-backed learning from successful fixes with signature-based retrieval
- **Experience reuse**: Template patcher auto-uses previously successful patches for similar errors
- **Patch safety mechanisms**: `--dry-run`, `--diff-only`, backup options for safe patching (`331c32f`)
- **Docker sandbox isolation**: Container-based patch validation with configurable timeouts (`331c32f`)
- **DeepSeek LLM support**: DeepSeek API provider integration alongside Anthropic (`6504866`)
- **HTML dashboard**: Dark-themed statistics page with stat cards, tables, and fix success rate
- **VCR test replay**: 319 test cassette recordings for deterministic CI testing (`fea8c8f`)
- **CI/CD automation**: GitHub Actions workflow with auto test→repair→report→issue pipeline (`affb83c`)
- **Async batch processing**: 3x speedup via concurrent patch generation and validation
- **Plugin architecture**: Dynamic component registration and discovery system
- **YAML configuration**: Full `selfheal.yaml` config with defaults for all pipeline stages
- **CLI interface**: Click-based CLI with `watch`, `classify`, `patch`, `validate`, `report` subcommands
- **344 tests + 20 benchmarks**: 100% pass rate on CI
- **UTF-8 encoding**: Full unicode/emoji support in all output paths (`6504866`)

### Changed

- (Initial release — no prior versions)

### Fixed

- Node.js 20 deprecation warning suppressed in CI (`affb83c`)
- VCR base_url fix for reliable offline replay (`fea8c8f`)

### Security

- API key sanitization: VCR cassettes and logs strip all real API credentials
- Docker validation uses isolated containers with no network access by default

---

## [Unreleased]

### Added

- Chart.js trend charts and category doughnut charts in HTML dashboard
- `metrics_snapshot` table for daily pipeline statistics tracking
- Package-embedded Jinja2 templates for reliable path resolution
- CI status badge in README
- CHANGELOG.md

### Fixed

- Jinja2 template path resolution: templates now embedded in package to work in all install modes

---

> **Version 0.1.0** marks the first feature-complete release with full pipeline automation,
> LLM integration, Docker sandboxing, CI/CD, and 344 tests.
