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
| 💬 主动回复 | 群聊中无需 @ 即可主动回复，支持概率触发和模型判定 |
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
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enable` | 启用 | `false` |
| `mode` | 触发模式：`probability`（概率）/ `model_choice`（模型判定） | `"probability"` |
| `possibility` | 回复概率（`probability` 模式） | `0.1` |
| `model_stack_size` | 模型判定栈长度 | `8` |
| `model_history_messages` | 模型判定时附带的额外历史消息条数（0 表示不附带） | `0` |
| `model_choice_provider_id` | 模型判定用的 Provider | `` |
| `model_choice_prompt` | 模型判定提示词，支持 `{stack_size}`、`{messages}`、`{history_count}`、`{history_context}` 占位符 | 内置默认 |
| `active_reply_guidance` | 主动回复命中后注入 LLM 的额外引导，让模型知道是机器人主动加入话题而非用户来找机器人；留空则不注入 | 内置默认 |
| `whitelist` | 白名单：英文逗号分隔的 `unified_msg_origin` 或群 ID，留空表示所有群可触发 | `` |

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
