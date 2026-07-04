import json
from array import array

import bpy


VRC_RECIPES = {
    "sil": (0.00, 0.00, 0.00),
    "pp": (0.00, 0.00, 0.18),
    "ff": (0.00, 0.00, 0.35),
    "th": (0.20, 0.00, 0.35),
    "dd": (0.30, 0.00, 0.45),
    "kk": (0.45, 0.00, 0.20),
    "ch": (0.00, 0.00, 1.00),
    "ss": (0.00, 0.00, 0.70),
    "nn": (0.55, 0.00, 0.15),
    "rr": (0.20, 0.45, 0.00),
    "aa": (1.00, 0.00, 0.00),
    "e": (0.60, 0.00, 0.25),
    "ih": (0.35, 0.00, 0.50),
    "oh": (0.00, 1.00, 0.00),
    "ou": (0.10, 0.85, 0.10),
}

MMD_RECIPES = {
    "A": (1.00, 0.00, 0.00),
    "I": (0.10, 0.00, 0.90),
    "U": (0.10, 0.85, 0.05),
    "E": (0.55, 0.00, 0.35),
    "O": (0.00, 1.00, 0.00),
}

MMD_JP_NAMES = {"A": "あ", "I": "い", "U": "う", "E": "え", "O": "お"}

ARKIT_BASE_RECIPES = {
    "AA": [("JawOpen", 0.85), ("MouthFunnel", 0.12)],
    "OH": [("JawOpen", 0.35), ("MouthFunnel", 0.60), ("MouthPucker", 0.20)],
    "CH": [("MouthSmileLeft", 0.36), ("MouthSmileRight", 0.36), ("MouthStretchLeft", 0.28), ("MouthStretchRight", 0.28)],
}


def _normalize_name(name):
    return "".join(ch for ch in str(name or "").casefold() if ch.isalnum())


def _find_keyblock_case_insensitive(key_blocks, name):
    direct = key_blocks.get(str(name or "").strip())
    if direct is not None:
        return direct
    target = _normalize_name(name)
    if not target:
        return None
    for key_block in key_blocks:
        if _normalize_name(getattr(key_block, "name", "")) == target:
            return key_block
    return None


def _coords_of_keyblock(keyblock, vert_count):
    coords = array("f", [0.0]) * (vert_count * 3)
    keyblock.data.foreach_get("co", coords)
    return coords


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
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_settings(module, data):
    module.config_payload = json.dumps(data or {}, ensure_ascii=False, sort_keys=True)


def _get_setting(module, key, default):
    return _settings(module).get(key, default)


def _set_setting(module, key, value):
    data = _settings(module)
    data[key] = value
    _save_settings(module, data)


def _target_object(context, panel_api):
    obj = panel_api.get_object("target_object") if panel_api is not None else None
    if obj is None:
        obj = getattr(context, "object", None)
    if obj is None or getattr(obj, "type", None) != "MESH":
        raise Exception("请选择带形态键的网格物体")
    shape_keys = getattr(getattr(obj.data, "shape_keys", None), "key_blocks", None)
    if shape_keys is None or not shape_keys:
        raise Exception("目标物体没有形态键")
    return obj, shape_keys


def _set_object_mode(context, obj):
    try:
        context.view_layer.objects.active = obj
    except Exception:
        pass
    if getattr(obj, "mode", "OBJECT") != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def _create_or_update_from_sources(obj, key_blocks, target_name, source_weights, overwrite):
    existing = _find_keyblock_case_insensitive(key_blocks, target_name)
    if existing is not None and not overwrite:
        return "EXISTS", []

    basis = key_blocks[0]
    vert_count = len(basis.data)
    basis_coords = _coords_of_keyblock(basis, vert_count)
    source_coords = []
    missing = []
    for source_name, weight in source_weights:
        source_key = _find_keyblock_case_insensitive(key_blocks, source_name)
        if source_key is None:
            missing.append(source_name)
            continue
        source_coords.append((_coords_of_keyblock(source_key, vert_count), float(weight)))
    if missing or not source_coords:
        return "MISSING", missing

    target = existing if existing is not None else obj.shape_key_add(name=target_name, from_mix=False)
    total = vert_count * 3
    mixed = array("f", [0.0]) * total
    for index in range(total):
        base_value = basis_coords[index]
        delta = 0.0
        for coords, weight in source_coords:
            delta += (coords[index] - base_value) * weight
        mixed[index] = base_value + delta
    target.data.foreach_set("co", mixed)
    return ("UPDATED" if existing is not None else "CREATED"), []


def _generate_base_keys(obj, key_blocks, overwrite, names):
    results = []
    for recipe_name, target_name in names:
        status, missing = _create_or_update_from_sources(obj, key_blocks, target_name, ARKIT_BASE_RECIPES[recipe_name], overwrite)
        results.append((recipe_name, target_name, status, missing))
    return results


def _viseme_tasks(preset, vrc_prefix, mmd_use_japanese):
    tasks = []
    if preset in {"VRCHAT", "BOTH"}:
        prefix = vrc_prefix or ""
        for viseme_id, weights in VRC_RECIPES.items():
            tasks.append((f"{prefix}{viseme_id}", weights))
    if preset in {"MMD", "BOTH"}:
        for vowel, weights in MMD_RECIPES.items():
            tasks.append((MMD_JP_NAMES[vowel] if mmd_use_japanese else vowel, weights))
    return tasks


def run(context, scene, workflow, module):
    panel_api = _panel_api()
    module_state = _module_state()
    obj, key_blocks = _target_object(context, panel_api)
    _set_object_mode(context, obj)

    preset = panel_api.get_enum("preset", "VRCHAT") if panel_api is not None else "VRCHAT"
    vrc_prefix = panel_api.get_text("vrc_prefix", "vrc.v_") if panel_api is not None else "vrc.v_"
    mmd_use_japanese = panel_api.get_bool("mmd_use_japanese", True) if panel_api is not None else True
    overwrite = panel_api.get_bool("overwrite_existing", False) if panel_api is not None else False
    strength = float(panel_api.get_float("strength", 1.0) if panel_api is not None else 1.0)
    use_arkit_base = panel_api.get_bool("generate_base_from_arkit", False) if panel_api is not None else False
    aa_name = "AA"
    oh_name = "OH"
    ch_name = "CH"
    if panel_api is not None:
        aa_name = str(panel_api.get_text("base_aa", "AA") or "AA").strip() or "AA"
        oh_name = str(panel_api.get_text("base_oh", "OH") or "OH").strip() or "OH"
        ch_name = str(panel_api.get_text("base_ch", "CH") or "CH").strip() or "CH"

    base_summary = []
    if use_arkit_base:
        base_results = _generate_base_keys(obj, key_blocks, overwrite, (("AA", aa_name), ("OH", oh_name), ("CH", ch_name)))
        base_missing = []
        for recipe_name, target_name, status, missing in base_results:
            base_summary.append(f"{recipe_name}->{target_name}:{status}")
            if status == "MISSING":
                base_missing.append(f"{recipe_name} 缺少 {', '.join(missing)}")
        if base_missing:
            raise Exception("ARKit 合成 AA/OH/CH 失败：" + "；".join(base_missing))
        key_blocks = getattr(getattr(obj.data, "shape_keys", None), "key_blocks", key_blocks)

    kb_aa = _find_keyblock_case_insensitive(key_blocks, aa_name)
    kb_oh = _find_keyblock_case_insensitive(key_blocks, oh_name)
    kb_ch = _find_keyblock_case_insensitive(key_blocks, ch_name)
    missing_base = [name for name, kb in (("AA", kb_aa), ("OH", kb_oh), ("CH", kb_ch)) if kb is None]
    if missing_base:
        raise Exception("缺少基础形态键：" + "、".join(missing_base))

    basis = key_blocks[0]
    vert_count = len(basis.data)
    basis_coords = _coords_of_keyblock(basis, vert_count)
    aa_coords = _coords_of_keyblock(kb_aa, vert_count)
    oh_coords = _coords_of_keyblock(kb_oh, vert_count)
    ch_coords = _coords_of_keyblock(kb_ch, vert_count)
    tasks = _viseme_tasks(preset, vrc_prefix, mmd_use_japanese)

    created = 0
    updated = 0
    skipped = 0
    total = vert_count * 3
    for target_name, (w_aa, w_oh, w_ch) in tasks:
        existing = _find_keyblock_case_insensitive(key_blocks, target_name)
        if existing is not None and not overwrite:
            skipped += 1
            continue
        target = existing if existing is not None else obj.shape_key_add(name=target_name, from_mix=False)
        if existing is None:
            created += 1
            key_blocks = getattr(getattr(obj.data, "shape_keys", None), "key_blocks", key_blocks)
        else:
            updated += 1
        coords = array("f", [0.0]) * total
        for index in range(total):
            base_value = basis_coords[index]
            mixed = (
                w_aa * (aa_coords[index] - base_value)
                + w_oh * (oh_coords[index] - base_value)
                + w_ch * (ch_coords[index] - base_value)
            )
            coords[index] = base_value + (mixed * strength)
        target.data.foreach_set("co", coords)

    obj.data.update()
    status = f"口型生成完成：新建 {created}，更新 {updated}，跳过 {skipped}"
    if base_summary:
        status = f"{status}；ARKit 基础键：{', '.join(base_summary)}"
    if panel_api is not None:
        panel_api.set_status(status, level="OK")
    if module_state is not None:
        module_state.set("last_result", status)
    return {"FINISHED"}


def draw_panel(layout, context, scene, workflow, module, panel_api, module_state):
    box = panel_api.section(layout, "口型生成 / AA-OH-CH", icon="SHAPEKEY_DATA")
    panel_api.draw_object_picker_inline(box, "target_object", "目标物体:", show_active_button=True, factor=0.24)
    panel_api.draw_toggle_inline(box, "generate_base_from_arkit", "先用 ARKit 形态键合成 AA/OH/CH", default=_get_setting(module, "generate_base_from_arkit", False), factor=0.24)
    panel_api.draw_text_input_inline(box, "base_aa", "AA 键名:", default="AA", factor=0.24)
    panel_api.draw_text_input_inline(box, "base_oh", "OH 键名:", default="OH", factor=0.24)
    panel_api.draw_text_input_inline(box, "base_ch", "CH 键名:", default="CH", factor=0.24)
    panel_api.draw_enum(box, "preset", "生成目标", [("VRCHAT", "VRChat"), ("MMD", "MMD"), ("BOTH", "全部")], default=_get_setting(module, "preset", "VRCHAT"))
    if panel_api.get_enum("preset", _get_setting(module, "preset", "VRCHAT")) in {"VRCHAT", "BOTH"}:
        panel_api.draw_text_input_inline(box, "vrc_prefix", "VRChat 前缀:", default=_get_setting(module, "vrc_prefix", "vrc.v_"), factor=0.24)
    if panel_api.get_enum("preset", _get_setting(module, "preset", "VRCHAT")) in {"MMD", "BOTH"}:
        panel_api.draw_toggle_inline(box, "mmd_use_japanese", "MMD 使用日文键名", default=_get_setting(module, "mmd_use_japanese", True), factor=0.24)
    panel_api.draw_float_input_inline(box, "strength", "强度", default=_get_setting(module, "strength", 1.0), factor=0.24)
    panel_api.draw_toggle_inline(box, "overwrite_existing", "覆盖已有目标形态键", default=_get_setting(module, "overwrite_existing", False), factor=0.24)

    note = panel_api.foldout_section(box, "show_viseme_note", "说明", icon="INFO", default_open=False)
    if note is not None:
        if panel_api.get_bool("generate_base_from_arkit", _get_setting(module, "generate_base_from_arkit", False)):
            panel_api.label(note, "启用后会先按 ARKit 源键生成/更新 AA、OH、CH，再继续生成口型。", icon="CHECKMARK")
        else:
            panel_api.label(note, "关闭时直接使用现有 AA、OH、CH 作为基础键。", icon="INFO")

    status = module_state.get("last_result", "") if module_state is not None else ""
    if status:
        panel_api.label(box, status, icon="INFO")
    panel_api.draw_run_button(box, "生成口型", icon="PLAY")
    panel_api.draw_status(box)


def on_panel_action(action, context, scene, workflow, module, panel_api, module_state):
    tracked = (
        "generate_base_from_arkit",
        "base_aa",
        "base_oh",
        "base_ch",
        "preset",
        "vrc_prefix",
        "mmd_use_japanese",
        "strength",
        "overwrite_existing",
    )
    for key in tracked:
        getter = None
        if key in {"generate_base_from_arkit", "mmd_use_japanese", "overwrite_existing"}:
            getter = lambda name=key: panel_api.get_bool(name, _get_setting(module, name, False))
        elif key == "strength":
            getter = lambda name=key: panel_api.get_float(name, _get_setting(module, name, 1.0))
        elif key == "preset":
            getter = lambda name=key: panel_api.get_enum(name, _get_setting(module, name, "VRCHAT"))
        else:
            getter = lambda name=key: panel_api.get_text(name, _get_setting(module, name, ""))
        _set_setting(module, key, getter())
    return {"FINISHED"}
