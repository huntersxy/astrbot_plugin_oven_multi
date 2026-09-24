# Changelog

## v1.48.0 (2026-09-25)

### New Features

- **被@回复 Jev 判读**: 被 @机器人 / 唤醒前缀 / 引用唤醒（非主动回复）时，注入一次 Jev 多维判读，重点是**意图与情绪**（独立标题「被动回复 · Jev 判读」+ 置顶「重点 ·」行）；判读失败只降级为普通回复。裸@第一条仍走 AstrBot 原生空@流程（jev 裁决 0.99）。
- **保留「@机器人」文本**: aiocqhttp 适配器构造正文时会丢弃第一个指向机器人的 @段，本插件在最高优先级 handler（`priority=maxsize`，先于 AstrBot 裸@拦截）把 `@昵称(qq)` 补回 `message_str`，模型与判读上下文都能看到被@；无正文（裸@）、非 aiocqhttp、正文已含该@时不动，消息链不受影响。
- **兼容 AstrBot「群聊消息记录注入上下文」**: 对原生漏记的「纯@消息」（无 Plain/Json/Image 组件，如先发一句话、再单独@机器人）在 `group_icl_enable` 开启时自动补记一条原生记录（经 `star_map` 调用原生 `GroupChatContext.handle_message`，按 `_group_context_record_id` 去重，跨版本差异仅降级不报错）；原生记录读消息链、@自带 `⚠️[DIRECTED AT YOU]` 标记，与本插件改 `message_str` 互不冲突——原生注入历史原文块，Jev 注入判读，互为补充。适配最新 AstrBot 源码（v4.28.1）。
- **高危风险闸门**: 新增 `jev.suppress_high_risk`（默认开启）——主动回复（概率命中或 `model_choice` 判定）判读为高危时**强制不回复**并写告警日志，避免机器人对诈骗/引战等内容接话；被@回复不受影响（必须回），关闭后仅保留判读建议中的 ⚠️ 提示。
- **注入块全文日志**: `debug_mode` 开启时输出**最终注入块全文**（含行动建议），补齐「输入 → 原始输出 → 解析 → 注入内容」的最后一环。
- **判读上下文与触发解耦**: 群消息历史（含@消息）改由最高优先级 handler 独立记录，不再依赖主动回复闸门——主动回复关闭时被@判读同样有上下文。
- **上下文优先读原生消息历史**: 判读 state 优先读取 AstrBot 原生持久化消息历史（`provider_ltm_settings.group_message_history_enable` 开启时；落库、重启不丢、含机器人自己的回复），按当前事件记录 id 过滤后取最近 `history_rounds` 条 + 当前消息；原生关闭或读取异常时回退插件内存缓冲。debug 日志会标注 `上下文来源=原生历史/插件缓冲`。
- **判读输入英文化 + QQ 号不再发往第三方**: 发给 Jev 的问题全部为英文（state 场景/区块标题、instructions、choice 选项 key 与描述、score 等级、Noul 判定描述——Jev 主训练语言，CJK 精度较低）；choice 返回的英文 key 在解析边界映射回中文（如 `asking_help → 提问求助`），注入块与行动建议保持中文。state 区块标记改为 `[Scene]/[Current]/[Context]`；插件缓冲行格式去掉发送者 id（`[昵称 时间]: 内容`），原生行格式化同样不输出 At 的 QQ 号——发送者/机器人 QQ 号不再进入第三方请求。
- **`jev.debug_mode` 调试开关**: 开启后以 INFO 级别输出每次 Jev 调用的完整输入（state 上下文原文 + 全部问题 JSON）与原始输出（HTTP 状态 + 响应体），用于核对上下文是否真的进来了；排查完请关闭。

### Changed

- **题型对齐 TypeSafe 官方 primitives（jev 裁决优先级第 1）**: 风险、期待回复由 `choice` 改为有序光谱 **`score`**（criteria 按低→高排序，返回等级位置与各级概率）；「是否主动回复」由 `choice` 改为 **`noul`**（返回「该主动回复」概率，直接与 `decision_min_confidence` 阈值比较，语义从"模型把握"修正为"该回复的概率"）；`intent` 意图列表补「其他」兜底项。注入展示格式不变（仍显示等级标签+置信度）。
- **配置结构（jev 裁决：升顶层，置信 1.0）**: `active_reply.jev` 升级为顶层 `jev` 配置节（18 项），新增场景开关 `inject_on_active_reply`（主动场景注入，`model_choice` 触发判定不受影响）与 `inject_on_mention`（被@场景注入）；`active_reply` 只保留触发配置。**白名单仅作用于主动回复触发**——判读注入与历史记录不检查白名单（被@回复是被动响应，与白名单无关）。`/烤箱状态` 新增独立「Jev 判读」状态行（显示生效场景与 DEBUG 标记）。
- **`min_message_chars` 语义扩展**: 概率命中与被@判读共用——去掉开头 @标记与空白后短于阈值则不判读，不影响是否回复。

### Credits

- @保留与纯@补记思路参考 astrbot_plugin_qq_group_enhance（MIT，by rytte），按最新 AstrBot 源码按本插件场景独立实现（未采用其 monkey-patch 方案）。

## v1.47.0 (2026-09-24)

### New Features

- **主动回复 · Jev 多维判读**: 主动回复接入 TypeSafe SystemOne（Jev）多维判读，新增 `active_reply.jev` 配置节（14 项）。
  - 判读维度：说话对象 / 意图 / 情绪 / 对bot态度 / 期待回复 / 风险，均带置信度；可按规则附带「行动建议」，以 `extra_user_content_parts` + `mark_as_temp()` 注入本轮 LLM 请求，不写入对话历史
  - `probability` 模式：概率命中后调用 Jev 判读并注入；判读失败或未启用时不影响触发，仅跳过注入；过短消息（`min_message_chars`）不消耗判读调用
  - `model_choice` 模式：改由 Jev 判定是否触发——附加「是否主动回复」判定问题，判为「回复」且置信度不低于 `decision_min_confidence`（默认 0.6）才触发；上下文自动覆盖整个触发栈
  - 费用与隐私：判读上下文取自本插件维护的最近群聊消息（默认 6 条、上限 1200 字符），发送前本地脱敏（手机号/邮箱/身份证/银行卡/超长数字）；超时/限流按 `retries` 重试
  - 判读块新增 header/footer 标记；`/烤箱状态` 显示 Jev 判读启用状态

### Removed

- **model_choice 模式的 LLM 文本判定**: 判定职责由 Jev 承担，移除对应配置 `model_history_messages`、`model_choice_provider_id`、`model_choice_prompt`

### Credits

- 判读维度体系与注入思路参考 astrbot_plugin_jev_intent_boost（MIT，作者：汐兮雨），本插件为独立精简实现

## v1.46.1 (2026-09-16)

### Bug Fixes

- **修复插件加载失败**: `file_read` 与 `file_search` 两个 LLM Tool 的 docstring `Args:` 块未写参数类型。AstrBot 依据 docstring 生成参数 schema（不读取 Python 类型注解），类型为空时在导入期抛出 `LLM 函数工具 ...main_file_read 的参数 file_name 缺少类型注释`，导致插件整体无法加载；现分别补为 `file_name(string)` 与 `query(string)`。

## v1.46.0 (2026-09-15)

### New Features

- **文件读取**: 集成 [astrbot_plugin_file_reader_pro](https://github.com/zz6zz666/astrbot_plugin_file_reader_pro) (MIT) by zz6zz666 的文件读取核心能力，在其基础上重新设计为可开关的模块化实现。
  - 支持 pdf / docx / xlsx / xls / ods / pptx / csv / tsv 及常见文本与代码格式，自动检测编码；旧版 .doc/.ppt 与压缩包给出明确提示
  - **预读取**（`file_reader.preread.enabled`，默认开启）：收到文件后自动解析、语义分块并向量化，后续提问自动检索相关内容注入回复上下文
  - **预读取通知**（`file_reader.preread.notify`，默认开启）：可单独关闭处理结果通知
  - **LLM Tool**（`file_reader.tool.enabled`，默认开启）：向 LLM 暴露 `file_list` / `file_read` / `file_search` 三个工具，支持关闭预读取、仅保留 Tool 的纯按需读取模式
  - **RAG 检索**（`file_reader.rag`）：基于 AstrBot 内核的 RecursiveCharacterChunker 与 FaissVecDB；缺少 Embedding Provider 时自动降级，全文读取不受影响；检索结果默认以 `mark_as_temp()` 临时内容注入，不写入对话历史
  - 文件生命周期：保留时间（默认 60 分钟）与使用轮数（默认 5 轮）双策略自动清理，新增 `/清除文件` 命令
- 相对原版的修复与调整：修复 system 上下文轮数清理的变量未定义问题；会话 ID 中的非法路径字符（如冒号）自动替换，兼容 Windows；文件副本保留有效期内可反复读取（原版向量化后即删除）；`.doc` 转换等不可用逻辑改为明确报错

### Credits

- 文件读取核心实现改写自 [astrbot_plugin_file_reader_pro](https://github.com/zz6zz666/astrbot_plugin_file_reader_pro)（MIT License，Copyright (c) 2025 xiewoc），许可证文本随模块保存于 `features/file_reader/LICENSE`

## v1.45.0 (2026-08-29)

### Improvements

- **主动回复增加被动触发引导注入**: 主动回复命中后，通过 `extra_user_content_parts` + `mark_as_temp()` 向 LLM 注入引导，让模型意识到这不是用户主动寻找机器人或向机器人提问，而是机器人主动加入群聊话题；回复应更自然、避免服务式/被召唤式口吻。新增 `active_reply.active_reply_guidance` 配置，可自定义注入文本，留空禁用；旧配置无该字段时自动使用内置默认引导。

## v1.44.0 (2026-08-01)

### Improvements

- **括号匹配新增「只补全第一个缺失括号」选项**: 新增 `bracket_matching.only_first_missing` 配置，开启后仅补全最近一个未闭合的括号，而不是一次性补全所有缺失括号。

## v1.43.2 (2026-08-01)

### Bug Fixes

- **修复 Dashboard 删除会话按钮文字不可见**: `.ghost.danger` 与 `button.danger` 的样式冲突导致红字红底，已显式保持透明背景。

## v1.43.1 (2026-08-01)

### Bug Fixes

- **修复 Dashboard 删除按钮无反应**: 插件页面运行在 sandbox iframe 中（无 `allow-modals`），原生 `confirm()` 被浏览器静默拦截，导致「删除会话」「全部删除」点击无效；改为页面内自绘确认弹窗，并新增失败操作的 Toast 提示。

## v1.43.0 (2026-08-01)

### Improvements

- **Dashboard 风格管理**: 状态页面新增风格控制，支持删除单条风格特征、删除单个会话的全部风格、清空全部会话风格；后端新增 `POST /astrbot_plugin_oven_multi/style/manage` 管理接口。

### Bug Fixes

- **修复 Dashboard 数据解析**: AstrBot bridge 会把插件接口响应解包一层（直接返回 `response.data.data`），页面按信封结构解析导致功能状态/余额/风格全部渲染为空；现已兼容两种返回形态。

## v1.42.0 (2026-08-01)

### Removed

- **移除图片转述缓存功能**: 删除 `features/image_caption_cache/`（缓存核心、包装层与 AstrBot 核心函数运行时补丁），不再对 AstrBot 图片转述流程打 monkey-patch。移除相关配置节、`image_caption_cache_stats` / `image_caption_cache_clear` 命令、`on_astrbot_loaded` 补丁应用逻辑与文档说明。

## v1.41.0 (2026-08-01)

### Improvements

- **风格学习防“学步”**: 学习结果拆分为「稳定风格」与「场景化表达」两类。学习提示词显式禁止把 666/233 等数字梗、一次性梗写入稳定风格，并增加启发式护栏：含数字串或短重复串的特征自动降级为场景化表达。
- **场景门控注入**: 场景化表达仅在当前消息语境匹配时注入（触发词命中或嵌入相似度达到 `situational_similarity_threshold`），并附「仅供理解与自然接梗，不要主动扩散」约束；稳定风格注入带「参考而非复读」约束，避免 AI 每条回复都强行带梗。
- **缓存命中率保持**: 所有动态注入继续走 `extra_user_content_parts` + `mark_as_temp()`，system 提示词保持稳定以命中 LLM 前缀缓存；新增稳定风格文本按数据版本缓存与场景梗嵌入缓存，特征未变化时不重复计算。
- **参考插件**: 设计参考 astrbot_plugin_self_learning（黑话与风格分离、注入即解释不扩散）与 astrbot_plugin_angel_memory（记忆筛选与衰减）。
- 新增配置项：`enable_situational_inject`、`max_situational_inject`、`situational_similarity_threshold`；Dashboard 页面新增场景化表达展示。

## v1.40.0 (2026-08-01)

### Refactor

- **精简主入口**: `main.py` 由约 700 行压缩至约 400 行。修复 `_is_enabled` 重复定义问题；移除 LLM 请求中的「差分捕捉」调试逻辑；括号/复读回复由 fire-and-forget 任务改为直接 `await` 发送；新增组合 Web API `/status` 供页面一次获取全部状态。
- **删除无效脚手架**: 移除未被任何模块使用的 `core/`（ConfigManager、BaseFeature）与 `utils/`（decorators、constants），配置访问改为极简辅助函数。
- **合并风格学习子模块**: `learning_manager` / `scheduler` / `style_injector` / `style_selector` / `system_prompt_rewriter` 五个文件合并为 `style_learning.py`；system prompt 清理只保留实际用到的 LTM 剥离与去重逻辑。
- **精简图片转述缓存包装层**: `feature.py` 不再依赖 ConfigManager，直接接收功能配置节；移除冗余的类型强制转换辅助。
- **修复余额查询会话泄漏**: `terminate()` 现在会关闭 `BalanceChecker` 持有的 aiohttp 会话。
- **清理无效配置项**: 移除代码中从未读取的 `bracket_matching.check_group_messages` / `check_private_messages`。
- **重构 Dashboard 页面**: 由 `pages/style_status`（约 12KB）重写为 `pages/status` 单页仪表盘，一次请求展示功能状态、余额与风格学习，支持深浅色主题。

## v1.39.1 (2026-07-07)

### Bug Fixes

- **修复 safe_eval 跨包导入失败**: `features/balance_checker.py` 中 `from ..utils.safe_eval` 在 AstrBot 插件加载器下无法解析，将 `safe_eval.py` 移至 `features/` 内改为同包导入。
- **修复风格学习 JSON 解析失败**: `_manual_extract_strings` 中数组末尾元素的闭合引号被错误判定为内嵌引号，导致标准 JSON 解析失败时手动回退也无法提取字符串。改进判定逻辑并增加 JSON 解析异常日志输出。
- **修复 main.py 遗留旧 import 路径**: `on_llm_request_style` 内仍引用 `from .learning_style...` 而非 `from .features.learning_style...`。

## v1.39.0 (2026-06-29)

### Bug Fixes

- **修复 safe_eval 模块未注册**: 在 `utils/__init__.py` 中添加 `from .safe_eval import safe_eval`，解决 `No module named 'utils.safe_eval'` 错误。

## v1.38.0 (2026-06-29)

### Improvements

- **发布包调整**: 恢复 `.gitattributes`、`.gitignore`、`CHANGELOG.md` 到发布包中，仅排除 `.trae/` 和 `参考资料/`。

## v1.37.0 (2026-06-29)

### Improvements

- **发布包瘦身**: 通过 `.gitattributes` 的 `export-ignore` 规则，`git archive` 生成的 zip 包不再包含 `.trae/`、`参考资料/`、`.gitattributes`、`.gitignore`、`CHANGELOG.md`。

## v1.36.0 (2026-06-29)

### Improvements

- **新增 .gitattributes**: 统一仓库换行符为 LF，消除 CRLF 警告。

## v1.35.0 (2026-06-29)

### Improvements

- **余额查询默认配置**: 将 `_conf_schema.json` 中的默认配置从教程注释改为实际可用的 Deepseek + SiliconFlow 示例，用户只需替换 API Key 即可使用。

## v1.34.0 (2026-06-29)

### Improvements

- **README 余额查询文档**: 新增详细的余额配置教程，包含内置类型和自定义类型的 YAML 示例、result_template 语法说明。

## v1.33.0 (2026-06-29)

### Improvements

- **余额查询配置教程**: 重写 YAML 配置默认内容，新增详细的教程注释，列出所有内置类型、result_template 语法和自定义示例。

## v1.32.0 (2026-06-29)

### New Features

- **@功能发言人数据持久化**: 活跃发言人列表现在会保存到 `data` 目录下的 `speakers.json`，重启插件后不再丢失。采用 5 秒节流写入，避免频繁磁盘 IO。

## v1.31.0 (2026-06-29)

### Bug Fixes

- **修复 @功能 handler 永远不执行的问题**: `on_llm_request_speakers` 中使用了 `filter.EventMessageType.GROUP_MESSAGE` 与 `event.get_message_type()` 的返回值比较，但两者属于不同的枚举类型，导致群消息永远被判定为"非群消息"而跳过。改为使用 `astrbot.api.platform.MessageType.GROUP_MESSAGE` 进行正确比较。

## v1.30.0 (2026-06-29)

### New Features

- **嵌入向量 Provider 配置**: 新增 `style_learning.embedding_provider_id` 配置选项
  - 支持选择专用的 Embedding Provider 用于风格特征嵌入向量计算
  - 留空时自动使用第一个可用的 EmbeddingProvider
  - 参考 `astrbot_plugin_astrbot_enhance_mode` 的实现方式

### Improvements

- **嵌入向量选择优化**: 改进嵌入向量获取逻辑
  - 优先使用标准 `EmbeddingProvider.get_embedding()` 接口
  - 兼容旧的 `get_embeddings/text_embedding` 方法
  - 提供商解析失败时自动回退到熟练度排序

## v1.29.0 (2026-06-29)

### New Features

- **风格学习调试模式**: 新增 `style_learning.debug_mode` 配置选项（默认关闭）
  - 开启后会在日志中打印每次风格注入的完整内容，包括本地风格列表、全局风格列表和最终注入文本
  - 方便排查风格注入相关问题

- **@功能调试模式**: 新增 `mention_parser.debug_mode` 配置选项（默认关闭）
  - 开启后会在日志中打印每次 @列表 注入的完整内容
  - 方便排查 @功能 注入相关问题

## v1.28.0 (2026-06-28)

### New Features

- **风格注入优化：跨群风格 + 嵌入向量选择**: 重构风格注入模块，新增跨群风格共享和嵌入向量辅助选择能力。
  - `style_learning.enable_cross_group`（默认关闭）: 开启后可引用其他群的风格特征，通过嵌入向量选择最相关的全局风格
  - `style_learning.enable_emb_style_selection`（默认开启）: 使用 LLM Provider 的嵌入 API 按语义相关度选择风格特征，而非仅按熟练度排序；Provider 不支持时自动回退
  - `style_learning.max_global_styles`（默认 3）: 跨群风格最多注入条数
  - 注入格式改为 `本群风格：xxx；全局风格：xxx`，区分本地和跨群风格来源
  - 嵌入向量在注入时实时计算查询文本与风格特征的余弦相似度，选择最匹配的 top-N

## v1.27.0 (2026-06-28)

### New Features

- **@功能 - 活跃发言人追踪与 Mention 标签解析**: 集成 [astrbot_plugin_astrbot_enhance_mode](https://github.com/) (AGPL-3.0) by 阿汐 的 mention/quote 标签解析功能，简化为仅保留 @ 能力。
  - 自动追踪群聊中最近发言的用户昵称和 QQ ID
  - 在 LLM prompt 中注入活跃发言人列表（位于风格注入之后），让 AI 知道可以 @ 谁
  - LLM 输出 `<mention id="用户ID"/>` 时自动转换为平台 At 消息组件
  - 配置项 `mention_parser.enabled` 控制开关（默认开启），`mention_parser.max_speakers` 设置追踪人数上限

## v1.26.0 (2026-06-28)

### New Features

- **图片转述缓存**: 集成 [astrbot_plugin_image_caption_cache](https://github.com/FloranceYeh/astrbot_plugin_image_caption_cache) (AGPL-3.0) by Florance，为图片转述结果增加 TTL 与图片数量双策略内存缓存。
  - 支持主对话图片转述缓存和引用消息图片转述缓存
  - 支持 `base64://`、`data:image`、本地文件、`file://` 和远程 URL 的图片指纹
  - 远程图片可选使用内容指纹（处理平台临时 URL 变化）
  - 插件卸载时自动恢复被补丁覆盖的 AstrBot 核心函数
  - 新指令：`/image_caption_cache_stats` 查看缓存状态、`/image_caption_cache_clear` 清空缓存
  - 配置项位于 `image_caption_cache` 配置块，共 9 项可调参数
  - `烤箱状态` 显示缓存启用状态和策略参数

### Removed

- **移除好感度系统**: 删除好感度管理功能及其相关代码。
  - 移除 `favor_manager.py` 模块
  - 移除 `FEATURE_FAVOR` 常量
  - 移除好感度命令 Handler（`/好感度`、`/管理`）
  - 移除 LLM 请求/响应中的好感度注入和解析逻辑

## v1.24.0 (2026-06-26)

### Removed

- **移除合并转发功能**: 删除合并转发消息解析功能及其相关代码。
  - 移除 `features/forward_handler.py` 模块
  - 移除 `ParseForwardTool` LLM Tool 注册
  - 移除 `FEATURE_FORWARD` 常量及相关日志

## v1.23.0 (2026-06-26)

### Refactoring

- **模块化重构**: 将单体 `main.py` 拆分为多层模块结构，提升复用性和可维护性。
  - `core/config_manager.py`: 统一配置管理器，消除各模块重复的配置验证逻辑
  - `core/base_feature.py`: 功能模块基类，定义统一接口规范
  - `utils/constants.py`: 集中管理插件名称、版本号、功能常量
  - `utils/decorators.py`: 通用错误处理/计时/日志装饰器
  - `features/bracket_matcher.py`: 括号匹配功能独立模块
  - `features/repeater.py`: 消息复读功能独立模块
  - `features/thinking_manager.py`: 思考表情管理独立模块
  - `features/forward_handler.py`: 合并转发处理独立模块（含缓存管理）
  - `features/active_reply.py`: 主动回复功能独立模块
  - `main.py`: 精简为插件入口，Handler 注册 + 功能模块协调

## v1.22.1 (2026-06-25)

### Bug Fixes

- **合并转发检测诊断日志**：添加详细的 debug 日志帮助诊断转发消息检测失败的原因。

## v1.22.0 (2026-06-25)

### New Features

- **合并转发消息解析 Tool**: 新增 `parse_forward_message` LLM Tool，自动检测群聊中的合并转发（Forward）消息并供 LLM 调用获取内容。
  - 支持直接发送的合并转发和回复中引用的合并转发
  - 通过 OneBot11 `get_forward_msg` API 提取文本内容
  - LLM 可主动调用 tool 获取转发内容，按需加载不占上下文空间
  - 配置项 `forward_message.enabled` 控制开关（默认开启）
  - 来源于 `astrbot_plugin_group_context` (AGPL-3.0) 的合并转发解析逻辑

## v1.21.0 (2026-06-24)

### New Features

- **好感度系统**: 集成 likability-level (AGPL-3.0) by wuyan1003 的好感度管理功能。
  - LLM 回复末尾添加 `[好感度上升/下降/持平]` 标记，自动更新用户好感度
  - 好感度等级影响 AI 对用户的态度（极度厌恶 → 挚爱）
  - 低好感度自动拉黑，支持定时清理
  - 注入方式采用 `extra_user_content_parts` + `mark_as_temp()`，与风格学习一致
  - 新指令：`/好感度` 查询、`/管理` 管理员维护
  - 配置项 `favor_system` 含 16 项可调参数

## v1.20.0 (2026-06-24)

### Refactoring

- **风格注入迁移至 extra_user_content_parts**: 风格内容不再注入 system_prompt，改为通过 `req.extra_user_content_parts` 注入，并调用 `mark_as_temp()` 标记为临时内容（不持久化到会话历史），提升 LLM Provider prefix cache 命中率。
- **差分捕捉其他插件注入**: 在 `on_llm_request` 中快照其他插件的 `contexts`/`prompt`/`extra_user_content_parts` 状态，记录差异日志。
- 来源于 `astrbot_plugin_iris_chat_memory` (AGPL-3.0) 的注入策略和 `astrbot_plugin_group_chat_plus` (AGPL-3.0) 的差分机制。

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
