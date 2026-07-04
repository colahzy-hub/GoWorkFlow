import csv
import getpass
import json
import math
import os
import socket
import time
from datetime import datetime

import bpy


ADDON_NAME = "形态键实时采集记录器"
SCHEMA_VERSION = 2
SESSION_PREFIX = "blender_shape_key_capture"
FLUSH_EVERY_SAMPLES = 10
EPSILON = 1e-8
_STATE_KEY = "go_workflow.shape_key_capture_module"

BASE_CSV_COLUMNS = [
    "sample_index",
    "timestamp_unix_ns",
    "timestamp_perf_ns",
    "timestamp_iso",
    "elapsed_s",
    "sample_delta_s",
    "scene_frame",
    "scene_subframe",
    "scene_frame_float",
    "faceit_receiver_enabled",
    "faceit_live_source",
]

ARKIT_SHAPE_NAMES = {
    "eyeBlinkLeft",
    "eyeBlinkRight",
    "eyeWideLeft",
    "eyeWideRight",
    "eyeLookUpLeft",
    "eyeLookUpRight",
    "eyeLookDownLeft",
    "eyeLookDownRight",
    "eyeLookOutLeft",
    "eyeLookOutRight",
    "eyeLookInLeft",
    "eyeLookInRight",
    "browDownLeft",
    "browDownRight",
    "browInnerUp",
    "browOuterUpLeft",
    "browOuterUpRight",
    "jawOpen",
    "mouthClose",
    "mouthFunnel",
    "mouthPucker",
    "jawLeft",
    "jawRight",
    "jawForward",
    "mouthLeft",
    "mouthRight",
    "mouthShrugLower",
    "mouthShrugUpper",
    "mouthRollLower",
    "mouthRollUpper",
    "mouthSmileLeft",
    "mouthSmileRight",
    "mouthDimpleLeft",
    "mouthDimpleRight",
    "mouthPressLeft",
    "mouthPressRight",
    "mouthFrownLeft",
    "mouthFrownRight",
    "mouthStretchLeft",
    "mouthStretchRight",
    "cheekSquintLeft",
    "cheekSquintRight",
    "eyeSquintLeft",
    "eyeSquintRight",
    "mouthLowerDownLeft",
    "mouthLowerDownRight",
    "mouthUpperUpLeft",
    "mouthUpperUpRight",
    "cheekPuff",
    "tongueOut",
    "noseSneerLeft",
    "noseSneerRight",
}

REPLAY_KEY_SET_AUTO = "AUTO"
REPLAY_KEY_SET_ARKIT = "ARKIT"
REPLAY_KEY_SET_MMD = "MMD"
REPLAY_KEY_SET_ALL = "ALL"
REPLAY_KEY_SET_VALUES = {
    REPLAY_KEY_SET_AUTO,
    REPLAY_KEY_SET_ARKIT,
    REPLAY_KEY_SET_MMD,
    REPLAY_KEY_SET_ALL,
}
REPLAY_KEY_SET_LABELS = {
    REPLAY_KEY_SET_AUTO: "自动",
    REPLAY_KEY_SET_ARKIT: "ARKit",
    REPLAY_KEY_SET_MMD: "MMD",
    REPLAY_KEY_SET_ALL: "全部",
}

MMD_SHAPE_NAMES = {
    "あ",
    "い",
    "う",
    "え",
    "お",
    "まばたき",
    "笑い",
    "ウィンク",
    "ウィンク右",
    "ウィンク2",
    "ウィンク２",
    "ウィンク２右",
    "びっくり",
    "じと目",
    "なごみ",
    "はぅ",
    "怒り",
    "上",
    "下",
    "困る",
    "真面目",
    "にやり",
    "口角上げ",
    "口角下げ",
    "口横広げ",
    "口横狭め",
    "頬染め",
    "照れ",
    "涙",
    "a",
    "i",
    "u",
    "e",
    "o",
    "aa",
    "ih",
    "ou",
    "ee",
    "oh",
    "blink",
    "blinkleft",
    "blinkright",
    "wink",
    "winkleft",
    "winkright",
    "smile",
    "angry",
    "sad",
    "sorrow",
    "fun",
}
_REPLAY_FAMILY_REFERENCE_CACHE = {}


def _runtime_state():
    return bpy.app.driver_namespace.setdefault(
        _STATE_KEY,
        {
            "recorder": None,
            "replayer": None,
            "capture_callback": None,
            "replay_callback": None,
        },
    )


def _module_state():
    return globals().get("module_state")


def _panel_api():
    return globals().get("panel_api")


def _scene_prop(scene, name, default=None):
    if scene is None or not hasattr(scene, name):
        return default
    return getattr(scene, name)


def _config(module):
    raw = getattr(module, "config_payload", "") or ""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_config(module, data):
    module.config_payload = json.dumps(data or {}, ensure_ascii=False, sort_keys=True)


def _config_get(module, key, default):
    return _config(module).get(key, default)


def _config_set(module, key, value):
    data = _config(module)
    data[key] = value
    _save_config(module, data)


def _status(text, level="INFO"):
    panel_api = _panel_api()
    module_state = _module_state()
    if module_state is not None:
        module_state.set("last_result", str(text or ""))
    if panel_api is not None:
        panel_api.set_status(str(text or ""), level=level)


def _desktop_dir():
    home_dir = os.path.expanduser("~")
    candidates = [
        os.path.join(home_dir, "Desktop"),
        os.path.join(os.environ.get("USERPROFILE", home_dir), "Desktop"),
    ]
    for path in candidates:
        if path and os.path.isdir(path):
            return path
    return home_dir


def _session_dir_path():
    base_dir = _desktop_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    root_name = f"{SESSION_PREFIX}_{timestamp}"
    candidate = os.path.join(base_dir, root_name)
    suffix = 1
    while os.path.exists(candidate):
        candidate = os.path.join(base_dir, f"{root_name}_{suffix:02d}")
        suffix += 1
    return candidate


def _now_iso():
    return datetime.now().astimezone().isoformat()


def _round_float(value, digits=8):
    return round(float(value), digits)


def _reason_to_text(reason):
    reason_map = {
        "user_stop": "手动停止",
        "capture_error": "采集异常停止",
        "replay_error": "回放异常停止",
        "module_cleanup": "模块清理",
    }
    return reason_map.get(reason, str(reason or ""))


def _get_faceit_status(scene):
    if scene is None:
        return {"available": False, "receiver_enabled": False, "live_source": ""}
    return {
        "available": hasattr(scene, "faceit_osc_receiver_enabled"),
        "receiver_enabled": bool(getattr(scene, "faceit_osc_receiver_enabled", False)),
        "live_source": str(getattr(scene, "faceit_live_source", "")),
    }


def _iter_candidate_objects(scene, only_selected):
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        if only_selected and not obj.select_get():
            continue
        shape_keys = getattr(obj.data, "shape_keys", None)
        if shape_keys is None or not shape_keys.key_blocks:
            continue
        yield obj


def _is_separator_shape_key(name):
    stripped = str(name or "").strip()
    return stripped.startswith("--") and stripped.endswith("--")


def _shape_key_allowed(name, index, include_basis, skip_separator_keys, arkit_only):
    if index == 0 and not include_basis:
        return False
    if skip_separator_keys and _is_separator_shape_key(name):
        return False
    if arkit_only and name not in ARKIT_SHAPE_NAMES:
        return False
    return True


def _capture_manifest(scene, include_basis, only_selected, skip_separator_keys, arkit_only):
    captured_objects = []
    flat_columns = []
    for obj in _iter_candidate_objects(scene, only_selected):
        shape_keys = obj.data.shape_keys
        shape_entries = []
        for index, key_block in enumerate(shape_keys.key_blocks):
            if not _shape_key_allowed(
                key_block.name,
                index,
                include_basis,
                skip_separator_keys,
                arkit_only,
            ):
                continue
            shape_entries.append(
                {
                    "name": key_block.name,
                    "index": index,
                    "slider_min": float(key_block.slider_min),
                    "slider_max": float(key_block.slider_max),
                    "value_at_start": float(key_block.value),
                    "mute": bool(key_block.mute),
                    "vertex_group": key_block.vertex_group,
                    "relative_key": key_block.relative_key.name if key_block.relative_key else "",
                    "interpolation": key_block.interpolation,
                }
            )
            flat_columns.append(f"{obj.name}::{key_block.name}")
        if not shape_entries:
            continue
        captured_objects.append(
            {
                "object_name": obj.name,
                "object_data_name": obj.data.name,
                "shape_key_data_name": shape_keys.name,
                "shape_keys": shape_entries,
            }
        )
    return captured_objects, flat_columns


def _safe_mean(values):
    return (sum(values) / len(values)) if values else 0.0


def _safe_std(values):
    if len(values) < 2:
        return 0.0
    mean_value = _safe_mean(values)
    variance = sum((value - mean_value) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def _quality_label_from_metrics(mean_hz, jitter_cv, active_key_count):
    if mean_hz >= 50.0 and jitter_cv <= 0.08 and active_key_count >= 30:
        return "高"
    if mean_hz >= 28.0 and jitter_cv <= 0.20 and active_key_count >= 20:
        return "中"
    return "低"


def _normalize_shape_key_name(name):
    normalized = []
    previous_is_lower = False
    for char in str(name or ""):
        if char.isupper() and previous_is_lower:
            normalized.append(" ")
        if char.isalnum():
            normalized.append(char.lower())
        else:
            normalized.append(" ")
        previous_is_lower = char.islower() or char.isdigit()
    return "".join(normalized)


def _tokenize_shape_key_name(name):
    aliases = {
        "l": "left",
        "left": "left",
        "r": "right",
        "right": "right",
        "uplook": "up",
        "downlook": "down",
    }
    raw_tokens = [token for token in _normalize_shape_key_name(name).split() if token]
    tokens = []
    for token in raw_tokens:
        token = aliases.get(token, token)
        if token in {"shape", "key", "ctrl", "control", "expr", "expression"}:
            continue
        tokens.append(token)
    return tuple(tokens)


def _name_has_side(tokens, side):
    return side in tokens


def _candidate_normalized_names(name):
    base = _normalize_shape_key_name(name).replace(" ", "")
    candidates = {base}
    replacements = [
        ("left", "l"),
        ("right", "r"),
        ("eye", ""),
        ("mouth", ""),
        ("brow", ""),
        ("cheek", ""),
        ("nose", ""),
    ]
    queue = [base]
    seen = {base}
    while queue:
        current = queue.pop()
        for old, new in replacements:
            if old in current:
                candidate = current.replace(old, new)
                if candidate and candidate not in seen:
                    seen.add(candidate)
                    candidates.add(candidate)
                    queue.append(candidate)
    return candidates


def _normalize_replay_key_set(value):
    normalized = str(value or REPLAY_KEY_SET_AUTO).strip().upper()
    if normalized not in REPLAY_KEY_SET_VALUES:
        return REPLAY_KEY_SET_AUTO
    return normalized


def _normalized_reference_shape_names(cache_key, names):
    cached = _REPLAY_FAMILY_REFERENCE_CACHE.get(cache_key)
    if cached is None:
        cached = {_normalize_shape_key_name(item).replace(" ", "") for item in names}
        _REPLAY_FAMILY_REFERENCE_CACHE[cache_key] = cached
    return cached


def _shape_name_family(name):
    raw_name = str(name or "").strip()
    if not raw_name or raw_name.lower() == "basis":
        return ""
    collapsed = _normalize_shape_key_name(raw_name).replace(" ", "")
    arkit_names = _normalized_reference_shape_names(REPLAY_KEY_SET_ARKIT, ARKIT_SHAPE_NAMES)
    if raw_name in ARKIT_SHAPE_NAMES or collapsed in arkit_names:
        return REPLAY_KEY_SET_ARKIT
    mmd_names = _normalized_reference_shape_names(REPLAY_KEY_SET_MMD, MMD_SHAPE_NAMES)
    if raw_name in MMD_SHAPE_NAMES or collapsed in mmd_names:
        return REPLAY_KEY_SET_MMD
    return ""


def _shape_names_family_counts(names):
    counts = {REPLAY_KEY_SET_ARKIT: 0, REPLAY_KEY_SET_MMD: 0, "UNKNOWN": 0}
    for name in names or ():
        family = _shape_name_family(name)
        if family == REPLAY_KEY_SET_ARKIT:
            counts[REPLAY_KEY_SET_ARKIT] += 1
        elif family == REPLAY_KEY_SET_MMD:
            counts[REPLAY_KEY_SET_MMD] += 1
        else:
            counts["UNKNOWN"] += 1
    return counts


def _shape_key_allowed_for_replay_set(name, replay_key_set):
    replay_key_set = _normalize_replay_key_set(replay_key_set)
    if replay_key_set in {REPLAY_KEY_SET_AUTO, REPLAY_KEY_SET_ALL}:
        return True
    return _shape_name_family(name) == replay_key_set


def _object_shape_key_names(obj):
    key_blocks = _target_key_blocks(obj)
    if key_blocks is None:
        return []
    return [key_block.name for key_block in key_blocks]


def _sample_shape_key_names(sample, source_object_name):
    source_values = sample.get("objects", {}).get(source_object_name, {}) if sample else {}
    if isinstance(source_values, dict):
        return list(source_values.keys())
    return []


def _effective_replay_key_set(requested_key_set, source_counts=None, target_counts=None):
    requested_key_set = _normalize_replay_key_set(requested_key_set)
    if requested_key_set in {REPLAY_KEY_SET_ARKIT, REPLAY_KEY_SET_MMD, REPLAY_KEY_SET_ALL}:
        return requested_key_set
    source_counts = source_counts or {}
    target_counts = target_counts or {}
    source_arkit_count = int(source_counts.get(REPLAY_KEY_SET_ARKIT, 0) or 0)
    source_mmd_count = int(source_counts.get(REPLAY_KEY_SET_MMD, 0) or 0)
    if source_arkit_count > 0 and source_mmd_count == 0:
        return REPLAY_KEY_SET_ARKIT
    if source_mmd_count > 0 and source_arkit_count == 0:
        return REPLAY_KEY_SET_MMD
    if source_arkit_count > 0 and source_mmd_count > 0:
        return REPLAY_KEY_SET_ALL
    target_arkit_count = int(target_counts.get(REPLAY_KEY_SET_ARKIT, 0) or 0)
    target_mmd_count = int(target_counts.get(REPLAY_KEY_SET_MMD, 0) or 0)
    if target_arkit_count > 0 and target_mmd_count == 0:
        return REPLAY_KEY_SET_ARKIT
    if target_mmd_count > 0 and target_arkit_count == 0:
        return REPLAY_KEY_SET_MMD
    return REPLAY_KEY_SET_ALL


def _format_family_counts(counts):
    counts = counts or {}
    parts = []
    arkit_count = int(counts.get(REPLAY_KEY_SET_ARKIT, 0) or 0)
    mmd_count = int(counts.get(REPLAY_KEY_SET_MMD, 0) or 0)
    unknown_count = int(counts.get("UNKNOWN", 0) or 0)
    if arkit_count:
        parts.append(f"ARKit {arkit_count}")
    if mmd_count:
        parts.append(f"MMD {mmd_count}")
    if unknown_count:
        parts.append(f"其他 {unknown_count}")
    return " / ".join(parts) if parts else "未识别"


def _target_object_from_panel(context, panel_api):
    scene = getattr(context, "scene", None) or getattr(bpy.context, "scene", None)
    obj = getattr(context, "active_object", None) or getattr(bpy.context, "active_object", None)
    if _target_key_blocks(obj) is not None:
        return obj
    target_object = getattr(scene, "sk_replay_target_object", None) if scene is not None and hasattr(scene, "sk_replay_target_object") else None
    if target_object is None and panel_api is not None:
        target_object = panel_api.get_object("replay_target_object")
    if target_object is not None:
        return target_object
    return obj


def _target_key_blocks(target_object):
    if target_object is None or target_object.type != "MESH":
        return None
    shape_keys = getattr(target_object.data, "shape_keys", None)
    return getattr(shape_keys, "key_blocks", None)


def _resolve_replay_file_path(file_path):
    resolved = bpy.path.abspath(str(file_path or "")).strip()
    if not resolved:
        raise RuntimeError("请指定要投射的 samples.jsonl 或 samples_denoised.jsonl 文件。")
    if not os.path.isfile(resolved):
        raise RuntimeError(f"找不到回放文件: {resolved}")
    return resolved


def _load_replay_samples(file_path):
    resolved_path = _resolve_replay_file_path(file_path)
    samples = []
    with open(resolved_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            samples.append(json.loads(line))
    if not samples:
        raise RuntimeError("回放文件为空。")
    first_sample = samples[0]
    source_objects = list(first_sample.get("objects", {}).keys())
    if not source_objects:
        raise RuntimeError("回放文件中没有 objects 数据。")
    return resolved_path, samples, source_objects


def _choose_source_object_name(source_objects, preferred_name):
    if preferred_name and preferred_name in source_objects:
        return preferred_name
    return source_objects[0]


def _find_best_target_name(source_name, key_blocks, used_target_names, replay_key_set=REPLAY_KEY_SET_ALL):
    if key_blocks is None:
        return None
    replay_key_set = _normalize_replay_key_set(replay_key_set)
    if (
        key_blocks.get(source_name) is not None
        and source_name not in used_target_names
        and _shape_key_allowed_for_replay_set(source_name, replay_key_set)
    ):
        return source_name
    normalized_to_names = {}
    token_entries = []
    for key_block in key_blocks:
        target_name = key_block.name
        if not _shape_key_allowed_for_replay_set(target_name, replay_key_set):
            continue
        normalized = _normalize_shape_key_name(target_name).replace(" ", "")
        normalized_to_names.setdefault(normalized, []).append(target_name)
        token_entries.append((target_name, set(_tokenize_shape_key_name(target_name))))
    for candidate in _candidate_normalized_names(source_name):
        for target_name in normalized_to_names.get(candidate, []):
            if target_name not in used_target_names:
                return target_name
    source_tokens = set(_tokenize_shape_key_name(source_name))
    if not source_tokens:
        return None
    best_name = None
    best_score = 0.0
    source_left = _name_has_side(source_tokens, "left")
    source_right = _name_has_side(source_tokens, "right")
    for target_name, target_tokens in token_entries:
        if target_name in used_target_names:
            continue
        if source_left and "right" in target_tokens:
            continue
        if source_right and "left" in target_tokens:
            continue
        overlap = len(source_tokens & target_tokens)
        if overlap == 0:
            continue
        score = overlap / max(len(source_tokens), 1)
        if source_left and "left" in target_tokens:
            score += 0.15
        if source_right and "right" in target_tokens:
            score += 0.15
        if score > best_score:
            best_score = score
            best_name = target_name
    return best_name if best_score >= 0.60 else None


def _build_replay_mapping(target_object, sample, source_object_name, replay_key_set=REPLAY_KEY_SET_ALL):
    key_blocks = _target_key_blocks(target_object)
    if key_blocks is None:
        raise RuntimeError("目标物体没有形态键。")
    source_values = sample.get("objects", {}).get(source_object_name)
    if source_values is None:
        raise RuntimeError(f"回放文件中没有源对象: {source_object_name}")
    replay_key_set = _normalize_replay_key_set(replay_key_set)
    source_counts = _shape_names_family_counts(source_values.keys())
    target_counts = _shape_names_family_counts(_object_shape_key_names(target_object))
    effective_key_set = _effective_replay_key_set(replay_key_set, source_counts, target_counts)
    if effective_key_set in {REPLAY_KEY_SET_ARKIT, REPLAY_KEY_SET_MMD}:
        source_names = [name for name in source_values.keys() if _shape_key_allowed_for_replay_set(name, effective_key_set)]
    else:
        source_names = list(source_values.keys())
    mapping = []
    used_target_names = set()
    unmapped = []
    for source_name in source_names:
        target_name = _find_best_target_name(source_name, key_blocks, used_target_names, effective_key_set)
        if target_name is None:
            unmapped.append(source_name)
            continue
        target_block = key_blocks.get(target_name)
        mapping.append(
            {
                "source_name": source_name,
                "target_name": target_name,
                "slider_min": float(target_block.slider_min),
                "slider_max": float(target_block.slider_max),
            }
        )
        used_target_names.add(target_name)
    return {
        "target_object_name": target_object.name,
        "source_object_name": source_object_name,
        "mapped_count": len(mapping),
        "source_count": len(source_names),
        "source_total_count": len(source_values),
        "requested_key_set": replay_key_set,
        "effective_key_set": effective_key_set,
        "source_family_counts": source_counts,
        "target_family_counts": target_counts,
        "unmapped_source_names": unmapped,
        "mapping": mapping,
    }


def _apply_replay_mapping(target_object, sample, mapping_info, strength):
    key_blocks = _target_key_blocks(target_object)
    if key_blocks is None:
        raise RuntimeError("目标物体没有形态键。")
    source_values = sample.get("objects", {}).get(mapping_info["source_object_name"], {})
    applied_count = 0
    for item in mapping_info["mapping"]:
        target_block = key_blocks.get(item["target_name"])
        if target_block is None:
            continue
        value = source_values.get(item["source_name"])
        if value is None:
            continue
        final_value = float(value) * float(strength)
        final_value = max(item["slider_min"], min(item["slider_max"], final_value))
        target_block.value = final_value
        applied_count += 1
    return applied_count


class _LowPassFilter:
    def __init__(self):
        self.initialized = False
        self.last_value = 0.0

    def filter(self, value, alpha):
        if not self.initialized:
            self.initialized = True
            self.last_value = value
            return value
        self.last_value = alpha * value + (1.0 - alpha) * self.last_value
        return self.last_value


class _OneEuroFilter:
    def __init__(self, min_cutoff, beta, d_cutoff):
        self.min_cutoff = max(float(min_cutoff), 0.0001)
        self.beta = max(float(beta), 0.0)
        self.d_cutoff = max(float(d_cutoff), 0.0001)
        self.x_filter = _LowPassFilter()
        self.dx_filter = _LowPassFilter()
        self.last_value = None

    @staticmethod
    def _alpha(cutoff, dt):
        cutoff = max(float(cutoff), 0.0001)
        dt = max(float(dt), 1e-6)
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def filter(self, value, dt):
        value = float(value)
        dt = max(float(dt), 1e-6)
        if self.last_value is None:
            self.last_value = value
            return self.x_filter.filter(value, 1.0)
        dx = (value - self.last_value) / dt
        self.last_value = value
        edx = self.dx_filter.filter(dx, self._alpha(self.d_cutoff, dt))
        cutoff = self.min_cutoff + self.beta * abs(edx)
        return self.x_filter.filter(value, self._alpha(cutoff, dt))


class ShapeKeyCaptureRecorder:
    def __init__(self, scene, settings):
        self.scene_name = scene.name
        self.sample_interval = max(float(settings["capture_interval"]), 0.001)
        self.include_basis = bool(settings["capture_include_basis"])
        self.only_selected = bool(settings["capture_only_selected"])
        self.skip_separator_keys = bool(settings["capture_skip_separator_keys"])
        self.arkit_only = bool(settings["capture_arkit_only"])
        self.export_denoised = bool(settings["capture_export_denoised"])
        self.denoise_deadband = max(float(settings["capture_denoise_deadband"]), 0.0)
        self.denoise_min_cutoff = max(float(settings["capture_denoise_min_cutoff"]), 0.0001)
        self.denoise_beta = max(float(settings["capture_denoise_beta"]), 0.0)
        self.denoise_d_cutoff = max(float(settings["capture_denoise_d_cutoff"]), 0.0001)
        self.session_dir = ""
        self.metadata_path = ""
        self.samples_jsonl_path = ""
        self.samples_csv_path = ""
        self.samples_denoised_jsonl_path = ""
        self.samples_denoised_csv_path = ""
        self.summary_path = ""
        self.events_path = ""
        self.curve_report_path = ""
        self.sample_index = 0
        self.start_unix_ns = 0
        self.start_perf_ns = 0
        self.start_iso = ""
        self.last_perf_ns = 0
        self.last_elapsed_s = 0.0
        self.stop_reason = ""
        self.is_running = False
        self.captured_objects = []
        self.flat_columns = []
        self.missing_target_counts = {}
        self.sample_deltas = []
        self.per_key_stats = {}
        self.curve_report = None
        self._samples_file = None
        self._events_file = None
        self._csv_file = None
        self._csv_writer = None
        self._hostname = socket.gethostname()
        self._username = getpass.getuser()

    def start(self):
        scene = self._scene_or_raise()
        self.captured_objects, self.flat_columns = _capture_manifest(
            scene=scene,
            include_basis=self.include_basis,
            only_selected=self.only_selected,
            skip_separator_keys=self.skip_separator_keys,
            arkit_only=self.arkit_only,
        )
        if not self.captured_objects:
            raise RuntimeError("没有找到可采集的形态键对象。")
        self._init_per_key_stats()
        self.session_dir = _session_dir_path()
        os.makedirs(self.session_dir, exist_ok=False)
        self.metadata_path = os.path.join(self.session_dir, "metadata.json")
        self.samples_jsonl_path = os.path.join(self.session_dir, "samples.jsonl")
        self.samples_csv_path = os.path.join(self.session_dir, "samples_flat.csv")
        self.samples_denoised_jsonl_path = os.path.join(self.session_dir, "samples_denoised.jsonl")
        self.samples_denoised_csv_path = os.path.join(self.session_dir, "samples_denoised.csv")
        self.summary_path = os.path.join(self.session_dir, "summary.json")
        self.events_path = os.path.join(self.session_dir, "events.jsonl")
        self.curve_report_path = os.path.join(self.session_dir, "curve_report.json")
        self.start_unix_ns = time.time_ns()
        self.start_perf_ns = time.perf_counter_ns()
        self.start_iso = _now_iso()
        self._open_streams()
        self._write_metadata(scene)
        self._log_event(
            "capture_started",
            {
                "sample_interval_s": self.sample_interval,
                "include_basis": self.include_basis,
                "only_selected": self.only_selected,
                "skip_separator_keys": self.skip_separator_keys,
                "arkit_only": self.arkit_only,
                "export_denoised": self.export_denoised,
                "captured_object_count": len(self.captured_objects),
                "captured_shape_key_count": len(self.flat_columns),
            },
        )
        self.is_running = True
        self._write_sample()

    def stop(self, reason):
        self.stop_reason = reason
        self.is_running = False
        self._log_event(
            "capture_stopped",
            {
                "reason": reason,
                "samples_written": self.sample_index,
                "elapsed_s": self.last_elapsed_s,
            },
        )
        self._flush_streams()
        if self.export_denoised:
            self._write_denoised_outputs()
        self.curve_report = self._build_curve_report()
        self._write_curve_report()
        self._write_summary()
        self._flush_streams()
        self._close_streams()

    def timer_tick(self):
        if not self.is_running:
            return None
        self._write_sample()
        return self.sample_interval

    def _scene_or_raise(self):
        scene = bpy.data.scenes.get(self.scene_name)
        if scene is None:
            raise RuntimeError("当前采集绑定的场景已不可用。")
        return scene

    def _init_per_key_stats(self):
        self.per_key_stats = {}
        for object_entry in self.captured_objects:
            object_name = object_entry["object_name"]
            for shape_entry in object_entry["shape_keys"]:
                key = f"{object_name}::{shape_entry['name']}"
                self.per_key_stats[key] = {
                    "object_name": object_name,
                    "shape_key_name": shape_entry["name"],
                    "slider_min": float(shape_entry["slider_min"]),
                    "slider_max": float(shape_entry["slider_max"]),
                    "max_abs": 0.0,
                    "avg_abs_sum": 0.0,
                    "nonzero_samples": 0,
                    "saturated_samples": 0,
                }

    def _open_streams(self):
        self._samples_file = open(self.samples_jsonl_path, "w", encoding="utf-8", newline="\n")
        self._events_file = open(self.events_path, "w", encoding="utf-8", newline="\n")
        self._csv_file = open(self.samples_csv_path, "w", encoding="utf-8", newline="")
        self._csv_writer = csv.writer(self._csv_file)
        self._csv_writer.writerow(BASE_CSV_COLUMNS + self.flat_columns)
        self._flush_streams()

    def _flush_streams(self):
        for stream in (self._samples_file, self._events_file, self._csv_file):
            if stream is not None:
                stream.flush()

    def _close_streams(self):
        for stream_name in ("_samples_file", "_events_file", "_csv_file"):
            stream = getattr(self, stream_name)
            if stream is not None:
                stream.close()
                setattr(self, stream_name, None)
        self._csv_writer = None

    def _write_metadata(self, scene):
        fps = float(scene.render.fps) / float(scene.render.fps_base or 1.0)
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "addon_name": ADDON_NAME,
            "capture_started_at": self.start_iso,
            "capture_started_unix_ns": self.start_unix_ns,
            "hostname": self._hostname,
            "username": self._username,
            "desktop_session_dir": self.session_dir,
            "blend_file": bpy.data.filepath,
            "scene_name": scene.name,
            "fps": fps,
            "sample_interval_s": self.sample_interval,
            "capture_basis": self.include_basis,
            "only_selected_objects": self.only_selected,
            "skip_separator_keys": self.skip_separator_keys,
            "arkit_only": self.arkit_only,
            "export_denoised": self.export_denoised,
            "denoise_settings": {
                "algorithm": "one_euro",
                "deadband": self.denoise_deadband,
                "min_cutoff": self.denoise_min_cutoff,
                "beta": self.denoise_beta,
                "d_cutoff": self.denoise_d_cutoff,
            },
            "flat_csv_columns": self.flat_columns,
            "faceit_status_at_start": _get_faceit_status(scene),
            "captured_objects": self.captured_objects,
        }
        with open(self.metadata_path, "w", encoding="utf-8", newline="\n") as metadata_file:
            json.dump(metadata, metadata_file, ensure_ascii=False, indent=2)

    def _write_sample(self):
        scene = self._scene_or_raise()
        timestamp_unix_ns = time.time_ns()
        timestamp_perf_ns = time.perf_counter_ns()
        elapsed_s = (timestamp_perf_ns - self.start_perf_ns) / 1_000_000_000.0
        sample_delta_s = 0.0
        if self.last_perf_ns:
            sample_delta_s = (timestamp_perf_ns - self.last_perf_ns) / 1_000_000_000.0
            self.sample_deltas.append(sample_delta_s)
        frame = int(scene.frame_current)
        subframe = float(scene.frame_subframe)
        frame_float = float(frame) + subframe
        faceit_status = _get_faceit_status(scene)
        sample_payload = {
            "sample_index": self.sample_index,
            "timestamp_unix_ns": timestamp_unix_ns,
            "timestamp_perf_ns": timestamp_perf_ns,
            "timestamp_iso": _now_iso(),
            "elapsed_s": _round_float(elapsed_s),
            "sample_delta_s": _round_float(sample_delta_s),
            "scene_frame": frame,
            "scene_subframe": _round_float(subframe),
            "scene_frame_float": _round_float(frame_float),
            "faceit_receiver_enabled": faceit_status["receiver_enabled"],
            "faceit_live_source": faceit_status["live_source"],
            "objects": {},
        }
        csv_row = [
            self.sample_index,
            timestamp_unix_ns,
            timestamp_perf_ns,
            sample_payload["timestamp_iso"],
            sample_payload["elapsed_s"],
            sample_payload["sample_delta_s"],
            frame,
            sample_payload["scene_subframe"],
            sample_payload["scene_frame_float"],
            int(faceit_status["receiver_enabled"]),
            faceit_status["live_source"],
        ]
        for object_entry in self.captured_objects:
            object_name = object_entry["object_name"]
            object_values = {}
            obj = scene.objects.get(object_name)
            key_blocks = None
            if obj is not None and obj.type == "MESH":
                shape_keys = getattr(obj.data, "shape_keys", None)
                if shape_keys is not None:
                    key_blocks = shape_keys.key_blocks
            for shape_entry in object_entry["shape_keys"]:
                shape_name = shape_entry["name"]
                value = None
                if key_blocks is not None:
                    key_block = key_blocks.get(shape_name)
                    if key_block is not None:
                        value = float(key_block.value)
                        self._update_per_key_stats(object_name, shape_name, value, shape_entry)
                    else:
                        self._mark_missing(object_name, shape_name, "shape_key_missing")
                else:
                    self._mark_missing(object_name, shape_name, "object_missing")
                object_values[shape_name] = value
                csv_row.append("" if value is None else f"{value:.8f}")
            sample_payload["objects"][object_name] = object_values
        self._samples_file.write(json.dumps(sample_payload, ensure_ascii=False) + "\n")
        self._csv_writer.writerow(csv_row)
        self.sample_index += 1
        self.last_perf_ns = timestamp_perf_ns
        self.last_elapsed_s = elapsed_s
        if self.sample_index % FLUSH_EVERY_SAMPLES == 0:
            self._flush_streams()

    def _update_per_key_stats(self, object_name, shape_name, value, shape_entry):
        key = f"{object_name}::{shape_name}"
        stats = self.per_key_stats.get(key)
        if stats is None:
            return
        abs_value = abs(float(value))
        stats["max_abs"] = max(stats["max_abs"], abs_value)
        stats["avg_abs_sum"] += abs_value
        if abs_value > EPSILON:
            stats["nonzero_samples"] += 1
        slider_min = float(shape_entry["slider_min"])
        slider_max = float(shape_entry["slider_max"])
        range_size = max(slider_max - slider_min, EPSILON)
        if slider_max - abs_value <= range_size * 0.02:
            stats["saturated_samples"] += 1

    def _mark_missing(self, object_name, shape_name, reason):
        key = f"{object_name}::{shape_name}::{reason}"
        self.missing_target_counts[key] = self.missing_target_counts.get(key, 0) + 1

    def _log_event(self, event_type, payload):
        if self._events_file is None:
            return
        event = {
            "event_type": event_type,
            "timestamp_unix_ns": time.time_ns(),
            "timestamp_iso": _now_iso(),
        }
        event.update(payload)
        self._events_file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._events_file.flush()

    def _shape_config_map(self):
        config = {}
        for object_entry in self.captured_objects:
            object_name = object_entry["object_name"]
            for shape_entry in object_entry["shape_keys"]:
                config[f"{object_name}::{shape_entry['name']}"] = shape_entry
        return config

    def _write_denoised_outputs(self):
        if not os.path.exists(self.samples_jsonl_path):
            return
        config_map = self._shape_config_map()
        filters = {}
        with open(self.samples_jsonl_path, "r", encoding="utf-8") as raw_file, open(
            self.samples_denoised_jsonl_path, "w", encoding="utf-8", newline="\n"
        ) as jsonl_out, open(self.samples_denoised_csv_path, "w", encoding="utf-8", newline="") as csv_out:
            csv_writer = csv.writer(csv_out)
            csv_writer.writerow(BASE_CSV_COLUMNS + self.flat_columns)
            for line in raw_file:
                sample = json.loads(line)
                dt = float(sample.get("sample_delta_s") or 0.0)
                if dt <= 0.0:
                    dt = self.sample_interval
                csv_row = [
                    sample["sample_index"],
                    sample["timestamp_unix_ns"],
                    sample["timestamp_perf_ns"],
                    sample["timestamp_iso"],
                    sample["elapsed_s"],
                    sample.get("sample_delta_s", 0.0),
                    sample["scene_frame"],
                    sample["scene_subframe"],
                    sample["scene_frame_float"],
                    int(bool(sample["faceit_receiver_enabled"])),
                    sample["faceit_live_source"],
                ]
                denoised_objects = {}
                for object_entry in self.captured_objects:
                    object_name = object_entry["object_name"]
                    source_values = sample["objects"].get(object_name, {})
                    denoised_values = {}
                    for shape_entry in object_entry["shape_keys"]:
                        shape_name = shape_entry["name"]
                        filter_key = f"{object_name}::{shape_name}"
                        value = source_values.get(shape_name)
                        if value is None:
                            denoised_value = None
                        else:
                            raw_value = float(value)
                            if abs(raw_value) < self.denoise_deadband:
                                raw_value = 0.0
                            one_euro = filters.get(filter_key)
                            if one_euro is None:
                                one_euro = _OneEuroFilter(
                                    min_cutoff=self.denoise_min_cutoff,
                                    beta=self.denoise_beta,
                                    d_cutoff=self.denoise_d_cutoff,
                                )
                                filters[filter_key] = one_euro
                            denoised_value = one_euro.filter(raw_value, dt)
                            slider_min = float(config_map[filter_key]["slider_min"])
                            slider_max = float(config_map[filter_key]["slider_max"])
                            denoised_value = max(slider_min, min(slider_max, denoised_value))
                            if abs(denoised_value) < self.denoise_deadband * 0.5:
                                denoised_value = 0.0
                            denoised_value = _round_float(denoised_value)
                        denoised_values[shape_name] = denoised_value
                        csv_row.append("" if denoised_value is None else f"{denoised_value:.8f}")
                    denoised_objects[object_name] = denoised_values
                denoised_sample = dict(sample)
                denoised_sample["denoise"] = {
                    "algorithm": "one_euro",
                    "deadband": self.denoise_deadband,
                    "min_cutoff": self.denoise_min_cutoff,
                    "beta": self.denoise_beta,
                    "d_cutoff": self.denoise_d_cutoff,
                }
                denoised_sample["objects"] = denoised_objects
                jsonl_out.write(json.dumps(denoised_sample, ensure_ascii=False) + "\n")
                csv_writer.writerow(csv_row)

    def _build_curve_report(self):
        timing_mean = _safe_mean(self.sample_deltas)
        timing_std = _safe_std(self.sample_deltas)
        timing_min = min(self.sample_deltas) if self.sample_deltas else 0.0
        timing_max = max(self.sample_deltas) if self.sample_deltas else 0.0
        mean_hz = (1.0 / timing_mean) if timing_mean > 0.0 else 0.0
        jitter_cv = (timing_std / timing_mean) if timing_mean > 0.0 else 0.0
        active_keys = []
        saturated_keys = []
        inactive_keys = []
        for stats in self.per_key_stats.values():
            avg_abs = stats["avg_abs_sum"] / max(self.sample_index, 1)
            entry = {
                "object_name": stats["object_name"],
                "shape_key_name": stats["shape_key_name"],
                "max_abs": _round_float(stats["max_abs"]),
                "avg_abs": _round_float(avg_abs),
                "nonzero_samples": stats["nonzero_samples"],
                "saturated_samples": stats["saturated_samples"],
            }
            if stats["nonzero_samples"] > 0:
                active_keys.append(entry)
            else:
                inactive_keys.append(entry)
            if stats["saturated_samples"] > 0:
                saturated_keys.append(entry)
        active_keys.sort(key=lambda item: (item["max_abs"], item["nonzero_samples"]), reverse=True)
        saturated_keys.sort(key=lambda item: item["saturated_samples"], reverse=True)
        recommendations = []
        if mean_hz < 45.0:
            recommendations.append("实际采样率偏低，更适合中等细度曲线拟合。")
        if jitter_cv > 0.12:
            recommendations.append("采样间隔抖动明显，建议优先使用 One Euro 或 EMA 降噪。")
        if any(item["max_abs"] >= 0.98 for item in active_keys):
            recommendations.append("部分键位频繁打到 1.0，这类样本更像预设触发。")
        if not recommendations:
            recommendations.append("采样率与活跃键数量都在可用区间。")
        return {
            "schema_version": SCHEMA_VERSION,
            "capture_quality": {
                "label": _quality_label_from_metrics(mean_hz, jitter_cv, len(active_keys)),
                "mean_sample_interval_s": _round_float(timing_mean),
                "std_sample_interval_s": _round_float(timing_std),
                "min_sample_interval_s": _round_float(timing_min),
                "max_sample_interval_s": _round_float(timing_max),
                "measured_sample_rate_hz": _round_float(mean_hz),
                "jitter_cv": _round_float(jitter_cv),
                "active_key_count": len(active_keys),
                "inactive_key_count": len(inactive_keys),
            },
            "top_active_keys": active_keys[:20],
            "top_saturated_keys": saturated_keys[:20],
            "inactive_keys": inactive_keys,
            "denoise_recommendation": {
                "algorithm": "one_euro",
                "deadband": self.denoise_deadband,
                "min_cutoff": self.denoise_min_cutoff,
                "beta": self.denoise_beta,
                "d_cutoff": self.denoise_d_cutoff,
            },
            "notes": recommendations,
        }

    def _write_curve_report(self):
        if not self.curve_report_path or self.curve_report is None:
            return
        with open(self.curve_report_path, "w", encoding="utf-8", newline="\n") as curve_file:
            json.dump(self.curve_report, curve_file, ensure_ascii=False, indent=2)

    def _write_summary(self):
        mean_dt = _safe_mean(self.sample_deltas)
        std_dt = _safe_std(self.sample_deltas)
        average_hz = _round_float(1.0 / mean_dt) if mean_dt > 0.0 else 0.0
        summary = {
            "schema_version": SCHEMA_VERSION,
            "addon_name": ADDON_NAME,
            "capture_started_at": self.start_iso,
            "capture_stopped_at": _now_iso(),
            "stop_reason": self.stop_reason,
            "stop_reason_text": _reason_to_text(self.stop_reason),
            "desktop_session_dir": self.session_dir,
            "samples_written": self.sample_index,
            "configured_sample_interval_s": self.sample_interval,
            "measured_sample_interval_mean_s": _round_float(mean_dt),
            "measured_sample_interval_std_s": _round_float(std_dt),
            "capture_duration_s": _round_float(self.last_elapsed_s),
            "average_sample_rate_hz": average_hz,
            "captured_object_count": len(self.captured_objects),
            "captured_shape_key_count": len(self.flat_columns),
            "missing_target_counts": self.missing_target_counts,
            "export_denoised": self.export_denoised,
            "output_files": {
                "metadata": self.metadata_path,
                "samples_jsonl": self.samples_jsonl_path,
                "samples_flat_csv": self.samples_csv_path,
                "samples_denoised_jsonl": self.samples_denoised_jsonl_path if self.export_denoised else "",
                "samples_denoised_csv": self.samples_denoised_csv_path if self.export_denoised else "",
                "events_jsonl": self.events_path,
                "curve_report": self.curve_report_path,
            },
        }
        with open(self.summary_path, "w", encoding="utf-8", newline="\n") as summary_file:
            json.dump(summary, summary_file, ensure_ascii=False, indent=2)


class ShapeKeyReplaySession:
    def __init__(self, scene, context, settings):
        self.scene_name = scene.name
        self.context = context
        self.file_path = settings["replay_file_path"]
        self.preferred_source_object = str(settings["replay_source_object"] or "").strip()
        self.target_object_name = ""
        self.speed = max(float(settings["replay_speed"]), 0.01)
        self.strength = float(settings["replay_strength"])
        self.loop = bool(settings["replay_loop"])
        self.replay_key_set = _normalize_replay_key_set(settings.get("replay_key_set", REPLAY_KEY_SET_AUTO))
        self.effective_key_set = REPLAY_KEY_SET_ALL
        self.samples = []
        self.mapping_info = None
        self.source_object_name = ""
        self.start_perf_ns = 0
        self.is_running = False
        self.file_path_resolved = ""
        self.current_index = max(0, int(settings["replay_sample_index"]))

    def prepare(self):
        scene = self._scene_or_raise()
        target_object = _target_object_from_panel(self.context, _panel_api())
        if target_object is None:
            raise RuntimeError("请指定目标物体，或先选中一个带形态键的物体。")
        self.target_object_name = target_object.name
        self.file_path_resolved, self.samples, source_objects = _load_replay_samples(self.file_path)
        self.source_object_name = _choose_source_object_name(source_objects, self.preferred_source_object)
        self.mapping_info = _build_replay_mapping(target_object, self.samples[0], self.source_object_name, self.replay_key_set)
        self.effective_key_set = self.mapping_info.get("effective_key_set", REPLAY_KEY_SET_ALL)
        if hasattr(scene, "sk_replay_total_samples"):
            scene.sk_replay_total_samples = len(self.samples)
        if hasattr(scene, "sk_replay_total_source_keys"):
            scene.sk_replay_total_source_keys = int(self.mapping_info["source_count"])
        if hasattr(scene, "sk_replay_mapped_count"):
            scene.sk_replay_mapped_count = int(self.mapping_info["mapped_count"])
        if hasattr(scene, "sk_replay_source_object"):
            scene.sk_replay_source_object = self.source_object_name
        if hasattr(scene, "sk_replay_key_set"):
            scene.sk_replay_key_set = self.replay_key_set
        if self.mapping_info["mapped_count"] == 0:
            if hasattr(scene, "sk_replay_status"):
                scene.sk_replay_status = "未匹配到可用形态键"
            raise RuntimeError("未匹配到可用形态键。")
        if hasattr(scene, "sk_replay_status"):
            scene.sk_replay_status = (
                f"已加载：{self.mapping_info['mapped_count']}/"
                f"{self.mapping_info['source_count']} 键 -> {self.target_object_name}"
            )
        return self.mapping_info

    def start(self):
        scene = self._scene_or_raise()
        if self.mapping_info is None:
            self.prepare()
        current_index = self.current_index
        if hasattr(scene, "sk_replay_sample_index"):
            current_index = int(getattr(scene, "sk_replay_sample_index", current_index) or current_index)
        self.current_index = max(0, min(int(current_index), len(self.samples) - 1))
        self.start_perf_ns = time.perf_counter_ns() - int(
            float(self.samples[self.current_index].get("elapsed_s", 0.0)) * 1_000_000_000.0 / self.speed
        )
        self.is_running = True
        if hasattr(scene, "sk_replay_running"):
            scene.sk_replay_running = True
        self.apply_index(self.current_index)

    def stop(self, reason):
        self.is_running = False
        scene = bpy.data.scenes.get(self.scene_name)
        if scene is not None:
            if hasattr(scene, "sk_replay_running"):
                scene.sk_replay_running = False
            if hasattr(scene, "sk_replay_status"):
                reason_map = {
                    "user_stop": "用户停止",
                    "completed": "播放完成，已回到初始",
                    "replay_error": "回放错误",
                    "module_cleanup": "模块清理",
                }
                scene.sk_replay_status = f"回放已停止（{reason_map.get(str(reason), str(reason))}）"

    def timer_tick(self):
        if not self.is_running:
            return None
        scene = self._scene_or_raise()
        target_elapsed = (time.perf_counter_ns() - self.start_perf_ns) / 1_000_000_000.0 * self.speed
        while self.current_index + 1 < len(self.samples):
            next_elapsed = float(self.samples[self.current_index + 1].get("elapsed_s", 0.0))
            if next_elapsed > target_elapsed:
                break
            self.current_index += 1
            self.apply_index(self.current_index)
        if self.current_index >= len(self.samples) - 1:
            if self.loop:
                self.current_index = 0
                self.start_perf_ns = time.perf_counter_ns()
                self.apply_index(self.current_index)
                if hasattr(scene, "sk_replay_status"):
                    scene.sk_replay_status = "回放循环中"
                return 0.01
            return None
        return 0.01

    def apply_index(self, sample_index):
        scene = self._scene_or_raise()
        target_object = bpy.data.objects.get(self.target_object_name)
        if target_object is None:
            raise RuntimeError("目标物体已不存在。")
        clamped_index = max(0, min(int(sample_index), len(self.samples) - 1))
        applied_count = _apply_replay_mapping(
            target_object,
            self.samples[clamped_index],
            self.mapping_info,
            self.strength,
        )
        self.current_index = clamped_index
        if hasattr(scene, "sk_replay_sample_index"):
            scene.sk_replay_sample_index = clamped_index
        if hasattr(scene, "sk_replay_status"):
            scene.sk_replay_status = (
                f"已应用样本 {clamped_index + 1}/{len(self.samples)}"
                f"，映射 {applied_count}/{self.mapping_info['mapped_count']} 键"
            )
        return {
            "sample_index": clamped_index,
            "sample_count": len(self.samples),
            "applied_count": applied_count,
            "mapping_info": self.mapping_info,
        }

    def reset_to_initial(self):
        if not self.samples or self.mapping_info is None:
            return None
        return self.apply_index(0)

    def _scene_or_raise(self):
        scene = bpy.data.scenes.get(self.scene_name)
        if scene is None:
            raise RuntimeError("回放所在场景已不可用。")
        return scene


def _apply_scene_settings(scene, settings):
    if scene is None:
        return
    assignments = {
        "sk_capture_interval": float(settings["capture_interval"]),
        "sk_capture_include_basis": bool(settings["capture_include_basis"]),
        "sk_capture_only_selected": bool(settings["capture_only_selected"]),
        "sk_capture_skip_separator_keys": bool(settings["capture_skip_separator_keys"]),
        "sk_capture_arkit_only": bool(settings["capture_arkit_only"]),
        "sk_capture_export_denoised": bool(settings["capture_export_denoised"]),
        "sk_capture_denoise_deadband": float(settings["capture_denoise_deadband"]),
        "sk_capture_denoise_min_cutoff": float(settings["capture_denoise_min_cutoff"]),
        "sk_capture_denoise_beta": float(settings["capture_denoise_beta"]),
        "sk_capture_denoise_d_cutoff": float(settings["capture_denoise_d_cutoff"]),
        "sk_replay_file_path": str(settings["replay_file_path"] or ""),
        "sk_replay_source_object": str(settings["replay_source_object"] or ""),
        "sk_replay_strength": float(settings["replay_strength"]),
        "sk_replay_speed": float(settings["replay_speed"]),
        "sk_replay_loop": bool(settings["replay_loop"]),
        "sk_replay_sample_index": int(settings["replay_sample_index"]),
        "sk_replay_key_set": _normalize_replay_key_set(settings["replay_key_set"]),
    }
    for prop_name, value in assignments.items():
        if hasattr(scene, prop_name):
            try:
                setattr(scene, prop_name, value)
            except Exception:
                pass
    if hasattr(scene, "sk_replay_target_object"):
        try:
            scene.sk_replay_target_object = settings["replay_target_object"]
        except Exception:
            pass


def _sync_scene_runtime(scene, recorder=None, replayer=None, module_state=None):
    if scene is None:
        return
    if hasattr(scene, "sk_capture_running"):
        scene.sk_capture_running = recorder is not None
    if hasattr(scene, "sk_capture_status"):
        scene.sk_capture_status = (
            module_state.get("last_result", "") if module_state is not None and module_state.get("last_result", "") else ("采集中" if recorder is not None else "空闲")
        )
    if hasattr(scene, "sk_capture_sample_count"):
        scene.sk_capture_sample_count = int(getattr(recorder, "sample_count", 0) or 0)
    if hasattr(scene, "sk_capture_session_dir"):
        scene.sk_capture_session_dir = str(getattr(recorder, "session_dir", "") or "")
    if hasattr(scene, "sk_replay_running"):
        scene.sk_replay_running = replayer is not None
    if hasattr(scene, "sk_replay_status"):
        current_status = str(getattr(scene, "sk_replay_status", "") or "").strip()
        if not current_status:
            scene.sk_replay_status = "回放中" if replayer is not None else "空闲"
    if hasattr(scene, "sk_replay_sample_index") and replayer is not None:
        scene.sk_replay_sample_index = int(getattr(replayer, "current_index", 0) or 0)
    if hasattr(scene, "sk_replay_mapped_count"):
        scene.sk_replay_mapped_count = int(module_state.get("replay_mapped_count", 0) if module_state is not None else 0)
    if hasattr(scene, "sk_replay_total_source_keys"):
        scene.sk_replay_total_source_keys = int(module_state.get("replay_total_source_keys", 0) if module_state is not None else 0)
    if hasattr(scene, "sk_replay_total_samples"):
        scene.sk_replay_total_samples = int(module_state.get("replay_total_samples", 0) if module_state is not None else 0)


def _settings_from_ui(context, module, panel_api):
    scene = getattr(context, "scene", None) or getattr(bpy.context, "scene", None)
    return {
        "capture_interval": float(_scene_prop(scene, "sk_capture_interval", _config_get(module, "capture_interval", 1.0 / 60.0))),
        "capture_include_basis": bool(_scene_prop(scene, "sk_capture_include_basis", _config_get(module, "capture_include_basis", False))),
        "capture_only_selected": bool(_scene_prop(scene, "sk_capture_only_selected", _config_get(module, "capture_only_selected", False))),
        "capture_skip_separator_keys": bool(_scene_prop(scene, "sk_capture_skip_separator_keys", _config_get(module, "capture_skip_separator_keys", True))),
        "capture_arkit_only": bool(_scene_prop(scene, "sk_capture_arkit_only", _config_get(module, "capture_arkit_only", False))),
        "capture_export_denoised": bool(_scene_prop(scene, "sk_capture_export_denoised", _config_get(module, "capture_export_denoised", True))),
        "capture_denoise_deadband": float(_scene_prop(scene, "sk_capture_denoise_deadband", _config_get(module, "capture_denoise_deadband", 0.002))),
        "capture_denoise_min_cutoff": float(_scene_prop(scene, "sk_capture_denoise_min_cutoff", _config_get(module, "capture_denoise_min_cutoff", 1.2))),
        "capture_denoise_beta": float(_scene_prop(scene, "sk_capture_denoise_beta", _config_get(module, "capture_denoise_beta", 0.15))),
        "capture_denoise_d_cutoff": float(_scene_prop(scene, "sk_capture_denoise_d_cutoff", _config_get(module, "capture_denoise_d_cutoff", 1.0))),
        "replay_file_path": str(_scene_prop(scene, "sk_replay_file_path", _config_get(module, "replay_file_path", "")) or ""),
        "replay_source_object": "",
        "replay_strength": float(_scene_prop(scene, "sk_replay_strength", _config_get(module, "replay_strength", 1.0))),
        "replay_speed": float(_scene_prop(scene, "sk_replay_speed", _config_get(module, "replay_speed", 1.0))),
        "replay_loop": bool(_scene_prop(scene, "sk_replay_loop", _config_get(module, "replay_loop", False))),
        "replay_sample_index": int(_scene_prop(scene, "sk_replay_sample_index", _config_get(module, "replay_sample_index", 0))),
        "replay_key_set": _normalize_replay_key_set(_scene_prop(scene, "sk_replay_key_set", _config_get(module, "replay_key_set", REPLAY_KEY_SET_AUTO))),
        "replay_target_object": _scene_prop(scene, "sk_replay_target_object", None),
    }


def _persist_settings(module, settings):
    for key, value in settings.items():
        if key == "replay_target_object":
            continue
        _config_set(module, key, value)


def _stop_capture(reason):
    state = _runtime_state()
    recorder = state.get("recorder")
    callback = state.get("capture_callback")
    state["recorder"] = None
    state["capture_callback"] = None
    if callback is not None:
        try:
            bpy.app.timers.unregister(callback)
        except Exception:
            pass
    if recorder is not None:
        recorder.stop(reason)
    return recorder


def _stop_replay(reason, reset_to_initial=False):
    state = _runtime_state()
    replayer = state.get("replayer")
    callback = state.get("replay_callback")
    state["replayer"] = None
    state["replay_callback"] = None
    if callback is not None:
        try:
            bpy.app.timers.unregister(callback)
        except Exception:
            pass
    if replayer is not None and reset_to_initial:
        try:
            replayer.reset_to_initial()
        except Exception:
            pass
    if replayer is not None:
        replayer.stop(reason)
    return replayer


def _register_capture_timer():
    state = _runtime_state()
    token_recorder = state.get("recorder")

    def _tick():
        if state.get("recorder") is not token_recorder or token_recorder is None:
            return None
        try:
            return token_recorder.timer_tick()
        except Exception as exc:
            _status(f"采集失败: {exc}", level="ERROR")
            _stop_capture("capture_error")
            return None

    state["capture_callback"] = _tick
    bpy.app.timers.register(_tick, first_interval=max(0.001, token_recorder.sample_interval))


def _register_replay_timer():
    state = _runtime_state()
    token_replayer = state.get("replayer")

    def _tick():
        if state.get("replayer") is not token_replayer or token_replayer is None:
            return None
        try:
            next_interval = token_replayer.timer_tick()
            if next_interval is None:
                info = token_replayer.reset_to_initial()
                _status(
                    f"回放完成，已回到初始: {info['sample_index'] + 1}/{info['sample_count']}，映射 {info['applied_count']} 键",
                    level="OK",
                )
                _stop_replay("completed")
                return None
            return next_interval
        except Exception as exc:
            _status(f"回放失败: {exc}", level="ERROR")
            _stop_replay("replay_error")
            return None

    state["replay_callback"] = _tick
    bpy.app.timers.register(_tick, first_interval=0.01)


def run(context, scene, workflow, module):
    settings = _settings_from_ui(context, module, _panel_api())
    _persist_settings(module, settings)
    if _runtime_state().get("recorder") is not None:
        return on_panel_action("STOP_CAPTURE", context, scene, workflow, module, _panel_api(), _module_state())
    return on_panel_action("START_CAPTURE", context, scene, workflow, module, _panel_api(), _module_state())


def draw_panel(layout, context, scene, workflow, module, panel_api, module_state):
    settings = _settings_from_ui(context, module, panel_api)
    runtime = _runtime_state()
    recorder = runtime.get("recorder")
    replayer = runtime.get("replayer")
    capture_running = recorder is not None
    replay_running = replayer is not None
    capture_status_text = module_state.get("last_result", "") if module_state is not None else ""
    if not capture_status_text:
        capture_status_text = "采集中" if capture_running else "空闲"
    capture_sample_count = int(getattr(recorder, "sample_count", 0) or 0)
    capture_session_dir = str(getattr(recorder, "session_dir", "") or "")
    replay_status_text = str(getattr(scene, "sk_replay_status", "") or ("回放中" if replay_running else "空闲"))
    replay_mapped_count = int(getattr(scene, "sk_replay_mapped_count", 0) or 0)
    replay_total_source_keys = int(getattr(scene, "sk_replay_total_source_keys", 0) or 0)
    replay_total_samples = int(getattr(scene, "sk_replay_total_samples", 0) or 0)

    capture_box = panel_api.section(layout, "采集设置", icon="REC")
    panel_api.prop(capture_box, scene, "sk_capture_interval")
    panel_api.prop(capture_box, scene, "sk_capture_include_basis")
    panel_api.prop(capture_box, scene, "sk_capture_only_selected")
    panel_api.prop(capture_box, scene, "sk_capture_skip_separator_keys")
    panel_api.prop(capture_box, scene, "sk_capture_arkit_only")

    denoise_box = panel_api.section(layout, "曲线优化", icon="MODIFIER_DATA", enabled=not capture_running)
    panel_api.prop(denoise_box, scene, "sk_capture_export_denoised")
    if scene.sk_capture_export_denoised:
        panel_api.prop(denoise_box, scene, "sk_capture_denoise_deadband")
        panel_api.prop(denoise_box, scene, "sk_capture_denoise_min_cutoff")
        panel_api.prop(denoise_box, scene, "sk_capture_denoise_beta")
        panel_api.prop(denoise_box, scene, "sk_capture_denoise_d_cutoff")

    action_row = panel_api.row(layout, align=True)
    try:
        action_row.scale_y = 1.2
    except Exception:
        pass
    if capture_running:
        panel_api.draw_button(action_row, "STOP_CAPTURE", "结束采集", icon="CANCEL")
    else:
        panel_api.draw_button(action_row, "START_CAPTURE", "开始采集", icon="PLAY")

    info_box = panel_api.section(layout, "采集状态", icon="INFO")
    panel_api.draw_key_value(info_box, "状态", capture_status_text)
    panel_api.draw_key_value(info_box, "样本数", capture_sample_count)
    if capture_session_dir:
        session_row = info_box.row()
        try:
            session_row.enabled = False
        except Exception:
            pass
        panel_api.label(session_row, "会话目录")
        panel_api.label(session_row, capture_session_dir, icon="FILE_FOLDER")

    faceit_status = _get_faceit_status(scene)
    faceit_box = panel_api.section(layout, "Faceit", icon="RADIOBUT_ON" if faceit_status["available"] else "RADIOBUT_OFF")
    if faceit_status["available"]:
        receiver_text = "开启" if faceit_status["receiver_enabled"] else "关闭"
        source_text = faceit_status["live_source"] or "-"
        panel_api.draw_key_value(faceit_box, "接收器", receiver_text)
        panel_api.draw_key_value(faceit_box, "来源", source_text)
    else:
        panel_api.label(faceit_box, "未检测到 Faceit 接收器属性", icon="INFO")

    replay_box = panel_api.section(layout, "数据投射/回放", icon="FILE_REFRESH")
    panel_api.prop(replay_box, scene, "sk_replay_file_path", text="记录文件")
    panel_api.prop(replay_box, scene, "sk_replay_strength")
    panel_api.prop(replay_box, scene, "sk_replay_speed")
    panel_api.prop(replay_box, scene, "sk_replay_loop")
    panel_api.prop(replay_box, scene, "sk_replay_sample_index")

    replay_key_set = _normalize_replay_key_set(settings.get("replay_key_set", REPLAY_KEY_SET_AUTO))
    replay_target_object = _target_object_from_panel(context, panel_api)
    target_family_counts = _shape_names_family_counts(_object_shape_key_names(replay_target_object))
    source_family_counts = module_state.get("replay_source_family_counts", {}) if module_state is not None else {}
    effective_key_set = _effective_replay_key_set(replay_key_set, source_family_counts, target_family_counts)
    key_set_label = REPLAY_KEY_SET_LABELS.get(replay_key_set, replay_key_set)
    effective_label = REPLAY_KEY_SET_LABELS.get(effective_key_set, effective_key_set)
    arkit_available = int(target_family_counts.get(REPLAY_KEY_SET_ARKIT, 0) or 0) > 0
    mmd_available = int(target_family_counts.get(REPLAY_KEY_SET_MMD, 0) or 0) > 0
    panel_api.draw_key_value(replay_box, "识别套件", _format_family_counts(target_family_counts))
    panel_api.draw_key_value(replay_box, "验证套件", f"{key_set_label} -> {effective_label}" if replay_key_set == REPLAY_KEY_SET_AUTO else key_set_label)
    key_set_row = panel_api.row(replay_box, align=True)
    auto_button_row = key_set_row.row(align=True)
    panel_api.draw_button(
        auto_button_row,
        "SET_REPLAY_KEY_SET::AUTO",
        ("[自动]" if replay_key_set == REPLAY_KEY_SET_AUTO else "自动"),
        icon="FILE_REFRESH",
    )
    arkit_button_row = key_set_row.row(align=True)
    try:
        arkit_button_row.enabled = arkit_available
    except Exception:
        pass
    panel_api.draw_button(
        arkit_button_row,
        "SET_REPLAY_KEY_SET::ARKIT",
        ("[ARKit]" if replay_key_set == REPLAY_KEY_SET_ARKIT else "ARKit"),
        icon="SHAPEKEY_DATA",
    )
    mmd_button_row = key_set_row.row(align=True)
    try:
        mmd_button_row.enabled = mmd_available
    except Exception:
        pass
    panel_api.draw_button(
        mmd_button_row,
        "SET_REPLAY_KEY_SET::MMD",
        ("[MMD]" if replay_key_set == REPLAY_KEY_SET_MMD else "MMD"),
        icon="OUTLINER_OB_ARMATURE",
    )

    replay_actions = panel_api.row(replay_box, align=True)
    panel_api.draw_button(replay_actions, "ANALYZE_REPLAY", "分析映射", icon="VIEWZOOM")
    panel_api.draw_button(replay_actions, "APPLY_REPLAY_SAMPLE", "应用当前样本", icon="IMPORT")

    replay_actions_2 = panel_api.row(replay_box, align=True)
    if replay_running:
        panel_api.draw_button(replay_actions_2, "STOP_REPLAY", "停止回放", icon="CANCEL")
    else:
        panel_api.draw_button(replay_actions_2, "START_REPLAY", "开始回放", icon="PLAY")

    replay_info = panel_api.section(replay_box, "回放状态", icon="INFO")
    panel_api.draw_key_value(replay_info, "状态", replay_status_text)
    panel_api.draw_key_value(replay_info, "映射键", f"{replay_mapped_count}/{replay_total_source_keys}")
    panel_api.draw_key_value(replay_info, "样本总数", replay_total_samples)

    panel_api.draw_status(layout)

def on_panel_action(action, context, scene, workflow, module, panel_api, module_state):
    settings = _settings_from_ui(context, module, panel_api)
    _persist_settings(module, settings)
    state = _runtime_state()

    if action.startswith("SET_REPLAY_KEY_SET::"):
        replay_key_set = _normalize_replay_key_set(action.split("::", 1)[1])
        if replay_key_set in {REPLAY_KEY_SET_ARKIT, REPLAY_KEY_SET_MMD}:
            target_counts = _shape_names_family_counts(_object_shape_key_names(_target_object_from_panel(context, panel_api)))
            if int(target_counts.get(replay_key_set, 0) or 0) <= 0:
                _status(f"当前活动物体未识别到 {REPLAY_KEY_SET_LABELS.get(replay_key_set, replay_key_set)} 形态键", level="WARNING")
                return {"CANCELLED"}
        _config_set(module, "replay_key_set", replay_key_set)
        if hasattr(scene, "sk_replay_key_set"):
            scene.sk_replay_key_set = replay_key_set
        settings["replay_key_set"] = replay_key_set
        if state.get("replayer") is not None:
            _stop_replay("user_stop", reset_to_initial=True)
        _status(f"验证套件已切换为 {REPLAY_KEY_SET_LABELS.get(replay_key_set, replay_key_set)}", level="OK")
        return {"FINISHED"}

    if action == "START_CAPTURE":
        _stop_capture("module_cleanup")
        recorder = ShapeKeyCaptureRecorder(scene, settings)
        recorder.start()
        state["recorder"] = recorder
        _register_capture_timer()
        _status(f"采集已开始: {recorder.session_dir}", level="OK")
        return {"FINISHED"}

    if action == "STOP_CAPTURE":
        recorder = _stop_capture("user_stop")
        if recorder is None:
            _status("当前没有采集在运行", level="INFO")
        else:
            _status(f"采集已停止: {recorder.session_dir}", level="OK")
        return {"FINISHED"}

    if action == "ANALYZE_REPLAY":
        replay = ShapeKeyReplaySession(scene, context, settings)
        mapping_info = replay.prepare()
        module_state.set("replay_total_samples", len(replay.samples))
        module_state.set("replay_total_source_keys", mapping_info["source_count"])
        module_state.set("replay_mapped_count", mapping_info["mapped_count"])
        module_state.set("replay_source_family_counts", mapping_info.get("source_family_counts", {}))
        module_state.set("replay_target_family_counts", mapping_info.get("target_family_counts", {}))
        module_state.set("replay_effective_key_set", mapping_info.get("effective_key_set", REPLAY_KEY_SET_ALL))
        module_state.set("last_result", f"已匹配 {mapping_info['mapped_count']}/{mapping_info['source_count']} 个形态键")
        _status(f"已匹配 {mapping_info['mapped_count']}/{mapping_info['source_count']} 个形态键", level="OK")
        return {"FINISHED"}

    if action == "APPLY_REPLAY_SAMPLE":
        replay = ShapeKeyReplaySession(scene, context, settings)
        replay.prepare()
        result = replay.apply_index(settings["replay_sample_index"])
        module_state.set("replay_total_samples", result["sample_count"])
        module_state.set("replay_total_source_keys", result["mapping_info"]["source_count"])
        module_state.set("replay_mapped_count", result["mapping_info"]["mapped_count"])
        module_state.set("replay_source_family_counts", result["mapping_info"].get("source_family_counts", {}))
        module_state.set("replay_target_family_counts", result["mapping_info"].get("target_family_counts", {}))
        module_state.set("replay_effective_key_set", result["mapping_info"].get("effective_key_set", REPLAY_KEY_SET_ALL))
        module_state.set("last_result", f"已应用样本 {result['sample_index'] + 1}/{result['sample_count']}，映射 {result['applied_count']} 键")
        _status(
            f"已应用样本 {result['sample_index'] + 1}/{result['sample_count']}，映射 {result['applied_count']} 键",
            level="OK",
        )
        return {"FINISHED"}

    if action == "START_REPLAY":
        _stop_replay("module_cleanup")
        replay = ShapeKeyReplaySession(scene, context, settings)
        replay.prepare()
        replay.start()
        state["replayer"] = replay
        _register_replay_timer()
        module_state.set("replay_total_samples", len(replay.samples))
        module_state.set("replay_total_source_keys", replay.mapping_info["source_count"])
        module_state.set("replay_mapped_count", replay.mapping_info["mapped_count"])
        module_state.set("replay_source_family_counts", replay.mapping_info.get("source_family_counts", {}))
        module_state.set("replay_target_family_counts", replay.mapping_info.get("target_family_counts", {}))
        module_state.set("replay_effective_key_set", replay.mapping_info.get("effective_key_set", REPLAY_KEY_SET_ALL))
        module_state.set("last_result", "回放已开始")
        _status("回放已开始", level="OK")
        return {"FINISHED"}

    if action == "STOP_REPLAY":
        replay = _stop_replay("user_stop", reset_to_initial=True)
        if replay is None:
            _status("当前没有回放在运行", level="INFO")
        else:
            _status("回放已停止", level="OK")
        return {"FINISHED"}

    if action.startswith("FIELD_WRITE::"):
        return {"FINISHED"}

    return {"FINISHED"}


def cleanup_runtime(scene=None, workflow=None, module=None, module_state=None):
    _stop_capture("module_cleanup")
    _stop_replay("module_cleanup")
