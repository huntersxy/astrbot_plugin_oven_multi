# 插座的多功能烤箱 · Oven Multi

面向 AstrBot 的群聊工具箱：整理消息、智能分段、判读语境、按需读取文件，在原生 Panel 查看功能状态与服务余额。

[使用配置](#配置) · [状态总览](#panel-状态总览) · [更新记录](CHANGELOG.md) · [烤箱之心](https://github.com/huntersxy/astrbot_plugin_ovenheart)

> **风格学习已废弃**：从 **1.49.0** 起，Oven Multi 移除风格采集、分析、管理和注入功能。人格、表达方式与群聊词汇学习，建议使用 **[烤箱之心（OvenHeart）](https://github.com/huntersxy/astrbot_plugin_ovenheart)**。
>
> 旧 `style_learning` 配置不再生效；数据目录中的 `universal.json`、`chat_history.json` 保留，不再读取或更新，也不会自动导入烤箱之心。需要清理时请先自行备份。

> **主动回复已移除**：从 **1.50.0** 起，Oven Multi 不再提供无需 @ 的主动回复。群聊参与（自主接话、名称提及响应等）请使用 **[烤箱之心（OvenHeart）](https://github.com/huntersxy/astrbot_plugin_ovenheart)** 的「群聊参与」能力；旧 `active_reply` 配置节已于 1.52.0 清理。

## 能做什么

| 能力 | 使用场景 |
| --- | --- |
| 消息处理 | 补齐缺失括号、群聊复读、清理回复空行、显示思考表情 |
| 分段回复 | 把长回复按标点与字数规则切成多条消息，带延迟发送，模拟真人打字节奏 |
| 结构化提及 | 追踪活跃发言人，将模型输出的 `<mention id="ID"/>` 转为平台 @ 组件 |
| Jev 多维判读 | 被 @ 时结合上下文判断说话对象、意图、情绪、态度、回复期待与风险，以临时内容辅助回复 |
| 文件读取 | 支持预读取、RAG 检索和 `file_list` / `file_read` / `file_search` 工具 |
| 服务余额 | 按需查询已配置服务，成功结果缓存 30 秒，按服务原始币种分别展示 |
| 上下文压缩 | 经本地 [billion-context](https://github.com/ranxianglei/billion-context) 代理接管上下文压缩，长会话不截断、API 侧大幅省 token |
| 原生 Panel | 运行概览与服务余额分开，刷新状态无需请求外部余额接口 |

## 开始使用

需要 **AstrBot >= 4.29.1 且 < 5**，支持 **aiocqhttp**（NapCat、Lagrange 等）。思考表情、群消息与 @ 相关能力依赖对应平台接口；Jev 判读需要单独配置 TypeSafe API Key。

1. 在 AstrBot 插件市场安装，或通过仓库地址安装本插件。
2. 打开插件配置，按需填写 Jev API Key、文件读取的 Embedding / Rerank Provider 及余额查询 YAML——配置页只保留这些密钥与选择器。
3. 从插件卡片打开 **状态总览**，在「设置」页签管理其余全部参数；外部服务是否可用仍需实际调用确认。

需要机器人在群聊中自主接话时，请安装 [烤箱之心](https://github.com/huntersxy/astrbot_plugin_ovenheart) 并开启其「群聊参与」；本插件的消息处理、分段回复、文件工具与余额查询可与之共存。

从 1.54.0 起，与启用的烤箱之心共存时，成员提及功能自动让路，由烤箱之心统一负责回复对象和 @。面板显示接管状态，原开关值保留；烤箱之心停用或未接管当前群聊时，恢复本插件的独立行为。配合烤箱之心 0.58.0，Jev 使用其冻结的目标、同成员前文、引用与机器人自身图片描述，向参与规划和回复提供结构化参考；不再重复注入旧判读，不独立决定是否回复。Jev 仍需在本插件配置密钥并开启，超时或失败时继续正常回复；单独安装保持原有唤醒判读。

## 配置

> **配置双入口（1.52.0 起）**：
>
> - **AstrBot 插件配置页**只保留 5 个字段：Jev `api_key`、余额查询的 `config_content` / `config_mode`（YAML 编辑器）与文件读取的 `rag.embedding_provider_id` / `rag.rerank_provider_id`（原生 Provider 选择器）。其余字段仍在配置文件中生效，但已在配置页隐藏。
> - **状态总览 → 设置**管理上述字段之外的全部配置项（黑名单、消息与回复、分段回复、Jev 判读、文件读取五个分组），保存后立即生效并写入插件配置；文件读取等需要初始化的功能，开关变更后请重新加载插件。
>
> 下方各表按配置节列出全部字段与默认值，供查阅。

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

### 抖音直播通知 (`douyin`)

集成原自有插件 `astrbot_plugin_douyin_live_monitor`。在「状态总览 → 设置 → 抖音通知」启用，调整轮询间隔（至少 30 秒）、请求超时、开播／下播通知、封面图、每会话订阅上限和订阅管理权限。新安装默认关闭。

| 命令 | 作用 |
| --- | --- |
| `抖音订阅 <直播间链接／抖音号／用户主页> [备注]` | 为当前群或私聊订阅账号 |
| `抖音取关 <链接／抖音号>` | 删除当前会话订阅 |
| `抖音列表` / `抖音检查` | 查看订阅／立即刷新本会话状态 |
| `抖音监控` / `抖音登录` | 查看帮助／登录态概况 |
| `抖音Cookie clear` | AstrBot 管理员清除登录态 |
| `抖音CookieFile` | 管理员导入数据目录下的 `douyin_cookie.txt` |

登录 Cookie 是可选项。未配置时自动获取匿名访客标识 ttwid 进行监控；遇到风控或部分主页无法解析时，登录 Cookie 可能有帮助，但不保证绕过限制。

需要登录态时，把 Cookie 写入服务器插件数据目录 `douyin/douyin_cookie.txt`，再由管理员执行 `抖音CookieFile`。不要把 Cookie 发进群聊或普通聊天：直接传串的命令会经过聊天平台，也可能进入 AstrBot 消息日志。Cookie 以明文保存在服务器的 `data/plugin_data/astrbot_plugin_oven_multi/douyin/cookies.json`，状态页只返回是否配置，不返回原文。订阅账号保存在同目录的 `subscriptions.json`；这些运行数据不属于插件源码，Git 和发行包均排除 Cookie、订阅文件及插件数据目录。

同一账号被多个群订阅时，每轮仅查询一次，按各会话上次状态分别通知。首次启用静默校准；网络失败或风控导致的未知状态保留上次结果，不当作下播。登录态失效后可降级匿名监控；状态页只显示是否配置 Cookie，不返回原文。

### Jev 多维判读 (`jev`)

> `api_key` 在 AstrBot 插件配置页填写，其余参数在「状态总览 → 设置」中管理。

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

### 上下文压缩 (`bili`)

从 1.56.0 起，原生 Panel 的「上下文压缩」提供总览、协议统计、系统状态、会话搜索与分页，点击对话可查看上下文、最近计费输入、缓存未命中构成与模型变更。当前页按 AstrBot 会话归类对话，无法关联的记录列在「未匹配」中；搜索使用代理保存的标题或对话 ID，分页仍按对话计算。数据来自 billion-context 的只读管理接口，包含内存与磁盘历史；累计节省为理论请求累计值，净节省扣除回填和摘要成本，均为 token 而非金额。缺少计量时显示「—」，刷新失败保留上次数据。当前页每 10 秒刷新，后台页暂停，不额外调用模型。

设置页按功能分类，常用参数与高级参数分开；所有原有字段仍可编辑，密钥、余额配置与原生 Provider 选择器继续在 AstrBot 插件配置页维护。

> 基于 [billion-context](https://github.com/ranxianglei/billion-context)（本地 HTTP 代理）接管 AstrBot 的上下文压缩。经代理的会话请求会从代理清单注册 `compress`、`decompress`、`search_context`、`acp_status` 和 `acp_cache`，由 AstrBot 工具执行器转发到代理；会话 ID 由当前请求绑定，模型不能指定其他会话，也不开放写文件参数。烤箱之心自主回复会保留这些会话内维护工具，其余工具仍按原权限限制。代理负责压缩与恢复，不改写 AstrBot 本地历史；无会话标识的规划、学习等辅助请求直通上游，不进入持久压缩会话。
>
> **懒人模式**：在「状态总览 → 设置 → 上下文压缩」只打开启用开关即可——插件自动确保代理可用（端口上已有健康 bili 则直接复用；缺失时自动安装到插件持久化数据目录 `billion-runtime`，通过 Node 直接运行入口，不依赖全局 PATH 或写权限，要求宿主机 **Node ≥ 20**；不会代装 Node），播种代理配置（以 AstrBot 自带的 `x-astrbot-conversation-id` 请求头作为会话标识，按会话隔离压缩状态），检测当前生效的对话 provider 并自动接管。关闭开关则自动尝试恢复原配置。自动安装默认开启，保留已有关闭值；关闭自动安装后仍可点击状态卡的「安装／重试」。安装在后台执行，状态卡自动显示安装、启动及失败原因，保存其他设置不会打断安装。

接管时会做以下**可恢复**的修改（记录保存在插件数据目录 `bili_wire_record.json`，只记被改字段原值，不含 API Key）：

| 位置 | 改为 | 目的 |
|------|------|------|
| 目标 provider 的 `api_base` | `http://127.0.0.1:8787/bili/<原地址>` | 请求经代理压缩 |
| `agent_runner.config.compression.max_turns` | `-1` | 停用轮数截断（历史无界增长是前提） |
| `agent_runner.config.compression.overflow_strategy` | `truncate_by_turns` | 停用原生 LLM 压缩（避免双重压缩） |
| 目标 provider 的 `max_context_tokens` | `10000000` | 抬高 AstrBot 自身 82% 触发阈值 |

**默认自动跟随模型**：启用压缩后，切换聊天模型会在下一次请求前自动接管当前模型及备用链，当次请求即经过代理，无需重载 Provider。原有接管记录增量保留，辅助调用继续透传；关闭「自动跟随聊天模型」后不再新增接管，已有模型保持接管。首次启用涉及宿主管线压缩策略切换，已初始化的旧管线可能仍需重载 AstrBot；关闭或恢复后的旧 SDK 也仍按下面的生命周期处理。命令：`烤箱压缩状态`（所有人）、`烤箱压缩接管 确认` / `烤箱压缩恢复 确认`（管理员，两步确认）；面板设置页顶部也有状态卡与接管/恢复按钮。

注意事项与边界：

- 自动和手动接管都要求代理健康、会话头验证通过；多个模型且未明确选择当前模型时不猜测接管目标。
- 接管记录原子写入，宿主配置写入失败时保留恢复依据；恢复支持失败重试，用户另行修改的字段保留。
- 关闭或卸载会尝试恢复配置；运行中的 SDK 要等宿主重载后才切换。若仍使用代理，卸载会保留自有代理进程以避免断流。重启 AstrBot 后确认所有 Provider 已切回，再停止保留的 bili 实例。代理运行期间不能在面板更换地址、端口或运行方式。
- 当前压缩配置接管范围是 AstrBot 默认配置档；额外会话配置档需要单独协调压缩设置。

- **真实模型窗口须在 bili 侧配置**（托管模式下插件数据目录 `billion-context.json` 的 `providers.<url前缀>.models` / `modelContextLimit`；外部实例使用其自身配置文件），AstrBot 侧调大的 `max_context_tokens` 只是防误触发，不会传给代理。
- 接管**不会找回**此前已被原生「LLM 压缩上下文」摘要覆盖的历史，从当前状态继续。
- AstrBot 本地对话历史将无界增长（这正是"十亿上下文"的前提），每次全量发往**本地**代理、由代理在上游压缩。
- 若被接管 provider 配置了**备用 provider**（fallback），请一并接管或移除备用链：代理不可用时 AstrBot 会把完整历史直送未接管的备用上游（状态卡会告警）。
- Jev 判读直连 TypeSafe，余额查询直连各服务商，文件 RAG 的向量调用使用 Embedding Provider。烤箱之心的规划、检索、学习等辅助 LLM 调用可能共享被接管模型：从 1.54.1 起，支持的 SDK 实例对不带会话 ID 的请求加入 bili 官方 `x-bili-passthrough: 1`，经代理透传，不绑定匿名压缩会话、不注入压缩工具。带原生会话 ID 的聊天仍进入压缩流程，按对话隔离。
- 压缩状态回执不作为群聊正文发送；若本轮只有内部压缩回执，最多追加一次正常续答。已包含真实工具调用时不重放；普通文本与流式输出保留，续答用量合计。烤箱之心持久化的群聊记录、引用与自身媒体描述继续服务于目标定位，bili 处理发往模型的长会话历史，两者不互相替代或清空。
- 本地兼容验证使用 bili **0.1.206**、AstrBot **4.29.1** 真实 SDK 与假上游，包含实际压缩成块和压缩后重建；未使用付费模型，真实长聊质量及生产环境仍需单独验收。可设置 `BILI_TEST_ENTRY` 为已安装的 `bili` 的 `dist/index.js`，运行 `tests/test_bili_real_proxy.py` 重现，测试文件与会话隔离在临时目录。
| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `enabled` | 启用（开启即懒人模式） | `false` |
| `mode` | `managed`（插件拉起并守护）/ `external`（连接自跑代理） | `"managed"` |
| `executable` | bili 可执行文件（PATH 名或完整路径） | `"bili"` |
| `auto_install` | 缺失时自动安装到插件持久化数据目录 `billion-runtime`，通过 Node 直接运行入口，不依赖全局 PATH 或写权限 | `true` |
| `host` / `port` | 代理地址与端口 | `"127.0.0.1"` / `8787` |
| `provider_ids` | 接管的对话 provider，一行一个；**留空=懒人模式自动接管当前生效者** | `[]` |
| `session_header` | 会话标识请求头 | `"x-astrbot-conversation-id"` |
| `health_interval_sec` | 守护健康检查间隔（秒） | `60` |

### 余额查询 (`balance`)

通过 API 查询各服务商余额，在 Dashboard 页面查看。两项配置均在 AstrBot 插件配置页维护（YAML 编辑器）。

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

> Embedding / Rerank Provider 在 AstrBot 插件配置页选择，其余参数在「状态总览 → 设置」中管理。

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
| `烤箱压缩状态` | 查看上下文压缩代理与接管状态、巡检告警 |
| `烤箱压缩接管 确认` | 管理员：预览后接管 provider（先不带「确认」发送可看预览） |
| `烤箱压缩恢复 确认` | 管理员：恢复被接管的配置并删除接管记录 |

## Panel 状态总览

在 AstrBot 插件卡片中选择 **状态总览**，沿用 AstrBot 登录与插件页面桥接，无需额外端口或独立登录。

- **运行概览**：按“消息与回复”“判读与文件”分组显示开关、关键参数及缺失配置提醒。状态来自当前配置和初始化结果，不等同于外部服务健康检查。
- **服务余额**：点击“查询余额”才调用服务。并发查询合并，包含成功结果的响应缓存 30 秒；全部失败时允许直接重试。显示实际查询时间，各服务币种不混算。
- **设置**：面板是除密钥与 Provider 选择器外**全部配置项的管理入口**——黑名单、消息与回复、分段回复、Jev 判读、文件读取、上下文压缩六个分组，含表情 ID、关键词、断句符号等列表项（一行一个），保存后立即写入插件配置；文件读取等需初始化的功能，开关变更后请重载插件。Jev API Key 与 Embedding / Rerank Provider 仍在 AstrBot 插件配置页维护，分组顶部有指引。带 revision 与配置快照双重防陈旧：其他页面保存过或配置页改过相关字段时，旧表单会被拒绝并提示刷新。
- **上下文压缩状态卡**：设置页顶部的 billion-context 卡片显示代理健康、接管状态与巡检告警，并可二次确认执行接管/恢复。
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

上下文压缩基于 [billion-context](https://github.com/ranxianglei/billion-context) 与 [acp-kernel](https://github.com/ranxianglei/acp-kernel)（MIT + 署名条款）by ranxianglei —— 依其许可条款在此及 Panel 页面署名。


### Jev 独立判断（1.57）

原生配置填写 TypeSafe API Key、接口地址和模型名。面板 Jev 分类分别启用「主动回复判断」与「意图与情绪判断」，两者独立，均受 Jev 总开关约束。主动判断默认关闭，仅在非直接唤醒、原生访问规则允许时判断是否接话；故障时保持旁观。意图／情绪作为临时回复参考，不自行触发回复。

烤箱之心接管的群聊由核心独占自主回复，本插件自动跳过主动判断，原设置保留；意图／情绪仍可向核心提供冻结目标一致的参考。停用或移除核心后恢复独立判断。外发固定模板使用英文，聊天原文保留原语言。
