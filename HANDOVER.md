# SelfHeal 项目交接文档

> 生成日期：2026-05-02  
> 项目路径：`C:\Users\longhuihai\CodeBuddy\20260430210042\代码自迭代功能项目\selfheal\`  
> 版本：v0.1.0 (Alpha)

---

## 一、项目概述

**SelfHeal** 是一个智能测试自愈框架，核心能力是：**自动检测测试失败 → 分类错误 → 生成修复补丁 → 验证修复 → 输出报告**。

### 核心流程

```
Watcher → Classifier → Patcher → Validator → Reporter
  监视      分类        修补      验证       报告
                 ↓
              Store（持久化存储）
```

### 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.10+ |
| 配置管理 | YAML + Pydantic v2 |
| CLI | Click |
| 模板引擎 | Jinja2 |
| 存储 | SQLite / 内存 |
| 可选集成 | OpenAI / Anthropic / DeepSeek (LLM), Docker, GitHub |
| 测试框架 | pytest + pytest-asyncio + vcrpy |
| CI/CD | Jenkins (Jenkinsfile) |

---

## 二、目录结构

```
selfheal/
├── src/selfheal/                 # 源代码
│   ├── cli.py                    # 命令行入口（watch/classify/patch/validate/report/batch/rollback/backups/cleanup/metrics/dashboard/init）
│   ├── config.py                 # 配置模型（Pydantic）
│   ├── engine.py                 # 核心引擎（Pipeline 编排、Hook 系统、回滚）
│   ├── events.py                 # 事件定义（TestFailureEvent → ClassificationEvent → PatchEvent → ValidationEvent）
│   ├── registry.py               # 组件注册表（全局单例）
│   │
│   ├── interfaces/               # 抽象接口
│   │   ├── classifier.py         # ClassifierInterface
│   │   ├── patcher.py            # PatcherInterface
│   │   ├── validator.py          # ValidatorInterface
│   │   ├── reporter.py           # ReporterInterface
│   │   ├── store.py              # StoreInterface
│   │   ├── watcher.py            # WatcherInterface
│   │   └── pipeline_stage.py     # PipelineStage（ABC）
│   │
│   ├── core/                     # 核心实现
│   │   ├── watchers/             # PytestWatcher, RawLogWatcher, PluginWatcher
│   │   ├── classifiers/          # RuleClassifier, LLMClassifier, HybridClassifier
│   │   ├── patchers/             # TemplatePatcher, LLMPatcher
│   │   ├── validators/           # LocalValidator, DockerValidator
│   │   ├── reporters/            # TerminalReporter, GitHubReporter, WebhookReporter
│   │   ├── stores/               # MemoryStore, SQLiteStore
│   │   ├── pipeline_stages/      # ClassifyStage, PatchStage, ValidateStage, ReportStage, StoreStage
│   │   ├── applier.py            # PatchApplier（差量/全量应用、备份、回滚、干跑）
│   │   ├── experience.py         # ExperienceStore（成功修复经验 SQLite 持久化）
│   │   ├── cache.py              # LLMResponseCache（带 TTL 的内存缓存）
│   │   ├── metrics.py            # MetricsCollector
│   │   ├── hooks.py              # Hook（MetricsHook）
│   │   └── dashboard.py          # HTML Dashboard 生成器
│   │
│   ├── plugins/                  # 插件热加载
│   │   └── loader.py             # PluginLoader（支持热重载）
│   │
│   └── patches/                  # Jinja2 补丁模板 (.j2)
│
├── tests/                        # 测试套件（26 个测试文件）
├── docs/                         # 文档
├── pyproject.toml                # 项目配置
├── Jenkinsfile                   # CI/CD 流水线
├── selfheal.example.yaml         # 配置示例
└── runtests.py                   # 简易测试运行器
```

---

## 三、架构设计要点

### 3.1 Pipeline 模式

引擎 (`SelfHealEngine`) 采用可插拔的 Pipeline 架构：

```python
# 默认 Pipeline: classify → patch(含重试) → validate → report → store
# 可通过 selfheal.yaml 的 pipeline.stages 自定义
```

每个 Stage 继承 `PipelineStage` 抽象类，通过 `context` 字典传递中间结果：
- `context["event"]` → `TestFailureEvent`
- `context["classification"]` → `ClassificationEvent`
- `context["patches"]` → `list[PatchEvent]`
- `context["final_validation"]` → `ValidationEvent`

### 3.2 组件注册表

- 全局 Singleton `Registry`，按 `(category, name)` 注册组件类
- 默认组件在 `core/__init__.py` 的 `register_defaults()` 中注册
- 第三方插件通过 `PluginLoader` 自动发现并注册

### 3.3 Hook 系统

- `Hook` 抽象类，提供 `before_stage()` / `after_stage()` 回调
- 内置 `MetricsHook`：记录每阶段耗时
- Hook 失败不影响 Pipeline（仅日志记录）

### 3.4 经验学习

- `ExperienceStore`：成功的修复补丁按错误签名存储在 SQLite 中
- 下次遇到相似错误时，`TemplatePatcher` 优先复用历史成功补丁
- `cache.py` 提供独立的 LLM 响应缓存

### 3.5 安全机制

- `auto_apply` 默认关闭，补丁仅生成不应用
- 应用前自动备份原始文件到 `.selfheal/backups/`
- 验证失败时自动回滚已应用的补丁
- `rollback` CLI 支持手动回滚
- 插件完整性校验（SHA256 checksum）
- Dry-run 模式预览变更

---

## 四、已知问题（全部已修复 ✅）

> 修复日期：2026-05-02

| # | 严重度 | 文件 | 问题 | 修复方式 |
|---|--------|------|------|----------|
| 1 | 🔴 | `core/experience.py` | `prune()` 忽略 `max_age_days` 参数 | 改用 `timedelta(days=max_age_days)` 正确计算截止日期 |
| 2 | 🔴 | `core/patchers/template_patcher.py` | fallback patch 中 `\\n` 转义错误 | 改为 f-string 多行字符串，换行符正确 |
| 3 | 🔴 | `core/applier.py` | `list_backups()` 路径推断错误 | 修复 `target_name` 后缀丢失 + 路径层级修正为 3 级 |
| 4 | 🟡 | `Jenkinsfile` | 修复后测试通过未重置 CI 状态 | 显式设置 `currentBuild.result = 'SUCCESS'` / `'FAILURE'` |
| 5 | 🟡 | `core/pipeline_stages/validate_stage.py` | 硬编码 severity/confidence | 从实际 classification 对象读取 |
| 6 | 🟡 | `cache.py` + `experience.py` | `_make_error_signature()` 重复定义 | 移除重复函数 |
| 7 | 🟡 | `core/dashboard.py` | 调用私有方法 `_get_conn()` | 新增公共方法 `dashboard_data()` 替代 |
| 8 | 🟢 | `core/pipeline_stages/patch_stage.py` | metric 记录不一致 | 非 auto_apply 模式改为记录实际生成结果 `"generated"` |
| 9 | 🟢 | `core/watchers/plugin_watcher.py` | 文件末尾多余空行 | 清理空白行 |
| 10 | 🟢 | `core/pipeline_stages/classify_stage.py` | f-string 日志 | 改为 `%s` 延迟求值 |

---

## 五、快速开始

### 安装

```bash
cd selfheal
pip install -e .              # 基础安装
pip install -e ".[dev]"       # 含测试依赖
pip install -e ".[llm]"       # 含 LLM 支持
pip install -e ".[docker]"    # 含 Docker 验证
pip install -e ".[github]"    # 含 GitHub 集成
```

### 运行测试

```bash
python runtests.py                  # 运行全部测试
python -m pytest tests/ -v          # 详细输出
python -m pytest tests/ -v -k "test_engine"  # 运行特定测试
```

### 初始化配置

```bash
python -m selfheal init              # 生成 selfheal.yaml
```

### 使用 CLI

```bash
python -m selfheal watch -- pytest tests/      # 监视测试
python -m selfheal classify --rule error.log   # 分类错误
python -m selfheal batch --input failures.json --auto-apply  # 批量修复
python -m selfheal rollback                     # 列出可回滚补丁
python -m selfheal rollback --all               # 回滚全部补丁
python -m selfheal dashboard --output report.html  # 生成仪表板
python -m selfheal metrics --json               # 输出指标
```

### CI 集成（Jenkins）

Pipeline 分为 5 个阶段：
1. **Setup** — 检出代码、安装依赖
2. **Run Tests** — 运行测试套件
3. **Self-Heal Repair** — 仅在测试失败时触发，调用 `batch --auto-apply`
4. **Retry Tests** — 重新运行测试验证修复
5. **Metrics Report** — 生成并归档指标文件

需要配置 Jenkins credential：`openai-api-key`（用于 LLM 修复）。

---

## 六、配置说明

`selfheal.yaml` 核心配置项：

```yaml
watcher:
  type: pytest          # pytest | raw_log
  path: tests/

classifier:
  type: rule            # rule | llm | hybrid
  rules:                # 规则分类器的自定义规则
    - pattern: "AssertionError"
      category: assertion
      severity: medium

patcher:
  type: template        # template | llm
  templates_dir: patches/

validator:
  type: local           # local | docker
  timeout: 300

reporter:
  type: terminal        # terminal | github | webhook

store:
  type: sqlite          # memory | sqlite
  db_path: .selfheal/selfheal.db

engine:
  auto_apply: false     # 是否自动应用补丁
  dry_run: false        # 干跑模式
  max_retries: 3        # 最大重试次数
  max_concurrency: 1    # 并发数（>1 启用 asyncio）

pipeline:               # 可自定义 Pipeline 阶段
  stages:
    - type: classify
    - type: patch
      retry: 3
    - type: validate
    - type: report
    - type: store
```

---

## 七、扩展开发

### 添加自定义组件

1. 实现对应接口（`ClassifierInterface` / `PatcherInterface` 等）
2. 设置类属性 `name`
3. 放入 `plugins/` 目录，插件加载器会自动发现和注册

```python
from selfheal.interfaces.classifier import ClassifierInterface
from selfheal.events import TestFailureEvent, ClassificationEvent

class MyClassifier(ClassifierInterface):
    name = "my_classifier"
    
    def classify(self, event: TestFailureEvent) -> ClassificationEvent:
        # 实现分类逻辑
        pass
```

### 添加自定义 Pipeline 阶段

继承 `PipelineStage` 并设置 `name` 属性，放入 `plugins/` 目录即可被自动发现。

---

## 八、联系人

- 项目地址：`https://github.com/wiwo-wuming/selfheal`
- 许可证：MIT
- 版本：v0.1.0 (Alpha)

---

> **交接人**：longhuihai  
> **日期**：2026-05-02
