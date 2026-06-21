# GoWorkFlow

GoWorkFlow 是一个 Blender 侧边栏工作流插件，用于管理 N 面板工作流、脚本库、自定义脚本模块、预设导入导出，以及内置 ARKit 形态键参考与验证流程。

## 支持版本

- Blender 3.6+
- 当前开发和测试重点：Blender 4.2.x
- Blender 5.x 仍建议按实际日志继续验证兼容性

## 主要功能

- 工作流式 N 面板管理
- 面板组扫描、排序、隐藏/显示
- 脚本库与脚本模板管理
- `.goworkflow` 预设导入导出
- 内置 ARKit 形态键工作流参考
- ARKit 形态键混合验证、全面混合验证和关键帧验证
- 口型生成、形态键鉴定、ARKit 合成形态键等内置脚本模块

## 安装

1. 从 GitHub Releases 下载最新 `go_workflow_v*.zip`。
2. 在 Blender 中打开 `编辑 > 偏好设置 > 扩展/插件`。
3. 选择从磁盘安装 zip。
4. 启用 `Go工作流 / Go Workflow`。

源码安装时，确保插件目录结构保持为：

```text
go_workflow_extension/
└─ go_workflow/
   ├─ __init__.py
   ├─ blender_manifest.toml
   └─ ...
```

## 开发说明

- 主插件代码位于 `go_workflow_extension/go_workflow/`。
- 内置脚本位于 `go_workflow_extension/go_workflow/builtin_scripts/`。
- 特殊预设位于 `go_workflow_extension/go_workflow/special_presets/`。
- 打包产物建议上传到 GitHub Releases，不建议提交进 Git 历史。
- 本仓库不提交本地测试工程、缓存、恢复目录、第三方参考插件目录和临时抓取文件。

## 许可证

插件 manifest 当前声明为 `GPL-3.0-or-later`。

ARKit 参考媒体和文档整理内容如来自外部资料，请在发布页保留来源说明和必要署名。

