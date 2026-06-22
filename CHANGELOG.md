# Changelog

## v1.5.0 (2026-06-22)

### New Features

- **风格学习模块**: 集成 `learning_style/` 模块，自动从聊天记录中学习群聊说话风格和内部梗，并注入到 LLM 回复中
  - `data_manager` — 三层表征（通用/情境/特定）的数据持久化
  - `learning_manager` — 调用 LLM 分析聊天记录并提取风格特征
  - `scheduler` — 定时任务：周期性分析 + 情境缓冲维护
  - `style_injector` — 将风格注入到 system prompt 或用户消息
  - `style_selector` — 表征数据格式化为提示文本
- 新增命令：`风格状态`、`清空风格`、`学习总结`
- 新增配置项：`style_learning` 配置块，含 `inject_as_system_prompt` 切换注入位置
- `烤箱状态` 显示风格学习统计信息

### Bug Fixes

- 复读功能屏蔽戳一戳（Poke）消息，修复不支持的消息类型报错
- 修复复读触发阈值配置不生效的问题
- 修复括号匹配后阻断 LLM 流程的问题

### Refactoring

- 去除 HTML 配置页面耦合，配置加载改为由框架自动注入
- 扩展括号匹配表至 22 对
- `.gitignore` 修正为 `__pycache__/` 匹配嵌套目录
- 风格注入追加到消息末尾，改善 LLM 前缀缓存命中率

---

## v1.4.1 (2026-06-22)

### Changes

- 更新仓库地址
- 扩展括号匹配对

---

## v1.4.0

### New Features

- 括号自动匹配功能
- 消息复读功能（含打断施法）
- 移除空行功能
- 思考表情功能
