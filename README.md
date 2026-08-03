# GoWorkFlow

Blender 的 N 面板工作流、脚本模块和预设管理插件。

GoWorkFlow 适合把常用的 Blender 面板、批处理脚本、形态键工具和测试预设整理成可切换的工作流。它不会替换 Blender 原生面板，而是在工作流中控制哪些面板显示、显示顺序以及模块脚本如何运行。

[![Latest Release](https://img.shields.io/github/v/release/colahzy-hub/GoWorkFlow?display_name=tag&sort=semver)](https://github.com/colahzy-hub/GoWorkFlow/releases)
[![Blender](https://img.shields.io/badge/Blender-3.6%2B-orange)](https://www.blender.org/)
[![License](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)](LICENSE)

## 当前版本

当前发布版本：**1.0.8**

- 下载：进入 [Releases](https://github.com/colahzy-hub/GoWorkFlow/releases) 下载 `go_workflow_v1.0.8.zip`。
- 当前重点测试版本：Blender 4.2.9。
- Blender 4.5：已加入状态恢复、预设导入和失效面板访问的兼容保护，建议使用最新 Release 后再按实际日志验证。
- Blender 5.x：暂不承诺完整兼容。

## 功能概览

### 工作流与 N 面板

- 创建、复制、删除和排序多个工作流。
- 扫描 Blender 侧边栏面板并按插件、面板组和层级整理。
- 按工作流显示或隐藏第三方 N 面板。
- 支持面板搜索、标签筛选、缺失面板提示和面板顺序调整。
- 支持 `VIEW_3D`、`IMAGE_EDITOR` 和 `NODE_EDITOR` 状态隔离。

### 脚本模块与脚本库

- 为工作流添加 Python 脚本模块。
- 绑定 `.py` 文件、Blender Text 数据块或内嵌脚本内容。
- 为模块提供自定义面板。
- 保存、载入、刷新和复用脚本库条目。
- 将脚本库条目切换到不同工作流。

### 预设

- 使用 `.gwpreset` 导出和导入工作流。
- 先载入预设查看其中的工作流，再单独导入当前选中的工作流。
- 预设中的脚本、配置和说明文件会按内容去重保存。
- 导入失败时自动回滚新增工作流，避免留下半成品状态。

### 内置工具

- ARKit 形态键合成与 VRM/MMD 目标形态键生成。
- VRChat/MMD 口型生成。
- 形态键鉴定和空键检查。
- 形态键实时采集、投射和回放。
- ARKit 面捕测试和 MMD 表情测试预设。

## 安装

推荐使用 GitHub Release 中的完整 zip 包：

1. 打开 [Releases](https://github.com/colahzy-hub/GoWorkFlow/releases)。
2. 下载最新的 `go_workflow_v*.zip`。
3. 在 Blender 中打开 `编辑 > 偏好设置 > 扩展` 或 `编辑 > 偏好设置 > 插件`。
4. 选择从磁盘安装 zip。
5. 启用 `Go工作流 / Go Workflow`。
6. 打开 3D 视图右侧 N 面板，进入 Go 工作流标签页。

完整安装、源码安装和升级说明见 [INSTALL.md](INSTALL.md)。

## 快速上手

1. 打开 Go 工作流面板。
2. 在设置中点击“初始化默认工作流”。
3. 在“工作流”页新建或选择工作流。
4. 在“面板库”页搜索面板并加入当前工作流。
5. 在“脚本模块”页新增模块，绑定脚本文件或编写脚本。
6. 运行模块前先确认目标对象、形态键或文件路径等参数。
7. 需要迁移配置时，在“预设”页导出 `.gwpreset`。

预设的详细使用方法见 [docs/USER_GUIDE.md](docs/USER_GUIDE.md)。

## 目录结构

```text
GoWorkFlow/
├─ go_workflow_extension/
│  └─ go_workflow/              # Blender 插件主目录
│     ├─ __init__.py            # 注册入口和主要 UI/工作流逻辑
│     ├─ blender_manifest.toml  # Blender 扩展清单
│     ├─ builtin_scripts/       # 内置脚本和脚本库清单
│     ├─ replay_presets/        # 面捕/表情回放预设
│     └─ special_presets/       # 特殊工作流预设和参考资源
├─ go_workflow.py               # 源码目录下的加载入口
├─ INSTALL.md                   # 安装和升级
├─ docs/                        # 面向用户和贡献者的文档
├─ CHANGELOG.md                 # 版本变更记录
└─ LICENSE                     # GPL-3.0-or-later
```

`00_工作区`、`02_版本迭代区` 和本地测试资源不属于 Release 安装包。发布包以 GitHub Release 附件为准。

## 兼容性与排错

如果出现面板绘制失败、预设导入卡顿或 Blender 报错：

1. 先确认使用的是最新 Release。
2. 重启 Blender 后重试一次。
3. 不要把本地 `_imported_presets` 缓存目录直接复制到另一台机器。
4. 保留 Blender 报错日志和触发步骤。
5. 按 [排错指南](docs/TROUBLESHOOTING.md) 收集信息后提交 Issue。

## 开发

源码修改集中在 `go_workflow_extension/go_workflow/`。提交前至少运行：

```powershell
python -m py_compile go_workflow_extension\go_workflow\__init__.py
git diff --check
```

Blender 相关改动建议使用 Blender 4.2.9 后台模式做最小加载和预设导入回归。

## 许可

GoWorkFlow 使用 [GPL-3.0-or-later](LICENSE) 发布。
