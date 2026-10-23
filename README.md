# 插座的多功能烤箱 · Oven Multi

面向 AstrBot 的群聊工具箱：整理消息、智能分段、判读语境、按需读取文件，在原生 Panel 查看功能状态与服务余额。

[使用配置](#配置) · [状态总览](#panel-状态总览) · [更新记录](CHANGELOG.md) · [烤箱之心](https://github.com/huntersxy/astrbot_plugin_ovenheart)

> **风格学习已废弃**：从 **1.49.0** 起，Oven Multi 移除风格采集、分析、管理和注入功能。人格、表达方式与群聊词汇学习，建议使用 **[烤箱之心（OvenHeart）](https://github.com/huntersxy/astrbot_plugin_ovenheart)**。
>
> 旧 `style_learning` 配置不再生效；数据目录中的 `universal.json`、`chat_history.json` 保留，不再读取或更新，也不会自动导入烤箱之心。需要清理时请先自行备份。

> **主动回复已移除**：从 **1.50.0** 起，Oven Multi 不再提供无需 @ 的主动回复。群聊参与（自主接话、名称提及响应等）请使用 **[烤箱之心（OvenHeart）](https://github.com/huntersxy/astrbot_plugin_ovenheart)** 的「群聊参与」能力；配置页原位置保留跳转指引，旧配置项不再生效。

## 能做什么

| 能力 | 使用场景 |
| --- | --- |
| 消息处理 | 补齐缺失括号、群聊复读、清理回复空行、显示思考表情 |
| 分段回复 | 把长回复按标点与字数规则切成多条消息，带延迟发送，模拟真人打字节奏 |
| 结构化提及 | 追踪活跃发言人，将模型输出的 `<mention id="ID"/>` 转为平台 @ 组件 |
| Jev 多维判读 | 被 @ 时结合上下文判断说话对象、意图、情绪、态度、回复期待与风险，以临时内容辅助回复 |
| 文件读取 | 支持预读取、RAG 检索和 `file_list` / `file_read` / `file_search` 工具 |
| 服务余额 | 按需查询已配置服务，成功结果缓存 30 秒，按服务原始币种分别展示 |
| 原生 Panel | 运行概览与服务余额分开，刷新状态无需请求外部余额接口 |

## 开始使用

需要 **AstrBot >= 4.29.1 且 < 5**，支持 **aiocqhttp**（NapCat、Lagrange 等）。思考表情、群消息与 @ 相关能力依赖对应平台接口；Jev 判读需要单独配置 TypeSafe API Key。

1. 在 AstrBot 插件市场安装，或通过仓库地址安装本插件。
2. 打开插件配置，按需开启 Jev、分段回复、文件读取及余额查询。
3. 从插件卡片打开 **状态总览**，检查配置状态；外部服务是否可用仍需实际调用确认。

需要机器人在群聊中自主接话时，请安装 [烤箱之心](https://github.com/huntersxy/astrbot_plugin_ovenheart) 并开启其「群聊参与」；本插件的消息处理、分段回复、文件工具与余额查询可与之共存。

## 配置

> 使用 AstrBot 原生插件配置保存设置。文件读取等需要初始化的功能，保存后请重新加载插件。状态总览为只读页面，不维护第二套配置。

### 括号自动匹配 (`bracket_matching`)
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 启用 | `true` |
| `only_first_missing` | 只补全第一个缺失括号（最近一个未闭合的），而非补全全部 | `false` |

### 消息复读 (`repetition`)
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 启用 | `true` |
| `repeat_threshold` | 复读触发阈值（相同消息出现次数） | `2` |
| `break_spell_probability` | 打断施法概率 (0-1) | `0.3` |
| `break_spell_text` | 打断施法文本 | `"打断施法！"` |

### 移除空行 (`remove_blank_lines`)
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 启用 | `true` |
| `max_consecutive_newlines` | 最大保留连续换行数 | `1` |

### 思考表情 (`iam_thinking`)
> 仅支持 aiocqhttp 平台（NapCat、Lagrange 等），仅群聊生效。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 启用 | `true` |
| `thinking_emoji_ids` | 思考中表情 ID | `[66]` |
| `done_emoji_ids` | 完成后表情 ID | `[74]` |
| `remove_thinking_on_done` | 完成后移除思考表情 | `true` |
| `add_done_emoji` | 完成后添加完成表情 | `true` |

### 分段回复 (`segment_reply`)

> 核心实现改写自 [astrbot_plugin_custome_segment_reply](https://github.com/LinJohn8/astrbot_plugin_custome_segment_reply)（MIT）by LinJohn8，去掉了主动消息分支、对话历史改写与「正在输入」状态调用：完整回复在进入装饰阶段前已由 AstrBot 按原文写入对话历史，分段只是发送层的事，上下文天然连贯。
>
> 分段流程：先按**强制分隔符**预切，再对每段按**字数范围 + 断句符号优先级**细分（允许超出时继续向后找标点，直到硬性上限），最后把过短的尾巴并入上一段。首段随原生装饰（@回复/引用/前缀）发送，其余各段在消息发出后按配置间隔逐条直发。
>
> - **只对 LLM 回复生效**（默认）：`process_non_llm` 关闭时，插件指令、命令返回等非 LLM 输出原样发送。
> - 开启 `process_non_llm` 后所有出站纯文本都会分段（含指令输出），可用「无需分段的关键词」排除菜单/帮助类内容。
> - 与 WebUI 内置「分段回复」二选一，建议关闭内置功能，避免重复处理。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 启用分段回复 | `false` |
| `process_non_llm` | 是否处理非 LLM 输出（插件指令、命令返回等） | `false` |
| `min_length` | 单段最小字数（内尽量不断句） | `20` |
| `max_length` | 单段常规最大字数，优先在 `[min_length, max_length]` 内找标点 | `50` |
| `allow_exceed_max` | 范围内没找到标点时允许向后超长寻找断点 | `true` |
| `hard_max_limit` | 允许超出时的硬性截断字数 | `100` |
| `merge_short_tail` | 最后一段过短时并入上一段 | `true` |
| `short_tail_threshold` | 短尾合并阈值（字数） | `8` |
| `force_split_symbols` | 强制分隔符（先按它预切） | `["\\n\\n", "\\n"]` |
| `split_symbols` | 断句符号（按停顿强度优先级排列） | `["\\n\\n", "\\n", "。", "！", "？", "；", "……", …]` |
| `keep_symbol` | 断句后保留标点 | `true` |
| `extend_to_trailing_symbols` | 命中分段符后顺延吞掉紧随的连续符号 | `true` |
| `protect_paired_symbols` | 成对符号（括号/引号等）内部不分段 | `false` |
| `exclude_keywords` | 回复包含关键词时整段跳过 | 见配置默认 |
| `skip_longer_than` | 总字数超过阈值时原样发送（`0` 禁用） | `0` |
| `delay_type` | 发送间隔：`fixed` / `random`（区间正态随机）/ `smart`（按字数打字间隔 + 标点修正） | `"random"` |
| `fixed_delay_seconds` | 固定间隔秒数（`fixed`） | `1.0` |
| `random_delay_range` | 随机间隔范围秒（`random`） | `[1, 3]` |

### Jev 多维判读 (`jev`)

> 用 TypeSafe SystemOne（Jev）为**被 @ / 唤醒回复**注入一次多维判读（`extra_user_content_parts` 临时内容，不写入对话历史）：被 @机器人 / 唤醒前缀 / 引用唤醒时判读，重点是**意图与情绪**（置顶「重点 ·」行）+ 行动建议；裸@第一条仍走 AstrBot 原生空@流程，其后 60 秒内的消息正常判读；判读失败只降级为普通回复。
>
> 判读维度：说话对象 / 意图 / 情绪 / 对bot态度 / 期待回复 / 风险（均带置信度）。题型按 TypeSafe 官方 primitives 选择：无序集合（说话对象/意图/情绪/态度，意图含「其他」兜底）用 `choice`，有序光谱（风险、期待回复）用 `score`。**发给 Jev 的问题全部为英文**（场景说明、指令、选项 key、选项描述、score 等级——Jev 主训练语言，CJK 精度较低）；选项返回后在解析边界映射回中文，注入块与行动建议保持中文。
> **上下文来源**：优先读取 AstrBot 原生持久化消息历史（平台设置 → 消息历史 `group_message_history_enable` 开启时；落库、重启不丢、含机器人自己的回复、行格式不含 QQ 号），关闭时回退本插件内存缓冲（会话内，含@消息，随群消息自动记录）；发送前经本地脱敏（手机号/邮箱/身份证等）。
> **日志**：`debug_mode` 除 Jev 输入/原始输出外，还会输出**最终注入块全文**（含行动建议）与每道闸门的跳过原因、每条群消息的待命状态行。
>
> **与 AstrBot「群聊消息记录注入上下文」的兼容**：原生记录从消息链读取（@机器人 自带 `⚠️[DIRECTED AT YOU]` 标记），本插件的 @保留只改 `message_str`，二者互不冲突、互为补充——原生注入历史原文块，Jev 注入对当前消息的判读。对原生漏记的「纯@消息」（如先发一句话、再单独 @机器人），本插件在最高优先级自动补记一条原生记录（仅在该功能开启时，无需改 AstrBot 源码）。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 总开关：关闭后被@/唤醒不判读不注入，判读上下文也不记录 | `false` |
| `inject_on_mention` | 被@/唤醒场景注入判读（重点：意向与情绪） | `true` |
| `debug_mode` | 调试完整日志：以 INFO 级别输出每次 Jev 调用的完整输入（state 上下文原文 + 全部问题）与原始输出，用于核对上下文是否真的进来了；排查完请关闭 | `false` |
| `api_key` | TypeSafe API Key，在 https://console.typesafe.ai 获取 | `` |
| `base_url` | TypeSafe 接口地址 | `"https://api.typesafe.ai"` |
| `model` | Jev 模型名 | `"jev-latest"` |
| `timeout_sec` | 请求超时（秒），超时后判读降级为普通回复 | `8` |
| `retries` | 限流/过载（429/503/529）重试次数 | `1` |
| `history_rounds` | 判读带入的上下文条数 | `6` |
| `max_state_chars` | 发送给 Jev 的上下文长度上限 | `1200` |
| `min_message_chars` | 判读最小字数（被@时生效：去掉开头 @标记与空白后短于此不判读，不影响是否回复） | `2` |
| `desensitize` | 发送前本地脱敏 | `true` |
| `enable_advice` | 注入块附带行动建议 | `true` |
| `show_confidence` | 注入块显示各维度置信度 | `true` |
| `max_injection_chars` | 注入块长度上限 | `600` |

### 余额查询 (`balance`)

通过 API 查询各服务商余额，在 Dashboard 页面查看。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `config_content` | YAML 格式的服务配置 | 见下方示例 |
| `config_mode` | 配置模式 (`simple`/`yaml`) | `"yaml"` |

#### 内置类型（只需 `type` + `api_key`）

支持的服务商：`deepseek` / `siliconflow` / `openrouter` / `oneapi` / `moonshot` / `openai` / `onething` / `minimax`

```yaml
services:
  Deepseek:
    type: deepseek
    api_key: "sk-xxx"

  SiliconFlow:
    type: siliconflow
    api_key: "sk-xxx"

  # oneapi 需要额外填写 base_url
  OneAPI:
    type: oneapi
    api_key: "sk-xxx"
    base_url: "https://your-oneapi.com"
```

#### 自定义类型（需 `url` + `headers` + `result_template`）

```yaml
services:
  Deepseek:
    url: "https://api.deepseek.com/user/balance"
    headers:
      Accept: "application/json"
      Authorization: "Bearer sk-xxx"
    result_template: "Deepseek: {{balance_infos.0.total_balance}} 元"

  SiliconFlow:
    url: "https://api.siliconflow.cn/v1/user/info"
    headers:
      Authorization: "Bearer sk-xxx"
      Content-Type: "application/json"
    result_template: "SiliconFlow: {{data.totalBalance}} 元"
```

> `sk-xxx` 请替换为你自己的 API Key。

#### result_template 语法

| 语法 | 说明 | 示例 |
|------|------|------|
| `{{字段路径}}` | 取值 | `{{data.balance}}` |
| `{{字段.0.xxx}}` | 数组索引 | `{{balance_infos.0.total_balance}}` |
| `{{round({a}-{b})}}` | 表达式计算 | `{{round({data.used}/{data.total}*100, 1)}}%` |

### 文件读取 (`file_reader`)

> 核心实现改写自 [astrbot_plugin_file_reader_pro](https://github.com/zz6zz666/astrbot_plugin_file_reader_pro)（MIT）。
>
> 支持 pdf / docx / xlsx / xls / ods / pptx / csv / tsv 及常见文本与代码格式（自动检测编码）。旧版 `.doc` / `.ppt` 与压缩包不支持，会给出明确提示。可选安装 `python-magic` 获得精确的 MIME 类型检测，未安装时按扩展名判断。
>
> 两种使用方式可同时启用或单独启用：
> - **预读取 + RAG**：收到文件后自动解析、语义分块并向量化，后续提问自动检索相关内容注入回复上下文（默认以临时内容注入，不写入对话历史）；需要 Embedding Provider，缺少时自动降级，日志中会有提示；
> - **LLM Tool**：LLM 主动调用 `file_list`（列出文件）、`file_read`（读取全文）、`file_search`（语义检索）按需获取内容。
>
> 关闭预读取（`preread.enabled = false`）即可得到仅 Tool 的按需读取模式。文件在有效期内保留副本，可反复读取。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 总开关 | `false` |
| `preread.enabled` | 预读取：收到文件后自动解析并向量化，提问时自动检索注入 | `true` |
| `preread.notify` | 预读取后发送处理结果通知 | `true` |
| `tool.enabled` | 向 LLM 暴露 `file_list` / `file_read` / `file_search` 工具 | `true` |
| `tool.max_chars` | `file_read` 单次返回的最大字符数（超出截断） | `12000` |
| `rag.enabled` | RAG 语义检索（预读取向量化与 `file_search` 依赖此项） | `true` |
| `rag.injection_type` | 检索结果注入方式：`temp_part`（临时内容，不写入历史）/ `prompt`（拼接在用户输入后） | `"temp_part"` |
| `rag.embedding_provider_id` | 文件向量化用的 Embedding Provider，留空自动选择 | `` |
| `rag.rerank_provider_id` | 检索结果重排序用的 Rerank Provider，留空自动选择或不启用 | `` |
| `rag.chunk_size` | 分块大小（字符） | `512` |
| `rag.chunk_overlap` | 分块重叠（字符） | `100` |
| `rag.retrieve_top_k` | 每次检索返回的片段数 | `5` |
| `rag.fetch_k` | 重排序前的候选片段数 | `20` |
| `rag.enable_rerank` | 启用重排序 | `true` |
| `max_file_size_mb` | 接受的最大文件大小（MB） | `100` |
| `retention_minutes` | 文件保留时间（分钟），`0` 表示不按时间过期 | `60` |
| `max_rounds` | 文件最大使用轮数（检索/读取计数），`0` 表示不限 | `5` |
| `cleanup_interval_minutes` | 过期文件清理检查间隔（分钟） | `15` |
| `supported_file_types` | 允许的扩展名列表，留空表示全部内置类型 | `[]` |

## 命令

| 命令 | 说明 |
|------|------|
| `烤箱状态` | 查看所有功能的启用状态 |
| `清除文件` | 清理当前会话的所有已上传文件 |

## Panel 状态总览

在 AstrBot 插件卡片中选择 **状态总览**，沿用 AstrBot 登录与插件页面桥接，无需额外端口或独立登录。

- **运行概览**：按“消息与回复”“判读与文件”分组显示开关、关键参数及缺失配置提醒。状态来自当前配置和初始化结果，不等同于外部服务健康检查。
- **服务余额**：点击“查询余额”才调用服务。并发查询合并，包含成功结果的响应缓存 30 秒；全部失败时允许直接重试。显示实际查询时间，各服务币种不混算。
- **设置**：直接在面板修改常用开关与参数（各功能开关、复读/空行阈值、分段回复全部参数、Jev 场景开关等），保存后立即生效并写入插件配置，无需重载插件。可编辑字段是白名单子集（元数据与边界直接取自配置 schema）；密钥、符号/关键词列表等高级字段仍请使用 AstrBot 插件配置页。带 revision 与配置快照双重防陈旧：其他页面保存过或配置页改过相关字段时，旧表单会被拒绝并提示刷新。
- **失败与重试**：刷新失败保留上次结果并提示重试；未配置余额时提供配置指引。支持浅色、深色和移动端布局。

页面入口名称仍为“状态总览”。目录由旧 `pages/status` 调整为原生 `views/status`；旧风格管理区域与 `风格状态`、`清空风格`、`学习总结` 命令一并移除，后续学习管理请使用 [烤箱之心](https://github.com/huntersxy/astrbot_plugin_ovenheart)。

## 许可证

GNU Affero General Public License v3.0

## 致谢

本插件修改自以下开源项目：

- astrbot_plugin_pairit (AGPL-3.0) by GamerNoTitle — 括号匹配
- astrbot_plugin_repetition by FengYing1314 — 消息复读
- astrbot_plugin_iamthinking (AGPL-3.0) by sssn-tech — 思考表情
- astrbot_plugin_iearning_style (AGPL-3.0) by qa296 — 历史风格学习实现（1.49.0 已废弃）
- astrbot_plugin_astrbot_enhance_mode by 阿汐 — 历史主动回复实现（1.50.0 已移除）
- astrbot_plugin_custome_segment_reply (MIT) by LinJohn8 — 分段回复引擎
- astrbot_plugin_group_chat_plus (AGPL-3.0) by Him666233 — 历史 System prompt 兼容实现（1.49.0 已移除）
- astrbot_plugin_iris_chat_memory (AGPL-3.0) by  — `extra_user_content_parts` 注入策略与 `mark_as_temp()` 实践
- astrbot_plugin_remove_blank_lines (MIT) by Codex — 移除空行
- astrbot_plugin_balance by BUGJI — 余额查询
- astrbot_plugin_file_reader_pro (MIT) by zz6zz666 — 文件读取与 RAG 索引

另参考：astrbot_plugin_jev_intent_boost (MIT) by 汐兮雨 — Jev 多维判读的维度体系与注入思路；astrbot_plugin_qq_group_enhance (MIT) by rytte — @保留与纯@补记思路（本插件按最新 AstrBot 源码独立实现）。
