# Agent Rules

## 许可证合规（AGPL-3.0）

本项目使用 GNU Affero General Public License v3.0。修改代码时必须遵守以下规则：

1. **保留许可证头部**
   - 每个源文件必须保留 AGPL-3.0 版权声明和许可证头部
   - 头部应包括：程序名称、版权年份、作者、许可证简短声明

2. **新增文件必须添加头部**
   - 新建源文件时必须包含 AGPL-3.0 许可证头部（参考现有文件格式）

3. **标注修改来源**
   - 修改基于第三方 AGPL 代码的文件时，必须添加 `Modified from <original_project> (<license>) by <author>` 注释
   - 保留原文件的版权声明和许可证信息

4. **不得增加额外限制**
   - 不得对 AGPL-3.0 授予的权利施加任何进一步限制
   - 不得添加违反 AGPL-3.0 的条款

5. **保留 LICENSE 文件**
   - 不得删除或修改 LICENSE 文件内容

## AstrBot 插件开发规范

1. **入口文件**
   - `main.py` 是插件唯一入口，必须包含继承自 `Star` 的插件类
   - 所有 Handler（`@filter.command`、`@filter.event_message_type` 等装饰器）必须注册在 `main.py` 的插件类中
   - Handler 的具体逻辑应委托给 `features/` 下的独立模块

2. **日志规范**
   - 必须使用 `from astrbot.api import logger`，禁止使用 `logging` 模块
   - 日志前缀格式：`[烤箱-{功能名}]`，例如 `[烤箱-合并转发]`

3. **配置管理**
   - 通过 `core/config_manager.py` 的 `ConfigManager` 统一访问配置
   - 禁止各模块直接解析 `self.config` 字典
   - 功能开关统一使用 `config_mgr.is_feature_enabled(FEATURE_NAME)` 检查

4. **持久化数据**
   - 持久化数据必须存储于 `data` 目录下（通过 `StarTools.get_data_dir()` 获取）
   - 禁止在插件目录内写入运行时数据

## 项目结构规范

```
astrbot_plugin_oven_multi/
├── main.py                    # 插件入口（Handler 注册 + 模块协调）
├── core/
│   ├── config_manager.py      # 统一配置管理器
│   └── base_feature.py        # 功能模块基类接口
├── features/
│   ├── bracket_matcher.py     # 括号匹配
│   ├── repeater.py            # 消息复读
│   ├── thinking_manager.py    # 思考表情
│   ├── forward_handler.py     # 合并转发处理
│   └── active_reply.py        # 主动回复
├── learning_style/            # 风格学习子系统（已有）
├── favor_manager.py           # 好感度系统
├── balance_checker.py         # 余额查询
├── utils/
│   ├── constants.py           # 常量定义（插件名、版本号、功能名）
│   └── decorators.py          # 通用装饰器
├── metadata.yaml              # 插件元数据
└── CHANGELOG.md               # 变更日志
```

### 新增功能模块流程

1. 在 `utils/constants.py` 中添加 `FEATURE_XXX` 常量
2. 在 `features/` 下创建独立模块文件，实现功能逻辑
3. 在 `main.py` 中导入模块并在插件类中注册 Handler
4. Handler 内部调用 `features/` 模块的方法，不直接实现业务逻辑

## 每次修改代码后必须执行

1. **更新版本号**
   - `metadata.yaml` 中的 `version` 字段
   - `utils/constants.py` 中的 `PLUGIN_VERSION` 常量

2. **更新 CHANGELOG.md**
   - 在顶部新增版本条目
   - 简要描述变更内容，分类使用 `### Bug Fixes`、`### New Features`、`### Refactoring`

3. **提交 git**
   - commit message 格式：`类型: 简短描述`
   - 例如：`feat: 添加主动回复功能` / `fix: 修复指令被风格学习记录的问题`

4. **更新 README.md**
   - 如有功能变更，更新相关功能描述
   - 如有新增配置项，更新配置说明部分
   - 如有 API 变更，更新接口文档部分
