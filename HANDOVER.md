# SelfHeal 项目交接文档

> 生成日期：2026-05-04  
> 版本：v0.3.0  
> 仓库：https://github.com/wiwo-wuming/selfheal

---

## 一、项目概述

SelfHeal 是一个智能测试自愈框架。自动检测测试失败 → 分类错误 → 生成修复补丁 → 验证修复 → 输出报告。

**核心能力**：规则引擎（免费）+ LLM 引擎（可接 API），策略失败自动切换。

---

## 二、架构

```
Watcher → Classifier → Patcher → Validator → Reporter
  监视       分类         修补       验证        报告
                           ↓
                     Store / Experience
```

每个阶段都是可插拔的 Pipeline Stage，用户可自定义顺序和组件。

### 核心模块

| 文件 | 职责 |
|------|------|
| `src/selfheal/engine.py` | 核心引擎，Pipeline 编排，Hook 系统，策略切换 |
| `src/selfheal/cli.py` | 12 个子命令：watch/classify/patch/validate/report/batch/apply/rollback/backups/cleanup/metrics/dashboard/init |
| `src/selfheal/config.py` | Pydantic v2 配置模型，环境变量 ${ENV} 解析 |
| `src/selfheal/events.py` | 事件数据类：TestFailureEvent → ClassificationEvent → PatchEvent → ValidationEvent |
| `src/selfheal/registry.py` | 组件注册表（单例） |

### 核心实现

| 目录 | 内容 |
|------|------|
| `core/watchers/` | PytestWatcher, RawLogWatcher, PluginWatcher |
| `core/classifiers/` | RuleClassifier（正则）, LLMClassifier（API）, HybridClassifier（规则+LLM 降级） |
| `core/patchers/` | TemplatePatcher（Jinja2+智能 typo 修复）, LLMPatcher（多轮 self-refine+质量评分） |
| `core/validators/` | LocalValidator（pytest）, DockerValidator（沙箱） |
| `core/reporters/` | TerminalReporter, GitHubReporter, WebhookReporter（HMAC 签名） |
| `core/stores/` | MemoryStore, SQLiteStore |
| `core/applier.py` | PatchApplier：备份/应用/回滚/干跑/清理 |
| `core/experience.py` | ExperienceStore：成功修复 SQLite 持久化+复用 |
| `core/cache.py` | LLM 响应缓存（带 TTL） |
| `core/metrics.py` | 指标收集 |
| `core/hooks.py` | Hook 系统（MetricsHook） |
| `core/dashboard.py` | HTML 仪表板（纯 Canvas 图表） |
| `core/dashboard_server.py` | Flask 仪表板服务器（支持 gunicorn） |
| `plugins/loader.py` | 插件热加载（SHA256 完整性校验） |

---

## 三、三大核心特性

### 1. 三层质量保障

| 层 | 机制 | 文件 |
|---|------|------|
| 质量检查 | 检测补丁中 pass/skip/xfail/importorskip，标记 low_quality | `patch_stage.py:_check_patch_quality()` |
| LLM 自审 | LLM 补丁生成后打分 0-10，低于阈值(默认4)拒绝 | `llm_patcher.py:_score_patch()` |
| 全量回归 | 修补后跑全量测试，不单文件 | `local_validator.py:_build_test_command()` |

### 2. 策略自动切换（Strategy Fallback）

```
Template patcher 生成补丁 → 验证失败 → 自动切 LLM patcher 重试
```

配置：`engine.strategy_fallback: true` + `patcher.llm: ...`

### 3. Template Patcher 智能补丁

| 错误类型 | 修复策略 |
|---------|---------|
| typo import (`Pathh→Path`) | 正则匹配 Did you mean 提示，修正拼写 |
| 缺失 import | `import module` |
| 子模块 import | `from module import name` |
| TypeError | `str()` 类型转换 |
| NetworkError | 3 重试 + 指数退避 |
| MemoryError | `islice` 分批 |
| flaky test | `@pytest.mark.flaky` |
| 运行时错误 | try-except 防护 |
| 断言不匹配 | 标记注释 |

---

## 四、仪表板

```bash
# 静态导出
python -m selfheal dashboard --output report.html

# 交互式服务器
python -m selfheal dashboard --serve --port 8080 --open

# 生产模式
python -m selfheal dashboard --serve --production
```

### 仪表板功能

- KPI 卡片（Total Fixes / Unique Errors / Successes / Pipeline Runs / Success Rate）
- 纯 Canvas 手绘折线图（趋势，渐变填充）
- 纯 Canvas 手绘环形图（分类分布，中心数字+右侧图例）
- 补丁列表（按分类/状态筛选，斑马纹，行点击）
- 弹窗查看补丁详情 + Apply / Rollback 一键操作
- 10 秒自动刷新 + LIVE 指示灯
- 入场动画（卡片依次淡入）
- 响应式（640px / 900px 断点）
- 深色主题 + CSS 变量配色

### API 端点

| 端点 | 方法 | 功能 |
|------|------|------|
| `/api/stats` | GET | 统计数据 |
| `/api/patches` | GET | 补丁列表（支持 category/status 筛选） |
| `/api/patches/:id/apply` | POST | 应用补丁 |
| `/api/patches/:id/rollback` | POST | 回滚补丁 |

---

## 五、测试

```bash
# 全量测试（不含 benchmark）
python -m pytest tests/ -k "not test_bench"

# VCR 回放模式（不需要 API key）
CI=1 python -m pytest tests/test_llm_vcr.py -v
```

**当前状态：400 passed, 6 skipped, 0 failed**

---

## 六、CI 集成

### GitHub Actions

```yaml
# 在目标项目 .github/workflows/selfheal.yml
- run: pip install selfheal
- run: python -m pytest tests/ --json-report --json-report-file=results.json || true
- shell: python
  run: |
    import json, subprocess
    # 从 results.json 提取 failures → failures.json
    subprocess.run(["python","-m","selfheal","batch","--input","failures.json","--dry-run"])
```

### Jenkins

见 `Jenkinsfile`：5 阶段 pipeline（Setup → Run Tests → Self-Heal Repair → Retry Tests → Metrics Report）

---

## 七、配置示例

```yaml
# selfheal.yaml
classifier:
  type: hybrid          # rule → free; LLM fallback for ambiguous
  llm:
    provider: deepseek
    model: deepseek-chat
    api_key: ${DEEPSEEK_API_KEY}
    base_url: https://api.deepseek.com

patcher:
  type: template        # free
  llm:                  # fallback on template failure
    provider: deepseek
    model: deepseek-chat
    api_key: ${DEEPSEEK_API_KEY}
    base_url: https://api.deepseek.com
  refine_rounds: 2      # LLM 多轮自审
  quality_threshold: 4  # LLM 补丁最低质量分(0-10)

engine:
  auto_apply: false     # 安全：不自动修改源文件
  strategy_fallback: true # 失败时切 LLM
  max_retries: 3

validator:
  type: local
```

---

## 八、依赖

| 分类 | 包 |
|------|----|
| 核心 | click, pyyaml, pydantic>=2.0, jinja2 |
| 可选-LLM | openai, anthropic |
| 可选-仪表板 | flask, gunicorn |
| 可选-插件热加载 | watchdog |
| 可选-Docker | docker |
| 可选-GitHub | PyGithub |
| 开发 | pytest, pytest-asyncio, pytest-benchmark, pytest-json-report, vcrpy, ruff, mypy |

---

## 九、已知局限

1. **Windows GBK 编码** — subprocess 中文本输出编码问题，已在 `local_validator.py` 加 `PYTHONIOENCODING=utf-8` 缓解，Linux/CI 无此问题
2. **Template patcher 边界** — 复杂逻辑 bug（非 typo/import/类型错误）修不准，需 LLM 兜底
3. **多语言** — 仅支持 Python/pytest

---

## 十、移交清单

| 项 | 位置 |
|----|------|
| 源码 | `src/selfheal/` |
| 测试 | `tests/` (28 个测试文件) |
| CI 配置 | `Jenkinsfile` + `.github/workflows/selfheal.yml` |
| README | `README.md` |
| 交接文档 | `HANDOVER.md`（本文件） |
| 仓库 | https://github.com/wiwo-wuming/selfheal |
| PyPI | 待发布（`dist/` 目录已 build） |
