# SelfHeal 产品需求文档

## 1. 概念与愿景

SelfHeal 是一个**智能测试自愈框架**，旨在解决测试用例脆弱性问题。当测试失败时，它能自动分析失败原因、生成修复补丁、验证修复效果，形成完整的"检测-修复-验证"闭环。让开发者从繁琐的测试维护工作中解放出来。

**核心理念**: 测试应该像生物体一样，具备自我修复的能力。

## 2. 系统架构

### 2.1 核心流程

```
┌─────────┐    ┌────────────┐    ┌──────────┐    ┌───────────┐    ┌──────────┐
│ Watcher │ -> │ Classifier │ -> │ Patcher  │ -> │ Validator │ -> │ Reporter │
└─────────┘    └────────────┘    └──────────┘    └───────────┘    └──────────┘
   监视           分类              修补            验证             报告
```

### 2.2 组件职责

| 组件 | 职责 | 输入 | 输出 |
|------|------|------|------|
| Watcher | 监视测试执行/日志 | 测试进程/日志文件 | 原始错误事件 |
| Classifier | 分类错误类型 | 错误事件 | 分类结果(类型/严重度) |
| Patcher | 生成修复补丁 | 分类结果+原始代码 | 修复补丁 |
| Validator | 验证修复效果 | 补丁+测试环境 | 验证结果(通过/失败) |
| Reporter | 输出报告 | 验证结果 | 格式化报告 |

## 3. 功能需求

### 3.1 Watcher (监视器)

#### 3.1.1 PytestWatcher
- 捕获 pytest 执行过程中的错误
- 解析测试输出，提取关键错误信息
- 支持自定义 pytest 选项

#### 3.1.2 RawLogWatcher
- 监视日志文件变化
- 使用 tail 模式实时读取新内容
- 支持多种日志格式

### 3.2 Classifier (分类器)

#### 3.2.1 RuleClassifier
- 基于正则规则的分类
- 内置常见错误模式:
  - `AssertionError` → assertion
  - `ImportError` → import
  - `TimeoutError` → timeout
  - `ConnectionError` → network
- 支持 YAML 配置自定义规则

#### 3.2.2 LLMClassifier
- 使用大语言模型进行智能分类
- 支持 OpenAI / Anthropic 接口
- 能够理解上下文和语义

### 3.3 Patcher (修补器)

#### 3.3.1 TemplatePatcher
- 基于模板的修复
- 模板目录结构:
  ```
  patches/
  ├── assertion/
  │   └── assert_equal.py.j2
  ├── import/
  │   └── missing_import.py.j2
  └── timeout/
      └── increase_timeout.py.j2
  ```
- 支持 Jinja2 模板语法

#### 3.3.2 LLMPatcher
- 使用 LLM 生成智能修复
- 提供完整的上下文信息
- 支持多轮对话澄清

### 3.4 Validator (验证器)

#### 3.4.1 LocalValidator
- 在本地环境执行测试
- 支持虚拟环境隔离
- 可配置超时时间

#### 3.4.2 DockerValidator
- 在 Docker 容器中执行
- 隔离环境，避免污染
- 支持自定义 Dockerfile

### 3.5 Reporter (报告器)

#### 3.5.1 TerminalReporter
- 彩色终端输出
- 显示修复前后对比
- 进度指示器

#### 3.5.2 GitHubReporter
- 自动创建 Issue
- 评论更新修复进度
- 支持 Label 和 Milestone

### 3.6 Store (存储)

#### 3.6.1 MemoryStore
- 内存字典存储
- 适合单次运行
- 无持久化

#### 3.6.2 SQLiteStore
- SQLite 数据库持久化
- 支持历史记录查询
- 适合长期运维

## 4. CLI 命令

| 命令 | 说明 |
|------|------|
| `selfheal watch` | 启动监视模式 |
| `selfheal classify` | 单次分类任务 |
| `selfheal patch` | 执行修补 |
| `selfheal validate` | 验证修复 |
| `selfheal report` | 生成报告 |
| `selfheal init` | 初始化配置 |

## 5. 配置项

### 5.1 全局配置 (~/.selfheal.yaml)

```yaml
llm:
  provider: openai  # openai | anthropic
  model: gpt-4
  api_key: ${OPENAI_API_KEY}

docker:
  image: python:3.11-slim
  timeout: 600

github:
  owner: yourname
  repo: yourproject
  token: ${GITHUB_TOKEN}
```

### 5.2 项目配置 (selfheal.yaml)

```yaml
watcher:
  type: pytest
  path: tests/
  pytest_args: [-v, --tb=short]

classifier:
  type: llm
  fallback: rule

patcher:
  type: llm
  fallback: template
  templates_dir: .selfheal/patches

validator:
  type: local
  timeout: 300
  venv_path: .venv

store:
  type: sqlite
  db_path: .selfheal/selfheal.db

reporter:
  type: terminal
  github:
    enabled: true
    labels: [self-heal, automated]
```

## 6. 事件系统

### 6.1 事件类型

```python
class TestFailureEvent:
    test_path: str
    error_type: str
    error_message: str
    traceback: str
    timestamp: datetime

class ClassificationEvent:
    category: str
    severity: str  # critical | high | medium | low
    confidence: float
    reasoning: str

class PatchEvent:
    patch_id: str
    patch_content: str
    generator: str  # template | llm

class ValidationEvent:
    patch_id: str
    result: str  # passed | failed
    test_output: str
    duration: float
```

## 7. 插件系统

支持自定义组件:

```python
from selfheal.plugins import hookimpl
from selfheal.interfaces import WatcherInterface

class MyWatcher(WatcherInterface):
    name = "my_watcher"

    @hookimpl
    def watch(self):
        # 自定义监视逻辑
        pass
```

## 8. 非功能需求

- **性能**: 单次分类 < 2秒, LLM 修补 < 30秒
- **可靠性**: 验证通过率 > 95%
- **可扩展性**: 支持自定义所有核心组件
- **安全性**: API Key 存储在环境变量

## 9. 路线图

### v0.1.0 (当前)
- [x] 项目结构搭建
- [x] 核心接口定义
- [x] PytestWatcher 实现
- [x] RuleClassifier 实现
- [x] 基本 CLI 命令

### v0.2.0
- [ ] LLMClassifier 实现
- [ ] LLMPatcher 实现
- [ ] SQLiteStore 实现
- [ ] GitHubReporter 实现

### v0.3.0
- [ ] DockerValidator 实现
- [ ] 插件系统完善
- [ ] 配置向导

### v1.0.0
- [ ] 完整测试覆盖
- [ ] 文档完善
- [ ] 正式发布
