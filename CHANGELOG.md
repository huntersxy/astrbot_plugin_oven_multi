# Changelog

## v1.8.2 (2026-06-23)

### Bug Fixes

- **修复主动回复**: 修正 `event.request_llm()` 调用方式，`yield` 直接传递 `ProviderRequest` 而非 `async for` 迭代

### New Features

- **主动回复**: 移植增强模式的主动回复功能，支持 probability（概率触发）和 model_choice（LLM 判定触发）两种模式

---

## v1.8.1 (2026-06-22)

### Bug Fixes

- **过滤指令消息**: 风格学习模块不再记录以 `/` 开头的指令消息到聊天历史，防止 `/学习总结`、`/烤箱状态` 等指令被 LLM 当作群聊风格特征学习

---

## v1.6.0 (2026-06-22)

### New Features

- **mem0 长期记忆**: 集成 mem0 托管服务，自动保存对话并检索相关记忆注入到 LLM 回复中
  - `on_llm_request`（优先级 5，先于风格注入）检索并注入记忆到 system prompt 末尾
  - `on_llm_response` 自动保存对话到 mem0
  - 命令：`/mem0 status`、`/mem0 search <query>`
  - 配置：`mem0` 配置块（11 项配置），默认启用
- `烤箱状态` 显示 mem0 记忆状态和用户 ID

### Refactoring

- 调整事件优先级：mem0(5) → style(10)，确保注入顺序：记忆→风格

---

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
