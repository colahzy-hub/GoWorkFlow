import json
import math

import bmesh
import bpy


DEFAULT_THRESHOLD = 1.0e-4
REALTIME_TIMER_INTERVAL = 0.24
_REALTIME_STATE = {"running": False, "token": 0}
_REALTIME_TIMER_STATE = {"callback": None}
_REALTIME_TIMER_REGISTRY_KEY = "go_workflow.realtime_diff_callbacks"


def _get_config(module):
    payload = getattr(module, "config_payload", "")
    if isinstance(payload, str) and payload.strip():
        try:
            data = json.loads(payload)
            return data if isinstance(data, dict) else {}
        except Exception:
            pass
    return {}


def _set_config(module, data):
    module.config_payload = json.dumps(data or {}, ensure_ascii=False, sort_keys=True)


def _module_state():
    return globals().get("module_state")


def _active_or_config_object(context, panel_api, config):
    obj = panel_api.get_object("target_object") if panel_api is not None else None
    if obj is None:
        target_name = str(config.get("target_object", "") or "")
        obj = bpy.data.objects.get(target_name) if target_name else None
    if obj is None:
        obj = getattr(context, "active_object", None)
    if obj is None:
        raise Exception("请先选择一个网格对象，或在面板里指定目标对象")
    if getattr(obj, "type", None) != "MESH":
        raise Exception("目标对象必须是网格类型")
    if not getattr(obj.data, "shape_keys", None):
        raise Exception(f'对象 "{obj.name}" 没有形态键')
    return obj


def _analyze_shape_key(kb, shape_keys, threshold):
    ref = kb.relative_key
    if ref is None:
        if shape_keys.key_blocks:
            ref = shape_keys.key_blocks[0]
        else:
            ref = None

    if ref is None or kb == ref:
        return True, 0.0, ref.name if ref else "None"

    if not kb.data or not ref.data or len(kb.data) != len(ref.data):
        return True, 0.0, ref.name if ref else "None"

    max_sq = 0.0
    for index, vertex in enumerate(kb.data):
        delta_sq = (vertex.co - ref.data[index].co).length_squared
        if delta_sq > max_sq:
            max_sq = delta_sq
    max_dist = math.sqrt(max_sq)
    return max_dist <= threshold, max_dist, ref.name


def _set_active_shape_key(context, obj, index):
    shape_keys = getattr(getattr(obj, "data", None), "shape_keys", None)
    key_blocks = getattr(shape_keys, "key_blocks", None)
    index = int(index)
    if key_blocks is None or index < 0 or index >= len(key_blocks):
        raise Exception("目标形态键索引无效")
    try:
        context.view_layer.objects.active = obj
    except Exception:
        pass
    try:
        obj.select_set(True)
    except Exception:
        pass
    obj.active_shape_key_index = index
    return key_blocks[index]


def _register_realtime_timer(callback):
    _REALTIME_TIMER_STATE["callback"] = callback
    try:
        registry = bpy.app.driver_namespace.setdefault(_REALTIME_TIMER_REGISTRY_KEY, set())
        registry.add(callback)
    except Exception:
        pass


def _release_realtime_timer(callback):
    if _REALTIME_TIMER_STATE.get("callback") is callback:
        _REALTIME_TIMER_STATE["callback"] = None
    try:
        registry = bpy.app.driver_namespace.get(_REALTIME_TIMER_REGISTRY_KEY)
        if registry is not None:
            registry.discard(callback)
            if not registry:
                bpy.app.driver_namespace.pop(_REALTIME_TIMER_REGISTRY_KEY, None)
    except Exception:
        pass


def _cancel_realtime_timer():
    callback = _REALTIME_TIMER_STATE.get("callback")
    if callback is None:
        return False
    try:
        bpy.app.timers.unregister(callback)
    except Exception:
        pass
    _release_realtime_timer(callback)
    return True


def _stop_realtime_diff(module_state, panel_api=None):
    _REALTIME_STATE["running"] = False
    _REALTIME_STATE["token"] += 1
    _cancel_realtime_timer()
    if panel_api is not None:
        try:
            panel_api.set_bool("realtime_diff_enabled", False)
        except Exception:
            pass
    if module_state is not None:
        module_state.set("realtime_enabled", False)


def cleanup_runtime(scene=None, workflow=None, module=None, module_state=None):
    _stop_realtime_diff(module_state)
    if module_state is not None:
        module_state.set("realtime_object_name", "")
        module_state.set("realtime_last_key_index", -1)
    return True


def _filtered_details(config):
    return [entry for entry in list(config.get("details", []) or []) if bool(entry.get("is_empty", False))]


def _sync_ui_list(context, config):
    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "bwflow_shape_key_inspector_items"):
        return
    items = scene.bwflow_shape_key_inspector_items
    items.clear()
    for entry in _filtered_details(config):
        item = items.add()
        item.object_name = str(config.get("scanned_object", "") or "")
        item.key_index = int(entry.get("index", -1))
        item.key_name = str(entry.get("name", "") or "")
        item.is_empty = bool(entry.get("is_empty", False))
        item.max_delta = float(entry.get("max_dist", 0.0) or 0.0)
        item.reference_name = str(entry.get("ref_name", "Basis") or "Basis")
    current_active = -1
    obj = bpy.data.objects.get(str(config.get("scanned_object", "") or ""))
    if obj is not None:
        current_active = int(getattr(obj, "active_shape_key_index", -1) or -1)
    selected = 0
    for idx, entry in enumerate(_filtered_details(config)):
        if int(entry.get("index", -1)) == current_active:
            selected = idx
            break
    scene.bwflow_shape_key_inspector_index = selected if len(items) else -1


def _copy_empty_names(context, config):
    names = [str(name).strip() for name in list(config.get("empty_keys", []) or []) if str(name).strip()]
    if not names:
        raise Exception("当前没有可复制的空形态键列表，请先扫描")
    context.window_manager.clipboard = "\n".join(names)
    return len(names)


def _current_active_stats(obj):
    key_blocks = getattr(getattr(obj.data, "shape_keys", None), "key_blocks", None)
    if key_blocks is None or len(key_blocks) <= 1:
        raise Exception("目标物体没有可检查的形态键")
    key_index = int(getattr(obj, "active_shape_key_index", 0) or 0)
    if key_index <= 0 or key_index >= len(key_blocks):
        raise Exception("请先选中一个非 Basis 的形态键")
    target = key_blocks[key_index]
    reference = getattr(target, "relative_key", None)
    if reference is None or reference == target:
        reference = key_blocks[0]
    if len(target.data) != len(reference.data):
        raise Exception("当前形态键与参考键顶点数量不一致")
    moved_count = 0
    max_delta_sq = 0.0
    indices = []
    for vertex_index, vertex in enumerate(target.data):
        delta_sq = (vertex.co - reference.data[vertex_index].co).length_squared
        if delta_sq > 0.0:
            moved_count += 1
            indices.append(vertex_index)
            if delta_sq > max_delta_sq:
                max_delta_sq = delta_sq
    return target, reference, moved_count, math.sqrt(max_delta_sq), indices


def _select_difference_vertices_in_edit_mode(context, obj, vertex_indices):
    # 注：timer callback 中的 bpy.context.view_layer.objects.active 可能不是 obj
    # 因此这里只检查 mode，不依赖 context.view_layer.objects.active
    if getattr(obj, "mode", "") != "EDIT":
        raise Exception("请先进入该物体的编辑模式")
    try:
        context.view_layer.objects.active = obj
    except Exception:
        pass
    try:
        bpy.ops.mesh.select_mode(type="VERT")
    except Exception:
        pass
    bm = bmesh.from_edit_mesh(obj.data)
    try:
        bm.verts.ensure_lookup_table()
    except Exception:
        pass
    selected = set(vertex_indices)
    # Step 1: 取消当前所有选择（仅操作 bmesh 内存，未推送视口）
    for vert in bm.verts:
        vert.select_set(False)
    # Step 2: 选中差异顶点（紧接上一步，无间隙）
    for vert in bm.verts:
        if vert.index in selected:
            vert.select_set(True)
    # 仅在最后一次性推送到视口，用户感知不到两步操作的间隔
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)
    try:
        obj.data.update()
    except Exception:
        pass
    try:
        wm = getattr(bpy.context, "window_manager", None)
        for window in getattr(wm, "windows", []) or []:
            screen = getattr(window, "screen", None)
            for area in getattr(screen, "areas", []) or []:
                if getattr(area, "type", "") == "VIEW_3D":
                    area.tag_redraw()
    except Exception:
        pass


def _apply_active_difference_selection(context, panel_api, module_state, obj):
    key_block, reference, moved_count, max_delta, indices = _current_active_stats(obj)
    summary = f"{key_block.name}：相对 {reference.name} 移动顶点 {moved_count} 个，最大位移 {max_delta:.6g}"
    if module_state is not None:
        module_state.set("active_summary", summary)
        module_state.set("last_result", summary)
        module_state.set("realtime_last_key_index", int(getattr(obj, "active_shape_key_index", 0) or 0))
    if getattr(obj, "mode", "") == "EDIT":
        _select_difference_vertices_in_edit_mode(context, obj, indices)
        if panel_api is not None:
            panel_api.set_status(f"已在编辑模式选中 {moved_count} 个差异顶点", level="OK")
    elif panel_api is not None:
        panel_api.set_status(f"{summary}；进入编辑模式后再点可直接选中这些顶点", level="WARNING")
    return key_block, reference, moved_count, max_delta, indices


def _realtime_enabled(panel_api, module_state):
    if panel_api is not None:
        return bool(panel_api.get_bool("realtime_diff_enabled", False))
    return bool(module_state.get("realtime_enabled", False)) if module_state is not None else False


def _start_realtime_diff(context, panel_api, module_state, obj):
    if getattr(obj, "mode", "") != "EDIT":
        raise Exception("实时检查差异点需要先进入编辑模式")
    _stop_realtime_diff(module_state)
    _REALTIME_STATE["running"] = True
    _REALTIME_STATE["token"] += 1
    token = int(_REALTIME_STATE["token"])
    object_name = obj.name_full
    if panel_api is not None:
        panel_api.set_bool("realtime_diff_enabled", True)
        panel_api.set_status("已开启实时检查差异点：切换活动形态键后会自动刷新顶点选择", level="OK")
    if module_state is not None:
        module_state.set("realtime_enabled", True)
        module_state.set("realtime_object_name", object_name)
        module_state.set("realtime_last_key_index", -1)
    try:
        active_index = int(getattr(obj, "active_shape_key_index", 0) or 0)
    except Exception:
        active_index = 0
    if active_index > 0:
        try:
            _apply_active_difference_selection(context, panel_api, module_state, obj)
        except Exception as exc:
            if module_state is not None:
                module_state.set("active_summary", f"实时检查启动失败：{exc}")
            raise
    elif module_state is not None:
        module_state.set("realtime_last_key_index", 0)
        module_state.set("active_summary", "当前是 Basis，切换到非 Basis 形态键后会自动选择差异顶点")

    def _tick():
        if not _REALTIME_STATE["running"] or token != _REALTIME_STATE["token"]:
            _release_realtime_timer(_tick)
            return None
        current_obj = bpy.data.objects.get(object_name)
        if current_obj is None or getattr(current_obj, "type", "") != "MESH":
            _stop_realtime_diff(module_state)
            _release_realtime_timer(_tick)
            return None
        if getattr(current_obj, "mode", "") != "EDIT":
            return REALTIME_TIMER_INTERVAL
        try:
            key_index = int(getattr(current_obj, "active_shape_key_index", 0) or 0)
        except Exception:
            return REALTIME_TIMER_INTERVAL
        last_index = int(module_state.get("realtime_last_key_index", -1) or -1) if module_state is not None else -1
        if key_index <= 0:
            if last_index != 0:
                try:
                    bm = bmesh.from_edit_mesh(current_obj.data)
                    try:
                        bm.verts.ensure_lookup_table()
                    except Exception:
                        pass
                    for vert in bm.verts:
                        vert.select_set(False)
                    bm.select_flush_mode()
                    bmesh.update_edit_mesh(current_obj.data, loop_triangles=False, destructive=False)
                except Exception:
                    pass
                if module_state is not None:
                    module_state.set("realtime_last_key_index", 0)
                    module_state.set("active_summary", "当前是 Basis，已清空差异顶点选择")
            return REALTIME_TIMER_INTERVAL
        if key_index == last_index:
            return REALTIME_TIMER_INTERVAL
        try:
            _apply_active_difference_selection(bpy.context, panel_api, module_state, current_obj)
        except Exception as _e:
            if module_state is not None:
                module_state.set("active_summary", f"实时检查出错：{_e}")
            return REALTIME_TIMER_INTERVAL
        return REALTIME_TIMER_INTERVAL

    _register_realtime_timer(_tick)
    bpy.app.timers.register(_tick, first_interval=0.02)


def _scan_object(obj, threshold):
    shape_keys = obj.data.shape_keys
    empty_keys = []
    details = []

    for index, key_block in enumerate(shape_keys.key_blocks):
        is_empty, max_dist, ref_name = _analyze_shape_key(key_block, shape_keys, threshold)
        item = {
            "index": index,
            "name": key_block.name,
            "is_empty": bool(is_empty),
            "max_dist": float(max_dist),
            "ref_name": str(ref_name),
        }
        details.append(item)
        if is_empty:
            empty_keys.append(key_block.name)

    return {
        "last_result": f'扫描完成。对象 "{obj.name}" 共 {len(shape_keys.key_blocks)} 个形态键，空键 {len(empty_keys)} 个',
        "scanned_object": obj.name,
        "total_keys": len(shape_keys.key_blocks),
        "empty_keys": empty_keys,
        "details": details,
        "threshold_used": float(threshold),
    }


def run(context, scene, workflow, module):
    config = _get_config(module)
    panel_api = globals().get("panel_api")
    obj = _active_or_config_object(context, panel_api, config)
    threshold = float(config.get("threshold", DEFAULT_THRESHOLD) or DEFAULT_THRESHOLD)
    if panel_api is not None:
        threshold = float(panel_api.get_float("threshold", threshold) or threshold)

    config.update(_scan_object(obj, threshold))
    config["target_object"] = obj.name
    config["threshold"] = threshold
    _set_config(module, config)
    _sync_ui_list(context, config)

    module_state = _module_state()
    if module_state is not None:
        module_state.set("last_result", config["last_result"])
    return {"FINISHED"}


def _selected_list_item(context):
    scene = getattr(context, "scene", None)
    if scene is None or not hasattr(scene, "bwflow_shape_key_inspector_items"):
        return None
    index = int(getattr(scene, "bwflow_shape_key_inspector_index", -1) or -1)
    items = scene.bwflow_shape_key_inspector_items
    if index < 0 or index >= len(items):
        return None
    return items[index]


def draw_panel(layout, context, scene, workflow, module, panel_api, module_state):
    config = _get_config(module)
    box = panel_api.section(layout, "形态键鉴定", icon="SHAPEKEY_DATA")

    panel_api.draw_object_picker_inline(box, "target_object", "目标对象:", show_active_button=True, factor=0.24)
    panel_api.draw_float_input_inline(
        box,
        "threshold",
        "判定阈值",
        default=float(config.get("threshold", DEFAULT_THRESHOLD) or DEFAULT_THRESHOLD),
        factor=0.24,
    )

    actions = panel_api.row(box, align=True)
    panel_api.draw_run_button(actions, "扫描空键", icon="VIEWZOOM")
    panel_api.draw_button(actions, "COPY_EMPTY_NAMES", "复制空键名称", icon="COPYDOWN")

    last_result = str(config.get("last_result", "") or "")
    if last_result:
        panel_api.label(box, last_result, icon="INFO")

    scanned_obj = str(config.get("scanned_object", "") or "")
    empty_keys = list(config.get("empty_keys", []) or [])
    threshold_used = float(config.get("threshold_used", config.get("threshold", DEFAULT_THRESHOLD)) or 0.0)
    total_details = list(config.get("details", []) or [])
    filtered_details = _filtered_details(config)

    if scanned_obj and total_details:
        summary = panel_api.section(box, "扫描结果", icon="INFO")
        panel_api.label(summary, f"扫描对象：{scanned_obj}", icon="OBJECT_DATA")
        panel_api.label(summary, f"形态键总数：{int(config.get('total_keys', 0) or 0)}  空键：{len(empty_keys)}", icon="SHAPEKEY_DATA")
        panel_api.label(summary, f"阈值：{threshold_used:.6f}", icon="DRIVER_DISTANCE")

        scene_box = panel_api.section(box, f"扫描结果列表 ({len(filtered_details)})", icon="ALIGN_JUSTIFY")
        row = scene_box.row()
        row.template_list(
            "BWFLOW_UL_shape_key_inspector_results",
            "",
            context.scene,
            "bwflow_shape_key_inspector_items",
            context.scene,
            "bwflow_shape_key_inspector_index",
            rows=10,
            maxrows=16,
        )
    else:
        panel_api.label(box, "当前没有扫描结果，请点击扫描空键", icon="INFO")

    current_box = panel_api.section(box, "当前活动形态键", icon="RESTRICT_SELECT_OFF")
    try:
        obj = _active_or_config_object(context, panel_api, config)
        key_blocks = obj.data.shape_keys.key_blocks
        active_index = int(getattr(obj, "active_shape_key_index", 0) or 0)
        if 0 < active_index < len(key_blocks):
            target = key_blocks[active_index]
            reference = getattr(target, "relative_key", None)
            if reference is None or reference == target:
                reference = key_blocks[0]
            panel_api.label(current_box, f"当前：{target.name}", icon="SHAPEKEY_DATA")
            panel_api.label(current_box, f"参考键：{reference.name}", icon="LINKED")
        else:
            panel_api.label(current_box, "当前是 Basis 或未选中形态键", icon="INFO")
    except Exception:
        panel_api.label(current_box, "请选择目标物体", icon="INFO")
    current_actions = panel_api.row(current_box, align=True)
    panel_api.draw_button(current_actions, "SELECT_DIFF_ACTIVE", "显示差异顶点", icon="VERTEXSEL")
    panel_api.draw_toggle_inline(current_box, "realtime_diff_enabled", "实时检查差异点", default=False, factor=0.24)
    if _realtime_enabled(panel_api, module_state):
        panel_api.label(current_box, "实时模式已开启：在编辑模式切换活动形态键时会自动刷新差异顶点。", icon="CHECKMARK")
    active_summary = str(module_state.get("active_summary", "") or "") if module_state is not None else ""
    if active_summary:
        panel_api.label(current_box, active_summary, icon="INFO")

    panel_api.draw_status(box)


def on_panel_action(action, context, scene, workflow, module, panel_api, module_state):
    config = _get_config(module)
    if action.startswith("FIELD_WRITE::"):
        field = action.split("::", 1)[1]
        if field == "target_object":
            obj = panel_api.get_object("target_object")
            config["target_object"] = obj.name if obj else ""
        elif field == "threshold":
            config["threshold"] = float(panel_api.get_float("threshold", DEFAULT_THRESHOLD) or DEFAULT_THRESHOLD)
        elif field == "realtime_diff_enabled":
            obj = _active_or_config_object(context, panel_api, config)
            if _realtime_enabled(panel_api, module_state):
                _start_realtime_diff(context, panel_api, module_state, obj)
            else:
                _stop_realtime_diff(module_state, panel_api=panel_api)
                panel_api.set_status("已关闭实时检查差异点", level="OK")
                if module_state is not None:
                    module_state.set("last_result", "已关闭实时检查差异点")
            return {"FINISHED"}
        _set_config(module, config)
        return {"FINISHED"}

    if action == "COPY_EMPTY_NAMES":
        copied = _copy_empty_names(context, config)
        panel_api.set_status(f"已复制 {copied} 个空形态键名称", level="OK")
        return {"FINISHED"}

    if action.startswith("ACTIVATE_KEY::"):
        index = int(action.split("::", 1)[1] or 0)
        obj = bpy.data.objects.get(str(config.get("scanned_object", "") or ""))
        if obj is None:
            obj = _active_or_config_object(context, panel_api, config)
        key_block = _set_active_shape_key(context, obj, index)
        panel_api.set_status(f"已切换到形态键：{key_block.name}", level="OK")
        return {"FINISHED"}

    if action == "SELECT_DIFF_ACTIVE" or action.startswith("SELECT_DIFF::"):
        obj = _active_or_config_object(context, panel_api, config)
        if action.startswith("SELECT_DIFF::"):
            index = int(action.split("::", 1)[1] or 0)
            _set_active_shape_key(context, obj, index)
        _apply_active_difference_selection(context, panel_api, module_state, obj)
        return {"FINISHED"}

    return {"FINISHED"}
