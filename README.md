# SelfHeal

[![CI](https://github.com/wiwo-wuming/selfheal/actions/workflows/selfheal.yml/badge.svg)](https://github.com/wiwo-wuming/selfheal/actions/workflows/selfheal.yml)
[![PyPI](https://img.shields.io/badge/pypi-0.1.0-blue)](https://pypi.org/project/selfheal/)

**测试失败了？让它自己修。**

SelfHeal 是一个智能测试自愈框架。自动检测测试失败 → 分类错误 → 生成修复补丁 → 验证修复 → 输出报告。支持纯本地规则引擎（免费），也支持接入 LLM（OpenAI/DeepSeek/Anthropic）做真正的代码修复。

---

## 一分钟看懂

```bash
pip install selfheal

# 给你的项目生成配置
selfheal init

# 让 SelfHeal 分析测试失败并生成修复
python -m pytest tests/ --json-report --json-report-file=results.json
python -m selfheal batch --input results.json --dry-run
```

输出类似：

```
[Classification] Category: import, Severity: high
[Generated Patch]
--- a/config.py
+++ b/config.py
-from pathlib import Pathh
+from pathlib import Path  # SelfHeal: fixed typo Pathh → Path
```

---

## 两种模式

| | 规则模式（默认，免费） | LLM 模式（需 API） |
|------|-----|------|
| **分类** | 正则匹配错误类型 | 理解 traceback 语义 |
| **补丁** | 智能模板：修正 typo、加 import、类型转换、重试等 | 理解代码逻辑，生成真正的修复 |
| **成本** | 0 | DeepSeek 约 0.01 元/次 |
| **适合** | 快速见效，CI 自动兜底 | 真正想修 bug |

**规则模式就够了** — typo 修正、import 补全、类型转换、重试、超时处理全都有。

---

## 安装

Python 3.10+。

```bash
pip install selfheal                    # 基础安装
pip install selfheal[dev]               # 开发依赖（pytest 插件等）
pip install selfheal[llm]               # LLM 支持（OpenAI/Anthropic）
pip install selfheal[dashboard]         # 仪表板服务（Flask + gunicorn）
```

---

## 5 分钟接 CI

### GitHub Actions

把下面内容放到 `.github/workflows/selfheal.yml`：

```yaml
name: SelfHeal
on: [push, pull_request]
jobs:
  heal:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install selfheal
      - run: pip install -r requirements.txt  # 你的项目依赖
      - run: python -m pytest tests/ --json-report --json-report-file=results.json || true
      - shell: python
        run: |
          import json, subprocess
          with open("results.json") as f:
              report = json.load(f)
          failures = [{"test_path": t["nodeid"], "error_type": "RuntimeError",
                       "error_message": (t.get("call",{}).get("longrepr","") or "")[:500],
                       "traceback": t.get("call",{}).get("longrepr","")}
                      for t in report.get("tests",[]) if t["outcome"] in ("failed","error")]
          if failures:
              with open("failures.json","w") as f: json.dump(failures, f)
              subprocess.run(["python","-m","selfheal","batch","--input","failures.json","--dry-run"])
```

推代码后，每次测试失败都会自动分析并生成修复建议。

---

## 配置

```yaml
# selfheal.yaml（可选，不写就用默认值）
classifier:
  type: rule              # rule | llm | hybrid

patcher:
  type: template          # template | llm
  refine_rounds: 2        # LLM 多轮自审（仅 llm 模式生效）

engine:
  auto_apply: false       # 安全：不自动改代码
  max_retries: 3

# LLM 配置（可选，用 llm 模式才需要）
classifier:
  type: llm
  llm:
    provider: deepseek
    model: deepseek-chat
    api_key: ${DEEPSEEK_API_KEY}
    base_url: https://api.deepseek.com
```

---

## 仪表板

```bash
selfheal dashboard --serve --port 8080 --open
```

打开 `http://localhost:8080`，可以看到：
- 修复统计（成功率、错误分类饼图、趋势图）
- 补丁列表（按分类/状态筛选）
- 点击任意补丁查看详情和 diff 预览
- 一键 Apply / Rollback
- 每 10 秒自动刷新

---

## CLI 全命令

```bash
selfheal init                        # 生成配置文件
selfheal watch -- pytest tests/      # 监听测试
selfheal classify --input err.json   # 分类单个错误
selfheal patch --input cls.json      # 生成单个补丁
selfheal validate --input patch.json # 验证补丁
selfheal apply --input patch.json    # 应用补丁
selfheal batch --input fails.json    # 批量处理
selfheal rollback                    # 列出/回滚补丁
selfheal metrics                     # 查看统计
selfheal dashboard --serve           # 启动仪表板
selfheal dashboard --output r.html   # 导出静态 HTML
```

---

## 安全

- `auto_apply` 默认关闭 — 补丁只生成不应用
- 应用前自动备份到 `.selfheal/backups/`
- 验证失败自动回滚
- `--dry-run` 预览变更不修改文件
- 插件 SHA256 完整性校验

---

## License

MIT
