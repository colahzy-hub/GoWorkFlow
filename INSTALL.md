# 安装与升级

## 1. Release 安装

这是普通用户推荐的安装方式。

1. 打开 [GoWorkFlow Releases](https://github.com/colahzy-hub/GoWorkFlow/releases)。
2. 下载最新的 `go_workflow_v*.zip`。
3. 在 Blender 中打开 `编辑 > 偏好设置 > 扩展` 或 `编辑 > 偏好设置 > 插件`。
4. 点击右上角菜单，选择从磁盘安装 zip。
5. 选择下载的 GoWorkFlow zip。
6. 启用 `Go工作流 / Go Workflow`。

安装后，在 3D 视图按 `N` 打开侧边栏，找到 Go 工作流标签页。

### 安装包结构检查

安装包内部应直接包含插件目录：

```text
go_workflow/
├─ __init__.py
├─ blender_manifest.toml
├─ builtin_scripts/
└─ special_presets/
```

如果解压后出现多余的嵌套目录，例如 `go_workflow_v1.0.8/go_workflow/go_workflow/`，不要继续安装该包，应重新从 Release 下载。

## 2. 升级

1. 关闭正在运行的 Blender。
2. 在 Blender 插件列表中禁用旧版 GoWorkFlow。
3. 安装新的 Release zip。
4. 启用新版插件。
5. 打开 Go 工作流设置，确认工作流和脚本库仍然存在。

升级不会主动删除用户工作流、脚本库和全局状态。预设导入生成的临时脚本资产会在后续维护任务中清理未引用文件。

## 3. 源码安装

源码安装适合开发和排错。

```powershell
git clone https://github.com/colahzy-hub/GoWorkFlow.git
```

在 Blender 中将下列目录作为插件目录使用：

```text
GoWorkFlow\go_workflow_extension\go_workflow\
```

也可以从仓库根目录运行 `go_workflow.py`，由入口脚本加载同一套运行时模块。

源码安装后，修改代码前建议关闭 Blender，避免旧模块仍留在 Python 进程中。

## 4. 预设迁移

工作流预设文件使用 `.gwpreset` 扩展名。

- 导出：设置 > 预设 > 导出勾选的工作流。
- 载入：设置 > 预设 > 选择 `.gwpreset` 预设包。
- 导入：在预设列表中选择工作流后，点击导入当前选中工作流。

预设可以跨机器迁移，但第三方插件面板必须在目标环境中安装，否则会显示缺失面板提示。脚本路径如果指向外部文件，也需要在目标机器上重新确认。

## 5. 卸载

1. 在 Blender 插件列表中禁用并卸载 GoWorkFlow。
2. 如果 Blender 无法删除插件目录，先执行 Go 工作流设置中的“恢复默认 N 面板”。
3. 关闭 Blender 后手动删除残留插件目录。

不要直接删除用户配置目录中的全局状态文件，除非需要重置全部工作流设置。遇到状态文件损坏时，优先保留 `.bak` 文件用于恢复。
