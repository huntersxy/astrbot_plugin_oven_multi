# 插座的多功能烤箱 (astrbot_plugin_oven_multi)

一个整合多种实用功能的 AstrBot 插件，包含括号自动匹配、消息复读、移除空行、思考表情、风格学习、mem0 长期记忆、主动回复和余额查询等功能。

## ✨ 功能特性

| 功能 | 说明 |
|------|------|
| 🔗 **括号自动匹配** | 自动检测消息中缺失的括号并补全，支持中英文多种括号类型 |
| 🔄 **消息复读** | 相同消息连续出现时有概率复读，支持"打断施法"趣味机制 |
| 📝 **移除空行** | 自动清理机器人回复中的多余连续空行，保持输出整洁 |
| 💭 **思考表情** | LLM 处理请求时自动贴表情提示"正在思考"，完成后可切换表情 |
| 🎨 **风格学习** | 从聊天中学习群聊的总体说话风格，并注入到 LLM 回复中 |
| 🧠 **mem0 记忆** | 使用 mem0 托管服务实现长期记忆，通过工具调用方式检索 |
| 💬 **主动回复** | 在群聊中主动回复（无需被 @），支持概率触发和模型判定两种模式 |
| 💰 **余额查询** | 通过 API 查询各服务商余额，可在 Dashboard 页面查看 |

## 📦 支持的括号类型

- 圆括号：`()`、`（）`
- 方括号：`[]`、`【】`
- 花括号：`{}`、`『』`
- 尖括号：`<>`、`《》`
- 书名号：`《》`、`《》`
- 其他：`「」`、`（ ）`

## 🛠️ 配置选项

> [!NOTE]
> 以下配置均可在 AstrBot 插件配置面板中调整，修改后无需重启即时生效。

### 黑名单设置
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `blacklist_groups` | 不启用插件的群号码列表 | `[]` |
| `blacklist_users` | 不启用插件的 QQ 用户号码列表 | `[]` |

### 括号自动匹配 (`bracket_matching`)
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 启用括号自动匹配 | `true` |
| `check_group_messages` | 检查群组消息 | `true` |
| `check_private_messages` | 检查私聊消息 | `false` |

### 消息复读 (`repetition`)
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 启用消息复读 | `true` |
| `repeat_threshold` | 复读触发阈值（相同消息出现次数） | `2` |
| `break_spell_probability` | 打断施法概率 (0-1) | `0.3` |
| `break_spell_text` | 打断施法显示文本 | `"打断施法！"` |

### 移除空行 (`remove_blank_lines`)
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 启用移除空行 | `true` |
| `max_consecutive_newlines` | 最大保留连续换行数 | `1` |

### 思考表情 (`iam_thinking`)
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 启用思考表情 | `true` |
| `thinking_emoji_ids` | 思考中表情 ID 列表 | `[66]` |
| `done_emoji_ids` | 完成后表情 ID 列表 | `[74]` |
| `remove_thinking_on_done` | 完成后移除思考表情 | `true` |
| `add_done_emoji` | 完成后添加完成表情 | `true` |

> [!TIP]
> 思考表情功能仅支持 **aiocqhttp** 平台（如 NapCat、Lagrange 等 OneBot v11 实现），且仅在群聊中生效。

### 风格学习 (`style_learning`)
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 启用风格学习 | `true` |
| `enable_style_injection` | 启用风格注入到 LLM 回复 | `true` |
| `style_provider_id` | 风格分析使用的 Provider ID | `` |
| `analysis_interval_seconds` | 分析频率（秒） | `3600` |
| `min_history_for_analysis` | 触发分析的最少消息数 | `10` |

### mem0 长期记忆 (`mem0`)
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enable` | 启用 mem0 记忆 | `true` |
| `mem0_api_key` | mem0 API Key | `` |
| `mem0_api_base` | mem0 API 地址 | `` |
| `memory_scope` | 记忆作用域 (`session`/`sender`) | `"session"` |
| `search_limit` | 最多检索记忆数量 | `5` |

### 主动回复 (`active_reply`)
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enable` | 启用主动回复 | `false` |
| `mode` | 触发模式 (`probability`/`model_choice`) | `"probability"` |
| `possibility` | 回复概率 | `0.1` |
| `model_stack_size` | 模型判定栈长度 | `8` |
| `model_choice_provider_id` | 模型判定 Provider ID | `` |

### 余额查询 (`balance`)
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enable` | 启用余额查询 | `false` |
| `config_content` | YAML 格式的服务配置 | 示例配置 |
| `config_mode` | 配置模式 (`simple`/`yaml`) | `"yaml"` |

## 📝 命令

| 命令 | 说明 |
|------|------|
| `烤箱状态` | 查看当前所有功能的启用状态及关键参数 |
| `学习总结` | 手动触发一次风格学习分析 |

### 状态示例
```
🍳 插座烤箱状态

🔗 括号匹配: ✅ 启用
🔄 消息复读: ✅ 启用
   └─ 打断施法概率: 30%
   └─ 打断文本: 打断施法！
📝 移除空行: ✅ 启用
   └─ 最大连续换行: 1 行
💭 思考表情: ✅ 启用
🎨 风格学习: ✅ 启用
🧠 mem0 记忆: ✅ 启用
💬 主动回复: ❌ 关闭
💰 余额查询: ❌ 关闭
```

## 📊 Dashboard 页面

插件提供 Dashboard 页面，可在 AstrBot 管理面板中访问，包含以下标签页：

- **风格学习**：查看每个群组的风格学习统计和表征预览
- **主动回复**：预留区域
- **余额查询**：查看各服务商余额信息
- **mem0 记忆**：预留区域

## 🔧 使用示例

### 括号自动匹配
用户发送：`你好（世界`
机器人回复：`)`

用户发送：`测试[未闭合`
机器人回复：`]`

### 消息复读
用户 A：`今天天气真好`
用户 B：`今天天气真好`
机器人：`今天天气真好` （或随机触发"打断施法！”）

### 移除空行
LLM 回复包含多个连续空行时，自动压缩为配置的最大行数。

### 思考表情
用户发送消息触发 LLM → 机器人贴上"思考中"表情 (ID: 66) → LLM 完成 → 移除思考表情，贴上"完成"表情 (ID: 74)

### 风格学习
插件自动收集聊天记录，定期分析群聊风格特征，将学习到的风格注入到 LLM 的 system prompt 中。

### mem0 记忆
LLM 可通过 `search_mem0_memory` 工具调用检索长期记忆，无需自动注入。

### 主动回复
在群聊中无需被 @ 即可主动回复消息，支持概率触发和模型判定两种模式。

### 余额查询
在 Dashboard 页面的"余额查询"标签页查看各服务商余额，配置示例：

```yaml
services:
  Deepseek:
    url: "https://api.deepseek.com/user/balance"
    headers:
      Accept: "application/json"
      Authorization: "Bearer your_api_key"
    result_template: "Deepseek: {{balance_infos.0.total_balance}} 元"
```

## ⚙️ 安装方法

1. 将插件文件夹放入 AstrBot 的 `data/plugins/` 目录
2. 在 AstrBot 管理面板中启用插件
3. 根据需要调整配置项

## 📋 要求

- AstrBot 最新版本
- OneBot v11 协议适配器（aiocqhttp 平台，如 NapCat、Lagrange）

## 📄 许可证

GNU Affero General Public License v3.0

## 🤝 贡献

欢迎提交 Issue 和 Pull Request 来改进这个插件！

---

<div align="center">
  <sub>由 <a href="https://github.com/huntersxy/astrbot_plugin_oven_multi">汐兮雨</a> 开发维护</sub>
</div>
