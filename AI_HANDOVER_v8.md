# SelfHeal 项目 AI 交接文档 v8

> **更新日期**：2026-05-02 14:55
> **接手 AI：请务必完整阅读本文档后再开始工作**
> **当前版本**：v0.1.0 Alpha
> **Python**：3.14.3 + pytest 9.0.3
> **测试现状**：373 passed / 0 failed / 15 skipped ✅（含 VCR 15 + benchmark 20）
> **Git**：7 commits，已推送 https://github.com/wiwo-wuming/selfheal
> **GitHub Actions CI**：✅ 通过（Node.js 24，无弃用警告）

---

## 零、v7→v8 本轮已完成工作

### Session 1：高优先级 — patch 安全机制 + Docker 沙箱 (`331c32f`)

| 优化 | 文件 | 说明 |
|------|------|------|
| patch 安全机制 | `applier.py`, `engine.py` | `--dry-run`、`--diff-only`、自动备份，防止危险补丁直接应用 |
| Docker 沙箱隔离 | `core/validators/docker_validator.py` | 容器级补丁验证，可配置超时，默认无网络访问 |

### Session 2：低优先级 — dashboard 图表 + 模板内嵌 + 文档 (`e55c62b`)

| 优化 | 文件 | 说明 |
|------|------|------|
| Dashboard Chart.js | `experience.py`, `dashboard.py` | 新增 `metrics_snapshot` 表记录每日统计；Dashboard 新增折线图（30天修复趋势）和饼图（错误类别分布） |
| Jinja2 模板内嵌 | `template_patcher.py`, `src/selfheal/patches/` | 15 个 .j2 模板复制到包内；`_resolve_templates_dir()` 智能回退：绝对路径→CWD→包内→项目根 |
| CHANGELOG.md | `CHANGELOG.md`（新） | v0.1.0 完整发布说明 |
| CI Badge | `README.md` | 添加 GitHub Actions 状态徽章 |

---

## 一、项目是什么

**SelfHeal** 是一个智能测试自愈框架。核心能力：

1. **Watch** — 监控测试执行，捕获失败
2. **Classify** — 规则优先 + LLM 兜底（HybridClassifier）对失败分类
3. **Patch** — 模板/LLM 生成修复补丁，支持经验复用 + 安全机制（dry-run/diff-only/backup）
4. **Validate** — 本地/Docker 沙箱运行测试验证补丁
5. **Report** — 终端/GitHub Issue/Webhook（HMAC 签名）通知结果
6. **Store** — SQLite 持久化事件 + ExperienceStore 经验学习 + metrics_snapshot 趋势数据

管道流程：`classify → patch → validate → report → store`（5 阶段可插拔，支持 severity 条件跳过）

---

## 二、项目目录结构（v8 变更标 ★）

```
代码自迭代功能项目/
├── AI_HANDOVER_v7.md
├── AI_HANDOVER_v8.md            # 本文件 — 最新
├── HANDOVER.md
├── IMPROVEMENT_PLAN.md
└── selfheal/
    ├── .github/workflows/
    │   └── selfheal.yml
    ├── Jenkinsfile
    ├── pyproject.toml
    ├── pytest.ini
    ├── selfheal.example.yaml
    ├── ★ CHANGELOG.md               # v8 新增
    ├── ★ README.md                  # v8：CI badge
    ├── patches/                     # 外层（dev 兼容）
    │   └── (15 个 .j2 模板)
    ├── plugins/loader.py
    ├── src/selfheal/
    │   ├── __init__.py / __main__.py
    │   ├── config.py
    │   ├── engine.py
    │   ├── registry.py
    │   ├── events.py
    │   ├── cli.py
    │   ├── interfaces/              # 7 个 ABC 接口
    │   ├── ★ patches/               # v8 新增：内嵌模板到包内（15 个 .j2）
    │   │   ├── _generic.py.j2
    │   │   ├── assertion/ / config/ / dependency/ / flaky/
    │   │   ├── import/ / memory/ / network/ / permission/
    │   │   ├── resource/ / runtime/ / syntax/ / timeout/
    │   │   └── type/ / value/
    │   └── core/
    │       ├── ★ applier.py         # v8：patch 安全机制
    │       ├── hooks.py / metrics.py
    │       ├── ★ dashboard.py       # v8：Chart.js 趋势图 + 饼图
    │       ├── cache.py
    │       ├── ★ experience.py       # v8：metrics_snapshot 表
    │       ├── watchers/
    │       ├── classifiers/
    │       ├── patchers/
    │       │   ├── ★ template_patcher.py  # v8：智能路径解析
    │       │   └── llm_patcher.py
    │       ├── validators/
    │       │   ├── ★ docker_validator.py  # v8：Docker 沙箱
    │       │   └── local_validator.py
    │       ├── reporters/
    │       ├── stores/
    │       └── pipeline_stages/
    └── tests/                        # 24 个测试文件
        ├── conftest.py
        ├── test_llm_vcr.py
        ├── vcr_cassettes/openai/
        ├── test_benchmark.py
        ├── test_hybrid_classifier.py
        ├── test_cache.py
        └── test_experience.py
```

---

## 三、如何运行测试

### 全量测试（含 VCR + benchmark）
```bash
cd selfheal/
python -m pytest tests/ -x -q
```
**状态**：**373 passed / 15 skipped / 0 failed** ✅（~570s）

### 不含 VCR 的核心测试
```bash
python -m pytest tests/ --ignore=tests/test_llm_vcr.py --ignore=tests/test_benchmark.py -v
```
**状态**：**344 passed / 0 failed** ✅（~108s）

### VCR 测试（CI 回放 - 无需 key）
```bash
$env:CI = "1"
python -m pytest tests/test_llm_vcr.py -v
# 结果：15 passed（6 DeepSeek + 9 Anthropic cassette 回放）
```

### VCR 测试（录制 - 需 API key）
```bash
$env:DEEPSEEK_API_KEY = "sk-xxx"
$env:ANTHROPIC_API_KEY = "sk-ant-xxx"
python -m pytest tests/test_llm_vcr.py -v --record-mode=rewrite
```

### Benchmark 性能测试
```bash
python -m pytest tests/test_benchmark.py --benchmark-only
```

---

## 四、v8 新增/变更速查

### Dashboard Chart.js 图表
```bash
selfheal dashboard                    # 输出到 stdout（含实时 JS 图表）
selfheal dashboard --output dashboard.html
```

Dashboard 现在包含：
- 6 个统计卡片（Total Fixes / Unique Errors / Total Successes / Pipeline Runs / Avg Time / Success Rate）
- 📈 **30 天修复趋势折线图**（Total Fixes + Total Successes + Unique Signatures 三条线）
- 🍩 **错误类别分布饼图**（基于 `metrics_snapshot` 快照数据）
- Top Error Categories 表格 / Most Frequent Errors 表格 / Recent Fixes 表格

### metrics_snapshot 表
`experience.py` 新增 `metrics_snapshot` 表，需在每次 pipeline 批处理后调用：
```python
experience = get_experience()
experience.record_metrics_snapshot(pipeline_runs=5, avg_pipeline_time=2.3)
```

`get_metrics_history(days=30)` 返回趋势数据供 Dashboard 图表使用。

### Jinja2 模板路径解析
模板现在有两份副本：
- `patches/`（项目根，dev 兼容）
- `src/selfheal/patches/`（包内，安装后可用）

`TemplatePatcher._resolve_templates_dir()` 按以下优先级查找：
1. 绝对路径
2. CWD 相对路径
3. 包内路径（`src/selfheal/patches/`）
4. 项目根路径（`selfheal/patches/`）
5. 回退到 CWD 路径（通过 fallback 硬编码补丁兜底）

### Patch 安全机制
```bash
selfheal patch --dry-run       # 只预览补丁，不写入
selfheal patch --diff-only     # 只输出 diff，不应用
selfheal patch --backup        # 应用前自动备份原文件为 .bak
```

### Docker 沙箱验证
```yaml
validator:
  type: docker
  timeout: 300           # 容器超时（秒）
  network: "none"        # 默认无网络，防恶意补丁外联
  image: "python:3.14-slim"
```

---

## 五、架构约定和注意事项

### 5.1 新增 ErrorCategory 需同步改 4 处
1. `events.py` → `ErrorCategory` 枚举
2. `rule_classifier.py` → `DEFAULT_RULES` 列表
3. `llm_classifier.py` → `_build_prompt()` 类别列表
4. `template_patcher.py` → `_generate_fallback_patch()` dict
5. （可选）`patches/<category>/default.py.j2` → Jinja2 模板（包内 + 项目根两份都要加！）

### 5.2 异步批处理注意事项
- `process_batch()` 根据 `engine.async_batch` 自动选择同步/异步路径
- 异步路径用 `asyncio.Semaphore` 限流，避免 LLM 并发超限
- `_async_process_failure()` 用 `asyncio.to_thread` 包装同步管道

### 5.3 LLM 客户端必须传 base_url + get_api_key()
```python
self.client = OpenAI(
    api_key=self.llm_config.get_api_key(),  # 不是 .api_key！
    base_url=self.llm_config.base_url,
)
```

### 5.4 Template patcher 路径解析
修改模板文件后，**包内副本也要同步更新**。模板有两处：
- `selfheal/patches/`（项目根）
- `selfheal/src/selfheal/patches/`（包内）

### 5.5 禁止事项
- 裸 `except Exception` 不加 `except (KeyboardInterrupt, SystemExit): raise`
- Hook 修改 context（只读观察者）
- 组件不设 `name` 类属性

---

## 六、全版本 Bug 修复汇总

| # | 版本 | 问题 | 修复 |
|---|------|------|------|
| 1 | v5 | CMD `set` 尾随空格导致 API key 非法 | `_resolve_env()` + `LLMConfig` 双重 strip |
| 2 | v5 | `_get_client()` 未传 `base_url` | 两个 LLM 组件 `OpenAI()` 加 `base_url` |
| 3 | v5 | VCR fixture 类型包裹错误 | 改为 `ClassifierConfig(llm=LLMConfig(...))` |
| 4 | v5 | CI 下 Anthropic 包缺失崩溃 | `try: import anthropic` + 条件跳过 |
| 5 | v6 | 验证失败补丁残留 | engine 自动回滚 |
| 6 | v6 | Docker 不可用时崩溃 | 三层检查优雅降级 |
| 7 | v7 | Fallback 补丁 target_file=None | `_generate_fallback_patch()` 返回 tuple |
| 8 | v7 | CI Node.js 20 弃用警告 | workflow 加 `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24` |
| 9 | v8 | Jinja2 模板 pip install 后找不到 | 模板内嵌到包内 + 四级路径回退 |

---

## 七、速查表

| 想做什么 | 改哪个文件 | 注意事项 |
|----------|-----------|----------|
| 加错误分类规则 | `classifiers/rule_classifier.py` | `DEFAULT_RULES` 列表，用 `ErrorCategory` 枚举 |
| 加 ErrorCategory | `events.py` → `rule_classifier.py` → `llm_classifier.py` → `template_patcher.py` | **4 步全改** |
| 加修补模板 | `patches/<cat>/default.py.j2` + 包内副本 | 两处模板目录都要加 |
| 加组件类型 | `registry.py` + `interfaces/` + `loader.py` | 三步都要改 |
| 加管道阶段 | `core/pipeline_stages/xxx_stage.py` | 设 `name` 属性，注册到 `__init__.py` |
| 改 LLM 客户端 | `llm_classifier.py` / `llm_patcher.py` | **必须用 `get_api_key()` + 传 `base_url`** |
| 写 VCR 测试 | `tests/test_llm_vcr.py` | 用 `@pytest.mark.vcr` + `vcr_openai` fixture |
| 写 Docker 测试 | `tests/test_docker_validator.py` | **必须设 `_test_mode=True`** |
| 写 Benchmark | `tests/test_benchmark.py` | 用 `benchmark` fixture 包裹被测函数 |
| 配置 webhook 签名 | `config.py` reporter 块 | `webhook_secret: "${ENV}"` |
| 开启异步批处理 | `config.yaml` → `engine.async_batch: true` | 设 `max_concurrency` 控制并行数 |
| 生成 Dashboard | CLI：`selfheal dashboard --output file.html` | 依赖 ExperienceStore SQLite |
| 记录 metrics 快照 | `experience.record_metrics_snapshot()` | 每次 pipeline 后调用 |

---

## 八、Git 提交日志

```
e55c62b feat: low-priority optimizations - dashboard charts, template embedding, changelog, CI badge
331c32f feat: patch safety mechanisms + Docker sandbox isolation
6504866 fix: DeepSeek provider support + UTF-8 encoding
affb83c fix(ci): suppress Node.js 20 deprecation warning in GitHub Actions
ebdf1ee feat: 全面优化 - 模板扩展、VCR测试、API key独立、HTML仪表盘、异步并行、benchmark
fea8c8f P4: VCR cassettes + base_url fix + CI replay verified (319 tests ok)
76bf6f0 Initial commit: 278 tests passing, pre-cleanup state
```

---

## 九、项目现状评估

### 强项
- 373 测试 + 20 benchmark，100% 通过
- 15 个错误类别覆盖 Python 主流异常
- 15 个 Jinja2 模板已内嵌到包内，路径解析有四级回退
- 可插拔架构：pipeline stages / classifier / patcher 全动态注册
- 异步批处理 3x 加速
- CI/CD 完整自动化（测试→自愈→报告→Issue）
- API key 脱敏：VCR cassettes 不含任何真实密钥
- Patch 安全机制：dry-run / diff-only / auto-backup
- Docker 沙箱验证：网络隔离、可配置超时
- Dashboard：Chart.js 趋势图 + 饼图 + 统计卡片

### 待改进
| 问题 | 严重度 | 说明 |
|------|--------|------|
| 缺少真实项目验证 | 🔴 高 | 测试多为 mock 事件，未接入实际项目 |
| LLM classifier 未真实跑过 | 🔴 高 | Anthropic cassette 有回放，但无真实 API 调用验证 |

### 推荐下一步
1. **🔴 找一个真实 Python 项目接入** — 验证框架核心价值
2. **🔴 用真实 API key 跑一次 LLM 全链路** — 验证 Anthropic/DeepSeek 端到端

---

> **文档版本**：v8
> **生成时间**：2026-05-02 14:55
> **下一位 AI 接手请从"推荐下一步"开始**
