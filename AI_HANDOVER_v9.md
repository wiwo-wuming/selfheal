# SelfHeal 项目 AI 交接文档 v9

> **更新日期**：2026-05-07
> **接手 AI：请务必完整阅读本文档后再开始工作**
> **当前版本**：v0.1.0 Alpha
> **Python**：3.14.3
> **测试现状**：436 passed / 0 failed / 0 skipped
> **Git**：9 commits，已推送 https://github.com/wiwo-wuming/selfheal
> **配置**：DeepSeek API key 在 `.env`，不在代码中

---

## v8→v9 本轮已完成工作

### Round 1（LLM 交互层现代化）

| 改动 | 文件 | 说明 |
|------|------|------|
| 统一 LLM 客户端工厂 | `core/llm_client.py`（新） | `LLMClientFactory` 单例 + `call_structured()` 带 tool use/function calling + `call_with_retry()` 指数退避 |
| structured output | `classifiers/llm_classifier.py` | 用 tool use（Anthropic）/ function calling（OpenAI）替代正则 JSON 解析；加 system prompt + prompt caching |
| 多轮对话合并 + 流式 | `patchers/llm_patcher.py` | generate→review→refine 从 4 次独立调用改为 1 次多轮对话 + 1 次评分；新增 `generate_stream()` |
| 配置扩展 | `config.py` | `LLMConfig` 新增 `max_retries`, `retry_backoff`, `enable_streaming`, `enable_prompt_caching`, `enable_tool_use`, `timeout` |
| cache 增强 | `core/cache.py` | 新增 `prompt_caching_hint` 属性 |
| VCR 重录 | `tests/vcr_cassettes/` | 15 个 cassette 全部用新 API 格式重录，包括 Anthropic 兼容端点录制 |

### Round 2（结构优化三路并行）

| 改动 | 文件 | 说明 |
|------|------|------|
| dotenv 自动加载 | `config.py`, `pyproject.toml` | `python-dotenv` 在模块加载时自动执行，try/except 静默回退 |
| 策略类拆分 | `patchers/template_patcher.py`→`strategies/` | 622 行拆为调度器 + 5 个策略文件（base/assertion/import_strategy/runtime/fallback），`TemplateRenderStrategy` 提取三级回退逻辑 |
| 共享 diff 解析器 | `core/diff_parser.py`（新） | 从 `applier.py` 和 `docker_validator.py` 提取 ~90 行重复 hunk 解析代码 |

### Round 3（缓存 + 安全 + CLI 拆分）

| 改动 | 文件 | 说明 |
|------|------|------|
| 规则结果缓存 | `classifiers/hybrid_classifier.py` | 复用 `LLMResponseCache`，相同错误签名跳过 26 条正则匹配 |
| Webhook 防重放 | `reporters/webhook_reporter.py` | timestamp + nonce + 增强 HMAC 签名覆盖三段；新增 `verify_request()` 静态验证方法 |
| CLI 拆分 | `cli.py`→`cli/` | 651 行拆为 `cli/__init__.py`（调度入口）+ 13 个 `cli/<command>.py` + `cli/utils.py` |

---

## 一、项目是什么

**SelfHeal** — 智能测试自愈框架。可插拔流水线：Watch → Classify → Patch → Validate → Report → Store。支持纯本地规则（免费）和 LLM（OpenAI/DeepSeek/Anthropic）两种模式。

### 两种模式

|  | 规则模式（免费） | LLM 模式 |
|------|-----|------|
| 分类 | 26 条正则匹配 | Tool use 结构化输出 |
| 补丁 | 15 种硬编码 diff 模板 + Jinja2 | 多轮对话 self-refinement |
| 验证 | 本地 pytest | 本地/Docker 沙箱 |
| 成本 | 0 | DeepSeek 约 0.01 元/次 |

---

## 二、项目结构（重点文件）

```
selfheal/
├── pyproject.toml              # 构建配置 + 依赖定义 + ruff/mypy 配置
├── pytest.ini                  # pytest 路径 + vcr marker
├── .env                        # API keys（不提交！）
├── CLAUDE.md                   # Claude Code 操作指南
├── selfheal.yaml               # 运行时配置（可选）
├── patches/                    # Jinja2 模板（15 种类型的 .j2 文件）
│
├── src/selfheal/
│   ├── __init__.py             # 版本号
│   ├── config.py               # Pydantic v2 配置模型（所有组件配置）
│   ├── engine.py               # SelfHealEngine — 流水线编排
│   ├── events.py               # 4 种事件 dataclass + ErrorSeverity/ErrorCategory 枚举
│   ├── registry.py             # 全局组件注册表（单例）
│   │
│   ├── interfaces/             # 7 个 ABC 抽象基类
│   │   ├── watcher.py, classifier.py, patcher.py
│   │   ├── validator.py, reporter.py, store.py
│   │   └── pipeline_stage.py
│   │
│   ├── core/
│   │   ├── llm_client.py       # 统一 LLM 客户端（tool use + retry + error 分类）
│   │   ├── diff_parser.py      # 共享 unified-diff 解析（applier + docker 共用）
│   │   ├── cache.py            # LLMResponseCache（内存缓存，按错误签名 key）
│   │   ├── experience.py       # ExperienceStore（SQLite，记录成功补丁供复用）
│   │   ├── applier.py          # PatchApplier — 备份/应用/回滚/dry-run
│   │   ├── metrics.py          # MetricsCollector — 计数 + 计时
│   │   ├── hooks.py            # Hook 观察者（MetricsHook 等）
│   │   ├── utils.py            # make_error_signature（SHA256）
│   │   ├── dashboard.py        # 静态 HTML 仪表板生成
│   │   ├── dashboard_server.py # Flask 仪表板服务器
│   │   │
│   │   ├── classifiers/
│   │   │   ├── rule_classifier.py      # 26 条正则规则
│   │   │   ├── llm_classifier.py       # LLM + tool use 分类
│   │   │   └── hybrid_classifier.py    # 规则优先 → LLM 兜底 + 规则缓存
│   │   │
│   │   ├── patchers/
│   │   │   ├── template_patcher.py     # 模板补丁调度器
│   │   │   ├── llm_patcher.py          # LLM 补丁（多轮对话 + 流式）
│   │   │   └── strategies/             # 策略类
│   │   │       ├── base.py             # PatchStrategy ABC + TemplateRenderStrategy
│   │   │       ├── assertion.py        # ASSERTION
│   │   │       ├── import_strategy.py  # IMPORT（三重策略）
│   │   │       ├── runtime.py          # RUNTIME/TYPE/VALUE/SYNTAX
│   │   │       └── fallback.py         # 其余 9 种类型
│   │   │
│   │   ├── validators/
│   │   │   ├── local_validator.py      # 本地 pytest 验证
│   │   │   └── docker_validator.py     # Docker 沙箱验证
│   │   │
│   │   ├── reporters/
│   │   │   ├── terminal_reporter.py    # 彩色终端输出
│   │   │   ├── github_reporter.py      # GitHub Issue
│   │   │   └── webhook_reporter.py     # Slack/Discord webhook + HMAC 签名
│   │   │
│   │   ├── watchers/
│   │   │   ├── pytest_watcher.py       # pytest 输出监听
│   │   │   ├── raw_log_watcher.py      # 日志文件 tail
│   │   │   └── plugin_watcher.py       # 插件热重载
│   │   │
│   │   ├── stores/
│   │   │   ├── memory_store.py         # 内存列表
│   │   │   └── sqlite_store.py         # SQLite 持久化
│   │   │
│   │   └── pipeline_stages/
│   │       ├── classify_stage.py, patch_stage.py, validate_stage.py
│   │       ├── report_stage.py, store_stage.py
│   │
│   ├── cli/
│   │   ├── __init__.py         # Click group 入口（main 函数）+ 命令注册
│   │   ├── utils.py            # 共享工具：reconstruct_* + make_rollback_patch
│   │   ├── watch.py, classify.py, patch.py, validate.py, report.py
│   │   ├── batch.py, rollback.py, backups.py, cleanup.py, metrics.py
│   │   ├── dashboard.py, init.py, apply.py
│   │
│   ├── plugins/
│   │   └── loader.py           # 插件发现 + SHA256 校验
│   │
│   └── patches/                # 包内嵌的 Jinja2 模板
│       ├── _generic.py.j2
│       ├── assertion/default.py.j2
│       └── (其他 14 个分类的模板)
│
├── tests/
│   ├── conftest.py             # 共享 fixture + mock 工厂
│   ├── test_llm_vcr.py         # VCR 录制/回放测试
│   ├── test_llm_integration.py # LLM mock 测试（16 个）
│   ├── test_integration_full.py # 全栈集成测试（39 个）
│   ├── test_diff_parser.py     # diff 解析器测试（9 个）
│   ├── test_cli.py             # CLI 测试（27 个命令）
│   ├── test_hybrid_classifier.py # Hybrid 分类器测试（9 个）
│   ├── test_template_patcher.py  # 模板补丁测试（22 个）
│   └── (test_applier, test_engine, test_cache, ...)
│
└── .selfheal/
    ├── backup_index.json       # 补丁备份索引
    ├── backups/                # .bak 备份文件
    └── experience.db           # 成功补丁经验库
```

---

## 三、关键架构决策

### 3.1 组件注册与创建

所有组件通过 `Registry` 单例注册，`SelfHealEngine._setup_components()` 在启动时根据 config 的 `type` 字段创建对应类型的实例：

```python
# registry.py 模式
registry.register_classifier("hybrid", HybridClassifier)
# engine.py 使用
cls = registry.get_classifier(config.classifier.type)
self.classifier = cls(config.classifier)
```

### 3.2 LLM 调用层次

```
用户代码
  → LLMClassifier.classify() / LLMPatcher.generate()
    → call_structured()  [llm_client.py]
      → call_with_retry()  [指数退避，最多 3 次]
        → OpenAI.chat.completions.create() / Anthropic.messages.create()
          → tool use / function calling（结构化输出）
          → regex fallback（解析失败时）

LLMClientFactory.get_client() 按 (provider, base_url) 缓存客户端实例。
```

### 3.3 补丁生成策略分发

```
TemplatePatcher.generate()
  → _try_experience_patch()   # 1. 经验库复用
  → get_strategy(category)    # 2. 策略分发
    → ImportStrategy.generate()     # IMPORT：三重策略（typo/子模块/裸import）
    → AssertionStrategy.generate()  # ASSERTION：Jinja2 模板
    → RuntimeStrategy.generate()    # RUNTIME/TYPE/VALUE/SYNTAX
    → FallbackStrategy.generate()   # 其余 9 种类型
  → generic template           # 3. 兜底：_generic.py.j2
  → _generate_fallback_patch() # 4. 最终回退：15 种硬编码 diff
```

### 3.4 三种 provider 两种 SDK 的适配

| Provider | SDK | 端点 | tool use 方式 |
|----------|-----|------|--------------|
| OpenAI | `openai.OpenAI` | `api.openai.com` | function calling |
| DeepSeek | `openai.OpenAI` | `api.deepseek.com` | function calling（OpenAI 兼容） |
| DeepSeek Anthropic | `anthropic.Anthropic` | `api.deepseek.com/anthropic` | Anthropic tool use |
| Anthropic | `anthropic.Anthropic` | `api.anthropic.com` | Anthropic tool use |

VCR 录制用 DeepSeek 而非 Claude（成本更低）。Anthropic cassettes 通过 `api.deepseek.com/anthropic` 兼容端点录制。

### 3.5 安全性

- `auto_apply` 默认 `False` — 不自动修改代码
- `PatchApplier` 应用前自动备份到 `.selfheal/backups/`，失败自动回滚
- `--dry-run` 预览变更不修改文件
- Webhook HMAC-SHA256 签名覆盖 timestamp+nonce+payload，`verify_request()` 用 `hmac.compare_digest` 防时序攻击
- 插件 SHA256 完整性校验框架
- API keys 通过环境变量注入，`.env` 不提交

---

## 四、开发环境

### 安装

```bash
pip install -e ".[dev,llm,dashboard,docker,github,hotreload]"
```

### 开发循环

```bash
pip install -e .                     # editable install
python -m pytest tests/ -q           # 全量（~10 min, 436 tests）
python -m pytest tests/test_XXX.py -v # 单文件
python -m pytest tests/ -x           # 遇错停止
ruff check src/                      # lint
mypy src/                            # type check
python -m selfheal --help            # CLI 可用性检查
```

### VCR 测试

```bash
# 录制（需要 API key）
OPENAI_API_KEY=sk-xxx python -m pytest tests/test_llm_vcr.py -v

# 重录特定 cassette
rm tests/vcr_cassettes/openai/test_llm_classify_assertion.yaml
OPENAI_API_KEY=sk-xxx pytest tests/test_llm_vcr.py::TestLLMClassifierVCR::test_llm_classify_assertion -v

# CI 回放模式（不需要 key）
CI=1 python -m pytest tests/test_llm_vcr.py -v
```

---

## 五、常见改动模式

### 新增一种错误分类策略

1. 在 `patchers/strategies/` 创建 `<name>.py`，继承 `TemplateRenderStrategy` 或 `PatchStrategy`
2. 设置 `category = ErrorCategory.XYZ`
3. 在 `strategies/__init__.py` 注册
4. 创建对应的 Jinja2 模板 `patches/<category>/default.py.j2`（可选，不创建则走回退链）

### 新增一个 CLI 子命令

1. 在 `cli/<name>.py` 创建 `@click.command()` 函数
2. 在 `cli/__init__.py` 底部添加 `from selfheal.cli.<name> import <name>; main.add_command(<name>)`

### API call pattern 变更后

1. 删除对应 cassettes：`rm tests/vcr_cassettes/openai/test_llm_*.yaml`
2. 重新录制：`OPENAI_API_KEY=sk-xxx pytest tests/test_llm_vcr.py -v`
3. 验证全量测试通过

---

## 六、已知限制与未来方向

- **Windows 兼容**：`diff_parser._apply_diff_subprocess` 调用系统 `patch` 命令，Windows 无此命令，会 fallback 到 False。可在 Windows 上跳过系统命令回退
- **Webhook 重试超时**：timestamp 在循环外生成，重试间隔超过 5 分钟会导致 `verify_request` 拒绝请求
- **mypy strict**：部分新文件（如 strategies）缺少完整类型注解，`mypy --strict` 可能报错
- **并发安全**：`LLMClientFactory._cache` 是普通 dict，多线程时可能有竞态条件
- **Dashboard**：图表用 Canvas 手绘，可升级为 Chart.js
- **Plugin 沙箱**：SHA256 校验框架已存在但未实际执行插件隔离

---

## 七、提交历史

```
61d5199 feat: add rule cache, webhook anti-replay, and split CLI into subcommands
c01e4cb refactor: add dotenv auto-load, split template patcher into strategies, extract shared diff parser
16af8c5 feat: add unified LLM client with tool use, retry, and prompt caching
77867a6 fix: dashboard trend chart empty when no metrics snapshots exist
ca9de2d docs: handover doc v0.3.0 + dashboard redesign
```

---

## 八、快速核对清单（接手后第一时间跑）

- [ ] `pip install -e ".[dev,llm]"` — 可编辑安装
- [ ] `python -m pytest tests/ -q` — 436 全过
- [ ] `python -m selfheal --help` — CLI 列出 12 个子命令
- [ ] `python -m selfheal init` — 能生成配置
- [ ] `ruff check src/` — 无 lint 错误
- [ ] 检查 `.env` 存在且包含 `DEEPSEEK_API_KEY`
