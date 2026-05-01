# SelfHeal

智能测试自愈框架 - 让测试用例能够自动检测问题、分类错误、生成修复并验证结果。

## 核心流程

```
Watcher → Classifier → Patcher → Validator → Reporter
  监视     分类        修补      验证       报告
```

## 功能特性

- **多源监视器**: 支持 pytest 测试执行监视和原始日志文件监视
- **智能分类**: 规则分类器 + LLM 分类器双引擎
- **自动修补**: 模板修补 + LLM 智能修补
- **多环境验证**: 本地验证 + Docker 容器验证
- **灵活报告**: 终端输出 + GitHub Issues 报告
- **持久化存储**: 内存存储 + SQLite 存储
- **插件扩展**: 支持自定义组件加载

## 安装

```bash
pip install -e .
pip install -e ".[dev]"        # 开发依赖
pip install -e ".[llm]"        # LLM 支持
pip install -e ".[docker]"     # Docker 支持
pip install -e ".[github]"     # GitHub 集成
```

## 快速开始

```bash
# 监视 pytest 测试
selfheal watch -- pytest tests/

# 使用规则分类器
selfheal classify --rule error.log

# 使用 LLM 修补
selfheal patch --llm --input failure.json

# 验证修复
selfheal validate --local

# 生成报告
selfheal report --terminal
```

## 项目结构

```
selfheal/
├── src/selfheal/
│   ├── cli.py              # 命令行入口
│   ├── config.py           # 配置管理
│   ├── engine.py           # 核心引擎
│   ├── events.py           # 事件系统
│   ├── registry.py         # 组件注册表
│   ├── interfaces/         # 接口定义
│   └── core/               # 核心实现
│       ├── watchers/       # 监视器
│       ├── classifiers/    # 分类器
│       ├── patchers/       # 修补器
│       ├── validators/     # 验证器
│       ├── reporters/      # 报告器
│       └── stores/         # 存储
└── tests/
```

## 配置

配置文件 `selfheal.yaml`:

```yaml
watcher:
  type: pytest  # 或 "raw_log"
  path: tests/

classifier:
  type: rule    # 或 "llm"
  rules:
    - pattern: "AssertionError"
      category: assertion
    - pattern: "ImportError"
      category: import

patcher:
  type: template  # 或 "llm"
  templates_dir: patches/

validator:
  type: local  # 或 "docker"
  timeout: 300

store:
  type: sqlite  # 或 "memory"
  db_path: selfheal.db

reporter:
  type: terminal  # 或 "github"
  github_token: ${GITHUB_TOKEN}
```

## 许可证

MIT
