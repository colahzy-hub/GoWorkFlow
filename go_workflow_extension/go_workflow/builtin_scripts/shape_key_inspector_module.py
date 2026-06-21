import json
from array import array

import bmesh
import bpy


DEFAULT_LIST_LIMIT = 24
EPSILON = 1.0e-7
REALTIME_TIMER_INTERVAL = 0.24
_REALTIME_STATE = {"running": False, "token": 0}
_REALTIME_TIMER_STATE = {"callback": None}
_REALTIME_TIMER_REGISTRY_KEY = "go_workflow.realtime_diff_callbacks"


def _panel_api():
    return globals().get("panel_api")


def _module_state():
    return globals().get("module_state")


def _settings(module):
    raw = getattr(module, "config_payload", "") or ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _save_settings(module, data):
    module.config_payload = json.dumps(data or {}, ensure_ascii=False, sort_keys=True)


def _get_setting(module, key, default):
    return _settings(module).get(key, default)


def _set_setting(module, key, value):
    data = _settings(module)
    data[key] = value
    _save_settings(module, data)


def _persist_ui_settings(module, panel_api):
    if panel_api is None:
        return
    _set_setting(module, "list_limit", int(panel_api.get_int("list_limit", _get_setting(module, "list_limit", DEFAULT_LIST_LIMIT))))
    _set_setting(module, "search_text", str(panel_api.get_text("search_text", _get_setting(module, "search_text", "")) or ""))


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


def _realtime_enabled(panel_api, module_state):
    if panel_api is not None:
        return bool(panel_api.get_bool("realtime_diff_enabled", False))
    return bool(module_state.get("realtime_enabled", False)) if module_state is not None else False


def _selected(context):
    return list(getattr(context, "selected_objects", []) or [])


def _target_object(context, panel_api):
    obj = panel_api.get_object("target_object") if panel_api is not None else None
    if obj is None:
        selected = _selected(context)
        obj = selected[0] if selected else getattr(context, "object", None)
    if obj is None or getattr(obj, "type", None) != "MESH":
        raise Exception("请选择带形态键的网格物体")
    shape_keys = getattr(getattr(obj.data, "shape_keys", None), "key_blocks", None)
    if shape_keys is None or len(shape_keys) <= 1:
        raise Exception("目标物体需要至少包含 Basis 和一个形态键")
    return obj, shape_keys


def _coords_of_keyblock(keyblock, vert_count):
    coords = array("f", [0.0]) * (vert_count * 3)
    keyblock.data.foreach_get("co", coords)
    return coords


def _iter_non_basis_items(key_blocks):
    for key_index in range(1, len(key_blocks)):
        yield key_index, key_blocks[key_index]


def _delta_stats(reference_coords, target_coords, vert_count):
    moved_count = 0
    max_delta_sq = 0.0
    indices = []
    for vertex_index in range(vert_count):
        offset = vertex_index * 3
        dx = target_coords[offset] - reference_coords[offset]
        dy = target_coords[offset + 1] - reference_coords[offset + 1]
        dz = target_coords[offset + 2] - reference_coords[offset + 2]
        delta_sq = (dx * dx) + (dy * dy) + (dz * dz)
        if delta_sq > EPSILON:
            moved_count += 1
            indices.append(vertex_index)
            if delta_sq > max_delta_sq:
                max_delta_sq = delta_sq
    return moved_count, max_delta_sq ** 0.5, indices


def _reference_key_block(key_blocks, key_index):
    target = key_blocks[key_index]
    reference = getattr(target, "relative_key", None)
    if reference is None:
        return key_blocks[0], "basis_fallback"
    if reference == target:
        return key_blocks[0], "basis_fallback"
    try:
        _vert_count = len(reference.data)
    except Exception:
        return key_blocks[0], "basis_self_fallback"
    return reference, "relative_key"


def _shape_key_delta_stats(key_blocks, key_index):
    reference, reference_mode = _reference_key_block(key_blocks, key_index)
    target = key_blocks[key_index]
    vert_count = len(reference.data)
    reference_coords = _coords_of_keyblock(reference, vert_count)
    target_coords = _coords_of_keyblock(target, vert_count)
    moved_count, max_delta, indices = _delta_stats(reference_coords, target_coords, vert_count)
    return moved_count == 0, moved_count, max_delta, indices, vert_count, reference, reference_mode


def _scan_shape_keys(obj, key_blocks):
    empty_items = []
    nearest_items = []
    max_seen_delta = 0.0
    non_empty_count = 0
    last_vert_count = 0

    for key_index, key_block in _iter_non_basis_items(key_blocks):
        is_empty, moved_count, max_delta, _indices, vert_count, reference, reference_mode = _shape_key_delta_stats(
            key_blocks, key_index
        )
        last_vert_count = vert_count
        max_seen_delta = max(max_seen_delta, float(max_delta))
        if is_empty:
            empty_items.append(
                {
                    "index": key_index,
                    "name": key_block.name,
                    "vertex_count": vert_count,
                    "match_mode": reference_mode,
                    "relative_key_name": getattr(reference, "name", "Basis"),
                    "max_delta": float(max_delta),
                }
            )
        else:
            non_empty_count += 1
            nearest_items.append(
                {
                    "index": key_index,
                    "name": key_block.name,
                    "moved_count": moved_count,
                    "relative_key_name": getattr(reference, "name", "Basis"),
                    "max_delta": float(max_delta),
                }
            )

    nearest_items.sort(key=lambda item: (item["moved_count"], item["max_delta"], item["name"].lower()))
    return {
        "object_name": obj.name,
        "shape_key_total": max(0, len(key_blocks) - 1),
        "empty_count": len(empty_items),
        "non_empty_count": non_empty_count,
        "vertex_count": last_vert_count,
        "empty_items": empty_items,
        "nearest_items": nearest_items[:8],
        "max_seen_delta": float(max_seen_delta),
    }


def _store_scan_result(module_state, result):
    if module_state is None:
        return
    module_state.set("scan_object_name", result["object_name"])
    module_state.set("shape_key_total", result["shape_key_total"])
    module_state.set("empty_count", result["empty_count"])
    module_state.set("non_empty_count", result["non_empty_count"])
    module_state.set("vertex_count", result["vertex_count"])
    module_state.set("empty_items", result["empty_items"])
    module_state.set("nearest_items", result["nearest_items"])
    module_state.set("scan_max_delta", result["max_seen_delta"])


def _refresh_scan(context, module):
    panel_api = _panel_api()
    module_state = _module_state()
    _persist_ui_settings(module, panel_api)
    obj, key_blocks = _target_object(context, panel_api)
    result = _scan_shape_keys(obj, key_blocks)
    _store_scan_result(module_state, result)
    status = f"已扫描 {result['object_name']}：共 {result['shape_key_total']} 个形态键，空形态键 {result['empty_count']}，非空 {result['non_empty_count']}"
    if panel_api is not None:
        panel_api.set_status(status, level="OK")
    if module_state is not None:
        module_state.set("last_result", status)
    return result


def _set_active_shape_key(obj, key_index):
    key_index = int(key_index)
    key_blocks = getattr(getattr(obj.data, "shape_keys", None), "key_blocks", None)
    if key_blocks is None or key_index <= 0 or key_index >= len(key_blocks):
        raise Exception("目标形态键索引无效")
    try:
        bpy.context.view_layer.objects.active = obj
    except Exception:
        pass
    try:
        obj.select_set(True)
    except Exception:
        pass
    obj.active_shape_key_index = key_index
    return key_blocks[key_index]


def _find_empty_item_by_index(module_state, key_index):
    items = list(module_state.get("empty_items", []) or []) if module_state is not None else []
    for item in items:
        try:
            if int(item.get("index", -1)) == int(key_index):
                return item
        except Exception:
            continue
    return None


def _activate_empty_shape_key(context, panel_api, module_state, key_index):
    obj, _key_blocks = _target_object(context, panel_api)
    key_block = _set_active_shape_key(obj, key_index)
    item = _find_empty_item_by_index(module_state, key_index)
    key_name = str(item.get("name", "") or key_block.name) if item else key_block.name
    if panel_api is not None:
        panel_api.set_int("selected_empty_key_index", int(key_index))
        panel_api.set_text("selected_empty_key_name", key_name)
    if module_state is not None:
        module_state.set("selected_empty_key_index", int(key_index))
        module_state.set("selected_empty_key_name", key_name)
        module_state.set("active_summary", f"已选中空形态键：{key_name}（索引 {int(key_index)}）")
        module_state.set("last_result", f"已选中空形态键：{key_name}（索引 {int(key_index)}）")
    if panel_api is not None:
        panel_api.set_status(f"已选中空形态键：{key_name}（索引 {int(key_index)}）", level="OK")
    return key_block


def _current_active_stats(obj):
    key_blocks = getattr(getattr(obj.data, "shape_keys", None), "key_blocks", None)
    if key_blocks is None or len(key_blocks) <= 1:
        raise Exception("目标物体没有可检查的形态键")
    key_index = int(getattr(obj, "active_shape_key_index", 0) or 0)
    if key_index <= 0 or key_index >= len(key_blocks):
        raise Exception("请先选中一个非 Basis 的形态键")
    basis, _reference_mode = _reference_key_block(key_blocks, key_index)
    target = key_blocks[key_index]
    vert_count = len(basis.data)
    basis_coords = _coords_of_keyblock(basis, vert_count)
    target_coords = _coords_of_keyblock(target, vert_count)
    moved_count, max_delta, indices = _delta_stats(basis_coords, target_coords, vert_count)
    return target, moved_count, max_delta, indices


def _select_difference_vertices_in_edit_mode(context, obj, vertex_indices):
    if getattr(context.view_layer.objects, "active", None) is not obj:
        raise Exception("请先把目标物体设为当前活动物体")
    if getattr(obj, "mode", "") != "EDIT":
        raise Exception("请先进入该物体的编辑模式")
    bm = bmesh.from_edit_mesh(obj.data)
    for vert in bm.verts:
        vert.select = False
    bm.select_flush_mode()
    selected = set(vertex_indices)
    for vert in bm.verts:
        vert.select = vert.index in selected
    bm.select_flush_mode()
    bmesh.update_edit_mesh(obj.data, loop_triangles=False, destructive=False)


def _apply_active_difference_selection(context, panel_api, module_state, obj):
    key_block, moved_count, max_delta, indices = _current_active_stats(obj)
    summary = f"{key_block.name}：移动顶点 {moved_count}，最大位移 {max_delta:.6g}"
    if module_state is not None:
        module_state.set("active_summary", summary)
        module_state.set("last_result", summary)
        module_state.set("realtime_last_key_index", int(getattr(obj, "active_shape_key_index", 0) or 0))
    if getattr(obj, "mode", "") == "EDIT":
        _select_difference_vertices_in_edit_mode(context, obj, indices)
        if panel_api is not None:
            panel_api.set_status(f"已在编辑模式选中 {moved_count} 个差异顶点", level="OK")
    else:
        if panel_api is not None:
            panel_api.set_status(f"{summary}；进入编辑模式后再点可直接选中这些顶点", level="WARNING")
    return key_block, moved_count, max_delta, indices


def _filtered_empty_items(module, panel_api, module_state):
    items = list(module_state.get("empty_items", []) or []) if module_state is not None else []
    search_text = ""
    if panel_api is not None:
        search_text = str(panel_api.get_text("search_text", _get_setting(module, "search_text", "")) or "").strip().lower()
    if not search_text:
        return items
    return [item for item in items if search_text in str(item.get("name", "") or "").lower()]


def _copy_empty_names(context, module_state):
    items = list(module_state.get("empty_items", []) or []) if module_state is not None else []
    names = [str(item.get("name", "")).strip() for item in items if str(item.get("name", "")).strip()]
    if not names:
        raise Exception("当前没有可复制的空形态键列表，请先刷新")
    context.window_manager.clipboard = "\n".join(names)
    return len(names)


def _stop_realtime_diff(module_state):
    _REALTIME_STATE["running"] = False
    _REALTIME_STATE["token"] += 1
    if module_state is not None:
        module_state.set("realtime_enabled", False)


def cleanup_runtime(scene=None, workflow=None, module=None, module_state=None):
    _cancel_realtime_timer()
    _stop_realtime_diff(module_state)
    if module_state is not None:
        module_state.set("realtime_object_name", "")
        module_state.set("realtime_last_key_index", -1)
    return True


def _start_realtime_diff(context, panel_api, module_state):
    obj, _key_blocks = _target_object(context, panel_api)
    if getattr(obj, "mode", "") != "EDIT":
        raise Exception("实时检查差异点需要先进入编辑模式")
    _REALTIME_STATE["running"] = True
    _REALTIME_STATE["token"] += 1
    token = int(_REALTIME_STATE["token"])
    object_name = obj.name_full
    if panel_api is not None:
        panel_api.set_bool("realtime_diff_enabled", True)
    if module_state is not None:
        module_state.set("realtime_enabled", True)
        module_state.set("realtime_object_name", object_name)
        module_state.set("realtime_last_key_index", -1)

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
                    for vert in bm.verts:
                        vert.select = False
                    bm.select_flush_mode()
                    bmesh.update_edit_mesh(current_obj.data, loop_triangles=False, destructive=False)
                except Exception:
                    pass
                if module_state is not None:
                    module_state.set("realtime_last_key_index", 0)
                    module_state.set("active_summary", "当前是 Basis，已清空差异点选择")
            return REALTIME_TIMER_INTERVAL
        if key_index == last_index:
            return REALTIME_TIMER_INTERVAL
        try:
            _apply_active_difference_selection(bpy.context, panel_api, module_state, current_obj)
        except Exception:
            return REALTIME_TIMER_INTERVAL
        return REALTIME_TIMER_INTERVAL

    _register_realtime_timer(_tick)
    bpy.app.timers.register(_tick, first_interval=0.02)
    if panel_api is not None:
        panel_api.set_status("已开启实时检查差异点：切换形态键后会自动刷新顶点选择", level="OK")


def run(context, scene, workflow, module):
    _refresh_scan(context, module)
    return {"FINISHED"}


def draw_panel(layout, context, scene, workflow, module, panel_api, module_state):
    box = panel_api.section(layout, "形态键鉴定", icon="SHAPEKEY_DATA")
    panel_api.draw_object_picker(box, "target_object", "目标物体")
    panel_api.draw_active_object_capture(box, "target_object", "吸取当前选中", icon="EYEDROPPER")
    panel_api.draw_int_input(box, "list_limit", "列表显示上限", default=_get_setting(module, "list_limit", DEFAULT_LIST_LIMIT))
    panel_api.draw_text_input(box, "search_text", "空键搜索", default=_get_setting(module, "search_text", ""))

    actions = panel_api.row(box, align=True)
    panel_api.draw_run_button(actions, "刷新列表", icon="FILE_REFRESH")
    panel_api.draw_button(actions, "COPY_EMPTY_NAMES", "复制空键名称", icon="COPYDOWN")

    summary_box = panel_api.section(box, "扫描结果", icon="INFO")
    object_name = module_state.get("scan_object_name", "") if module_state is not None else ""
    shape_key_total = int(module_state.get("shape_key_total", 0) or 0) if module_state is not None else 0
    empty_count = int(module_state.get("empty_count", 0) or 0) if module_state is not None else 0
    non_empty_count = int(module_state.get("non_empty_count", 0) or 0) if module_state is not None else 0
    if object_name:
        panel_api.label(summary_box, f"对象：{object_name}", icon="MESH_DATA")
        panel_api.label(summary_box, f"形态键总数：{shape_key_total}", icon="SHAPEKEY_DATA")
        panel_api.label(summary_box, f"空形态键：{empty_count}  非空：{non_empty_count}", icon="CHECKMARK")
        panel_api.label(summary_box, "空形态键判定：严格按当前键相对 relative_key 是否完全无位移来判断；找不到 relative_key 时才回退到 Basis。", icon="INFO")
    else:
        panel_api.label(summary_box, "请先点击刷新列表", icon="INFO")

    current_box = panel_api.section(box, "当前活动形态键", icon="RESTRICT_SELECT_OFF")
    try:
        obj, key_blocks = _target_object(context, panel_api)
        active_index = int(getattr(obj, "active_shape_key_index", 0) or 0)
        if 0 < active_index < len(key_blocks):
            panel_api.label(current_box, f"当前：{key_blocks[active_index].name}", icon="SHAPEKEY_DATA")
        else:
            panel_api.label(current_box, "当前是 Basis 或未选中形态键", icon="INFO")
    except Exception:
        panel_api.label(current_box, "请选择目标物体", icon="INFO")
    current_actions = panel_api.row(current_box, align=True)
    panel_api.draw_button(current_actions, "SELECT_DIFF_ACTIVE", "显示差异顶点", icon="VERTEXSEL")
    panel_api.draw_toggle(current_actions, "realtime_diff_enabled", "实时检查差异点", default=False)
    realtime_enabled = _realtime_enabled(panel_api, module_state)

    active_summary = module_state.get("active_summary", "") if module_state is not None else ""
    if active_summary:
        panel_api.label(current_box, active_summary, icon="INFO")
    if realtime_enabled:
        panel_api.label(current_box, "实时模式已开启：在编辑模式切换活动形态键时会自动清空并重新选中差异点。", icon="CHECKMARK")

    items = _filtered_empty_items(module, panel_api, module_state)
    all_items = list(module_state.get("empty_items", []) or []) if module_state is not None else []
    list_limit = max(1, int(panel_api.get_int("list_limit", _get_setting(module, "list_limit", DEFAULT_LIST_LIMIT))))
    list_box = panel_api.section(box, f"空形态键列表 ({len(items)}/{len(all_items)})", icon="ALIGN_JUSTIFY")
    if not all_items:
        panel_api.label(list_box, "当前没有扫描结果，或没有空形态键", icon="INFO")
    elif not items:
        panel_api.label(list_box, "没有搜索到匹配的空形态键", icon="INFO")
    else:
        try:
            obj, _key_blocks = _target_object(context, panel_api)
            active_index = int(getattr(obj, "active_shape_key_index", 0) or 0)
        except Exception:
            active_index = -1
        selected_index = int(module_state.get("selected_empty_key_index", -1) or -1) if module_state is not None else -1
        display_items = items[:list_limit]
        panel_api.label(list_box, "点击“选中”会同步切换 Blender 当前活动形态键。", icon="MOUSE_LMB")
        for item in display_items:
            key_index = int(item.get("index", -1))
            key_name = str(item.get("name", "") or "")
            row = panel_api.row(list_box, align=True)
            icon = "RADIOBUT_ON" if key_index in {active_index, selected_index} else "RADIOBUT_OFF"
            row.label(text=f"{key_index}. {key_name} [空键]", icon=icon)
            panel_api.draw_button(
                row,
                f"ACTIVATE_EMPTY::{key_index}",
                "选中",
                icon="RESTRICT_SELECT_OFF",
                tooltip=f"切换当前物体的活动形态键到 {key_name}",
            )
            panel_api.draw_button(
                row,
                f"SELECT_DIFF::{key_index}",
                "差异顶点",
                icon="VERTEXSEL",
                tooltip=f"切换到 {key_name} 并在编辑模式选中差异顶点",
            )
        hidden_count = len(items) - len(display_items)
        if hidden_count > 0:
            panel_api.label(list_box, f"还有 {hidden_count} 个未显示，可提高列表显示上限", icon="INFO")

    nearest_items = list(module_state.get("nearest_items", []) or []) if module_state is not None else []
    if nearest_items:
        diag_box = panel_api.section(box, "接近空键但未命中", icon="INFO")
        for item in nearest_items[:5]:
            panel_api.label(
                diag_box,
                f"{item.get('name', '')}：相对 {item.get('relative_key_name', 'Basis')} 移动 {int(item.get('moved_count', 0))} 点，最大位移 {float(item.get('max_delta', 0.0)):.8f}",
                icon="DOT",
            )

    panel_api.draw_status(box)


def on_panel_action(action, context, scene, workflow, module, panel_api, module_state):
    _persist_ui_settings(module, panel_api)
    if action == "COPY_EMPTY_NAMES":
        copied = _copy_empty_names(context, module_state)
        panel_api.set_status(f"已复制 {copied} 个空形态键名称", level="OK")
        if module_state is not None:
            module_state.set("last_result", f"已复制 {copied} 个空形态键名称")
        return {"FINISHED"}

    if action.startswith("ACTIVATE_EMPTY::"):
        key_index = int(action.split("::", 1)[1] or 0)
        _activate_empty_shape_key(context, panel_api, module_state, key_index)
        return {"FINISHED"}

    if action == "SELECT_DIFF_ACTIVE" or action.startswith("SELECT_DIFF::"):
        obj, _key_blocks = _target_object(context, panel_api)
        if action.startswith("SELECT_DIFF::"):
            key_index = int(action.split("::", 1)[1] or 0)
            _set_active_shape_key(obj, key_index)
        _apply_active_difference_selection(context, panel_api, module_state, obj)
        return {"FINISHED"}

    if action == "FIELD_WRITE::realtime_diff_enabled":
        realtime_enabled = _realtime_enabled(panel_api, module_state)
        if realtime_enabled:
            _start_realtime_diff(context, panel_api, module_state)
        else:
            _stop_realtime_diff(module_state)
            panel_api.set_status("已关闭实时检查差异点", level="OK")
            if module_state is not None:
                module_state.set("last_result", "已关闭实时检查差异点")
        return {"FINISHED"}

    return {"FINISHED"}
