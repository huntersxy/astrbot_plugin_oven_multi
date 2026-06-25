# Changelog

## v1.19.0 (2026-06-24)

### New Features

- **System prompt 兼容增强**: 集成 `SystemPromptRewriter`，在风格注入前剥离平台 LTM 注入并去重，减少 prompt 膨胀。
  来源于 `astrbot_plugin_group_chat_plus` (AGPL-3.0) by Him666233。

---

## v1.18.0 (2026-06-24)

### Bug Fixes

- **LLM 输出 JSON 解析鲁棒性**: 新增 `_manual_extract_strings` 手动回退解析，处理特征描述中 LLM 生成的未转义双引号。更新 prompt 提示 LLM 避免在特征中使用引号。

---

## v1.17.0 (2026-06-24)

### Refactoring

- **移除 mem0 长期记忆**: 删除 `mem0_client.py` 和相关所有代码（`/mem0` 命令、`on_llm_response_mem0`、`search_mem0_memory` 工具），从配置文件移除 `mem0` 配置块。
- **重写 README**: 精简内容结构，移除 mem0 相关文档，增加致谢列表。

---

## v1.16.0 (2026-06-24)

### Refactoring

- **统一风格学习为单一通用风格**: 移除情境（contextual）和特定（specific）风格层级，只学习总体语言风格。
- **移除 Embedding 依赖**: 移除 Embedding Provider 相关代码（语义注入、缓存、维护合并），风格注入回退为按熟练度简单排序选取。
- **移除 `/风格维护` 指令**: 不再需要手动触发维护任务，定时任务只保留周期性学习分析。
- **默认分析间隔改为 6 小时**: `analysis_interval_seconds` 默认值从 3600 改为 21600。
- **简化配置项**: 移除 `maintenance_interval_seconds`、`max_specific_per_session`、`specific_promotion_threshold`、`max_contextual_per_session`、`embedding_provider_id`、`embedding_threshold`、`max_contextual_inject`、`max_specific_inject`、`embedding_inject_enabled`、`max_contextual_per_session`。
- **简化数据管理**: `DataManager` 仅保留 `universal` 和 `chat_history` 两类数据。
- **简化页面**: Dashboard 仅展示通用风格和聊天记录。

---

## v1.15.0 (2026-06-24)

### New Features

- **Embedding 语义注入**: 新增配置项 `embedding_inject_enabled`（默认关闭）。开启后，注入 LLM 提示词时会根据当前用户消息的语义，从所有风格表征中选取最相关的几条，而非固定按熟练度/触发次数选取。需已配置 Embedding Provider。
- 风格表征 embedding 自动缓存（按内容 MD5 哈希），仅首次或变更时计算，不影响后续回复速度。

---

## v1.14.0 (2026-06-24)

### New Features

- **注入数量上限**: 新增三个配置项 `max_universal_inject`（默认 5）、`max_contextual_inject`（默认 3）、`max_specific_inject`（默认 3），控制注入到 system prompt 的各类型风格数量，防止累积百条后提示词过长。
- 通用风格按熟练度（proficiency）降序选取，情境只取非缓冲位数据，特定风格按触发次数（trigger_count）降序选取。

---

## v1.13.0 (2026-06-24)

### New Features

- **Embedding 驱动的风格维护**: 风格维护 (`/风格维护`) 现在支持使用 AstrBot Embedding Provider 进行语义合并。自动检测首个可用 Embedding Provider，也可通过配置 `style_learning.embedding_provider_id` 指定。未配置时自动回退到字符相似度（difflib）。
- **配置项 `embedding_provider_id`**: 指定用于风格维护的 Embedding Provider ID，留空则自动检测。
- **配置项 `embedding_threshold`**: 余弦相似度合并阈值，默认 0.75。
- 维护日志增加 `模式: embedding` / `模式: difflib` 提示，方便确认使用的匹配算法。

---

## v1.12.0 (2026-06-24)

### New Features

- **`/风格维护` 详细日志**: 现在执行时逐会话输出缓冲条目数、合并到通用/特定/滞留数，verbose 模式还会展示每条缓冲的合并明细（相似度分数）

---

## v1.11.0 (2026-06-24)

### New Features

- **新指令 `/风格维护`**: 手动触发风格维护任务（对所有会话执行情境表征合并 + 容量清理 + 强制保存，等同于定时维护任务 `perform_maintenance()`）

---

## v1.10.0 (2026-06-24)

### New Features

- **Dashboard Page**: 风格学习会话卡片支持折叠展开，点击头部收起/展开完整风格数据与聊天记录（通用/情境/特定风格 + 聊天记录）
- **Dashboard Page**: `/style_status` API 现在直接返回完整数据，无需额外请求

---

## v1.9.5 (2026-06-24)

### Bug Fixes

- **Dashboard Page**: 修复余额数据不显示的问题，内容容器默认显示

---

## v1.9.4 (2026-06-24)

### Refactoring

- **Dashboard Page**: 简化页面结构，移除标签页，余额查询始终启用
- **余额查询**: 移除 enable 开关，始终显示

---

## v1.9.3 (2026-06-24)

### Bug Fixes

- **Dashboard Page**: 修复余额查询 loading 状态默认显示的问题

---

## v1.9.2 (2026-06-24)

### Bug Fixes

- **余额查询**: 修复自定义配置缺少 result_template 时返回空数据的问题，更新配置示例

---

## v1.9.1 (2026-06-24)

### Bug Fixes

- **余额查询**: 更新配置默认值，提供完整的自定义服务配置示例

---

## v1.9.0 (2026-06-24)

### New Features

- **余额查询**: 集成 balance 插件功能，可在 Dashboard 页面查看各服务商余额

---

## v1.8.9 (2026-06-23)

### Refactoring

- **Dashboard Page**: 重构页面结构，添加标签页支持，为未来功能预留扩展空间

---

## v1.8.8 (2026-06-23)

### New Features

- **风格状态页面**: 新增插件 Dashboard Page，可查看每个群组的风格学习统计和表征预览

---

## v1.8.7 (2026-06-23)

### Bug Fixes

- **主动回复**: 改用消息链检查 Plain 段的方式判断文本消息，更可靠

---

## v1.8.6 (2026-06-23)

### Bug Fixes

- **主动回复**: 仅命中文本消息，过滤纯图片/表情/空消息

---

## v1.8.5 (2026-06-23)

### Refactoring

- **许可证合规**: 添加 AGPL-3.0 许可证文件，所有源文件添加许可证头部和修改声明，标注来源插件及许可证信息

---

## v1.8.4 (2026-06-23)

### Refactoring

- **主动回复日志**: 两个模式均增加日志输出。probability 模式输出"命中/未命中"及采样值；model_choice 模式输出栈填充进度和判定结果，与原插件一致。

---

## v1.8.3 (2026-06-23)

### Bug Fixes

- **主动回复自动创建对话**: 无对话历史时自动调用 `conversation_manager.new_conversation()` 创建新对话，不再因无对话而跳过

---

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
