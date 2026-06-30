# 代码审查标准与流程 — 交付概览

## 完成内容

本次为 `astrbot_plugin_oven_multi` 项目建立了完整的代码审查机制，包含以下内容：

### 📋 审查标准文档
**文件**: `CODE_REVIEW_GUIDE.md`

- **审查优先级定义**: 🔴 Blocker / 🟡 Suggestion / 💭 Nit 三级分类
- **6 大审查维度**: 安全性、正确性、可维护性、性能、测试、风格一致性
- **27 条具体检查项**: 每条都有明确的通过标准
- **v1.38.0 首次审查结果**: 发现 3 个 Blocker、6 个 Suggestion、3 个 Nit
- **改进路线图**: 三阶段渐进式改进计划

### 🔴 首次审查发现的关键问题

| # | 问题 | 严重度 | 文件 |
|---|------|--------|------|
| B1 | `eval()` 安全漏洞 — 余额模板表达式计算使用 eval，可被注入 | 🔴 Blocker | `balance_checker.py:289` |
| B2 | `_is_enabled` 方法重复定义 — 同一类中同名方法被后者覆盖 | 🔴 Blocker | `main.py:191` + `main.py:620` |
| B3 | `asyncio.create_task()` 返回值未保存 — 任务可能被 GC 回收 | 🔴 Blocker | `main.py:269,278,584` |
| S1 | 零测试覆盖 — 整个项目没有任何测试文件 | 🟡 Suggestion | — |
| S3 | `aiohttp.ClientSession` 未在 terminate() 中关闭 | 🟡 Suggestion | `main.py:159` |

### 🛠️ 质量基础设施

#### GitHub 模板
- `.github/PULL_REQUEST_TEMPLATE.md` — PR 提交模板（含作者自查清单）
- `.github/ISSUE_TEMPLATE/bug_report.md` — Bug 报告模板
- `.github/ISSUE_TEMPLATE/feature_request.md` — 功能建议模板

#### CI 流水线
- `.github/workflows/ci.yml` — Ruff lint + pytest 自动化检查

#### 项目配置
- `pyproject.toml` — Ruff、mypy、pytest 统一配置

#### 单元测试（6 个测试文件，~60 个测试用例）
- `tests/conftest.py` — 共享 fixtures
- `tests/test_bracket_matcher.py` — 括号匹配算法（20 个用例）
- `tests/test_style_selector.py` — 余弦相似度 + 风格选择（18 个用例）
- `tests/test_config_manager.py` — 配置管理器（20 个用例）
- `tests/test_mention_parser.py` — @标签解析 + 发言人追踪（15 个用例）
- `tests/test_balance_template.py` — 路径解析 + 模板渲染（15 个用例）
- `tests/test_learning_manager_json.py` — JSON 解析回退（11 个用例）

## 后续建议

1. **立即修复**: 3 个 Blocker 级问题（eval 安全漏洞、重复方法、create_task 引用丢失）
2. **质量基线**: 运行新增的单元测试，确保全部通过
3. **持续改进**: 参照 `CODE_REVIEW_GUIDE.md` 中的改进路线图逐步推进
