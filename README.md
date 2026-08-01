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
| `model_choice_provider_id` | 模型判定用的 Provider | `` |

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

## 命令

| 命令 | 说明 |
|------|------|
| `烤箱状态` | 查看所有功能的启用状态 |
| `风格状态` | 查看当前会话的风格学习统计 |
| `清空风格` | 清空当前会话的所有学习风格 |
| `学习总结` | 手动触发一次风格学习分析 |

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
