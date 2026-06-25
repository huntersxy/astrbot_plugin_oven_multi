# 插座的多功能烤箱 (astrbot_plugin_oven_multi)

整合多种实用功能的 AstrBot 插件。

## 功能

| 功能 | 说明 |
|------|------|
| 🔗 括号自动匹配 | 自动检测消息中缺失的括号并补全，支持中英文多种括号 |
| 🔄 消息复读 | 相同消息连续出现时有概率复读，支持"打断施法" |
| 📝 移除空行 | 自动清理机器人回复中的多余连续空行 |
| 💭 思考表情 | LLM 处理请求时自动贴表情提示"正在思考" |
| 🎨 风格学习 | 从聊天中学习群聊的总体说话风格，注入 LLM 回复 |
| 💬 主动回复 | 群聊中无需 @ 即可主动回复，支持概率触发和模型判定 |
| 💰 余额查询 | 查询各服务商余额，可在 Dashboard 页面查看 |

## 配置

> 可在 AstrBot 管理面板中调整，修改后即时生效。

### 括号自动匹配 (`bracket_matching`)
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 启用 | `true` |
| `check_group_messages` | 检查群组消息 | `true` |
| `check_private_messages` | 检查私聊消息 | `false` |

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

> 风格内容通过 `req.extra_user_content_parts` 注入（不修改 system_prompt），并标记为临时内容（`mark_as_temp()`，不持久化到会话历史），充分兼容其他插件的 prompt 注入。
>
> 注入前会自动剥离平台 LTM（Long-Term Memory）注入并进行去重，减少 prompt 膨胀。

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 启用 | `true` |
| `enable_style_injection` | 注入到 LLM 回复 | `true` |
| `style_provider_id` | 风格分析用的 LLM Provider，留空使用当前会话默认 | `` |
| `analysis_interval_seconds` | 分析频率（秒），默认 6 小时 | `21600` |
| `min_history_for_analysis` | 触发分析的最少消息数 | `10` |
| `max_universal_inject` | 每次注入的通用风格特征数量 | `5` |

### 主动回复 (`active_reply`)
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enable` | 启用 | `false` |
| `mode` | 触发模式：`probability`（概率）/ `model_choice`（模型判定） | `"probability"` |
| `possibility` | 回复概率（`probability` 模式） | `0.1` |
| `model_stack_size` | 模型判定栈长度 | `8` |
| `model_choice_provider_id` | 模型判定用的 Provider | `` |

### 余额查询 (`balance`)
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `config_content` | YAML 格式的服务配置 | 示例配置 |
| `config_mode` | 配置模式 (`simple`/`yaml`) | `"yaml"` |

## 命令

| 命令 | 说明 |
|------|------|
| `烤箱状态` | 查看所有功能的启用状态 |
| `风格状态` | 查看当前会话的风格学习统计 |
| `清空风格` | 清空当前会话的所有学习风格 |
| `学习总结` | 手动触发一次风格学习分析 |

## Dashboard

插件提供 Dashboard 页面，在 AstrBot 管理面板中访问：

- **风格学习** — 查看每个群组的通用风格和聊天记录
- **余额查询** — 查看各服务商余额

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
- astrbot_plugin_remove_blank_lines (MIT) by Codex — 移除空行
- astrbot_plugin_balance by BUGJI — 余额查询
- astrbot_plugin_group_chat_plus (AGPL-3.0) by Him666233 — System prompt 兼容增强
