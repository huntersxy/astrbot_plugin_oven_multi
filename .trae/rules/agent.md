# Agent Rules

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
