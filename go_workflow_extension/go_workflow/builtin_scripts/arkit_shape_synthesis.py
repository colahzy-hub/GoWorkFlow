import csv
import io
import json
import os
import re
import textwrap
import unicodedata
from array import array

import bpy


DEFAULT_TARGET_SET = "VRM_BASIC"
DEFAULT_PROFILE = "CONSERVATIVE"
CUSTOM_TARGET_SET = "CUSTOM_TARGET"
CUSTOM_TARGET_PROFILE = "__CUSTOM_TARGET__"


PROFILE_SETTINGS = {
    "CONSERVATIVE": {"label": "常规"},
    "AGGRESSIVE": {"label": "激进"},
    "CUSTOM": {"label": "自定义"},
}


TARGET_SET_LABELS = {
    "VRM_BASIC": "VRM 基础形态键",
    "MMD_POSSIBLE": "MMD 常用形态键",
    "CUSTOM_TARGET": "自定义目标预设",
}


VRM_BASIC_RECIPES_CONSERVATIVE = {
    "A": [("JawOpen", 0.82), ("MouthFunnel", 0.10)],
    "I": [("MouthSmileLeft", 0.36), ("MouthSmileRight", 0.36), ("MouthStretchLeft", 0.28), ("MouthStretchRight", 0.28)],
    "U": [("MouthPucker", 0.72), ("MouthFunnel", 0.32)],
    "E": [("JawOpen", 0.36), ("MouthSmileLeft", 0.24), ("MouthSmileRight", 0.24), ("MouthStretchLeft", 0.26), ("MouthStretchRight", 0.26)],
    "O": [("JawOpen", 0.38), ("MouthFunnel", 0.64), ("MouthPucker", 0.18)],
    "Blink": [("EyeBlinkLeft", 1.0), ("EyeBlinkRight", 1.0)],
    "Blink_L": [("EyeBlinkLeft", 1.0)],
    "Blink_R": [("EyeBlinkRight", 1.0)],
    "Joy": [("MouthSmileLeft", 0.34), ("MouthSmileRight", 0.34), ("CheekSquintLeft", 0.20), ("CheekSquintRight", 0.20), ("EyeSquintLeft", 0.18), ("EyeSquintRight", 0.18)],
    "Angry": [("BrowDownLeft", 0.30), ("BrowDownRight", 0.30), ("NoseSneerLeft", 0.20), ("NoseSneerRight", 0.20), ("MouthFrownLeft", 0.16), ("MouthFrownRight", 0.16)],
    "Sorrow": [("BrowInnerUp", 0.38), ("MouthFrownLeft", 0.28), ("MouthFrownRight", 0.28)],
    "Fun": [("MouthSmileLeft", 0.38), ("MouthSmileRight", 0.38), ("CheekSquintLeft", 0.18), ("CheekSquintRight", 0.18)],
    "Surprised": [("BrowInnerUp", 0.30), ("BrowOuterUpLeft", 0.24), ("BrowOuterUpRight", 0.24), ("EyeWideLeft", 0.28), ("EyeWideRight", 0.28), ("JawOpen", 0.36)],
}


VRM_BASIC_RECIPES_AGGRESSIVE = {
    "A": [("JawOpen", 0.95), ("MouthFunnel", 0.16)],
    "I": [("MouthSmileLeft", 0.42), ("MouthSmileRight", 0.42), ("MouthStretchLeft", 0.30), ("MouthStretchRight", 0.30)],
    "U": [("MouthPucker", 0.84), ("MouthFunnel", 0.42)],
    "E": [("JawOpen", 0.44), ("MouthSmileLeft", 0.30), ("MouthSmileRight", 0.30), ("MouthStretchLeft", 0.28), ("MouthStretchRight", 0.28)],
    "O": [("JawOpen", 0.46), ("MouthFunnel", 0.74), ("MouthPucker", 0.24)],
    "Blink": [("EyeBlinkLeft", 1.0), ("EyeBlinkRight", 1.0)],
    "Blink_L": [("EyeBlinkLeft", 1.0)],
    "Blink_R": [("EyeBlinkRight", 1.0)],
    "Joy": [("MouthSmileLeft", 0.44), ("MouthSmileRight", 0.44), ("CheekSquintLeft", 0.26), ("CheekSquintRight", 0.26), ("EyeSquintLeft", 0.24), ("EyeSquintRight", 0.24)],
    "Angry": [("BrowDownLeft", 0.38), ("BrowDownRight", 0.38), ("NoseSneerLeft", 0.28), ("NoseSneerRight", 0.28), ("MouthFrownLeft", 0.20), ("MouthFrownRight", 0.20)],
    "Sorrow": [("BrowInnerUp", 0.46), ("MouthFrownLeft", 0.36), ("MouthFrownRight", 0.36)],
    "Fun": [("MouthSmileLeft", 0.46), ("MouthSmileRight", 0.46), ("CheekSquintLeft", 0.24), ("CheekSquintRight", 0.24)],
    "Surprised": [("BrowInnerUp", 0.38), ("BrowOuterUpLeft", 0.30), ("BrowOuterUpRight", 0.30), ("EyeWideLeft", 0.36), ("EyeWideRight", 0.36), ("JawOpen", 0.48)],
}


MMD_POSSIBLE_RECIPES_CONSERVATIVE = {
    "あ": [("JawOpen", 0.82), ("MouthFunnel", 0.10)],
    "い": [("MouthSmileLeft", 0.34), ("MouthSmileRight", 0.34), ("MouthStretchLeft", 0.26), ("MouthStretchRight", 0.26)],
    "う": [("MouthPucker", 0.70), ("MouthFunnel", 0.28)],
    "え": [("JawOpen", 0.32), ("MouthSmileLeft", 0.24), ("MouthSmileRight", 0.24), ("MouthStretchLeft", 0.22), ("MouthStretchRight", 0.22)],
    "お": [("JawOpen", 0.34), ("MouthFunnel", 0.56), ("MouthPucker", 0.18)],
    "まばたき": [("EyeBlinkLeft", 1.0), ("EyeBlinkRight", 1.0)],
    "あせり": [("BrowInnerUp", 0.18), ("EyeWideLeft", 0.14), ("EyeWideRight", 0.14), ("MouthStretchLeft", 0.12), ("MouthStretchRight", 0.12)],
    "ウィンク": [("EyeBlinkLeft", 1.0)],
    "ウィンク右": [("EyeBlinkRight", 1.0)],
    "ウィンク２": [("EyeBlinkLeft", 1.0), ("EyeSquintLeft", 0.32), ("CheekSquintLeft", 0.16)],
    "ウィンク２右": [("EyeBlinkRight", 1.0), ("EyeSquintRight", 0.32), ("CheekSquintRight", 0.16)],
    "笑い": [("EyeSquintLeft", 0.30), ("EyeSquintRight", 0.30), ("CheekSquintLeft", 0.18), ("CheekSquintRight", 0.18)],
    "なごみ": [("EyeSquintLeft", 0.16), ("EyeSquintRight", 0.16), ("BrowInnerUp", 0.08), ("MouthSmileLeft", 0.10), ("MouthSmileRight", 0.10)],
    "はぅ": [("BrowInnerUp", 0.38), ("EyeBlinkLeft", 0.18), ("EyeBlinkRight", 0.18), ("MouthFrownLeft", 0.10), ("MouthFrownRight", 0.10)],
    "じと目": [("EyeSquintLeft", 0.24), ("EyeSquintRight", 0.24), ("BrowDownLeft", 0.14), ("BrowDownRight", 0.14)],
    "キリッ": [("BrowDownLeft", 0.30), ("BrowDownRight", 0.30), ("EyeWideLeft", 0.12), ("EyeWideRight", 0.12)],
    "びっくり": [("EyeWideLeft", 0.34), ("EyeWideRight", 0.34), ("BrowInnerUp", 0.24), ("BrowOuterUpLeft", 0.20), ("BrowOuterUpRight", 0.20)],
    "上": [("BrowInnerUp", 0.32), ("BrowOuterUpLeft", 0.28), ("BrowOuterUpRight", 0.28)],
    "怒り": [("BrowDownLeft", 0.44), ("BrowDownRight", 0.44), ("NoseSneerLeft", 0.22), ("NoseSneerRight", 0.22)],
    "にっこり": [("MouthSmileLeft", 0.42), ("MouthSmileRight", 0.42), ("CheekSquintLeft", 0.18), ("CheekSquintRight", 0.18)],
    "にやり": [("MouthSmileLeft", 0.30), ("MouthSmileRight", 0.30), ("MouthDimpleLeft", 0.18), ("MouthDimpleRight", 0.18)],
    "への字": [("MouthFrownLeft", 0.50), ("MouthFrownRight", 0.50), ("JawOpen", 0.08)],
    "困る": [("BrowInnerUp", 0.32), ("MouthStretchLeft", 0.20), ("MouthStretchRight", 0.20), ("JawOpen", 0.12)],
    "泣き": [("BrowInnerUp", 0.42), ("MouthFrownLeft", 0.32), ("MouthFrownRight", 0.32), ("EyeBlinkLeft", 0.08), ("EyeBlinkRight", 0.08)],
    "てへぺろ": [("TongueOut", 0.90), ("JawOpen", 0.18), ("MouthSmileLeft", 0.28), ("MouthSmileRight", 0.28)],
    "口角上げ": [("MouthSmileLeft", 0.50), ("MouthSmileRight", 0.50)],
    "口角下げ": [("MouthFrownLeft", 0.50), ("MouthFrownRight", 0.50)],
    "口横広げ": [("MouthStretchLeft", 0.50), ("MouthStretchRight", 0.50), ("MouthDimpleLeft", 0.20), ("MouthDimpleRight", 0.20)],
    "にこり": [("BrowInnerUp", 0.10), ("MouthSmileLeft", 0.24), ("MouthSmileRight", 0.24)],
    "下": [("BrowDownLeft", 0.28), ("BrowDownRight", 0.28)],
    "舌": [("TongueOut", 0.90), ("JawOpen", 0.12)],
    "ぺろっ": [("TongueOut", 0.90), ("JawOpen", 0.14)],
}


MMD_POSSIBLE_RECIPES_AGGRESSIVE = {
    "あ": [("JawOpen", 0.95), ("MouthFunnel", 0.16)],
    "い": [("MouthSmileLeft", 0.40), ("MouthSmileRight", 0.40), ("MouthStretchLeft", 0.30), ("MouthStretchRight", 0.30)],
    "う": [("MouthPucker", 0.82), ("MouthFunnel", 0.38)],
    "え": [("JawOpen", 0.40), ("MouthSmileLeft", 0.30), ("MouthSmileRight", 0.30), ("MouthStretchLeft", 0.28), ("MouthStretchRight", 0.28)],
    "お": [("JawOpen", 0.46), ("MouthFunnel", 0.72), ("MouthPucker", 0.24)],
    "まばたき": [("EyeBlinkLeft", 1.0), ("EyeBlinkRight", 1.0)],
    "あせり": [("BrowInnerUp", 0.24), ("EyeWideLeft", 0.20), ("EyeWideRight", 0.20), ("MouthStretchLeft", 0.16), ("MouthStretchRight", 0.16)],
    "ウィンク": [("EyeBlinkLeft", 1.0)],
    "ウィンク右": [("EyeBlinkRight", 1.0)],
    "ウィンク２": [("EyeBlinkLeft", 1.0), ("EyeSquintLeft", 0.44), ("CheekSquintLeft", 0.22)],
    "ウィンク２右": [("EyeBlinkRight", 1.0), ("EyeSquintRight", 0.44), ("CheekSquintRight", 0.22)],
    "笑い": [("EyeSquintLeft", 0.40), ("EyeSquintRight", 0.40), ("CheekSquintLeft", 0.24), ("CheekSquintRight", 0.24)],
    "なごみ": [("EyeSquintLeft", 0.22), ("EyeSquintRight", 0.22), ("BrowInnerUp", 0.12), ("MouthSmileLeft", 0.14), ("MouthSmileRight", 0.14)],
    "はぅ": [("BrowInnerUp", 0.48), ("EyeBlinkLeft", 0.22), ("EyeBlinkRight", 0.22), ("MouthFrownLeft", 0.16), ("MouthFrownRight", 0.16)],
    "じと目": [("EyeSquintLeft", 0.32), ("EyeSquintRight", 0.32), ("BrowDownLeft", 0.18), ("BrowDownRight", 0.18)],
    "キリッ": [("BrowDownLeft", 0.40), ("BrowDownRight", 0.40), ("EyeWideLeft", 0.16), ("EyeWideRight", 0.16)],
    "びっくり": [("EyeWideLeft", 0.42), ("EyeWideRight", 0.42), ("BrowInnerUp", 0.30), ("BrowOuterUpLeft", 0.24), ("BrowOuterUpRight", 0.24)],
    "上": [("BrowInnerUp", 0.40), ("BrowOuterUpLeft", 0.34), ("BrowOuterUpRight", 0.34)],
    "怒り": [("BrowDownLeft", 0.52), ("BrowDownRight", 0.52), ("NoseSneerLeft", 0.30), ("NoseSneerRight", 0.30)],
    "にっこり": [("MouthSmileLeft", 0.50), ("MouthSmileRight", 0.50), ("CheekSquintLeft", 0.24), ("CheekSquintRight", 0.24)],
    "にやり": [("MouthSmileLeft", 0.36), ("MouthSmileRight", 0.36), ("MouthDimpleLeft", 0.24), ("MouthDimpleRight", 0.24)],
    "への字": [("MouthFrownLeft", 0.58), ("MouthFrownRight", 0.58), ("JawOpen", 0.10)],
    "困る": [("BrowInnerUp", 0.40), ("MouthStretchLeft", 0.26), ("MouthStretchRight", 0.26), ("JawOpen", 0.16)],
    "泣き": [("BrowInnerUp", 0.50), ("MouthFrownLeft", 0.40), ("MouthFrownRight", 0.40), ("EyeBlinkLeft", 0.12), ("EyeBlinkRight", 0.12)],
    "てへぺろ": [("TongueOut", 0.90), ("JawOpen", 0.24), ("MouthSmileLeft", 0.36), ("MouthSmileRight", 0.36)],
    "口角上げ": [("MouthSmileLeft", 0.60), ("MouthSmileRight", 0.60)],
    "口角下げ": [("MouthFrownLeft", 0.60), ("MouthFrownRight", 0.60)],
    "口横広げ": [("MouthStretchLeft", 0.60), ("MouthStretchRight", 0.60), ("MouthDimpleLeft", 0.24), ("MouthDimpleRight", 0.24)],
    "にこり": [("BrowInnerUp", 0.14), ("MouthSmileLeft", 0.32), ("MouthSmileRight", 0.32)],
    "下": [("BrowDownLeft", 0.36), ("BrowDownRight", 0.36)],
    "舌": [("TongueOut", 0.94), ("JawOpen", 0.16)],
    "ぺろっ": [("TongueOut", 0.94), ("JawOpen", 0.20)],
}
TABLE_RULES = """混合系数表规则
1. 推荐三列 csv: target,source,weight
2. 每一行代表一个源形态键贡献；同一个 target 可以写多行。
3. 以 # 或 // 开头的行会被忽略。
4. 支持 csv、tab、分号、竖线；也支持纯文本 target source weight。
5. 也支持单行多源写法: target sourceA:0.5 sourceB:0.25
6. 名称匹配忽略大小写、空格、下划线、括号和常见中英文标点。
7. 指定合成键白名单支持写多个目标键名，键名之间用空格分隔；留空表示全部目标键。
8. 最终权重 = 表格 weight × 预设混合率 × 额外强度倍率。
9. 导入文件后会保存到当前工作流模块里，随 .goworkflow 一起导出。"""


MMD_TARGET_ALIASES = {
    "A": "あ",
    "I": "い",
    "U": "う",
    "E": "え",
    "O": "お",
    "Blink": "まばたき",
    "Blink_L": "ウィンク",
    "Blink_R": "ウィンク右",
    "Wink": "ウィンク",
    "Wink_R": "ウィンク右",
    "Wink2": "ウィンク２",
    "Wink2_R": "ウィンク２右",
    "Smile Eyes": "笑い",
    "SmileEyes": "笑い",
    "Calm": "なごみ",
    "CalmEyes": "なごみ",
    "SoftEyes": "なごみ",
    "Embarrassed": "はぅ",
    "Sullen": "じと目",
    "Unamused": "じと目",
    "Sharp": "キリッ",
    "Serious": "キリッ",
    "Troubled": "困る",
    "Joy": "にっこり",
    "Angry": "怒り",
    "Sorrow": "泣き",
    "Fun": "にやり",
    "Surprised": "びっくり",
    "Sweat": "あせり",
    "Nervous": "あせり",
    "Smile": "にっこり",
    "Grin": "にやり",
    "Smirk": "にやり",
    "Frown": "への字",
    "Tongue": "舌",
    "Tongue Out": "ぺろっ",
    "TongueOut": "舌",
}


def _normalize_name(name):
    value = unicodedata.normalize("NFKC", str(name or "")).strip()
    value = MMD_TARGET_ALIASES.get(value, value)
    value = value.casefold()
    return re.sub(r"[\s_\-(){}\[\]<>/\\,，。！？：；\"'`~]+", "", value)


def _shape_key_map(key_blocks):
    return {_normalize_name(getattr(key, "name", "")): key for key in key_blocks}


def _find_key(key_blocks, key_map, name):
    if name in key_blocks:
        return key_blocks[name]
    return key_map.get(_normalize_name(name))


def _shape_key_filter_tokens(text):
    tokens = []
    for raw in re.split(r"\s+", str(text or "").strip()):
        token = _normalize_name(raw)
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def _basis_shape_key_active(obj):
    try:
        return int(getattr(obj, "active_shape_key_index", 0) or 0) <= 0
    except Exception:
        return True


def _coords_of_keyblock(keyblock, count):
    coords = array("f", [0.0]) * (count * 3)
    keyblock.data.foreach_get("co", coords)
    return coords


def _ensure_object(context, panel_api):
    obj = panel_api.get_object("target_object") if panel_api is not None else None
    if obj is None:
        obj = getattr(context, "object", None)
    if obj is None or getattr(obj, "type", None) != "MESH":
        raise Exception("请先选择带形态键的网格物体")
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


def _default_recipes_for(target_set, profile):
    if target_set == CUSTOM_TARGET_SET:
        raise Exception("自定义目标预设需要先导入 csv/txt 混合系数表")
    if profile == "CUSTOM":
        raise Exception("自定义模式需要先导入当前目标预设对应的 csv/txt 混合系数表")
    if target_set == "MMD_POSSIBLE":
        return MMD_POSSIBLE_RECIPES_AGGRESSIVE if profile == "AGGRESSIVE" else MMD_POSSIBLE_RECIPES_CONSERVATIVE
    return VRM_BASIC_RECIPES_AGGRESSIVE if profile == "AGGRESSIVE" else VRM_BASIC_RECIPES_CONSERVATIVE


def _default_table_for(target_set, profile):
    rows = ["target,source,weight"]
    for target, recipe in _default_recipes_for(target_set, profile).items():
        for source, weight in recipe:
            rows.append(f"{target},{source},{float(weight):g}")
    return "\n".join(rows)


def _recipes_to_table(recipes):
    rows = ["target,source,weight"]
    for target, recipe in recipes.items():
        for source, weight in recipe:
            rows.append(f"{target},{source},{float(weight):g}")
    return "\n".join(rows)


def _parse_weight(value, line_number):
    try:
        return float(str(value).strip())
    except Exception:
        raise Exception(f"第 {line_number} 行的 weight 不是有效数字: {value}")


def _split_table_line(line):
    for delimiter in (",", "	", ";", "|"):
        if delimiter in line:
            reader = csv.reader(io.StringIO(line), delimiter=delimiter)
            return [part.strip() for part in next(reader)]
    return line.split()


def _parse_recipe_table(text):
    recipes = {}
    for line_number, raw_line in enumerate(str(text or "").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        parts = [part.strip() for part in _split_table_line(line) if str(part).strip()]
        if not parts:
            continue
        if len(parts) >= 3 and _normalize_name(parts[0]) in {"target", "shape", "shapekey", "targetshape"}:
            continue
        if len(parts) >= 3 and ":" not in parts[1]:
            target = parts[0]
            source = parts[1]
            weight = _parse_weight(parts[2], line_number)
            recipes.setdefault(target, []).append((source, weight))
            continue
        if len(parts) >= 2:
            target = parts[0]
            parsed_any = False
            for token in parts[1:]:
                if ":" not in token:
                    continue
                source, weight_text = token.rsplit(":", 1)
                source = source.strip()
                if not source:
                    continue
                recipes.setdefault(target, []).append((source, _parse_weight(weight_text, line_number)))
                parsed_any = True
            if parsed_any:
                continue
        raise Exception(f"第 {line_number} 行无法解析: {raw_line}")
    if not recipes:
        raise Exception("表格为空，或没有找到有效的 target/source/weight 行")
    return recipes


def _read_text_file(path):
    filepath = bpy.path.abspath(str(path or "").strip())
    if not filepath:
        raise Exception("请先选择 csv/txt 文件")
    if not os.path.isfile(filepath):
        raise Exception(f"找不到导入文件: {filepath}")
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            with open(filepath, "r", encoding=encoding) as handle:
                return handle.read(), filepath
        except UnicodeDecodeError:
            continue
    with open(filepath, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read(), filepath


def _write_text_file(path, text):
    filepath = bpy.path.abspath(str(path or "").strip())
    if not filepath:
        raise Exception("导出路径为空")
    folder = os.path.dirname(filepath)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(filepath, "w", encoding="utf-8", newline="") as handle:
        handle.write(str(text or ""))
    return filepath

def _module_config(module):
    raw = getattr(module, "config_payload", "") or ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_module_config(module, config):
    payload = json.dumps(config or {}, ensure_ascii=False, sort_keys=True)
    module.config_payload = payload if payload != "{}" else ""
    return config


def _table_storage_key(target_set, profile):
    return f"{target_set}::{profile}"


def _effective_table_profile(target_set, profile):
    if str(target_set or "") == CUSTOM_TARGET_SET:
        return CUSTOM_TARGET_PROFILE
    return str(profile or DEFAULT_PROFILE)


def _combo_label(target_set, profile):
    target_label = TARGET_SET_LABELS.get(target_set, target_set)
    if str(target_set or "") == CUSTOM_TARGET_SET:
        return f"{target_label} / 额外导入表"
    profile_label = PROFILE_SETTINGS.get(profile, PROFILE_SETTINGS["CONSERVATIVE"])["label"]
    return f"{target_label} / {profile_label}"


def _suggest_table_filename(target_set, profile, extension):
    target_name = str(target_set or "").strip().lower() or "target"
    profile_name = str(_effective_table_profile(target_set, profile) or "").strip().lower() or "profile"
    ext = extension if str(extension or "").startswith(".") else f".{extension}"
    return f"arkit_shape_table_{target_name}_{profile_name}{ext}"


def _resolve_table_filepath(raw_path, target_set, profile, extension):
    extension = ".csv" if str(extension or "").lower() == ".csv" else ".txt"
    default_name = _suggest_table_filename(target_set, profile, extension)
    path = bpy.path.abspath(str(raw_path or "").strip())
    if not path:
        path = bpy.path.abspath("//" + default_name)
    suffix = os.path.splitext(os.path.basename(path))[1].strip()
    if os.path.isdir(path) or not suffix:
        path = os.path.join(path, default_name)
    root, ext = os.path.splitext(path)
    if ext.lower() != extension:
        path = root + extension
    return path


def _custom_table(module, target_set, profile):
    config = _module_config(module)
    tables = config.get("arkit_shape_tables", {})
    if not isinstance(tables, dict):
        return ""
    exact_key = _table_storage_key(target_set, _effective_table_profile(target_set, profile))
    return str(tables.get(exact_key, "") or "").strip()


def _set_custom_table(module, target_set, profile, text):
    config = _module_config(module)
    tables = config.get("arkit_shape_tables", {})
    if not isinstance(tables, dict):
        tables = {}
    tables[_table_storage_key(target_set, _effective_table_profile(target_set, profile))] = str(text or "").strip()
    config["arkit_shape_tables"] = tables
    _save_module_config(module, config)


def _clear_custom_table(module, target_set, profile):
    config = _module_config(module)
    tables = config.get("arkit_shape_tables", {})
    if not isinstance(tables, dict):
        tables = {}
    removed = tables.pop(_table_storage_key(target_set, _effective_table_profile(target_set, profile)), None)
    if removed is None:
        tables.pop(target_set, None)
    if tables:
        config["arkit_shape_tables"] = tables
    else:
        config.pop("arkit_shape_tables", None)
    _save_module_config(module, config)


def _recipe_source_text(panel_api, module, target_set, profile):
    if target_set == CUSTOM_TARGET_SET:
        table = _custom_table(module, target_set, profile)
        if not table:
            raise Exception("自定义目标预设还没有导入混合系数表，请先导入符合 target,source,weight 规则的 csv/txt")
        return table, "当前自定义目标预设表"
    if profile == "CUSTOM":
        table = _custom_table(module, target_set, profile)
        if not table:
            raise Exception(f"{TARGET_SET_LABELS.get(target_set, target_set)} / 自定义 还没有导入混合系数表，请先导入符合 target,source,weight 规则的 csv/txt")
        return table, "当前目标预设的自定义表"
    return _default_table_for(target_set, profile), "当前选项内置默认表"


def _recipes_for(panel_api, module, target_set, profile):
    text, source_label = _recipe_source_text(panel_api, module, target_set, profile)
    recipes = _parse_recipe_table(text)
    return recipes, source_label, text


def _recipe_target_names(recipes):
    return sorted([str(name or "").strip() for name in dict(recipes or {}).keys() if str(name or "").strip()], key=str.casefold)


def _target_names_text(target_names):
    return " ".join(f"[{name}]" for name in list(target_names or []))


def _log_table_targets(panel_api, prefix, recipes, previous_target_names=None):
    target_names = _recipe_target_names(recipes)
    message = f"{prefix}: {len(target_names)} targets {_target_names_text(target_names)}".strip()
    if panel_api is not None:
        panel_api.log(message)
    else:
        print(message)
    previous = {str(name or "").strip() for name in list(previous_target_names or []) if str(name or "").strip()}
    added = [name for name in target_names if name not in previous]
    if added:
        added_message = f"{prefix}, added targets: {_target_names_text(added)}"
        if panel_api is not None:
            panel_api.log(added_message)
        else:
            print(added_message)
    return target_names


def _builtin_reference_target_names(target_set):
    if str(target_set or "") == CUSTOM_TARGET_SET:
        return []
    target_names = set()
    for profile_name in ("CONSERVATIVE", "AGGRESSIVE"):
        try:
            target_names.update(dict(_default_recipes_for(target_set, profile_name)).keys())
        except Exception:
            continue
    return sorted([str(name or "").strip() for name in target_names if str(name or "").strip()], key=str.casefold)


def _log_extra_custom_targets(panel_api, prefix, target_set, recipes):
    builtin_names = {str(name or "").strip() for name in _builtin_reference_target_names(target_set)}
    if not builtin_names:
        return []
    extra_names = [name for name in _recipe_target_names(recipes) if name not in builtin_names]
    if not extra_names:
        return []
    message = f"{prefix}, extra targets: {_target_names_text(extra_names)}"
    if panel_api is not None:
        panel_api.log(message)
    else:
        print(message)
    return extra_names


def _filtered_recipes(recipes, target_filter_text):
    tokens = _shape_key_filter_tokens(target_filter_text)
    if not tokens:
        return recipes, tokens
    selected = set(tokens)
    filtered = {}
    for target_name, recipe in recipes.items():
        if _normalize_name(target_name) in selected:
            filtered[target_name] = recipe
    return filtered, tokens


def _target_set(panel_api):
    if panel_api is None:
        return DEFAULT_TARGET_SET
    return panel_api.get_enum("target_set", DEFAULT_TARGET_SET)


def _profile(panel_api):
    if panel_api is None:
        return DEFAULT_PROFILE
    return panel_api.get_enum("mix_profile", DEFAULT_PROFILE)


def _overwrite(panel_api):
    return panel_api.get_bool("overwrite_existing", False) if panel_api is not None else False


def _actual_strength(panel_api):
    extra = panel_api.get_float("strength_scale", 1.0) if panel_api is not None else 1.0
    return max(0.0, float(extra))


def _draw_text_block(layout, text, icon="INFO", width=54, max_lines=None):
    value = str(text or "").strip()
    if not value:
        return
    first = True
    lines_drawn = 0
    for raw_line in value.splitlines():
        wrapped = textwrap.wrap(raw_line.strip(), width=max(8, int(width)), break_long_words=True, break_on_hyphens=False) or [raw_line]
        for line in wrapped:
            layout.label(text=line, icon=icon if first else "NONE")
            first = False
            lines_drawn += 1


def _draw_export_operator_button(layout, label, icon, extension, target_set, profile, table_text):
    op = layout.operator("bworkflow.module_export_text_file", text=label, icon=icon)
    op.target_text_key = "recipe_file_path"
    op.default_filename = _suggest_table_filename(target_set, profile, extension)
    op.module_action = ""
    op.status_prefix = "已导出混合系数表"
    op.filename_ext = extension
    op.filter_glob = "*.csv" if extension == ".csv" else "*.txt"
    op.text_payload = str(table_text or "")
    return op


def _create_mixed_key(obj, key_blocks, target_name, recipe, strength, overwrite):
    key_map = _shape_key_map(key_blocks)
    basis = key_blocks[0]
    count = len(basis.data)
    basis_coords = _coords_of_keyblock(basis, count)
    sources = []
    missing = []
    for source_name, weight in recipe:
        key = _find_key(key_blocks, key_map, source_name)
        if key is None:
            missing.append(source_name)
            continue
        sources.append((key, float(weight)))
    if missing or not sources:
        return "SKIPPED", missing

    existing = key_blocks.get(target_name)
    if existing is not None and not overwrite:
        return "EXISTS", []

    target = existing if existing is not None else obj.shape_key_add(name=target_name, from_mix=False)
    source_coords = [(_coords_of_keyblock(key, count), weight) for key, weight in sources]
    total = count * 3
    mixed = array("f", [0.0]) * total
    for index in range(total):
        value = basis_coords[index]
        delta = 0.0
        for coords, weight in source_coords:
            delta += (coords[index] - basis_coords[index]) * weight
        mixed[index] = value + delta * strength
    target.data.foreach_set("co", mixed)
    return "UPDATED" if existing is not None else "CREATED", []


def _table_summary_text(module, target_set, profile):
    combo_text = _combo_label(target_set, profile)
    custom_text = _custom_table(module, target_set, profile)
    if not custom_text:
        if target_set == CUSTOM_TARGET_SET:
            return f"{combo_text}：还没有导入表，请先导入符合 target,source,weight 规则的 csv/txt", "ERROR"
        if profile == "CUSTOM":
            return f"{combo_text}：还没有导入自定义混合表，请先导入符合 target,source,weight 规则的 csv/txt", "ERROR"
        return f"{combo_text}：当前没有自定义表，正在使用该选项的内置默认表", "INFO"
    try:
        recipes = _parse_recipe_table(custom_text)
        row_count = sum(len(recipe) for recipe in recipes.values())
        return f"{combo_text}：已保存自定义表，{len(recipes)} 个目标键，{row_count} 条混合项", "CHECKMARK"
    except Exception:
        return f"{combo_text}：已保存的自定义表格式有问题，请重新导入", "ERROR"


def run(context, scene, workflow, module):
    panel_api = globals().get("panel_api")
    module_state = globals().get("module_state")
    obj, key_blocks = _ensure_object(context, panel_api)
    _set_object_mode(context, obj)
    target_set = _target_set(panel_api)
    profile = _profile(panel_api)
    all_recipes, source_label, _source_text = _recipes_for(panel_api, module, target_set, profile)
    target_filter_text = panel_api.get_text("target_filter_text", "") if panel_api is not None else ""
    recipes, _tokens = _filtered_recipes(all_recipes, target_filter_text)
    strength = _actual_strength(panel_api)
    overwrite_requested = _overwrite(panel_api)
    overwrite = overwrite_requested

    created = updated = exists = skipped = 0
    filtered = max(0, len(all_recipes) - len(recipes))
    missing_lines = []
    warning_lines = []
    for target_name, recipe in recipes.items():
        result, missing = _create_mixed_key(obj, key_blocks, target_name, recipe, strength, overwrite)
        if result == "CREATED":
            created += 1
        elif result == "UPDATED":
            updated += 1
        elif result == "EXISTS":
            exists += 1
        else:
            skipped += 1
            missing_lines.append(f"{target_name}: 缺少 {', '.join(missing)}")

    obj.data.update()
    label = TARGET_SET_LABELS.get(target_set, target_set)
    profile_label = PROFILE_SETTINGS.get(profile, PROFILE_SETTINGS["CONSERVATIVE"])["label"]
    status = f"{label} / {profile_label} / {source_label}: 新建 {created}，更新 {updated}，已存在 {exists}，过滤 {filtered}，跳过 {skipped}"
    if warning_lines:
        status = f"{status}；{'；'.join(warning_lines)}"
    if panel_api is not None:
        panel_api.set_status(status, level="OK" if skipped == 0 else "WARNING")
    if module_state is not None:
        module_state.set("last_result", status)
        module_state.set("last_missing", missing_lines[:12])
        module_state.set("last_warning", warning_lines[:4])
    return {"FINISHED"}


def draw_panel(layout, context, scene, workflow, module, panel_api, module_state):
    target_set = _target_set(panel_api)
    profile = _profile(panel_api)
    summary_text, summary_icon = _table_summary_text(module, target_set, profile)

    box = panel_api.section(layout, "ARKit 合成形态键", icon="SHAPEKEY_DATA")
    panel_api.draw_object_picker_inline(box, "target_object", "目标网格:", show_active_button=True, factor=0.24)
    panel_api.draw_enum(box, "target_set", "目标预设", [("VRM_BASIC", "VRM 基础"), ("MMD_POSSIBLE", "MMD 常用"), ("CUSTOM_TARGET", "自定义目标预设")], default=DEFAULT_TARGET_SET)
    mix_profile_disabled = ("CONSERVATIVE", "AGGRESSIVE") if target_set == CUSTOM_TARGET_SET else ()
    panel_api.draw_enum(
        box,
        "mix_profile",
        "混合强度",
        [("CONSERVATIVE", "常规"), ("AGGRESSIVE", "激进"), ("CUSTOM", "自定义")],
        default=DEFAULT_PROFILE,
        disabled_items=mix_profile_disabled,
        display_value="CUSTOM" if target_set == CUSTOM_TARGET_SET else None,
    )
    if target_set == CUSTOM_TARGET_SET:
        panel_api.label(box, "自定义目标预设固定使用额外导入表，常规和激进在此模式下不可用。", icon="INFO")
    panel_api.draw_text_input_inline(box, "target_filter_text", "指定合成键:", default="", factor=0.24)
    panel_api.draw_float_input_inline(box, "strength_scale", "额外强度倍率", default=1.0, factor=0.24)
    panel_api.draw_toggle_inline(box, "overwrite_existing", "覆盖已有同名目标形态键", default=False, factor=0.24)
    panel_api.label(box, "白名单留空时生成当前表里的全部目标键。", icon="INFO")

    table_box = None
    if panel_api.get_bool("show_recipe_table", False):
        table_box = panel_api.foldout_section(box, "show_recipe_table", "混合系数表", icon="TEXT", default_open=True)
    else:
        table_box = panel_api.foldout_section(box, "show_recipe_table", "混合系数表", icon="TEXT", default_open=False)
    panel_api.label(box, "可通过 Blender 文件浏览器导入或导出 csv/txt。", icon="INFO")
    if table_box is not None:
        panel_api.label(table_box, f"当前表：{_combo_label(target_set, profile)}", icon="INFO")
        panel_api.label(table_box, summary_text, icon=summary_icon)
        note = panel_api.foldout_section(table_box, "show_recipe_note", "备注", icon="INFO", default_open=False)
        if note is not None:
            panel_api.label(note, "导出的是当前目标预设和混合强度对应的表。", icon="CHECKMARK")
            panel_api.label(note, "自定义表按 VRM 基础 / MMD 常用分开保存。", icon="CHECKMARK")
            panel_api.label(note, "自定义目标预设会单独保存一份额外导入表，不受常规/激进切换影响。", icon="CHECKMARK")
            panel_api.label(note, "自定义模式找不到有效表格时会报错，不会静默生成。", icon="CHECKMARK")

        row = panel_api.row(table_box, align=True)
        if profile == "CUSTOM" or target_set == CUSTOM_TARGET_SET:
            panel_api.draw_button(
                row,
                "IMPORT_TABLE_FILE",
                "导入自定义表",
                icon="IMPORT",
                tooltip="用 Blender 文件浏览器导入 csv 或 txt 到当前目标预设的自定义表",
            )
        try:
            _recipes_for_export, _source_label, table_text = _recipes_for(panel_api, module, target_set, profile)
        except Exception as exc:
            table_text = ""
            panel_api.label(row, str(exc), icon="ERROR")
        if table_text:
            _draw_export_operator_button(
                row,
                "导出 csv",
                icon="FILE_TICK",
                extension=".csv",
                target_set=target_set,
                profile=profile,
                table_text=table_text,
            )
            _draw_export_operator_button(
                row,
                "导出 txt",
                icon="TEXT",
                extension=".txt",
                target_set=target_set,
                profile=profile,
                table_text=table_text,
            )
        row = panel_api.row(table_box, align=True)
        panel_api.draw_button(
            row,
            "VALIDATE_TABLE",
            "校验表格",
            icon="CHECKMARK",
            tooltip="检查当前表是否能解析为 target/source/weight",
        )
        if (profile == "CUSTOM" or target_set == CUSTOM_TARGET_SET) and _custom_table(module, target_set, profile):
            panel_api.draw_button(
                row,
                "CLEAR_CUSTOM_TABLE",
                "清除额外导入表",
                icon="TRASH",
                tooltip="只清除当前目标预设和混合强度组合的额外导入表",
            )

        _draw_text_block(table_box, TABLE_RULES, icon="INFO", width=56, max_lines=None)
    else:
        panel_api.label(box, summary_text, icon=summary_icon)

    warning_lines = module_state.get("last_warning", []) if module_state is not None else []
    if warning_lines:
        warn_box = panel_api.section(box, "提示", icon="INFO")
        for line in warning_lines:
            _draw_text_block(warn_box, line, icon="INFO", width=54)

    panel_api.draw_run_button(box, "生成合成键", icon="PLAY")
    panel_api.draw_status(box)

    missing = module_state.get("last_missing", []) if module_state is not None else []
    if missing:
        warn = panel_api.section(layout, "缺失源形态键", icon="ERROR")
        for line in missing[:12]:
            _draw_text_block(warn, line, icon="ERROR", width=54)


def on_panel_action(action, context, scene, workflow, module, panel_api, module_state):
    target_set = _target_set(panel_api)
    profile = _profile(panel_api)
    if action == "IMPORT_TABLE_FILE":
        if profile != "CUSTOM" and target_set != CUSTOM_TARGET_SET:
            raise Exception("请先把混合强度切换为“自定义”，再导入当前 VRM/MMD 模式对应的自定义表")
        bpy.ops.bworkflow.module_import_text_file(
            "INVOKE_DEFAULT",
            target_text_key="recipe_file_path",
            status_prefix="已选择导入表格",
            workflow_name=getattr(panel_api, "workflow_name", ""),
            module_name=getattr(panel_api, "module_name", ""),
            target_set=target_set,
            mix_profile=profile,
            module_action="IMPORT_TABLE_FILE_APPLY",
        )
        return {"FINISHED"}
    if action == "IMPORT_TABLE_FILE_APPLY":
        if profile != "CUSTOM" and target_set != CUSTOM_TARGET_SET:
            raise Exception("导入自定义表时，混合强度必须为“自定义”，或目标预设切到“自定义目标预设”")
        previous_target_names = []
        previous_text = _custom_table(module, target_set, profile)
        if previous_text:
            try:
                previous_target_names = _recipe_target_names(_parse_recipe_table(previous_text))
            except Exception:
                previous_target_names = []
        text, filepath = _read_text_file(panel_api.get_text("recipe_file_path", ""))
        recipes = _parse_recipe_table(text)
        normalized = _recipes_to_table(recipes)
        _set_custom_table(module, target_set, profile, normalized)
        panel_api.set_enum("recipe_source", "MODULE")
        row_count = sum(len(recipe) for recipe in recipes.values())
        _log_table_targets(panel_api, f"Imported {_combo_label(target_set, profile)}: {os.path.basename(filepath)}", recipes, previous_target_names)
        _log_extra_custom_targets(panel_api, f"Imported {_combo_label(target_set, profile)}", target_set, recipes)
        panel_api.set_status(
            f"已导入 {_combo_label(target_set, profile)} 自定义表: {os.path.basename(filepath)}，{len(recipes)} 个目标键，{row_count} 条混合项",
            level="OK",
        )
        return {"FINISHED"}
    if action in {"EXPORT_TABLE_CSV", "EXPORT_TABLE_TXT"}:
        recipes, source_label, table_text = _recipes_for(panel_api, module, target_set, profile)
        extension = ".csv" if action.endswith("CSV") else ".txt"
        filepath = _write_text_file(panel_api.get_text("recipe_file_path", "") or _suggest_table_filename(target_set, profile, extension), table_text)
        row_count = sum(len(recipe) for recipe in recipes.values())
        panel_api.set_status(
            f"已导出 {_combo_label(target_set, profile)} / {source_label}: {os.path.basename(filepath)}，{len(recipes)} 个目标键，{row_count} 条混合项",
            level="OK",
        )
        return {"FINISHED"}
    if action == "CLEAR_CUSTOM_TABLE":
        _clear_custom_table(module, target_set, profile)
        panel_api.set_enum("recipe_source", "BUILTIN")
        panel_api.set_status(f"已清除 {_combo_label(target_set, profile)} 的额外导入表，恢复内置默认表", level="OK")
        return {"FINISHED"}
    if action == "VALIDATE_TABLE":
        recipes, source_label, _table_text = _recipes_for(panel_api, module, target_set, profile)
        row_count = sum(len(recipe) for recipe in recipes.values())
        _log_table_targets(panel_api, f"Validate table {_combo_label(target_set, profile)} / {source_label}", recipes)
        _log_extra_custom_targets(panel_api, f"Validate table {_combo_label(target_set, profile)} / {source_label}", target_set, recipes)
        panel_api.set_status(f"{_combo_label(target_set, profile)} / {source_label} 校验通过: {len(recipes)} 个目标键，{row_count} 条混合项", level="OK")
        return {"FINISHED"}
    return {"FINISHED"}
