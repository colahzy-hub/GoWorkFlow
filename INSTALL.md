# 安装方式

## 推荐方式：完整 zip 安装

1. 下载 `release_packages/go_workflow_full_v*.zip`。
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

## 拆分包用途

- `go_workflow_main_plugin_v*.zip`：主插件主体。
- `go_workflow_special_presets_v*.zip`：特殊预设。
- `go_workflow_builtin_scripts_v*.zip`：预设脚本库。

拆分包不作为普通用户的首选安装方式。后续如果要做在线安装器，可以按这三个拆分包分别下载并合并到同一个 `go_workflow_extension/go_workflow` 目录。
