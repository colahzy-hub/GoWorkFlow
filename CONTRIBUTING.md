# 贡献指南

感谢提交 GoWorkFlow 的问题、建议或代码。

## 提交问题

提交前请先搜索已有 Issue，并确认问题在最新 Release 中仍然存在。

Bug 报告至少应包含：

- Blender 和 GoWorkFlow 版本
- 操作系统
- 使用的编辑器空间
- 最小复现步骤
- 预期行为和实际行为
- 相关日志或 Python backtrace

不要上传包含模型、个人路径、完整用户配置或第三方插件源码的压缩包。

## 提交代码

1. Fork 仓库并创建独立分支。
2. 保持改动集中，不在无关文件中做格式化或重命名。
3. 修改 Blender 运行逻辑时优先使用 Blender 4.2.9 做回归。
4. 修改预设导入时至少测试一个 `.gwpreset`。
5. 更新用户可见行为时同步更新 `CHANGELOG.md` 或相关文档。

提交前运行：

```powershell
python -m py_compile go_workflow_extension\go_workflow\__init__.py
git diff --check
```

## Pull Request

Pull Request 应说明：

- 修改解决了什么问题
- 影响哪些工作流和编辑器空间
- 是否改变预设、配置或脚本库格式
- 使用了哪些 Blender 版本验证

不要把本地 `02_版本迭代区/version_history/` 压缩包提交到 Git 历史。正式包应作为 GitHub Release 附件发布。
