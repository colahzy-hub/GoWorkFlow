import bpy


SPECIAL_PRESET_INFO = {
    "preset_type": "your_special_preset",
    "preset_name": "你的特殊预设",
    "workflow_name": "你的特殊工作流",
    "module_name": "你的特殊模块",
    "data_file": "",
    "image_folder": "",
}


def _selected(context):
    return list(getattr(context, "selected_objects", []) or [])


def _validate(context, scene, workflow, module):
    items = _selected(context)
    if not items:
        raise Exception("请先选择要处理的对象")
    return items


def run(context, scene, workflow, module):
    """
    必须入口:
    1. 所有真实写入都放在这里或 on_panel_action。
    2. 成功返回 {'FINISHED'}。
    3. 条件不满足时 raise Exception('给用户看的中文错误原因')。
    """
    items = _validate(context, scene, workflow, module)
    processed = 0
    dry_run = panel_api.get_bool("dry_run", True)
    for obj in items:
        if obj is None:
            continue
        # TODO: 在这里实现特殊预设步骤；dry_run=True 时只检查不写入。
        processed += 1
    module_state.set("last_result", f"已处理 {processed} 个对象，dry_run={dry_run}")
    return {"FINISHED"}


def draw_panel(layout, context, scene, workflow, module, panel_api, module_state):
    """
    可选自定义面板:
    1. 只绘制 UI，不改场景、不写文件、不切模式、不调用 bpy.ops。
    2. 字段 key 使用英文，label 可以使用中文。
    3. 按钮动作交给 on_panel_action；运行按钮会调用 run。
    """
    box = panel_api.section(layout, SPECIAL_PRESET_INFO["preset_name"], icon="TOOL_SETTINGS")
    panel_api.draw_object_picker(box, "target_object", "目标对象")
    panel_api.draw_text_input(box, "step_note", "步骤说明", default="")
    panel_api.draw_toggle(box, "dry_run", "只检查不写入", default=True)
    panel_api.draw_float_input(box, "strength", "强度", default=1.0, min=0.0, max=1.0)

    status = module_state.get("last_result", "")
    if status:
        panel_api.label(layout, status, icon="INFO")

    row = panel_api.row(layout, align=True)
    panel_api.draw_button(row, "preview", "预览", icon="VIEWZOOM")
    panel_api.draw_run_button(row, "运行", icon="PLAY")


def on_panel_action(action, context, scene, workflow, module, panel_api, module_state):
    """
    可选按钮动作:
    - draw_button 传入的 action 会到这里。
    - FIELD_WRITE::字段名 表示某个输入字段刚刚回写。
    - 长期配置建议写入 module.config_payload，便于随 .goworkflow 导出。
    """
    if action == "preview":
        module_state.set("last_result", "预览完成")
        return {"FINISHED"}
    if action.startswith("FIELD_WRITE::"):
        return {"FINISHED"}
    return {"FINISHED"}
