# 插座的多功能烤箱 (astrbot_plugin_oven_multi)

整合多种实用功能的 AstrBot 插件。

## 功能

| 功能 | 说明 |
|------|------|
| 🔗 括号自动匹配 | 自动检测消息中缺失的括号并补全，支持中英文多种括号 |
| 🔄 消息复读 | 相同消息连续出现时有概率复读，支持"打断施法" |
| 📝 移除空行 | 自动清理机器人回复中的多余连续空行 |
| 💭 思考表情 | LLM 处理请求时自动贴表情提示"正在思考" |
| 🎨 风格学习 | 统一学习群聊的总体说话风格，支持跨群风格共享和嵌入向量选择，通过 `extra_user_content_parts` 注入 LLM |
| @功能 | 追踪活跃发言人并注入列表，LLM 可通过 `<mention id="ID"/>` 标签 @ 用户 |
| 💬 主动回复 | 群聊中无需 @ 即可主动回复，支持概率触发与 Jev 判定，触发后注入 Jev 多维判读 |
| 🏷️ @保留与被动判读 | 把被适配器丢弃的「@机器人」补回消息正文；被@/唤醒时注入 Jev 多维判读（重点：意向与情绪）；为原生群上下文补齐纯@漏记 |
| 💰 余额查询 | 查询各服务商余额，可在 Dashboard 页面查看 |
| 📎 文件读取 | 读取会话中的文件供 LLM 使用：预读取 + RAG 语义检索自动注入，或仅通过 LLM Tool（`file_list` / `file_read` / `file_search`）按需读取 |

## 配置

> 可在 AstrBot 管理面板中调整，修改后即时生效。

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

### 风格学习 (`style_learning`)

> 学习结果分为两类：
> - **稳定风格**：跨场景的语气、句式、措辞习惯，常驻注入，但带「参考而非复读」约束；
> - **场景化表达**：有明确触发场景的梗/数字梗（如 666、233），仅在当前消息语境匹配时注入，避免 AI 每条回复都强行带上。
>
> 学习提示词会显式要求 LLM 不要把数字梗/一次性梗写入稳定风格，代码层还有启发式护栏兜底（含数字串或短重复串的特征自动降级为场景化表达）。
>
> 风格内容通过 `req.extra_user_content_parts` 注入（不修改 system_prompt，保持 system 提示词稳定以命中 LLM 前缀缓存），并标记为临时内容（`mark_as_temp()`，不持久化到会话历史）。注入前会自动剥离平台 LTM 并进行去重。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 启用 | `true` |
| `enable_style_injection` | 注入到 LLM 回复 | `true` |
| `style_provider_id` | 风格分析用的 LLM Provider，留空使用当前会话默认 | `` |
| `analysis_interval_seconds` | 分析频率（秒），默认 6 小时 | `21600` |
| `min_history_for_analysis` | 触发分析的最少消息数 | `10` |
| `max_universal_inject` | 每次注入的通用风格特征数量 | `5` |
| `enable_cross_group` | 启用跨群风格，引用其他群的风格特征 | `false` |
| `enable_emb_style_selection` | 启用嵌入向量辅助选择，按语义相关度选取风格 | `true` |
| `max_global_styles` | 跨群风格最多注入条数（仅跨群开启时生效） | `3` |
| `enable_situational_inject` | 启用场景化表达注入（语境匹配时才注入） | `true` |
| `max_situational_inject` | 每轮最多注入的场景化表达条数 | `2` |
| `situational_similarity_threshold` | 场景化表达嵌入相似度阈值（0-1） | `0.4` |

### 主动回复 (`active_reply`)

> 两种触发模式：
> - **`probability`**：按概率随机触发；命中后调用 Jev 对触发消息做多维判读并注入本轮 LLM 请求（判读失败不影响触发，只是本轮没有注入）。
> - **`model_choice`**：累计 `model_stack_size` 条消息后由 Jev 判定是否触发——六维判读之外附加「是否主动回复」的 Noul 是非判定，「该主动回复」概率不低于 `decision_min_confidence` 才触发；需启用顶层 `jev` 总开关。
>
> 判读维度：说话对象 / 意图 / 情绪 / 对bot态度 / 期待回复 / 风险（均带置信度），可附带按规则生成的行动建议。判读块以 `extra_user_content_parts` 临时内容注入（`mark_as_temp()`，不写入对话历史），上下文取自本插件维护的最近群聊消息，发送前经本地脱敏（手机号/邮箱/身份证等）。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enable` | 启用 | `false` |
| `mode` | 触发模式：`probability`（概率）/ `model_choice`（Jev 判定） | `"probability"` |
| `possibility` | 回复概率（`probability` 模式） | `0.1` |
| `model_stack_size` | 触发栈长度（`model_choice` 模式） | `8` |
| `active_reply_guidance` | 主动回复命中后注入 LLM 的额外引导，让模型知道是机器人主动加入话题而非用户来找机器人；留空则不注入 | 内置默认 |
| `whitelist` | 白名单：英文逗号分隔的 `unified_msg_origin` 或群 ID，留空表示所有群可触发、也注入 Jev 判读 | `` |

### Jev 多维判读 (`jev`)

> 用 TypeSafe SystemOne（Jev）做多维判读，按两个场景注入本轮 LLM 请求（`extra_user_content_parts` 临时内容，不写入对话历史）：
> - **主动回复场景**（`inject_on_active_reply`）：`probability` 命中后注入六维判读（判读失败不影响触发）；`model_choice` 的触发判定始终依赖 Jev，该开关仅控制触发后是否注入判读块。
> - **被@场景**（`inject_on_mention`）：被 @机器人 / 唤醒前缀 / 引用唤醒（非主动回复）时注入一次判读，重点是**意图与情绪**（置顶「重点 ·」行）+ 行动建议；裸@第一条仍走 AstrBot 原生空@流程，其后 60 秒内的消息正常判读；判读失败只降级为普通回复。
>
> 判读维度：说话对象 / 意图 / 情绪 / 对bot态度 / 期待回复 / 风险（均带置信度）。题型按 TypeSafe 官方 primitives 选择：无序集合（说话对象/意图/情绪/态度，意图含「其他」兜底）用 `choice`，有序光谱（风险、期待回复）用 `score`（返回等级位置与各级概率），是否主动回复用 `noul`（概率直接对阈值）。上下文取自本插件独立维护的群聊消息记录（含@消息，与主动回复开关解耦），发送前经本地脱敏（手机号/邮箱/身份证等）。
>
> **与 AstrBot「群聊消息记录注入上下文」的兼容**：原生记录从消息链读取（@机器人 自带 `⚠️[DIRECTED AT YOU]` 标记），本插件的 @保留只改 `message_str`，二者互不冲突、互为补充——原生注入历史原文块，Jev 注入对当前消息的判读。对原生漏记的「纯@消息」（如先发一句话、再单独 @机器人），本插件在最高优先级自动补记一条原生记录（仅在该功能开启时，无需改 AstrBot 源码）。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 总开关：关闭后概率模式不判不注（触发不受影响）、`model_choice` 不触发、被@不判读 | `false` |
| `inject_on_active_reply` | 主动回复场景注入判读 | `true` |
| `inject_on_mention` | 被@/唤醒场景注入判读（重点：意向与情绪） | `true` |
| `debug_mode` | 调试完整日志：以 INFO 级别输出每次 Jev 调用的完整输入（state 上下文原文 + 全部问题）与原始输出，用于核对上下文是否真的进来了；排查完请关闭 | `false` |
| `api_key` | TypeSafe API Key，在 https://console.typesafe.ai 获取 | `` |
| `base_url` | TypeSafe 接口地址 | `"https://api.typesafe.ai"` |
| `model` | Jev 模型名 | `"jev-latest"` |
| `timeout_sec` | 请求超时（秒） | `8` |
| `retries` | 限流/过载（429/503/529）重试次数 | `1` |
| `history_rounds` | 判读带入的上下文条数（`model_choice` 自动不少于触发栈长度） | `6` |
| `max_state_chars` | 发送给 Jev 的上下文长度上限 | `1200` |
| `min_message_chars` | 判读最小字数（概率命中/被@时生效：去掉开头 @标记与空白后短于此不判读，不影响是否回复） | `2` |
| `desensitize` | 发送前本地脱敏 | `true` |
| `enable_advice` | 注入块附带行动建议 | `true` |
| `show_confidence` | 注入块显示各维度置信度 | `true` |
| `max_injection_chars` | 注入块长度上限 | `600` |
| `decision_min_confidence` | `model_choice`「该主动回复」概率（Noul）阈值 | `0.6` |

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
| `风格状态` | 查看当前会话的风格学习统计 |
| `清空风格` | 清空当前会话的所有学习风格 |
| `学习总结` | 手动触发一次风格学习分析 |
| `清除文件` | 清理当前会话的所有已上传文件 |

## Dashboard

插件提供「状态总览」页面（`pages/status`），在 AstrBot 管理面板中访问，可一次查看：

- **功能状态** — 各功能的启用状态与关键参数
- **余额查询** — 各服务商余额
- **风格学习** — 每个群组的通用风格、场景化表达和聊天记录（可折叠查看），并支持删除单条风格、删除单个会话风格、清空全部风格

## 安装

1. 将插件文件夹放入 AstrBot `data/plugins/` 目录
2. 在管理面板中启用插件
3. 根据需要调整配置

## 许可证

GNU Affero General Public License v3.0

## 致谢

本插件修改自以下开源项目：

- astrbot_plugin_pairit (AGPL-3.0) by GamerNoTitle — 括号匹配
- astrbot_plugin_astrbot_enhance_mode by 阿汐 — 主动回复
- astrbot_plugin_repetition by FengYing1314 — 消息复读
- astrbot_plugin_iamthinking (AGPL-3.0) by sssn-tech — 思考表情
- astrbot_plugin_iearning_style (AGPL-3.0) by qa296 — 风格学习
- astrbot_plugin_group_chat_plus (AGPL-3.0) by Him666233 — System prompt 兼容增强与差分捕捉机制
- astrbot_plugin_iris_chat_memory (AGPL-3.0) by  — `extra_user_content_parts` 注入策略与 `mark_as_temp()` 实践
- astrbot_plugin_remove_blank_lines (MIT) by Codex — 移除空行
- astrbot_plugin_balance by BUGJI — 余额查询
- astrbot_plugin_file_reader_pro (MIT) by zz6zz666 — 文件读取与 RAG 索引

另参考：astrbot_plugin_jev_intent_boost (MIT) by 汐兮雨 — Jev 多维判读的维度体系与注入思路；astrbot_plugin_qq_group_enhance (MIT) by rytte — @保留与纯@补记思路（本插件按最新 AstrBot 源码独立实现）。
