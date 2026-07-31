# 安装方式

## 推荐方式：完整 zip 安装

1. 从 GitHub Releases 下载最新安装包 `go_workflow_v*.zip`。
2. 在 Blender 中打开 `编辑 > 偏好设置 > 扩展/插件`。
3. 选择从磁盘安装 zip。
4. 启用 `Go工作流 / Go Workflow`。

## 源码方式

源码方式适合开发和排查问题。先安装 Git LFS：

```powershell
git lfs install
git clone https://github.com/colahzy-hub/GoWorkFlow.git
```

然后将 `go_workflow_extension/go_workflow` 作为 Blender 扩展目录使用，或从源码重新打完整 zip。

## 发布包

- `go_workflow_v*.zip`：完整插件安装包，包含主插件、内置脚本、特殊预设和参考资源。
- 本地开发迭代包保存在 `02_版本迭代区/version_history/`，正式分发以 GitHub Releases 附件为准。
