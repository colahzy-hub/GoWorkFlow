bl_info = {
    "name": "\u5f62\u6001\u952e\u5b9e\u65f6\u91c7\u96c6\u8bb0\u5f55\u5668",
    "author": "OpenAI Codex",
    "version": (1, 1, 0),
    "blender": (3, 0, 0),
    "location": "3D View > Sidebar > \u5f62\u6001\u91c7\u96c6",
    "description": "\u5b9e\u65f6\u91c7\u96c6\u6df7\u5408\u540e\u7684\u5f62\u6001\u952e\u6570\u503c\u5e76\u81ea\u52a8\u5bfc\u51fa\u539f\u59cb\u4e0e\u964d\u566a\u66f2\u7ebf\u3002",
    "category": "Animation",
}

import csv
import getpass
import json
import math
import os
import socket
import time
from datetime import datetime

import bpy
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel


ADDON_NAME = "\u5f62\u6001\u952e\u5b9e\u65f6\u91c7\u96c6\u8bb0\u5f55\u5668"
SCHEMA_VERSION = 2
SESSION_PREFIX = "blender_shape_key_capture"
FLUSH_EVERY_SAMPLES = 10
EPSILON = 1e-8

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

RECORDER = None
REPLAYER = None


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


def _get_scene_by_name(scene_name):
    return bpy.data.scenes.get(scene_name)


def _reason_to_text(reason):
    reason_map = {
        "user_stop": "\u624b\u52a8\u505c\u6b62",
        "save_pre": "\u4fdd\u5b58\u524d\u81ea\u52a8\u505c\u6b62",
        "load_pre": "\u8f7d\u5165\u524d\u81ea\u52a8\u505c\u6b62",
        "quit_pre": "\u9000\u51fa\u524d\u81ea\u52a8\u505c\u6b62",
        "capture_error": "\u91c7\u96c6\u5f02\u5e38\u505c\u6b62",
        "addon_unload": "\u63d2\u4ef6\u5378\u8f7d\u65f6\u505c\u6b62",
    }
    return reason_map.get(reason, reason)


def _get_faceit_status(scene):
    if scene is None:
        return {
            "available": False,
            "receiver_enabled": False,
            "live_source": "",
        }
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
    stripped = name.strip()
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
    if not values:
        return 0.0
    return sum(values) / len(values)


def _safe_std(values):
    if len(values) < 2:
        return 0.0
    mean_value = _safe_mean(values)
    variance = sum((value - mean_value) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def _quality_label_from_metrics(mean_hz, jitter_cv, active_key_count):
    if mean_hz >= 50.0 and jitter_cv <= 0.08 and active_key_count >= 30:
        return "\u9ad8"
    if mean_hz >= 28.0 and jitter_cv <= 0.20 and active_key_count >= 20:
        return "\u4e2d"
    return "\u4f4e"


def _normalize_shape_key_name(name):
    normalized = []
    previous_is_lower = False
    for char in str(name):
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


def _target_object_from_scene(scene, context=None):
    target_object = getattr(scene, "sk_replay_target_object", None)
    if target_object is not None:
        return target_object
    if context is not None:
        obj = getattr(context, "active_object", None)
        if obj is not None:
            return obj
    return getattr(bpy.context, "active_object", None)


def _target_key_blocks(target_object):
    if target_object is None or target_object.type != "MESH":
        return None
    shape_keys = getattr(target_object.data, "shape_keys", None)
    if shape_keys is None:
        return None
    return shape_keys.key_blocks


def _resolve_replay_file_path(file_path):
    resolved = bpy.path.abspath(file_path).strip()
    if not resolved:
        raise RuntimeError("\u8bf7\u6307\u5b9a\u8981\u6295\u5c04\u7684 samples.jsonl \u6216 samples_denoised.jsonl \u6587\u4ef6\u3002")
    if not os.path.isfile(resolved):
        raise RuntimeError(f"\u627e\u4e0d\u5230\u56de\u653e\u6587\u4ef6\uff1a{resolved}")
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
        raise RuntimeError("\u56de\u653e\u6587\u4ef6\u4e3a\u7a7a\u3002")
    first_sample = samples[0]
    source_objects = list(first_sample.get("objects", {}).keys())
    if not source_objects:
        raise RuntimeError("\u56de\u653e\u6587\u4ef6\u4e2d\u6ca1\u6709 objects \u6570\u636e\u3002")
    return resolved_path, samples, source_objects


def _choose_source_object_name(source_objects, preferred_name):
    if preferred_name and preferred_name in source_objects:
        return preferred_name
    return source_objects[0]


def _find_best_target_name(source_name, key_blocks, used_target_names):
    if key_blocks is None:
        return None
    if key_blocks.get(source_name) is not None and source_name not in used_target_names:
        return source_name

    normalized_to_names = {}
    token_entries = []
    for key_block in key_blocks:
        target_name = key_block.name
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
    if best_score >= 0.60:
        return best_name
    return None


def _build_replay_mapping(target_object, sample, source_object_name):
    key_blocks = _target_key_blocks(target_object)
    if key_blocks is None:
        raise RuntimeError("\u76ee\u6807\u7269\u4f53\u6ca1\u6709\u5f62\u6001\u952e\u3002")
    source_values = sample.get("objects", {}).get(source_object_name)
    if source_values is None:
        raise RuntimeError(f"\u56de\u653e\u6587\u4ef6\u4e2d\u6ca1\u6709\u6e90\u5bf9\u8c61\uff1a{source_object_name}")

    mapping = []
    used_target_names = set()
    unmapped = []
    for source_name in source_values.keys():
        target_name = _find_best_target_name(source_name, key_blocks, used_target_names)
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
        "source_count": len(source_values),
        "unmapped_source_names": unmapped,
        "mapping": mapping,
    }


def _apply_replay_mapping(target_object, sample, mapping_info, strength):
    key_blocks = _target_key_blocks(target_object)
    if key_blocks is None:
        raise RuntimeError("\u76ee\u6807\u7269\u4f53\u6ca1\u6709\u5f62\u6001\u952e\u3002")
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


def project_capture_file_to_object(file_path, target_object_name, sample_index=0, source_object_name="", strength=1.0):
    resolved_path, samples, source_objects = _load_replay_samples(file_path)
    target_object = bpy.data.objects.get(target_object_name)
    if target_object is None:
        raise RuntimeError(f"\u627e\u4e0d\u5230\u76ee\u6807\u7269\u4f53\uff1a{target_object_name}")
    source_object_name = _choose_source_object_name(source_objects, source_object_name)
    clamped_index = max(0, min(int(sample_index), len(samples) - 1))
    mapping_info = _build_replay_mapping(target_object, samples[0], source_object_name)
    if mapping_info["mapped_count"] == 0:
        raise RuntimeError("\u6ca1\u6709\u5339\u914d\u5230\u4efb\u4f55\u53ef\u7528\u7684\u5f62\u6001\u952e\u3002")
    applied_count = _apply_replay_mapping(target_object, samples[clamped_index], mapping_info, strength)
    return {
        "file_path": resolved_path,
        "sample_count": len(samples),
        "sample_index": clamped_index,
        "applied_count": applied_count,
        "mapping_info": mapping_info,
    }


def _stop_global_capture(reason):
    global RECORDER
    if RECORDER is None:
        return
    recorder = RECORDER
    RECORDER = None
    recorder.stop(reason)


def _stop_global_replay(reason):
    global REPLAYER
    if REPLAYER is None:
        return
    replay_session = REPLAYER
    REPLAYER = None
    replay_session.stop(reason)


def _capture_timer_tick():
    if RECORDER is None:
        return None
    return RECORDER.timer_tick()


def _replay_timer_tick():
    if REPLAYER is None:
        return None
    return REPLAYER.timer_tick()


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


@persistent
def _stop_capture_before_save(_dummy):
    _stop_global_capture("save_pre")
    _stop_global_replay("save_pre")


@persistent
def _stop_capture_before_load(_dummy):
    _stop_global_capture("load_pre")
    _stop_global_replay("load_pre")


@persistent
def _stop_capture_before_quit(_dummy):
    _stop_global_capture("quit_pre")
    _stop_global_replay("quit_pre")


class ShapeKeyCaptureRecorder:
    def __init__(self, scene):
        self.scene_name = scene.name
        self.sample_interval = max(float(scene.sk_capture_interval), 0.001)
        self.include_basis = bool(scene.sk_capture_include_basis)
        self.only_selected = bool(scene.sk_capture_only_selected)
        self.skip_separator_keys = bool(scene.sk_capture_skip_separator_keys)
        self.arkit_only = bool(scene.sk_capture_arkit_only)
        self.export_denoised = bool(scene.sk_capture_export_denoised)
        self.denoise_deadband = max(float(scene.sk_capture_denoise_deadband), 0.0)
        self.denoise_min_cutoff = max(float(scene.sk_capture_denoise_min_cutoff), 0.0001)
        self.denoise_beta = max(float(scene.sk_capture_denoise_beta), 0.0)
        self.denoise_d_cutoff = max(float(scene.sk_capture_denoise_d_cutoff), 0.0001)
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
        self.last_unix_ns = 0
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
            raise RuntimeError("\u6ca1\u6709\u627e\u5230\u53ef\u91c7\u96c6\u7684\u5f62\u6001\u952e\u5bf9\u8c61\u3002")

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

        try:
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
            scene.sk_capture_running = True
            scene.sk_capture_session_dir = self.session_dir
            scene.sk_capture_status = "\u91c7\u96c6\u4e2d"
            scene.sk_capture_sample_count = 0
            self._write_sample()
            if not bpy.app.timers.is_registered(_capture_timer_tick):
                bpy.app.timers.register(
                    _capture_timer_tick,
                    first_interval=self.sample_interval,
                    persistent=True,
                )
        except Exception:
            self.is_running = False
            self._close_streams()
            raise

    def stop(self, reason):
        self.stop_reason = reason
        self.is_running = False
        if bpy.app.timers.is_registered(_capture_timer_tick):
            try:
                bpy.app.timers.unregister(_capture_timer_tick)
            except ValueError:
                pass

        scene = _get_scene_by_name(self.scene_name)
        if self._events_file is not None:
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

        if scene is not None:
            scene.sk_capture_running = False
            scene.sk_capture_status = f"\u5df2\u505c\u6b62\uff08{_reason_to_text(reason)}\uff09"
            scene.sk_capture_sample_count = self.sample_index

    def timer_tick(self):
        if not self.is_running:
            return None
        try:
            self._write_sample()
        except Exception as exc:
            print(f"[{ADDON_NAME}] capture error: {exc}")
            self._log_event(
                "capture_error",
                {
                    "message": str(exc),
                },
            )
            _stop_global_capture("capture_error")
            return None
        return self.sample_interval

    def _scene_or_raise(self):
        scene = _get_scene_by_name(self.scene_name)
        if scene is None:
            raise RuntimeError("\u5f53\u524d\u91c7\u96c6\u7ed1\u5b9a\u7684\u573a\u666f\u5df2\u4e0d\u53ef\u7528\u3002")
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
        faceit_status = _get_faceit_status(scene)
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "addon_name": ADDON_NAME,
            "addon_version": list(bl_info["version"]),
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
            "sample_fields": {
                "timing": [
                    "sample_index",
                    "timestamp_unix_ns",
                    "timestamp_perf_ns",
                    "timestamp_iso",
                    "elapsed_s",
                    "sample_delta_s",
                ],
                "scene": [
                    "scene_frame",
                    "scene_subframe",
                    "scene_frame_float",
                ],
                "live_status": [
                    "faceit_receiver_enabled",
                    "faceit_live_source",
                ],
                "object_payload": "objects -> object_name -> shape_key_name -> value",
            },
            "flat_csv_columns": self.flat_columns,
            "faceit_status_at_start": faceit_status,
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
        self.last_unix_ns = timestamp_unix_ns
        self.last_perf_ns = timestamp_perf_ns
        self.last_elapsed_s = elapsed_s

        if self.sample_index % FLUSH_EVERY_SAMPLES == 0:
            self._flush_streams()

        scene.sk_capture_sample_count = self.sample_index
        scene.sk_capture_status = "\u91c7\u96c6\u4e2d"

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
        count = self.missing_target_counts.get(key, 0) + 1
        self.missing_target_counts[key] = count
        if count == 1:
            self._log_event(
                "missing_target_first_seen",
                {
                    "object_name": object_name,
                    "shape_key_name": shape_name,
                    "reason": reason,
                },
            )

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
        with open(self.samples_jsonl_path, "r", encoding="utf-8") as raw_file, \
                open(self.samples_denoised_jsonl_path, "w", encoding="utf-8", newline="\n") as jsonl_out, \
                open(self.samples_denoised_csv_path, "w", encoding="utf-8", newline="") as csv_out:
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

                denoised_sample = {
                    "sample_index": sample["sample_index"],
                    "timestamp_unix_ns": sample["timestamp_unix_ns"],
                    "timestamp_perf_ns": sample["timestamp_perf_ns"],
                    "timestamp_iso": sample["timestamp_iso"],
                    "elapsed_s": sample["elapsed_s"],
                    "sample_delta_s": sample.get("sample_delta_s", 0.0),
                    "scene_frame": sample["scene_frame"],
                    "scene_subframe": sample["scene_subframe"],
                    "scene_frame_float": sample["scene_frame_float"],
                    "faceit_receiver_enabled": sample["faceit_receiver_enabled"],
                    "faceit_live_source": sample["faceit_live_source"],
                    "denoise": {
                        "algorithm": "one_euro",
                        "deadband": self.denoise_deadband,
                        "min_cutoff": self.denoise_min_cutoff,
                        "beta": self.denoise_beta,
                        "d_cutoff": self.denoise_d_cutoff,
                    },
                    "objects": denoised_objects,
                }
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
        quality_label = _quality_label_from_metrics(mean_hz, jitter_cv, len(active_keys))

        recommendations = []
        if mean_hz < 45.0:
            recommendations.append("\u5b9e\u9645\u91c7\u6837\u7387\u504f\u4f4e\uff0c\u66f4\u9002\u5408\u4e2d\u7b49\u7ec6\u5ea6\u66f2\u7ebf\u62df\u5408\uff0c\u4e0d\u9002\u5408\u6781\u7ec6\u5fae\u8868\u60c5\u3002")
        if jitter_cv > 0.12:
            recommendations.append("\u91c7\u6837\u95f4\u9694\u5b58\u5728\u660e\u663e\u6296\u52a8\uff0c\u5efa\u8bae\u4f18\u5148\u4f7f\u7528 One Euro \u6216 EMA \u964d\u566a\u518d\u8f6c FCurve\u3002")
        if any(item["max_abs"] >= 0.98 for item in active_keys):
            recommendations.append("\u90e8\u5206\u952e\u4f4d\u9891\u7e41\u6253\u5230 1.0\uff0c\u8fd9\u7c7b\u6837\u672c\u66f4\u50cf\u9884\u8bbe/\u6d4b\u8bd5\u89e6\u53d1\uff0c\u4e0d\u662f\u6700\u4f73\u7684\u81ea\u7136\u4eba\u8138\u8bad\u7ec3\u96c6\u3002")
        if not recommendations:
            recommendations.append("\u91c7\u6837\u7387\u4e0e\u6d3b\u8dc3\u952e\u6570\u5747\u5728\u53ef\u7528\u533a\u95f4\uff0c\u53ef\u76f4\u63a5\u7528\u4e8e\u66f2\u7ebf\u62df\u5408\u3002")

        return {
            "schema_version": SCHEMA_VERSION,
            "capture_quality": {
                "label": quality_label,
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
        if not self.summary_path:
            return
        capture_duration_s = _round_float(self.last_elapsed_s)
        average_hz = 0.0
        mean_dt = _safe_mean(self.sample_deltas)
        std_dt = _safe_std(self.sample_deltas)
        if mean_dt > 0.0:
            average_hz = _round_float(1.0 / mean_dt)
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
            "capture_duration_s": capture_duration_s,
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
    def __init__(self, scene, context=None):
        self.scene_name = scene.name
        self.context = context
        self.file_path = scene.sk_replay_file_path
        self.preferred_source_object = scene.sk_replay_source_object.strip()
        self.target_object_name = ""
        self.speed = max(float(scene.sk_replay_speed), 0.01)
        self.strength = float(scene.sk_replay_strength)
        self.loop = bool(scene.sk_replay_loop)
        self.samples = []
        self.mapping_info = None
        self.source_object_name = ""
        self.start_perf_ns = 0
        self.is_running = False
        self.file_path_resolved = ""
        self.current_index = 0

    def prepare(self):
        scene = self._scene_or_raise()
        target_object = _target_object_from_scene(scene, self.context)
        if target_object is None:
            raise RuntimeError("\u8bf7\u6307\u5b9a\u76ee\u6807\u7269\u4f53\uff0c\u6216\u5148\u9009\u4e2d\u4e00\u4e2a\u5e26\u5f62\u6001\u952e\u7684\u7269\u4f53\u3002")
        self.target_object_name = target_object.name
        self.file_path_resolved, self.samples, source_objects = _load_replay_samples(self.file_path)
        self.source_object_name = _choose_source_object_name(source_objects, self.preferred_source_object)
        self.mapping_info = _build_replay_mapping(target_object, self.samples[0], self.source_object_name)
        scene.sk_replay_total_samples = len(self.samples)
        scene.sk_replay_total_source_keys = self.mapping_info["source_count"]
        scene.sk_replay_mapped_count = self.mapping_info["mapped_count"]
        scene.sk_replay_source_object = self.source_object_name
        if self.mapping_info["mapped_count"] == 0:
            scene.sk_replay_status = "\u672a\u5339\u914d\u5230\u53ef\u7528\u5f62\u6001\u952e"
            raise RuntimeError("\u672a\u5339\u914d\u5230\u53ef\u7528\u5f62\u6001\u952e\u3002")
        scene.sk_replay_status = (
            f"\u5df2\u52a0\u8f7d\uff1a{self.mapping_info['mapped_count']}/"
            f"{self.mapping_info['source_count']} \u952e -> {self.target_object_name}"
        )
        return self.mapping_info

    def start(self):
        scene = self._scene_or_raise()
        if self.mapping_info is None:
            self.prepare()
        self.current_index = max(0, min(int(scene.sk_replay_sample_index), len(self.samples) - 1))
        self.start_perf_ns = time.perf_counter_ns() - int(
            float(self.samples[self.current_index].get("elapsed_s", 0.0)) * 1_000_000_000.0 / self.speed
        )
        self.is_running = True
        scene.sk_replay_running = True
        self.apply_index(self.current_index)
        if not bpy.app.timers.is_registered(_replay_timer_tick):
            bpy.app.timers.register(_replay_timer_tick, first_interval=0.01, persistent=True)

    def stop(self, reason):
        self.is_running = False
        if bpy.app.timers.is_registered(_replay_timer_tick):
            try:
                bpy.app.timers.unregister(_replay_timer_tick)
            except ValueError:
                pass
        scene = _get_scene_by_name(self.scene_name)
        if scene is not None:
            scene.sk_replay_running = False
            scene.sk_replay_status = f"\u56de\u653e\u5df2\u505c\u6b62\uff08{_reason_to_text(reason)}\uff09"

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
                scene.sk_replay_status = "\u56de\u653e\u5faa\u73af\u4e2d"
                return 0.01
            _stop_global_replay("user_stop")
            return None

        return 0.01

    def apply_index(self, sample_index):
        scene = self._scene_or_raise()
        target_object = bpy.data.objects.get(self.target_object_name)
        if target_object is None:
            raise RuntimeError("\u76ee\u6807\u7269\u4f53\u5df2\u4e0d\u5b58\u5728\u3002")
        clamped_index = max(0, min(int(sample_index), len(self.samples) - 1))
        applied_count = _apply_replay_mapping(
            target_object,
            self.samples[clamped_index],
            self.mapping_info,
            self.strength,
        )
        scene.sk_replay_sample_index = clamped_index
        scene.sk_replay_status = (
            f"\u5df2\u5e94\u7528\u6837\u672c {clamped_index + 1}/{len(self.samples)}"
            f"\uff0c\u6620\u5c04 {applied_count}/{self.mapping_info['mapped_count']} \u952e"
        )
        return applied_count

    def _scene_or_raise(self):
        scene = _get_scene_by_name(self.scene_name)
        if scene is None:
            raise RuntimeError("\u56de\u653e\u6240\u5728\u573a\u666f\u5df2\u4e0d\u53ef\u7528\u3002")
        return scene


class SKCAPTURE_OT_start(Operator):
    bl_idname = "sk_capture.start"
    bl_label = "\u5f00\u59cb\u91c7\u96c6"
    bl_description = "\u5f00\u59cb\u8bb0\u5f55\u5f53\u524d\u6df7\u5408\u540e\u7684\u5f62\u6001\u952e\u6570\u503c"

    @classmethod
    def poll(cls, context):
        return RECORDER is None

    def execute(self, context):
        global RECORDER
        scene = context.scene
        recorder = ShapeKeyCaptureRecorder(scene)
        try:
            recorder.start()
        except Exception as exc:
            scene.sk_capture_running = False
            scene.sk_capture_status = f"\u542f\u52a8\u5931\u8d25\uff1a{exc}"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        RECORDER = recorder
        self.report({"INFO"}, f"\u91c7\u96c6\u5df2\u5f00\u59cb\uff1a{recorder.session_dir}")
        return {"FINISHED"}


class SKCAPTURE_OT_stop(Operator):
    bl_idname = "sk_capture.stop"
    bl_label = "\u7ed3\u675f\u91c7\u96c6"
    bl_description = "\u505c\u6b62\u8bb0\u5f55\u5e76\u8f93\u51fa\u539f\u59cb\u4e0e\u964d\u566a\u6570\u636e"

    @classmethod
    def poll(cls, context):
        return RECORDER is not None

    def execute(self, context):
        _stop_global_capture("user_stop")
        self.report({"INFO"}, "\u91c7\u96c6\u5df2\u505c\u6b62\u3002")
        return {"FINISHED"}


class SKCAPTURE_OT_replay_analyze(Operator):
    bl_idname = "sk_capture.replay_analyze"
    bl_label = "\u5206\u6790\u6620\u5c04"
    bl_description = "\u8bfb\u53d6\u8bb0\u5f55\u6587\u4ef6\u5e76\u5206\u6790\u5f62\u6001\u952e\u6620\u5c04"

    def execute(self, context):
        replay = ShapeKeyReplaySession(context.scene, context)
        try:
            mapping_info = replay.prepare()
        except Exception as exc:
            context.scene.sk_replay_status = f"\u5206\u6790\u5931\u8d25\uff1a{exc}"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report(
            {"INFO"},
            f"\u5df2\u5339\u914d {mapping_info['mapped_count']}/{mapping_info['source_count']} \u4e2a\u5f62\u6001\u952e",
        )
        return {"FINISHED"}


class SKCAPTURE_OT_replay_apply_sample(Operator):
    bl_idname = "sk_capture.replay_apply_sample"
    bl_label = "\u5e94\u7528\u5f53\u524d\u6837\u672c"
    bl_description = "\u628a\u6587\u4ef6\u4e2d\u5f53\u524d\u6837\u672c\u6295\u5c04\u5230\u76ee\u6807\u7269\u4f53"

    def execute(self, context):
        scene = context.scene
        target_object = _target_object_from_scene(scene, context)
        if target_object is None:
            scene.sk_replay_status = "\u5e94\u7528\u5931\u8d25\uff1a\u6ca1\u6709\u76ee\u6807\u7269\u4f53"
            self.report({"ERROR"}, "\u8bf7\u6307\u5b9a\u76ee\u6807\u7269\u4f53\uff0c\u6216\u5148\u9009\u4e2d\u4e00\u4e2a\u7269\u4f53\u3002")
            return {"CANCELLED"}
        try:
            result = project_capture_file_to_object(
                file_path=scene.sk_replay_file_path,
                target_object_name=target_object.name,
                sample_index=scene.sk_replay_sample_index,
                source_object_name=scene.sk_replay_source_object,
                strength=scene.sk_replay_strength,
            )
        except Exception as exc:
            scene.sk_replay_status = f"\u5e94\u7528\u5931\u8d25\uff1a{exc}"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}

        mapping_info = result["mapping_info"]
        scene.sk_replay_total_samples = result["sample_count"]
        scene.sk_replay_total_source_keys = mapping_info["source_count"]
        scene.sk_replay_mapped_count = mapping_info["mapped_count"]
        scene.sk_replay_source_object = mapping_info["source_object_name"]
        scene.sk_replay_status = (
            f"\u5df2\u5e94\u7528\u6837\u672c {result['sample_index'] + 1}/{result['sample_count']}"
            f"\uff0c\u6620\u5c04 {result['applied_count']} \u952e"
        )
        self.report({"INFO"}, scene.sk_replay_status)
        return {"FINISHED"}


class SKCAPTURE_OT_replay_start(Operator):
    bl_idname = "sk_capture.replay_start"
    bl_label = "\u5f00\u59cb\u56de\u653e"
    bl_description = "\u5b9e\u65f6\u56de\u653e\u8bb0\u5f55\u6570\u636e\u5e76\u9a71\u52a8\u5f62\u6001\u952e"

    @classmethod
    def poll(cls, context):
        return REPLAYER is None

    def execute(self, context):
        global REPLAYER
        replay = ShapeKeyReplaySession(context.scene, context)
        try:
            replay.prepare()
            replay.start()
        except Exception as exc:
            context.scene.sk_replay_running = False
            context.scene.sk_replay_status = f"\u56de\u653e\u542f\u52a8\u5931\u8d25\uff1a{exc}"
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        REPLAYER = replay
        self.report({"INFO"}, "\u56de\u653e\u5df2\u5f00\u59cb\u3002")
        return {"FINISHED"}


class SKCAPTURE_OT_replay_stop(Operator):
    bl_idname = "sk_capture.replay_stop"
    bl_label = "\u505c\u6b62\u56de\u653e"
    bl_description = "\u505c\u6b62\u5f62\u6001\u952e\u6295\u5c04\u56de\u653e"

    @classmethod
    def poll(cls, context):
        return REPLAYER is not None

    def execute(self, context):
        _stop_global_replay("user_stop")
        self.report({"INFO"}, "\u56de\u653e\u5df2\u505c\u6b62\u3002")
        return {"FINISHED"}


class SKCAPTURE_PT_panel(Panel):
    bl_label = "\u5f62\u6001\u952e\u91c7\u96c6"
    bl_idname = "SKCAPTURE_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "\u5f62\u6001\u91c7\u96c6"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        faceit_status = _get_faceit_status(scene)

        capture_box = layout.box()
        capture_box.label(text="\u91c7\u96c6\u8bbe\u7f6e")
        capture_box.prop(scene, "sk_capture_interval")
        capture_box.prop(scene, "sk_capture_include_basis")
        capture_box.prop(scene, "sk_capture_only_selected")
        capture_box.prop(scene, "sk_capture_skip_separator_keys")
        capture_box.prop(scene, "sk_capture_arkit_only")

        denoise_box = layout.box()
        denoise_box.label(text="\u66f2\u7ebf\u4f18\u5316")
        denoise_box.prop(scene, "sk_capture_export_denoised")
        denoise_box.enabled = not scene.sk_capture_running
        if scene.sk_capture_export_denoised:
            denoise_box.prop(scene, "sk_capture_denoise_deadband")
            denoise_box.prop(scene, "sk_capture_denoise_min_cutoff")
            denoise_box.prop(scene, "sk_capture_denoise_beta")
            denoise_box.prop(scene, "sk_capture_denoise_d_cutoff")

        row = layout.row(align=True)
        row.scale_y = 1.2
        if scene.sk_capture_running:
            row.operator("sk_capture.stop", icon="CANCEL")
        else:
            row.operator("sk_capture.start", icon="PLAY")

        info_box = layout.box()
        status_text = scene.sk_capture_status or "\u7a7a\u95f2"
        info_box.label(text=f"\u72b6\u6001\uff1a{status_text}")
        info_box.label(text=f"\u6837\u672c\u6570\uff1a{scene.sk_capture_sample_count}")
        if scene.sk_capture_session_dir:
            session_row = info_box.row()
            session_row.enabled = False
            session_row.prop(scene, "sk_capture_session_dir", text="\u4f1a\u8bdd\u76ee\u5f55")

        faceit_box = layout.box()
        faceit_box.label(text="Faceit")
        if faceit_status["available"]:
            receiver_text = "\u5f00\u542f" if faceit_status["receiver_enabled"] else "\u5173\u95ed"
            source_text = faceit_status["live_source"] or "-"
            faceit_box.label(text=f"\u63a5\u6536\u5668\uff1a{receiver_text}")
            faceit_box.label(text=f"\u6765\u6e90\uff1a{source_text}")
        else:
            faceit_box.label(text="\u672a\u68c0\u6d4b\u5230 Faceit \u63a5\u6536\u5668\u5c5e\u6027")

        replay_box = layout.box()
        replay_box.label(text="\u6570\u636e\u6295\u5c04/\u56de\u653e")
        replay_box.prop(scene, "sk_replay_file_path", text="\u8bb0\u5f55\u6587\u4ef6")
        replay_box.prop(scene, "sk_replay_target_object", text="\u76ee\u6807\u7269\u4f53")
        replay_box.prop(scene, "sk_replay_source_object", text="\u6e90\u5bf9\u8c61\u540d")
        replay_box.prop(scene, "sk_replay_strength")
        replay_box.prop(scene, "sk_replay_speed")
        replay_box.prop(scene, "sk_replay_loop")
        replay_box.prop(scene, "sk_replay_sample_index")

        replay_row = replay_box.row(align=True)
        replay_row.operator("sk_capture.replay_analyze", icon="VIEWZOOM")
        replay_row.operator("sk_capture.replay_apply_sample", icon="IMPORT")

        replay_row_2 = replay_box.row(align=True)
        if scene.sk_replay_running:
            replay_row_2.operator("sk_capture.replay_stop", icon="CANCEL")
        else:
            replay_row_2.operator("sk_capture.replay_start", icon="PLAY")

        replay_info = replay_box.box()
        replay_status_text = scene.sk_replay_status or "\u7a7a\u95f2"
        replay_info.label(text=f"\u72b6\u6001\uff1a{replay_status_text}")
        replay_info.label(
            text=f"\u6620\u5c04\u952e\uff1a{scene.sk_replay_mapped_count}/{scene.sk_replay_total_source_keys}"
        )
        replay_info.label(text=f"\u6837\u672c\u603b\u6570\uff1a{scene.sk_replay_total_samples}")


CLASSES = (
    SKCAPTURE_OT_start,
    SKCAPTURE_OT_stop,
    SKCAPTURE_OT_replay_analyze,
    SKCAPTURE_OT_replay_apply_sample,
    SKCAPTURE_OT_replay_start,
    SKCAPTURE_OT_replay_stop,
    SKCAPTURE_PT_panel,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)

    bpy.types.Scene.sk_capture_interval = FloatProperty(
        name="\u91c7\u6837\u95f4\u9694",
        description="\u6bcf\u6b21\u91c7\u6837\u4e4b\u95f4\u7684\u79d2\u6570",
        default=1.0 / 60.0,
        min=0.001,
        soft_min=0.001,
        soft_max=0.1,
        precision=4,
    )
    bpy.types.Scene.sk_capture_include_basis = BoolProperty(
        name="\u5305\u542b Basis",
        description="\u540c\u65f6\u91c7\u96c6 Basis \u5f62\u6001\u952e",
        default=False,
    )
    bpy.types.Scene.sk_capture_only_selected = BoolProperty(
        name="\u4ec5\u9009\u4e2d\u5bf9\u8c61",
        description="\u53ea\u91c7\u96c6\u5f53\u524d\u9009\u4e2d\u7684\u5e26\u5f62\u6001\u952e\u7f51\u683c\u5bf9\u8c61",
        default=False,
    )
    bpy.types.Scene.sk_capture_skip_separator_keys = BoolProperty(
        name="\u8df3\u8fc7\u5206\u7ec4\u952e",
        description="\u8df3\u8fc7 '-- ARKIT --' \u8fd9\u7c7b\u5206\u7ec4/\u6807\u7b7e\u5f62\u6001\u952e",
        default=True,
    )
    bpy.types.Scene.sk_capture_arkit_only = BoolProperty(
        name="\u4ec5 ARKit 52 \u952e",
        description="\u53ea\u8bb0\u5f55 ARKit 52 \u4e2a\u4e3b\u952e\uff0c\u4fbf\u4e8e\u8bad\u7ec3\u548c\u8f6c\u8bd1",
        default=False,
    )
    bpy.types.Scene.sk_capture_export_denoised = BoolProperty(
        name="\u8f93\u51fa\u964d\u566a\u66f2\u7ebf",
        description="\u7ed3\u675f\u91c7\u96c6\u65f6\u81ea\u52a8\u8f93\u51fa One Euro \u964d\u566a\u7248 JSONL/CSV",
        default=True,
    )
    bpy.types.Scene.sk_capture_denoise_deadband = FloatProperty(
        name="\u6b7b\u533a\u961f\u503c",
        description="\u5c0f\u4e8e\u6b64\u503c\u7684\u5fae\u5c0f\u6296\u52a8\u4f1a\u88ab\u5f52\u96f6",
        default=0.002,
        min=0.0,
        soft_max=0.05,
        precision=4,
    )
    bpy.types.Scene.sk_capture_denoise_min_cutoff = FloatProperty(
        name="One Euro Min Cutoff",
        description="\u57fa\u7840\u622a\u6b62\u9891\u7387\uff0c\u8d8a\u5927\u8d8a\u8ddf\u624b\uff0c\u8d8a\u5c0f\u8d8a\u5e73\u6ed1",
        default=1.2,
        min=0.01,
        soft_max=10.0,
        precision=3,
    )
    bpy.types.Scene.sk_capture_denoise_beta = FloatProperty(
        name="One Euro Beta",
        description="\u52a8\u6001\u8ddf\u624b\u5f3a\u5ea6\uff0c\u8d8a\u5927\u5728\u5feb\u901f\u8fd0\u52a8\u65f6\u8d8a\u4e0d\u4f1a\u62d6\u5c3e",
        default=0.15,
        min=0.0,
        soft_max=5.0,
        precision=3,
    )
    bpy.types.Scene.sk_capture_denoise_d_cutoff = FloatProperty(
        name="One Euro D Cutoff",
        description="\u901f\u5ea6\u9879\u7684\u6ee4\u6ce2\u622a\u6b62\u9891\u7387",
        default=1.0,
        min=0.01,
        soft_max=10.0,
        precision=3,
    )
    bpy.types.Scene.sk_capture_running = BoolProperty(
        name="\u91c7\u96c6\u8fd0\u884c\u4e2d",
        default=False,
    )
    bpy.types.Scene.sk_capture_status = StringProperty(
        name="\u91c7\u96c6\u72b6\u6001",
        default="\u7a7a\u95f2",
    )
    bpy.types.Scene.sk_capture_session_dir = StringProperty(
        name="\u4f1a\u8bdd\u76ee\u5f55",
        default="",
    )
    bpy.types.Scene.sk_capture_sample_count = IntProperty(
        name="\u6837\u672c\u6570",
        default=0,
        min=0,
    )
    bpy.types.Scene.sk_replay_file_path = StringProperty(
        name="\u8bb0\u5f55\u6587\u4ef6",
        description="\u9009\u62e9 samples.jsonl \u6216 samples_denoised.jsonl \u6587\u4ef6",
        default="",
        subtype="FILE_PATH",
    )
    bpy.types.Scene.sk_replay_target_object = PointerProperty(
        name="\u76ee\u6807\u7269\u4f53",
        type=bpy.types.Object,
    )
    bpy.types.Scene.sk_replay_source_object = StringProperty(
        name="\u6e90\u5bf9\u8c61\u540d",
        description="\u53ef\u9009\uff0c\u4e0d\u586b\u65f6\u9ed8\u8ba4\u4f7f\u7528\u6587\u4ef6\u4e2d\u7684\u7b2c\u4e00\u4e2a objects \u9879",
        default="",
    )
    bpy.types.Scene.sk_replay_strength = FloatProperty(
        name="\u6295\u5c04\u5f3a\u5ea6",
        description="\u5c06\u8bb0\u5f55\u503c\u4e58\u4ee5\u8be5\u5f3a\u5ea6\u518d\u5199\u5165\u76ee\u6807\u5f62\u6001\u952e",
        default=1.0,
        min=0.0,
        soft_max=2.0,
        precision=3,
    )
    bpy.types.Scene.sk_replay_speed = FloatProperty(
        name="\u56de\u653e\u500d\u901f",
        description="\u5b9e\u65f6\u56de\u653e\u7684\u500d\u901f",
        default=1.0,
        min=0.01,
        soft_max=4.0,
        precision=3,
    )
    bpy.types.Scene.sk_replay_loop = BoolProperty(
        name="\u5faa\u73af\u56de\u653e",
        description="\u64ad\u5230\u7ed3\u5c3e\u540e\u91cd\u65b0\u4ece\u5934\u64ad\u653e",
        default=False,
    )
    bpy.types.Scene.sk_replay_sample_index = IntProperty(
        name="\u5f53\u524d\u6837\u672c",
        description="\u7528\u4e8e\u5355\u6837\u672c\u5e94\u7528\u6216\u56de\u653e\u8d77\u70b9",
        default=0,
        min=0,
    )
    bpy.types.Scene.sk_replay_running = BoolProperty(
        name="\u56de\u653e\u8fd0\u884c\u4e2d",
        default=False,
    )
    bpy.types.Scene.sk_replay_status = StringProperty(
        name="\u56de\u653e\u72b6\u6001",
        default="\u7a7a\u95f2",
    )
    bpy.types.Scene.sk_replay_mapped_count = IntProperty(
        name="\u6620\u5c04\u952e\u6570",
        default=0,
        min=0,
    )
    bpy.types.Scene.sk_replay_total_source_keys = IntProperty(
        name="\u6e90\u952e\u6570",
        default=0,
        min=0,
    )
    bpy.types.Scene.sk_replay_total_samples = IntProperty(
        name="\u603b\u6837\u672c\u6570",
        default=0,
        min=0,
    )

    if _stop_capture_before_save not in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.append(_stop_capture_before_save)
    if _stop_capture_before_load not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_stop_capture_before_load)
    if hasattr(bpy.app.handlers, "quit_pre"):
        if _stop_capture_before_quit not in bpy.app.handlers.quit_pre:
            bpy.app.handlers.quit_pre.append(_stop_capture_before_quit)


def unregister():
    _stop_global_capture("addon_unload")
    _stop_global_replay("addon_unload")

    if _stop_capture_before_save in bpy.app.handlers.save_pre:
        bpy.app.handlers.save_pre.remove(_stop_capture_before_save)
    if _stop_capture_before_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_stop_capture_before_load)
    if hasattr(bpy.app.handlers, "quit_pre"):
        if _stop_capture_before_quit in bpy.app.handlers.quit_pre:
            bpy.app.handlers.quit_pre.remove(_stop_capture_before_quit)

    del bpy.types.Scene.sk_replay_total_samples
    del bpy.types.Scene.sk_replay_total_source_keys
    del bpy.types.Scene.sk_replay_mapped_count
    del bpy.types.Scene.sk_replay_status
    del bpy.types.Scene.sk_replay_running
    del bpy.types.Scene.sk_replay_sample_index
    del bpy.types.Scene.sk_replay_loop
    del bpy.types.Scene.sk_replay_speed
    del bpy.types.Scene.sk_replay_strength
    del bpy.types.Scene.sk_replay_source_object
    del bpy.types.Scene.sk_replay_target_object
    del bpy.types.Scene.sk_replay_file_path
    del bpy.types.Scene.sk_capture_sample_count
    del bpy.types.Scene.sk_capture_session_dir
    del bpy.types.Scene.sk_capture_status
    del bpy.types.Scene.sk_capture_running
    del bpy.types.Scene.sk_capture_denoise_d_cutoff
    del bpy.types.Scene.sk_capture_denoise_beta
    del bpy.types.Scene.sk_capture_denoise_min_cutoff
    del bpy.types.Scene.sk_capture_denoise_deadband
    del bpy.types.Scene.sk_capture_export_denoised
    del bpy.types.Scene.sk_capture_arkit_only
    del bpy.types.Scene.sk_capture_skip_separator_keys
    del bpy.types.Scene.sk_capture_only_selected
    del bpy.types.Scene.sk_capture_include_basis
    del bpy.types.Scene.sk_capture_interval

    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
