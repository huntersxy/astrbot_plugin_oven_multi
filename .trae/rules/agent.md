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

## 每次修改代码后必须执行

1. **更新版本号**
   - `metadata.yaml` 中的 `version` 字段
   - `main.py` 中 `@register()` 装饰器的版本号

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
