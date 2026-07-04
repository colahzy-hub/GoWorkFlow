import json
import hashlib
import gc
import math
import os
import re
import subprocess
import sys
import time

import bpy

DEFAULT_PRESET_DIR = os.path.dirname(os.path.abspath(__file__))
PRESET_BASENAME = os.path.splitext(os.path.basename(os.path.abspath(__file__)))[0]
PANEL_PREVIEW_SCALE = 6.0
PANEL_STATIC_PREVIEW_DIRNAME = "arkit_reference_preview_cache"
LOCAL_PREVIEW_DIRNAME = "arkit_reference_preview_jpg"
_REFERENCE_RUNTIME_STATE_KEY = "go_workflow.arkit_reference_runtime"
_PREVIEW_RUNTIME_STATE_KEY = "go_workflow.arkit_panel_preview_runtime"
_ANIMATION_STATE = {"running": False, "token": 0, "paused": False}
_VALIDATION_PREVIEW_STATE = {"object_name": "", "active_index": 0, "values": None}
_VALIDATION_TIMER_STATE = {"callback": None}
_VALIDATION_TIMER_REGISTRY_KEY = "go_workflow.validation_timer_callbacks"
_NODEPREVIEW_PLAYBACK_STATE = {"changed": False, "original": None}
_UNDO_PLAYBACK_STATE = {"changed": False, "original": None}
ANIMATION_TIMER_INTERVAL = 0.04
ANIMATION_DURATION_PER_KEY = 1.0
ANIMATION_STATUS_INTERVAL = 0.2
FULL_VALIDATION_TOTAL_SECONDS = 15.0
FULL_VALIDATION_TARGET_FRAMES = 600
FULL_VALIDATION_PLAYBACK_FPS = 30.0
FULL_VALIDATION_TIMER_INTERVAL = 1.0 / max(60.0, FULL_VALIDATION_PLAYBACK_FPS * 2.0)
PREVIEW_IDLE_RELEASE_SECONDS = 2.0
PREVIEW_PRELOAD_BATCH_SIZE = 1
PREVIEW_MAX_CACHE_IMAGES = 10
PREVIEW_HARD_CACHE_IMAGES = 32
PREVIEW_RELEASE_GRACE_SECONDS = 2.5
PREVIEW_RELEASE_RETRY_SECONDS = 0.85
PREVIEW_PRELOAD_QUEUE_LIMIT = 12
PREVIEW_DRAW_DEBOUNCE_SECONDS = 0.14
PERF_HISTORY_LIMIT = 180
PERF_EXPORT_DIRNAME = "arkit_perf_logs"
PERF_SUMMARY_REFRESH_SECONDS = 1.5
PAYLOAD_STAT_REFRESH_SECONDS = 0.65
MEDIA_STATUS_THROTTLE_SECONDS = 0.16


def _item_cache_key(item):
    if not isinstance(item, dict):
        return ""
    return "|".join(
        (
            str(item.get("shape_key", "") or ""),
            str(item.get("name_bilingual", "") or ""),
            str(item.get("category", "") or ""),
        )
    )


def _perf_export_dir():
    folder = os.path.join(DEFAULT_PRESET_DIR, PERF_EXPORT_DIRNAME)
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        return DEFAULT_PRESET_DIR
    return folder


def _read_process_memory_mb():
    if not hasattr(__import__("ctypes"), "windll"):
        return 0.0
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        process = kernel32.GetCurrentProcess()
        buf = (ctypes.c_byte * 128)()
        cb = ctypes.c_ulong(80)
        ctypes.memmove(buf, ctypes.addressof(cb), 4)
        psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_bool
        ok = psapi.GetProcessMemoryInfo(process, buf, 128)
        if not ok:
            return 0.0
        ws = ctypes.c_ulonglong.from_buffer(buf, 16)
        return float(ws.value) / (1024.0 * 1024.0)
    except Exception:
        return 0.0


def _perf_runtime_state():
    key = "go_workflow.arkit_reference_perf_runtime"
    try:
        store = bpy.app.driver_namespace.get(key)
        if not isinstance(store, dict):
            store = {}
            bpy.app.driver_namespace[key] = store
    except Exception:
        store = {}
    store.setdefault("draw_samples", [])
    store.setdefault("preview_events", [])
    store.setdefault("exports", [])
    store.setdefault("counts", {})
    store.setdefault("last_summary", {})
    store.setdefault("last_summary_at", 0.0)
    store.setdefault("memory_mb_current", 0.0)
    store.setdefault("last_draw_sample_at", 0.0)
    return store


def _perf_increment(name, amount=1):
    state = _perf_runtime_state()
    counts = state.get("counts", {})
    counts[name] = int(counts.get(name, 0) or 0) + int(amount)
    return counts[name]


def _perf_push_sample(bucket_name, sample, limit=PERF_HISTORY_LIMIT):
    state = _perf_runtime_state()
    bucket = state.get(bucket_name, [])
    bucket.append(sample)
    if len(bucket) > limit:
        del bucket[:-limit]
    state[bucket_name] = bucket


def _perf_capture_summary(extra=None):
    runtime_state = _preview_runtime_state()
    perf_state = _perf_runtime_state()
    now = time.perf_counter()
    last_summary = perf_state.get("last_summary", {})
    last_summary_at = float(perf_state.get("last_summary_at", 0.0) or 0.0)
    if isinstance(last_summary, dict) and last_summary and (now - last_summary_at) < PERF_SUMMARY_REFRESH_SECONDS:
        summary = dict(last_summary)
        if isinstance(extra, dict):
            summary.update(extra)
        return summary
    draw_samples = list(perf_state.get("draw_samples", []) or [])
    preview_events = list(perf_state.get("preview_events", []) or [])
    counts = dict(perf_state.get("counts", {}) or {})
    recent_draws = draw_samples[-12:]
    avg_draw_ms = (sum(float(item.get("ms", 0.0) or 0.0) for item in recent_draws) / float(len(recent_draws))) if recent_draws else 0.0
    max_draw_ms = max([float(item.get("ms", 0.0) or 0.0) for item in draw_samples] or [0.0])
    current_mem = float(perf_state.get("memory_mb_current", 0.0) or 0.0)
    if current_mem <= 0.0 or (now - last_summary_at) >= PERF_SUMMARY_REFRESH_SECONDS:
        current_mem = _read_process_memory_mb()
        perf_state["memory_mb_current"] = current_mem
        perf_state["last_summary_at"] = now
    peak_mem = max(float(counts.get("peak_memory_mb", 0.0) or 0.0), current_mem)
    counts["peak_memory_mb"] = peak_mem
    perf_state["counts"] = counts
    summary = {
        "draw_calls": int(counts.get("draw_calls", 0) or 0),
        "avg_draw_ms_recent": round(avg_draw_ms, 3),
        "max_draw_ms": round(max_draw_ms, 3),
        "preview_cache_items": len(runtime_state.get("image_cache", {}) or {}),
        "preview_pending_release": len(runtime_state.get("pending_release", {}) or {}),
        "preview_preload_queue": len(runtime_state.get("preload_queue", []) or []),
        "wrap_cache_items": len(runtime_state.get("text_wrap_cache", {}) or {}),
        "detail_cache_items": len(runtime_state.get("detail_lines_cache", {}) or {}),
        "preview_cache_hits": int(counts.get("preview_cache_hits", 0) or 0),
        "preview_cache_misses": int(counts.get("preview_cache_misses", 0) or 0),
        "preview_load_failures": int(counts.get("preview_load_failures", 0) or 0),
        "preview_release_removed": int(counts.get("preview_release_removed", 0) or 0),
        "memory_mb_current": round(current_mem, 2),
        "memory_mb_peak": round(peak_mem, 2),
        "preview_events_recent": len(preview_events[-20:]),
    }
    if isinstance(extra, dict):
        summary.update(extra)
    perf_state["last_summary"] = summary
    return summary


def _export_perf_log(module_state=None):
    summary = _perf_capture_summary()
    perf_state = _perf_runtime_state()
    payload = {
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "summary": summary,
        "counts": dict(perf_state.get("counts", {}) or {}),
        "draw_samples": list(perf_state.get("draw_samples", []) or []),
        "preview_events": list(perf_state.get("preview_events", []) or []),
    }
    stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    path = os.path.join(_perf_export_dir(), f"arkit_panel_perf_{stamp}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    exports = perf_state.get("exports", [])
    exports.append(path)
    perf_state["exports"] = exports[-24:]
    if module_state is not None:
        module_state.set("last_perf_log_path", path)
    return path


def _reset_perf_stats():
    state = _perf_runtime_state()
    state["draw_samples"] = []
    state["preview_events"] = []
    state["counts"] = {}
    state["last_summary"] = {}
    state["last_summary_at"] = 0.0
    state["memory_mb_current"] = 0.0
    state["last_draw_sample_at"] = 0.0


def _nodepreview_addon_preferences():
    try:
        addons = getattr(getattr(bpy.context, "preferences", None), "addons", None)
        if addons is None:
            return None
        addon = addons.get("NodePreview")
        if addon is None:
            return None
        return getattr(addon, "preferences", None)
    except Exception:
        return None


def _suspend_nodepreview_playback_updates():
    prefs = _nodepreview_addon_preferences()
    if prefs is None:
        return False
    try:
        current = bool(getattr(prefs, "update_during_animation_playback", False))
    except Exception:
        return False
    if not current:
        _NODEPREVIEW_PLAYBACK_STATE["changed"] = False
        _NODEPREVIEW_PLAYBACK_STATE["original"] = False
        return False
    try:
        prefs.update_during_animation_playback = False
    except Exception:
        return False
    _NODEPREVIEW_PLAYBACK_STATE["changed"] = True
    _NODEPREVIEW_PLAYBACK_STATE["original"] = True
    return True


def _restore_nodepreview_playback_updates():
    if not _NODEPREVIEW_PLAYBACK_STATE.get("changed"):
        _NODEPREVIEW_PLAYBACK_STATE["original"] = None
        return False
    prefs = _nodepreview_addon_preferences()
    original = _NODEPREVIEW_PLAYBACK_STATE.get("original")
    _NODEPREVIEW_PLAYBACK_STATE["changed"] = False
    _NODEPREVIEW_PLAYBACK_STATE["original"] = None
    if prefs is None or original is None:
        return False
    try:
        prefs.update_during_animation_playback = bool(original)
    except Exception:
        return False
    return True


def _undo_preferences():
    try:
        preferences = getattr(bpy.context, "preferences", None)
        return getattr(preferences, "edit", None)
    except Exception:
        return None


def _suspend_global_undo():
    prefs = _undo_preferences()
    if prefs is None:
        return False
    try:
        current = bool(getattr(prefs, "use_global_undo", True))
    except Exception:
        return False
    _UNDO_PLAYBACK_STATE["changed"] = False
    _UNDO_PLAYBACK_STATE["original"] = current
    if not current:
        return False
    try:
        prefs.use_global_undo = False
    except Exception:
        _UNDO_PLAYBACK_STATE["original"] = None
        return False
    _UNDO_PLAYBACK_STATE["changed"] = True
    return True


def _restore_global_undo():
    prefs = _undo_preferences()
    original = _UNDO_PLAYBACK_STATE.get("original")
    changed = bool(_UNDO_PLAYBACK_STATE.get("changed"))
    _UNDO_PLAYBACK_STATE["changed"] = False
    _UNDO_PLAYBACK_STATE["original"] = None
    if not changed or prefs is None or original is None:
        return False
    try:
        prefs.use_global_undo = bool(original)
    except Exception:
        return False
    return True
FULL_VALIDATION_TEXT_MIX_STATES = [
    {"name": "TextMix Calm Neutral", "seconds": 0.54, "transition_ratio": 0.88, "weights": {}},
    {
        "name": "TextMix Crooked Smile Spark",
        "seconds": 0.36,
        "transition_ratio": 0.78,
        "weights": {
            "BrowOuterUpLeft": 0.18,
            "BrowOuterUpRight": 0.12,
            "CheekSquintLeft": 0.10,
            "CheekSquintRight": 0.04,
            "MouthSmileLeft": 0.24,
            "MouthSmileRight": 0.10,
            "MouthDimpleLeft": 0.10,
            "MouthLeft": 0.06,
        },
    },
    {
        "name": "TextMix Front Smile Lift",
        "seconds": 0.42,
        "transition_ratio": 0.72,
        "weights": {
            "BrowOuterUpLeft": 0.24,
            "BrowOuterUpRight": 0.24,
            "EyeBlinkLeft": 0.08,
            "EyeBlinkRight": 0.08,
            "CheekSquintLeft": 0.22,
            "CheekSquintRight": 0.20,
            "MouthSmileLeft": 0.42,
            "MouthSmileRight": 0.38,
            "MouthDimpleLeft": 0.16,
            "MouthDimpleRight": 0.14,
            "JawOpen": 0.10,
        },
    },
    {
        "name": "TextMix Open Smile Lift",
        "seconds": 0.42,
        "transition_ratio": 0.64,
        "weights": {
            "BrowOuterUpLeft": 0.28,
            "BrowOuterUpRight": 0.28,
            "EyeBlinkLeft": 0.12,
            "EyeBlinkRight": 0.12,
            "CheekSquintLeft": 0.32,
            "CheekSquintRight": 0.30,
            "MouthSmileLeft": 0.56,
            "MouthSmileRight": 0.52,
            "MouthDimpleLeft": 0.22,
            "MouthDimpleRight": 0.20,
            "MouthStretchLeft": 0.10,
            "MouthStretchRight": 0.10,
            "JawOpen": 0.34,
        },
    },
    {
        "name": "TextMix Laugh Burst",
        "seconds": 0.34,
        "transition_ratio": 0.58,
        "weights": {
            "BrowOuterUpLeft": 0.30,
            "BrowOuterUpRight": 0.30,
            "CheekSquintLeft": 0.44,
            "CheekSquintRight": 0.42,
            "EyeBlinkLeft": 0.18,
            "EyeBlinkRight": 0.16,
            "EyeSquintLeft": 0.12,
            "EyeSquintRight": 0.12,
            "MouthSmileLeft": 0.72,
            "MouthSmileRight": 0.68,
            "MouthDimpleLeft": 0.28,
            "MouthDimpleRight": 0.26,
            "MouthStretchLeft": 0.16,
            "MouthStretchRight": 0.16,
            "JawOpen": 0.62,
        },
    },
    {
        "name": "TextMix Wide Laugh Crush",
        "seconds": 0.30,
        "transition_ratio": 0.48,
        "weights": {
            "BrowOuterUpLeft": 0.34,
            "BrowOuterUpRight": 0.34,
            "CheekSquintLeft": 0.58,
            "CheekSquintRight": 0.56,
            "EyeBlinkLeft": 0.26,
            "EyeBlinkRight": 0.24,
            "EyeSquintLeft": 0.20,
            "EyeSquintRight": 0.20,
            "MouthSmileLeft": 0.86,
            "MouthSmileRight": 0.82,
            "MouthDimpleLeft": 0.34,
            "MouthDimpleRight": 0.32,
            "MouthStretchLeft": 0.24,
            "MouthStretchRight": 0.24,
            "JawOpen": 0.84,
        },
    },
    {
        "name": "TextMix Laugh Snap Open",
        "seconds": 0.24,
        "transition_ratio": 0.46,
        "weights": {
            "BrowOuterUpLeft": 0.38,
            "BrowOuterUpRight": 0.38,
            "EyeWideLeft": 0.14,
            "EyeWideRight": 0.14,
            "CheekSquintLeft": 0.48,
            "CheekSquintRight": 0.46,
            "MouthSmileLeft": 0.78,
            "MouthSmileRight": 0.74,
            "MouthDimpleLeft": 0.30,
            "MouthDimpleRight": 0.28,
            "MouthStretchLeft": 0.28,
            "MouthStretchRight": 0.28,
            "JawOpen": 0.92,
        },
    },
    {
        "name": "TextMix Side Residual Grin",
        "seconds": 0.34,
        "transition_ratio": 0.66,
        "weights": {
            "EyeBlinkLeft": 0.10,
            "CheekSquintLeft": 0.24,
            "CheekSquintRight": 0.12,
            "MouthSmileLeft": 0.42,
            "MouthSmileRight": 0.20,
            "MouthDimpleLeft": 0.18,
            "MouthLeft": 0.10,
            "JawOpen": 0.16,
        },
    },
    {
        "name": "TextMix Recover Smile",
        "seconds": 0.40,
        "transition_ratio": 0.74,
        "weights": {
            "BrowOuterUpLeft": 0.16,
            "BrowOuterUpRight": 0.16,
            "CheekSquintLeft": 0.14,
            "CheekSquintRight": 0.14,
            "MouthSmileLeft": 0.30,
            "MouthSmileRight": 0.26,
            "MouthDimpleLeft": 0.10,
            "MouthDimpleRight": 0.10,
            "JawOpen": 0.08,
        },
    },
    {"name": "TextMix Back To Calm Neutral", "seconds": 0.52, "transition_ratio": 0.82, "weights": {}},
]
FULL_VALIDATION_REFERENCE_STATE_NAMES = (
    "开心咧嘴笑",
    "闭眼鼓起脸颊嘟嘟嘴",
    "不屑的表情嘴巴不屑上抬",
    "张开嘴巴歪嘴伸舌头",
    "往左上方歪嘴",
)
VALIDATION_SEQUENCE_RULES = {
    "browinnerup": [["BrowInnerUp"], ["BrowInnerUp", "BrowOuterUpLeft", "BrowOuterUpRight"]],
    "eyeblinkleft": [["EyeBlinkLeft", "EyeBlinkRight"]],
    "eyeblinkright": [["EyeBlinkLeft", "EyeBlinkRight"]],
    "browdownleft": [["BrowDownLeft"], ["BrowDownLeft", "BrowInnerUp"]],
    "browdownright": [["BrowDownRight"], ["BrowDownRight", "BrowInnerUp"]],
    "browouterupleft": [["BrowInnerUp", "BrowOuterUpLeft"]],
    "browouterupright": [["BrowInnerUp", "BrowOuterUpRight"]],
    "eyesquintleft": [["EyeSquintLeft"], ["EyeSquintLeft", "EyeBlinkLeft"], ["EyeSquintLeft", "CheekSquintLeft"]],
    "eyesquintright": [["EyeSquintRight"], ["EyeSquintRight", "EyeBlinkRight"], ["EyeSquintRight", "CheekSquintRight"]],
    "cheeksquintleft": [["CheekSquintLeft"], ["CheekSquintLeft", "EyeSquintLeft"]],
    "cheeksquintright": [["CheekSquintRight"], ["CheekSquintRight", "EyeSquintRight"]],
    "jawopen": [["JawOpen", "MouthClose"], ["JawOpen", "MouthClose"], ["JawOpen", "JawForward", "JawLeft", "JawRight"]],
    "mouthclose": [["JawOpen", "MouthClose"]],
    "mouthpucker": [["JawOpen", "MouthPucker"], ["JawOpen", "MouthPucker", "MouthFunnel"], ["JawOpen", "MouthPucker", "MouthFunnel", "MouthClose"]],
    "mouthfunnel": [["JawOpen", "MouthFunnel"], ["JawOpen", "MouthFunnel", "MouthPucker"], ["JawOpen", "MouthFunnel", "MouthPucker", "MouthClose"]],
    "mouthrollupper": [["MouthRollUpper", "MouthRollLower"], ["JawOpen", "MouthRollUpper"]],
    "mouthrolllower": [["MouthRollUpper", "MouthRollLower"], ["JawOpen", "MouthRollLower"]],
    "mouthdimpleleft": [["JawOpen", "MouthSmileLeft"], ["MouthDimpleLeft"]],
    "mouthdimpleright": [["JawOpen", "MouthSmileRight"], ["MouthDimpleRight"]],
    "mouthleft": [["MouthLeft", "TongueOut"]],
    "mouthright": [["MouthRight", "TongueOut"]],
    "mouthsmileleft": [["JawOpen", "MouthSmileLeft"], ["TongueOut"], ["MouthSmileRight"]],
    "mouthsmileright": [["JawOpen", "MouthSmileRight"], ["TongueOut"], ["MouthSmileLeft"]],
    "mouthfrownleft": [["JawOpen"], ["JawOpen", "MouthFrownLeft"], ["JawOpen", "MouthFrownLeft", "MouthLowerDownLeft"]],
    "mouthfrownright": [["JawOpen"], ["JawOpen", "MouthFrownRight"], ["JawOpen", "MouthFrownRight", "MouthLowerDownRight"]],
    "mouthstretchleft": [["JawOpen", "MouthStretchLeft"], ["MouthSmileLeft"]],
    "mouthstretchright": [["JawOpen", "MouthStretchRight"], ["MouthSmileRight"]],
    "mouthupperupleft": [["JawOpen", "MouthUpperUpLeft", "MouthLowerDownLeft"]],
    "mouthupperupright": [["JawOpen", "MouthUpperUpRight", "MouthLowerDownRight"]],
    "mouthlowerdownleft": [["JawOpen", "MouthUpperUpLeft", "MouthLowerDownLeft"]],
    "mouthlowerdownright": [["JawOpen", "MouthUpperUpRight", "MouthLowerDownRight"]],
    "tongueout": [["JawOpen", "TongueOut"], ["MouthSmileLeft"], ["MouthSmileRight"]],
}
CUMULATIVE_VALIDATION_SEQUENCE_KEYS = {
    "browinnerup",
    "browdownleft",
    "browdownright",
    "eyesquintleft",
    "eyesquintright",
    "cheeksquintleft",
    "cheeksquintright",
    "mouthpucker",
    "mouthfunnel",
    "mouthleft",
    "mouthright",
    "mouthsmileleft",
    "mouthsmileright",
    "mouthdimpleleft",
    "mouthdimpleright",
    "mouthfrownleft",
    "mouthfrownright",
    "mouthstretchleft",
    "mouthstretchright",
    "tongueout",
}
FULL_VALIDATION_SEQUENCE = [
    ["EyeBlinkLeft", "EyeBlinkRight"],
    ["EyeSquintLeft", "EyeSquintRight"],
    ["BrowInnerUp", "BrowOuterUpLeft", "BrowOuterUpRight"],
    ["BrowDownLeft", "BrowDownRight"],
    ["EyeLookUpLeft", "EyeLookUpRight"],
    ["EyeLookDownLeft", "EyeLookDownRight"],
    ["EyeLookInLeft", "EyeLookInRight"],
    ["EyeLookOutLeft", "EyeLookOutRight"],
    ["CheekPuff"],
    ["NoseSneerLeft", "NoseSneerRight"],
    ["JawForward"],
    ["JawLeft", "JawRight"],
    ["JawOpen", "MouthClose"],
    ["MouthPressLeft", "MouthPressRight"],
    ["MouthShrugUpper", "MouthShrugLower"],
    ["MouthRollUpper", "MouthRollLower"],
    ["MouthUpperUpLeft", "MouthUpperUpRight", "MouthLowerDownLeft", "MouthLowerDownRight"],
    ["JawOpen", "MouthFunnel", "MouthPucker"],
    ["MouthSmileLeft", "MouthSmileRight"],
    ["MouthFrownLeft", "MouthFrownRight"],
    ["MouthStretchLeft", "MouthStretchRight"],
    ["TongueOut", "JawOpen", "MouthSmileLeft", "MouthSmileRight"],
]
FULL_VALIDATION_STATES = [
    {"name": "眼部提神-半强度", "weights": {"BrowInnerUp": 0.5, "BrowOuterUpLeft": 0.45, "BrowOuterUpRight": 0.45, "EyeWideLeft": 0.5, "EyeWideRight": 0.5}},
    {"name": "眼部提神-满强度", "weights": {"BrowInnerUp": 1.0, "BrowOuterUpLeft": 0.85, "BrowOuterUpRight": 0.85, "EyeWideLeft": 1.0, "EyeWideRight": 1.0}},
    {"name": "闭眼压眉-半强度", "weights": {"EyeBlinkLeft": 0.5, "EyeBlinkRight": 0.5, "BrowDownLeft": 0.5, "BrowDownRight": 0.5, "CheekSquintLeft": 0.3, "CheekSquintRight": 0.3}},
    {"name": "闭眼压眉-满强度", "weights": {"EyeBlinkLeft": 1.0, "EyeBlinkRight": 1.0, "BrowDownLeft": 1.0, "BrowDownRight": 1.0, "CheekSquintLeft": 0.65, "CheekSquintRight": 0.65}},
    {"name": "眼球上看", "weights": {"EyeLookUpLeft": 0.82, "EyeLookUpRight": 0.82, "BrowOuterUpLeft": 0.22, "BrowOuterUpRight": 0.22}},
    {"name": "眼球下看", "weights": {"EyeLookDownLeft": 0.82, "EyeLookDownRight": 0.82, "BrowInnerUp": 0.18}},
    {"name": "眼球左看", "weights": {"EyeLookOutLeft": 0.86, "EyeLookInRight": 0.86, "BrowOuterUpLeft": 0.16, "MouthLeft": 0.12}},
    {"name": "眼球右看", "weights": {"EyeLookInLeft": 0.86, "EyeLookOutRight": 0.86, "BrowOuterUpRight": 0.16, "MouthRight": 0.12}},
    {"name": "左侧挑眉挤眼", "weights": {"EyeSquintLeft": 0.9, "BrowDownLeft": 0.92, "BrowOuterUpRight": 0.55, "CheekSquintLeft": 0.48, "MouthSmileLeft": 0.28}},
    {"name": "右侧挑眉挤眼", "weights": {"EyeSquintRight": 0.9, "BrowDownRight": 0.92, "BrowOuterUpLeft": 0.55, "CheekSquintRight": 0.48, "MouthSmileRight": 0.28}},
    {"name": "鼻翼收缩", "weights": {"NoseSneerLeft": 0.88, "NoseSneerRight": 0.88, "BrowDownLeft": 0.22, "BrowDownRight": 0.22}},
    {"name": "鼓腮憋气", "weights": {"CheekPuff": 1.0, "MouthClose": 0.4, "JawOpen": 0.4}},
    {"name": "完整微笑", "weights": {"MouthSmileLeft": 1.0, "MouthSmileRight": 1.0, "MouthDimpleLeft": 0.7, "MouthDimpleRight": 0.7, "CheekSquintLeft": 0.42, "CheekSquintRight": 0.42, "JawOpen": 0.22, "EyeSquintLeft": 0.2, "EyeSquintRight": 0.2}},
    {"name": "嘴角下压", "weights": {"JawOpen": 0.36, "MouthFrownLeft": 0.9, "MouthFrownRight": 0.9, "MouthShrugUpper": 0.42, "MouthShrugLower": 0.34, "BrowInnerUp": 0.16}},
    {"name": "横向拉伸", "weights": {"MouthStretchLeft": 0.92, "MouthStretchRight": 0.92, "MouthUpperUpLeft": 0.24, "MouthUpperUpRight": 0.24, "JawOpen": 0.22, "EyeSquintLeft": 0.18, "EyeSquintRight": 0.18}},
    {"name": "抿唇收紧", "weights": {"MouthClose": 0.82, "MouthPressLeft": 0.62, "MouthPressRight": 0.62, "JawOpen": 0.82}},
    {"name": "卷唇咀嚼", "weights": {"MouthRollUpper": 0.78, "MouthRollLower": 0.78, "MouthClose": 0.28, "JawOpen": 0.28}},
    {"name": "A口型", "weights": {"JawOpen": 0.84, "MouthLowerDownLeft": 0.56, "MouthLowerDownRight": 0.56, "MouthUpperUpLeft": 0.22, "MouthUpperUpRight": 0.22}},
    {"name": "E口型", "weights": {"JawOpen": 0.54, "MouthStretchLeft": 0.62, "MouthStretchRight": 0.62, "MouthSmileLeft": 0.18, "MouthSmileRight": 0.18, "MouthDimpleLeft": 0.16, "MouthDimpleRight": 0.16}},
    {"name": "I口型", "weights": {"JawOpen": 0.2, "MouthSmileLeft": 0.58, "MouthSmileRight": 0.58, "MouthStretchLeft": 0.36, "MouthStretchRight": 0.36, "MouthDimpleLeft": 0.2, "MouthDimpleRight": 0.2}},
    {"name": "O口型", "weights": {"JawOpen": 0.62, "MouthFunnel": 0.88, "MouthPucker": 0.34, "MouthUpperUpLeft": 0.14, "MouthUpperUpRight": 0.14}},
    {"name": "U口型", "weights": {"JawOpen": 0.28, "MouthPucker": 0.74, "MouthFunnel": 0.44, "MouthClose": 0.18}},
    {"name": "左歪嘴", "weights": {"MouthLeft": 0.88, "JawLeft": 0.46, "MouthStretchLeft": 0.28, "MouthPressLeft": 0.14}},
    {"name": "右歪嘴", "weights": {"MouthRight": 0.88, "JawRight": 0.46, "MouthStretchRight": 0.28, "MouthPressRight": 0.14}},
    {"name": "Single side left upper", "weights": {"BrowDownLeft": 0.72, "EyeSquintLeft": 0.82, "EyeBlinkLeft": 0.42, "CheekSquintLeft": 0.46, "NoseSneerLeft": 0.2}},
    {"name": "Single side right upper", "weights": {"BrowDownRight": 0.72, "EyeSquintRight": 0.82, "EyeBlinkRight": 0.42, "CheekSquintRight": 0.46, "NoseSneerRight": 0.2}},
    {"name": "Single side left mouth", "weights": {"JawOpen": 0.38, "MouthLeft": 0.72, "MouthSmileLeft": 0.58, "MouthDimpleLeft": 0.42, "MouthStretchLeft": 0.35, "MouthLowerDownLeft": 0.22}},
    {"name": "Single side right mouth", "weights": {"JawOpen": 0.38, "MouthRight": 0.72, "MouthSmileRight": 0.58, "MouthDimpleRight": 0.42, "MouthStretchRight": 0.35, "MouthLowerDownRight": 0.22}},
    {"name": "张口吐舌", "weights": {"TongueOut": 1.0, "JawOpen": 0.78, "MouthLowerDownLeft": 0.24, "MouthLowerDownRight": 0.24, "MouthStretchLeft": 0.12, "MouthStretchRight": 0.12}},
    {"name": "张口吐舌-偏左", "weights": {"TongueOut": 0.94, "JawOpen": 0.84, "MouthLeft": 0.42, "JawLeft": 0.24, "MouthSmileLeft": 0.16, "MouthStretchLeft": 0.16}},
    {"name": "张口吐舌-偏右", "weights": {"TongueOut": 0.94, "JawOpen": 0.84, "MouthRight": 0.42, "JawRight": 0.24, "MouthSmileRight": 0.16, "MouthStretchRight": 0.16}},
    {"name": "张嘴画圆-上", "base_weights": {"JawOpen": 0.86, "TongueOut": 0.86}, "weights": {"MouthUpperUpLeft": 0.72, "MouthUpperUpRight": 0.72, "MouthShrugUpper": 0.24}},
    {"name": "张嘴画圆-右上", "base_weights": {"JawOpen": 0.86, "TongueOut": 0.86}, "weights": {"MouthRight": 0.46, "JawRight": 0.18, "MouthUpperUpRight": 0.72, "MouthStretchRight": 0.16, "MouthUpperUpLeft": 0.14}},
    {"name": "张嘴画圆-右", "base_weights": {"JawOpen": 0.86, "TongueOut": 0.86}, "weights": {"MouthRight": 0.82, "JawRight": 0.4, "MouthStretchRight": 0.26}},
    {"name": "张嘴画圆-右下", "base_weights": {"JawOpen": 0.86, "TongueOut": 0.86}, "weights": {"MouthRight": 0.46, "JawRight": 0.18, "MouthLowerDownRight": 0.74, "MouthStretchRight": 0.16, "MouthLowerDownLeft": 0.14}},
    {"name": "张嘴画圆-下", "base_weights": {"JawOpen": 0.86, "TongueOut": 0.86}, "weights": {"MouthLowerDownLeft": 0.78, "MouthLowerDownRight": 0.78, "MouthShrugLower": 0.24}},
    {"name": "张嘴画圆-左下", "base_weights": {"JawOpen": 0.86, "TongueOut": 0.86}, "weights": {"MouthLeft": 0.46, "JawLeft": 0.18, "MouthLowerDownLeft": 0.74, "MouthStretchLeft": 0.16, "MouthLowerDownRight": 0.14}},
    {"name": "张嘴画圆-左", "base_weights": {"JawOpen": 0.86, "TongueOut": 0.86}, "weights": {"MouthLeft": 0.82, "JawLeft": 0.4, "MouthStretchLeft": 0.26}},
    {"name": "张嘴画圆-左上", "base_weights": {"JawOpen": 0.86, "TongueOut": 0.86}, "weights": {"MouthLeft": 0.46, "JawLeft": 0.18, "MouthUpperUpLeft": 0.72, "MouthStretchLeft": 0.16, "MouthUpperUpRight": 0.14}},
    {"name": "张口吐舌圆形歪嘴-上", "base_weights": {"JawOpen": 0.88, "TongueOut": 0.86}, "weights": {"MouthUpperUpLeft": 0.82, "MouthUpperUpRight": 0.82, "MouthShrugUpper": 0.24}},
    {"name": "张口吐舌圆形歪嘴-右上", "base_weights": {"JawOpen": 0.88, "TongueOut": 0.86}, "weights": {"MouthRight": 0.48, "JawRight": 0.22, "MouthUpperUpRight": 0.82, "MouthStretchRight": 0.18, "MouthUpperUpLeft": 0.14}},
    {"name": "张口吐舌圆形歪嘴-右", "base_weights": {"JawOpen": 0.88, "TongueOut": 0.86}, "weights": {"MouthRight": 0.88, "JawRight": 0.46, "MouthStretchRight": 0.32, "MouthUpperUpRight": 0.18, "MouthLowerDownRight": 0.18}},
    {"name": "张口吐舌圆形歪嘴-右下", "base_weights": {"JawOpen": 0.88, "TongueOut": 0.86}, "weights": {"MouthRight": 0.48, "JawRight": 0.22, "MouthLowerDownRight": 0.82, "MouthStretchRight": 0.18, "MouthLowerDownLeft": 0.14}},
    {"name": "张口吐舌圆形歪嘴-下", "base_weights": {"JawOpen": 0.88, "TongueOut": 0.86}, "weights": {"MouthLowerDownLeft": 0.88, "MouthLowerDownRight": 0.88, "MouthShrugLower": 0.24}},
    {"name": "张口吐舌圆形歪嘴-左下", "base_weights": {"JawOpen": 0.88, "TongueOut": 0.86}, "weights": {"MouthLeft": 0.48, "JawLeft": 0.22, "MouthLowerDownLeft": 0.82, "MouthStretchLeft": 0.18, "MouthLowerDownRight": 0.14}},
    {"name": "张口吐舌圆形歪嘴-左", "base_weights": {"JawOpen": 0.88, "TongueOut": 0.86}, "weights": {"MouthLeft": 0.88, "JawLeft": 0.46, "MouthStretchLeft": 0.32, "MouthUpperUpLeft": 0.18, "MouthLowerDownLeft": 0.18}},
    {"name": "张口吐舌圆形歪嘴-左上", "base_weights": {"JawOpen": 0.88, "TongueOut": 0.86}, "weights": {"MouthLeft": 0.48, "JawLeft": 0.22, "MouthUpperUpLeft": 0.82, "MouthStretchLeft": 0.18, "MouthUpperUpRight": 0.14}},
    {"name": "欢乐协同", "weights": {"BrowOuterUpLeft": 0.82, "BrowOuterUpRight": 0.82, "EyeSquintLeft": 0.62, "EyeSquintRight": 0.62, "MouthSmileLeft": 1.0, "MouthSmileRight": 1.0, "CheekSquintLeft": 0.45, "CheekSquintRight": 0.45, "JawOpen": 0.28}},
    {"name": "生气协同", "weights": {"BrowDownLeft": 1.0, "BrowDownRight": 1.0, "EyeSquintLeft": 0.74, "EyeSquintRight": 0.74, "MouthFrownLeft": 0.84, "MouthFrownRight": 0.84, "MouthPressLeft": 0.36, "MouthPressRight": 0.36, "NoseSneerLeft": 0.2, "NoseSneerRight": 0.2, "JawForward": 0.26}},
    {"name": "惊讶协同", "weights": {"BrowInnerUp": 1.0, "BrowOuterUpLeft": 0.84, "BrowOuterUpRight": 0.84, "EyeWideLeft": 1.0, "EyeWideRight": 1.0, "JawOpen": 0.84, "MouthFunnel": 0.18}},
    {"name": "悲伤协同", "weights": {"BrowInnerUp": 0.88, "EyeLookDownLeft": 0.42, "EyeLookDownRight": 0.42, "MouthFrownLeft": 0.76, "MouthFrownRight": 0.76, "MouthShrugUpper": 0.28, "MouthShrugLower": 0.24}},
    {"name": "闭眼张口大笑", "weights": {"EyeBlinkLeft": 1.0, "EyeBlinkRight": 1.0, "MouthSmileLeft": 1.0, "MouthSmileRight": 1.0, "JawOpen": 0.96, "TongueOut": 0.14}},
    {"name": "困倦压嘴", "weights": {"BrowDownLeft": 0.9, "BrowDownRight": 0.9, "EyeLookDownLeft": 0.78, "EyeLookDownRight": 0.78, "MouthPressLeft": 0.72, "MouthPressRight": 0.72, "JawForward": 0.58}},
    {"name": "左半脸极限", "weights": {"BrowDownLeft": 1.0, "BrowOuterUpLeft": 1.0, "EyeBlinkLeft": 1.0, "EyeSquintLeft": 1.0, "EyeWideLeft": 1.0, "MouthSmileLeft": 1.0, "MouthFrownLeft": 1.0, "MouthStretchLeft": 1.0, "MouthUpperUpLeft": 1.0, "MouthLowerDownLeft": 1.0, "JawLeft": 1.0}},
    {"name": "右半脸极限", "weights": {"BrowDownRight": 1.0, "BrowOuterUpRight": 1.0, "EyeBlinkRight": 1.0, "EyeSquintRight": 1.0, "EyeWideRight": 1.0, "MouthSmileRight": 1.0, "MouthFrownRight": 1.0, "MouthStretchRight": 1.0, "MouthUpperUpRight": 1.0, "MouthLowerDownRight": 1.0, "JawRight": 1.0}},
    {"name": "极限堆叠压测", "weights": {"BrowInnerUp": 1.0, "BrowDownLeft": 1.0, "BrowDownRight": 1.0, "EyeBlinkLeft": 1.0, "EyeBlinkRight": 1.0, "EyeWideLeft": 1.0, "EyeWideRight": 1.0, "MouthSmileLeft": 1.0, "MouthSmileRight": 1.0, "MouthStretchLeft": 1.0, "MouthStretchRight": 1.0, "JawOpen": 1.0, "JawForward": 1.0, "CheekSquintLeft": 1.0, "CheekSquintRight": 1.0}},
    {"name": "开心咧嘴笑", "weights": {"MouthSmileLeft": 1.0, "MouthSmileRight": 1.0, "MouthStretchLeft": 0.78, "MouthStretchRight": 0.78, "JawOpen": 0.36, "CheekSquintLeft": 0.42, "CheekSquintRight": 0.42, "EyeSquintLeft": 0.18, "EyeSquintRight": 0.18, "MouthDimpleLeft": 0.38, "MouthDimpleRight": 0.38}},
    {"name": "闭眼鼓起脸颊嘟嘟嘴", "weights": {"EyeBlinkLeft": 1.0, "EyeBlinkRight": 1.0, "CheekPuff": 1.0, "MouthPucker": 0.8, "MouthClose": 0.24, "JawOpen": 0.24, "MouthPressLeft": 0.12, "MouthPressRight": 0.12}},
    {"name": "不屑的表情嘴巴不屑上抬", "weights": {"MouthShrugUpper": 0.72, "MouthUpperUpRight": 0.58, "MouthSmileRight": 0.22, "MouthFrownLeft": 0.18, "MouthRight": 0.16, "JawOpen": 0.18, "EyeSquintRight": 0.12}},
    {"name": "张开嘴巴歪嘴伸舌头", "weights": {"TongueOut": 1.0, "JawOpen": 0.96, "MouthLeft": 0.34, "JawLeft": 0.18, "MouthStretchLeft": 0.22, "MouthLowerDownLeft": 0.18, "MouthStretchRight": 0.12, "CheekSquintLeft": 0.14}},
    {"name": "往左上方歪嘴", "weights": {"MouthLeft": 0.92, "JawLeft": 0.34, "MouthUpperUpLeft": 0.72, "MouthStretchLeft": 0.24, "MouthSmileLeft": 0.18, "JawOpen": 0.18, "EyeSquintLeft": 0.12, "MouthUpperUpRight": 0.14}},
]
# Override the legacy validation library with a capture-oriented set of states.
FULL_VALIDATION_TEXT_MIX_STATES = [
    {"name": "Story Neutral Base", "seconds": 0.58, "transition_ratio": 0.84, "weights": {}},
    {"name": "Story Smile Spark", "seconds": 0.26, "transition_ratio": 0.62, "weights": {"BrowOuterUpLeft": 0.18, "BrowOuterUpRight": 0.12, "CheekSquintLeft": 0.10, "CheekSquintRight": 0.06, "MouthSmileLeft": 0.26, "MouthSmileRight": 0.14, "MouthDimpleLeft": 0.12, "MouthLeft": 0.05}},
    {"name": "Story Joy Bloom", "seconds": 0.34, "transition_ratio": 0.58, "weights": {"BrowOuterUpLeft": 0.34, "BrowOuterUpRight": 0.30, "EyeWideLeft": 0.12, "EyeWideRight": 0.10, "CheekSquintLeft": 0.26, "CheekSquintRight": 0.24, "MouthSmileLeft": 0.56, "MouthSmileRight": 0.48, "MouthDimpleLeft": 0.24, "MouthDimpleRight": 0.18, "JawOpen": 0.14}},
    {"name": "Story Full Laugh", "seconds": 0.28, "transition_ratio": 0.48, "weights": {"BrowOuterUpLeft": 0.44, "BrowOuterUpRight": 0.42, "CheekSquintLeft": 0.54, "CheekSquintRight": 0.52, "EyeSquintLeft": 0.20, "EyeSquintRight": 0.18, "MouthSmileLeft": 0.84, "MouthSmileRight": 0.78, "MouthDimpleLeft": 0.34, "MouthDimpleRight": 0.30, "MouthStretchLeft": 0.26, "MouthStretchRight": 0.24, "JawOpen": 0.76}},
    {"name": "Story Snap Shock", "seconds": 0.22, "transition_ratio": 0.42, "weights": {"BrowInnerUp": 0.76, "BrowOuterUpLeft": 0.52, "BrowOuterUpRight": 0.52, "EyeWideLeft": 1.0, "EyeWideRight": 1.0, "JawOpen": 1.0, "MouthFunnel": 0.28, "NoseSneerLeft": 0.08, "NoseSneerRight": 0.08}},
    {"name": "Story Fear Hold", "seconds": 0.30, "transition_ratio": 0.66, "weights": {"BrowInnerUp": 0.62, "BrowDownLeft": 0.24, "BrowDownRight": 0.24, "EyeWideLeft": 0.82, "EyeWideRight": 0.84, "JawOpen": 0.70, "MouthStretchLeft": 0.18, "MouthStretchRight": 0.22, "MouthLowerDownLeft": 0.22, "MouthLowerDownRight": 0.24}},
    {"name": "Story Sad Fold", "seconds": 0.40, "transition_ratio": 0.72, "weights": {"BrowInnerUp": 0.44, "BrowDownLeft": 0.36, "BrowDownRight": 0.34, "EyeLookDownLeft": 0.22, "EyeLookDownRight": 0.22, "MouthFrownLeft": 0.56, "MouthFrownRight": 0.52, "MouthShrugUpper": 0.24, "MouthShrugLower": 0.20, "JawOpen": 0.12}},
    {"name": "Story Disgust Clamp", "seconds": 0.34, "transition_ratio": 0.62, "weights": {"BrowDownLeft": 0.62, "BrowDownRight": 0.66, "EyeSquintLeft": 0.44, "EyeSquintRight": 0.42, "NoseSneerLeft": 0.62, "NoseSneerRight": 0.58, "MouthPressLeft": 0.48, "MouthPressRight": 0.46, "MouthRollUpper": 0.34, "JawForward": 0.28}},
    {"name": "Story Residual Smirk", "seconds": 0.28, "transition_ratio": 0.70, "weights": {"CheekSquintLeft": 0.16, "CheekSquintRight": 0.08, "MouthSmileLeft": 0.18, "MouthDimpleLeft": 0.12, "MouthLeft": 0.08}},
    {"name": "Story Return Neutral", "seconds": 0.52, "transition_ratio": 0.86, "weights": {}},
]
FULL_VALIDATION_TEXT_MIX_STATES = []
FULL_VALIDATION_REFERENCE_STATE_NAMES = (
    "Reference Happy Wide Smile",
    "Reference Puff Kiss Blink",
    "Reference Crooked Smirk",
    "Reference Tongue Open Left",
    "Reference Upper Left Corner",
)
FULL_VALIDATION_STATES = [
    {"name": "Alert Brows Soft", "seconds": 0.18, "transition_ratio": 0.58, "weights": {"BrowInnerUp": 0.34, "BrowOuterUpLeft": 0.30, "BrowOuterUpRight": 0.28, "EyeWideLeft": 0.22, "EyeWideRight": 0.20}},
    {"name": "Alert Brows Peak", "seconds": 0.16, "transition_ratio": 0.46, "weights": {"BrowInnerUp": 0.70, "BrowOuterUpLeft": 0.58, "BrowOuterUpRight": 0.56, "EyeWideLeft": 0.44, "EyeWideRight": 0.42}},
    {"name": "Blink Compression", "seconds": 0.14, "transition_ratio": 0.34, "weights": {"EyeBlinkLeft": 0.92, "EyeBlinkRight": 0.88, "BrowDownLeft": 0.18, "BrowDownRight": 0.16, "CheekSquintLeft": 0.18, "CheekSquintRight": 0.16}},
    {"name": "Squint Focus Left Bias", "seconds": 0.16, "transition_ratio": 0.44, "weights": {"EyeSquintLeft": 0.72, "EyeSquintRight": 0.58, "BrowDownLeft": 0.44, "BrowDownRight": 0.30, "CheekSquintLeft": 0.28, "CheekSquintRight": 0.18}},
    {"name": "Squint Focus Right Bias", "seconds": 0.16, "transition_ratio": 0.44, "weights": {"EyeSquintLeft": 0.58, "EyeSquintRight": 0.72, "BrowDownLeft": 0.30, "BrowDownRight": 0.44, "CheekSquintLeft": 0.18, "CheekSquintRight": 0.28}},
    {"name": "Eye Look Up Anchor", "seconds": 0.16, "transition_ratio": 0.56, "weights": {"EyeLookUpLeft": 0.82, "EyeLookUpRight": 0.82, "BrowOuterUpLeft": 0.18, "BrowOuterUpRight": 0.18}},
    {"name": "Eye Look Down Anchor", "seconds": 0.16, "transition_ratio": 0.56, "weights": {"EyeLookDownLeft": 0.82, "EyeLookDownRight": 0.82, "BrowInnerUp": 0.12}},
    {"name": "Eye Look Left", "seconds": 0.15, "transition_ratio": 0.54, "weights": {"EyeLookOutLeft": 0.86, "EyeLookInRight": 0.86, "MouthLeft": 0.06}},
    {"name": "Eye Look Right", "seconds": 0.15, "transition_ratio": 0.54, "weights": {"EyeLookInLeft": 0.86, "EyeLookOutRight": 0.86, "MouthRight": 0.06}},
    {"name": "Nose Sneer Tension", "seconds": 0.18, "transition_ratio": 0.58, "weights": {"NoseSneerLeft": 0.74, "NoseSneerRight": 0.70, "BrowDownLeft": 0.16, "BrowDownRight": 0.16}},
    {"name": "Cheek Puff Hold", "seconds": 0.22, "transition_ratio": 0.64, "weights": {"CheekPuff": 0.96, "MouthClose": 0.34, "JawOpen": 0.22}},
    {"name": "Soft Smile Entry", "seconds": 0.22, "transition_ratio": 0.60, "weights": {"MouthSmileLeft": 0.46, "MouthSmileRight": 0.42, "MouthDimpleLeft": 0.18, "MouthDimpleRight": 0.16, "CheekSquintLeft": 0.14, "CheekSquintRight": 0.12}},
    {"name": "Full Smile Hold", "seconds": 0.24, "transition_ratio": 0.56, "weights": {"MouthSmileLeft": 0.88, "MouthSmileRight": 0.82, "MouthDimpleLeft": 0.34, "MouthDimpleRight": 0.30, "CheekSquintLeft": 0.34, "CheekSquintRight": 0.30, "EyeSquintLeft": 0.14, "EyeSquintRight": 0.12, "JawOpen": 0.16}},
    {"name": "Smile To Open Laugh", "seconds": 0.20, "transition_ratio": 0.48, "weights": {"MouthSmileLeft": 0.74, "MouthSmileRight": 0.68, "MouthStretchLeft": 0.24, "MouthStretchRight": 0.22, "CheekSquintLeft": 0.32, "CheekSquintRight": 0.28, "JawOpen": 0.56}},
    {"name": "Mouth Frown Fold", "seconds": 0.24, "transition_ratio": 0.66, "weights": {"MouthFrownLeft": 0.74, "MouthFrownRight": 0.70, "MouthShrugUpper": 0.28, "MouthShrugLower": 0.20, "BrowInnerUp": 0.14, "JawOpen": 0.10}},
    {"name": "Mouth Stretch Speech", "seconds": 0.20, "transition_ratio": 0.54, "weights": {"MouthStretchLeft": 0.76, "MouthStretchRight": 0.72, "MouthSmileLeft": 0.14, "MouthSmileRight": 0.12, "JawOpen": 0.18}},
    {"name": "Mouth Press Clamp", "seconds": 0.18, "transition_ratio": 0.50, "weights": {"MouthClose": 0.76, "MouthPressLeft": 0.56, "MouthPressRight": 0.56, "JawOpen": 0.18}},
    {"name": "Lip Roll Tension", "seconds": 0.18, "transition_ratio": 0.54, "weights": {"MouthRollUpper": 0.66, "MouthRollLower": 0.62, "MouthClose": 0.18}},
    {"name": "Vowel A Open", "seconds": 0.20, "transition_ratio": 0.50, "weights": {"JawOpen": 0.82, "MouthLowerDownLeft": 0.48, "MouthLowerDownRight": 0.50, "MouthUpperUpLeft": 0.18, "MouthUpperUpRight": 0.18}},
    {"name": "Vowel E Spread", "seconds": 0.18, "transition_ratio": 0.52, "weights": {"JawOpen": 0.34, "MouthStretchLeft": 0.56, "MouthStretchRight": 0.54, "MouthSmileLeft": 0.16, "MouthSmileRight": 0.14}},
    {"name": "Vowel I Narrow Smile", "seconds": 0.18, "transition_ratio": 0.54, "weights": {"JawOpen": 0.16, "MouthSmileLeft": 0.50, "MouthSmileRight": 0.48, "MouthStretchLeft": 0.30, "MouthStretchRight": 0.28, "MouthDimpleLeft": 0.18, "MouthDimpleRight": 0.16}},
    {"name": "Vowel O Funnel", "seconds": 0.20, "transition_ratio": 0.46, "weights": {"JawOpen": 0.46, "MouthFunnel": 0.78, "MouthPucker": 0.24}},
    {"name": "Vowel U Pucker", "seconds": 0.20, "transition_ratio": 0.44, "weights": {"JawOpen": 0.18, "MouthPucker": 0.72, "MouthFunnel": 0.38, "MouthClose": 0.16}},
    {"name": "Side Mouth Left", "seconds": 0.18, "transition_ratio": 0.52, "weights": {"MouthLeft": 0.82, "JawLeft": 0.36, "MouthStretchLeft": 0.22, "MouthSmileLeft": 0.12}},
    {"name": "Side Mouth Right", "seconds": 0.18, "transition_ratio": 0.52, "weights": {"MouthRight": 0.82, "JawRight": 0.36, "MouthStretchRight": 0.22, "MouthSmileRight": 0.12}},
    {"name": "Upper Left Asymmetry", "seconds": 0.18, "transition_ratio": 0.48, "weights": {"BrowDownLeft": 0.60, "EyeSquintLeft": 0.74, "EyeBlinkLeft": 0.32, "CheekSquintLeft": 0.34, "NoseSneerLeft": 0.16}},
    {"name": "Upper Right Asymmetry", "seconds": 0.18, "transition_ratio": 0.48, "weights": {"BrowDownRight": 0.60, "EyeSquintRight": 0.74, "EyeBlinkRight": 0.32, "CheekSquintRight": 0.34, "NoseSneerRight": 0.16}},
    {"name": "Mouth Left Asymmetry", "seconds": 0.20, "transition_ratio": 0.50, "weights": {"JawOpen": 0.24, "MouthLeft": 0.62, "MouthSmileLeft": 0.48, "MouthDimpleLeft": 0.28, "MouthStretchLeft": 0.28, "MouthLowerDownLeft": 0.16}},
    {"name": "Mouth Right Asymmetry", "seconds": 0.20, "transition_ratio": 0.50, "weights": {"JawOpen": 0.24, "MouthRight": 0.62, "MouthSmileRight": 0.48, "MouthDimpleRight": 0.28, "MouthStretchRight": 0.28, "MouthLowerDownRight": 0.16}},
    {"name": "Tongue Open Center", "seconds": 0.24, "transition_ratio": 0.46, "weights": {"TongueOut": 0.94, "JawOpen": 0.78, "MouthLowerDownLeft": 0.20, "MouthLowerDownRight": 0.20}},
    {"name": "Tongue Open Left", "seconds": 0.24, "transition_ratio": 0.46, "weights": {"TongueOut": 0.92, "JawOpen": 0.80, "MouthLeft": 0.34, "JawLeft": 0.20, "MouthStretchLeft": 0.16}},
    {"name": "Tongue Open Right", "seconds": 0.24, "transition_ratio": 0.46, "weights": {"TongueOut": 0.92, "JawOpen": 0.80, "MouthRight": 0.34, "JawRight": 0.20, "MouthStretchRight": 0.16}},
    {"name": "Tongue Circle Top", "seconds": 0.22, "transition_ratio": 0.50, "base_weights": {"JawOpen": 0.84, "TongueOut": 0.82}, "weights": {"MouthUpperUpLeft": 0.68, "MouthUpperUpRight": 0.68, "MouthShrugUpper": 0.18}},
    {"name": "Tongue Circle Top Right", "seconds": 0.20, "transition_ratio": 0.50, "base_weights": {"JawOpen": 0.84, "TongueOut": 0.82}, "weights": {"MouthRight": 0.40, "JawRight": 0.16, "MouthUpperUpRight": 0.68, "MouthStretchRight": 0.14}},
    {"name": "Tongue Circle Right", "seconds": 0.20, "transition_ratio": 0.50, "base_weights": {"JawOpen": 0.84, "TongueOut": 0.82}, "weights": {"MouthRight": 0.72, "JawRight": 0.34, "MouthStretchRight": 0.22}},
    {"name": "Tongue Circle Bottom Right", "seconds": 0.20, "transition_ratio": 0.50, "base_weights": {"JawOpen": 0.84, "TongueOut": 0.82}, "weights": {"MouthRight": 0.40, "JawRight": 0.16, "MouthLowerDownRight": 0.68, "MouthStretchRight": 0.14}},
    {"name": "Tongue Circle Bottom", "seconds": 0.22, "transition_ratio": 0.50, "base_weights": {"JawOpen": 0.84, "TongueOut": 0.82}, "weights": {"MouthLowerDownLeft": 0.72, "MouthLowerDownRight": 0.72, "MouthShrugLower": 0.18}},
    {"name": "Tongue Circle Bottom Left", "seconds": 0.20, "transition_ratio": 0.50, "base_weights": {"JawOpen": 0.84, "TongueOut": 0.82}, "weights": {"MouthLeft": 0.40, "JawLeft": 0.16, "MouthLowerDownLeft": 0.68, "MouthStretchLeft": 0.14}},
    {"name": "Tongue Circle Left", "seconds": 0.20, "transition_ratio": 0.50, "base_weights": {"JawOpen": 0.84, "TongueOut": 0.82}, "weights": {"MouthLeft": 0.72, "JawLeft": 0.34, "MouthStretchLeft": 0.22}},
    {"name": "Tongue Circle Top Left", "seconds": 0.20, "transition_ratio": 0.50, "base_weights": {"JawOpen": 0.84, "TongueOut": 0.82}, "weights": {"MouthLeft": 0.40, "JawLeft": 0.16, "MouthUpperUpLeft": 0.68, "MouthStretchLeft": 0.14}},
]
FULL_VALIDATION_CAPTURE_PROFILE = {
    "target_fps": 30.0,
    "intensity_scale": 0.92,
    "transition_seconds": 0.7,
    "idle": {
        "EyeLookDownLeft": 0.060,
        "EyeLookDownRight": 0.060,
        "BrowInnerUp": 0.040,
        "JawOpen": 0.020,
        "MouthClose": 0.055,
        "MouthFunnel": 0.020,
        "MouthPucker": 0.024,
        "MouthShrugLower": 0.050,
        "MouthShrugUpper": 0.036,
        "MouthRollLower": 0.030,
        "MouthSmileLeft": 0.022,
        "MouthSmileRight": 0.020,
        "MouthPressLeft": 0.026,
        "MouthPressRight": 0.026,
        "MouthStretchLeft": 0.030,
        "MouthStretchRight": 0.028,
        "CheekSquintLeft": 0.020,
        "CheekSquintRight": 0.020,
        "NoseSneerLeft": 0.040,
        "NoseSneerRight": 0.044,
    },
    "motifs": [
        {"name": "Capture Idle Observe", "seconds": 0.42, "transition_ratio": 0.72, "weights": {"EyeLookDownLeft": 0.16, "EyeLookDownRight": 0.16, "BrowInnerUp": 0.12, "MouthClose": 0.08, "MouthShrugLower": 0.10, "MouthStretchLeft": 0.06, "MouthStretchRight": 0.06, "NoseSneerLeft": 0.08, "NoseSneerRight": 0.09}},
        {"name": "Capture Smile Left Drift", "seconds": 0.34, "transition_ratio": 0.56, "weights": {"MouthSmileLeft": 0.22, "MouthSmileRight": 0.16, "MouthDimpleLeft": 0.08, "MouthStretchLeft": 0.16, "MouthStretchRight": 0.12, "CheekSquintLeft": 0.10, "CheekSquintRight": 0.08, "BrowInnerUp": 0.08}},
        {"name": "Capture Smile Right Drift", "seconds": 0.34, "transition_ratio": 0.56, "weights": {"MouthSmileLeft": 0.16, "MouthSmileRight": 0.22, "MouthDimpleRight": 0.08, "MouthStretchLeft": 0.12, "MouthStretchRight": 0.16, "CheekSquintLeft": 0.08, "CheekSquintRight": 0.10, "BrowInnerUp": 0.08}},
        {"name": "Capture Side Pull Left", "seconds": 0.30, "transition_ratio": 0.50, "weights": {"MouthLeft": 0.40, "JawLeft": 0.10, "MouthStretchLeft": 0.20, "MouthLowerDownLeft": 0.18, "NoseSneerLeft": 0.14}},
        {"name": "Capture Side Pull Right", "seconds": 0.30, "transition_ratio": 0.50, "weights": {"MouthRight": 0.34, "JawRight": 0.06, "MouthStretchRight": 0.18, "MouthLowerDownRight": 0.18, "NoseSneerRight": 0.16}},
        {"name": "Capture Speech Open", "seconds": 0.30, "transition_ratio": 0.46, "weights": {"JawOpen": 0.42, "MouthStretchLeft": 0.24, "MouthStretchRight": 0.22, "MouthLowerDownLeft": 0.26, "MouthLowerDownRight": 0.24, "JawForward": 0.12}},
        {"name": "Capture Funnel Lead", "seconds": 0.28, "transition_ratio": 0.42, "weights": {"MouthFunnel": 0.30, "MouthPucker": 0.26, "MouthClose": 0.10, "JawOpen": 0.12, "MouthShrugUpper": 0.10}},
        {"name": "Capture Scrunch Tension", "seconds": 0.32, "transition_ratio": 0.52, "weights": {"BrowDownLeft": 0.22, "BrowDownRight": 0.20, "EyeSquintLeft": 0.16, "EyeSquintRight": 0.16, "NoseSneerLeft": 0.24, "NoseSneerRight": 0.26, "MouthPressLeft": 0.14, "MouthPressRight": 0.14}},
        {"name": "Capture Eye Track Left", "seconds": 0.26, "transition_ratio": 0.48, "weights": {"EyeLookOutLeft": 0.20, "EyeLookInRight": 0.26, "BrowInnerUp": 0.08}},
        {"name": "Capture Eye Track Right", "seconds": 0.26, "transition_ratio": 0.48, "weights": {"EyeLookInLeft": 0.28, "EyeLookOutRight": 0.24, "BrowInnerUp": 0.08}},
        {"name": "Capture Tongue Flash", "seconds": 0.22, "transition_ratio": 0.40, "weights": {"TongueOut": 0.92, "JawOpen": 0.54, "MouthLowerDownLeft": 0.18, "MouthLowerDownRight": 0.18}},
    ],
    "peak_events": [
        {"name": "Capture Full Eye Close", "attack": 8, "hold": 4, "release": 8, "weights": {"EyeBlinkLeft": 0.98, "EyeBlinkRight": 0.97, "EyeSquintLeft": 0.66, "EyeSquintRight": 0.63, "BrowInnerUp": 0.18}},
        {"name": "Capture Wide Jaw Open", "attack": 7, "hold": 3, "release": 9, "weights": {"JawOpen": 0.96, "MouthLowerDownLeft": 0.72, "MouthLowerDownRight": 0.69, "MouthStretchLeft": 0.46, "MouthStretchRight": 0.43, "JawForward": 0.20}},
        {"name": "Capture Tight Pucker", "attack": 6, "hold": 3, "release": 8, "weights": {"MouthPucker": 0.95, "MouthFunnel": 0.76, "MouthShrugUpper": 0.42, "MouthClose": 0.20, "JawOpen": 0.12}},
        {"name": "Capture Cheek Puff Peak", "attack": 5, "hold": 3, "release": 7, "weights": {"CheekPuff": 0.74, "MouthPucker": 0.48, "MouthClose": 0.16, "NoseSneerLeft": 0.16, "NoseSneerRight": 0.18}},
        {"name": "Capture Single Blink Peak", "attack": 4, "hold": 1, "release": 5, "weights": {"EyeBlinkLeft": 0.88, "EyeBlinkRight": 0.94, "EyeSquintLeft": 0.12, "EyeSquintRight": 0.14}},
    ],
}
_FULL_VALIDATION_RUNTIME_DEFAULTS = {
    "running": False,
    "paused": False,
    "token": 0,
    "current_label": "",
    "current_values": "",
    "status": "",
    "paused_at": 0.0,
    "pause_accumulated": 0.0,
    "current_index": 0,
    "current_factor": 0.0,
    "total": 0,
}
_FULL_VALIDATION_RUNTIME = dict(_FULL_VALIDATION_RUNTIME_DEFAULTS)
_FULL_VALIDATION_RUNTIME_BY_SCOPE = {}
_FULL_VALIDATION_RUNTIME_KEYS = {
    "running": "arkit_fv_running",
    "paused": "arkit_fv_paused",
    "token": "arkit_fv_token",
    "current_label": "arkit_fv_label",
    "current_values": "arkit_fv_values",
    "status": "arkit_fv_status",
    "paused_at": "arkit_fv_paused_at",
    "pause_accumulated": "arkit_fv_pause_accumulated",
    "current_index": "arkit_fv_index",
    "current_factor": "arkit_fv_factor",
    "total": "arkit_fv_total",
}
_VALIDATION_PREVIEW_STORE_KEYS = {
    "object_name": "arkit_preview_object_name",
    "active_index": "arkit_preview_active_index",
    "values_json": "arkit_preview_values_json",
    "action_name": "arkit_preview_action_name",
    "frame_start": "arkit_preview_frame_start",
    "frame_end": "arkit_preview_frame_end",
    "frame_current": "arkit_preview_frame_current",
    "render_fps": "arkit_preview_render_fps",
    "render_fps_base": "arkit_preview_render_fps_base",
}
_FULL_VALIDATION_PLAN_KEY = "arkit_fv_plan_json"
_MODULE_RUNTIME_STORE_ROOT = "_go_workflow_module_state"


def _runtime_slug(text, fallback):
    value = re.sub(r"[^0-9A-Za-z_\-]+", "_", str(text or ""))
    value = value.strip("_")
    return value or fallback


def _module_runtime_store_snapshot(scene, workflow, module):
    if scene is None:
        return {}
    root_store = scene.get(_MODULE_RUNTIME_STORE_ROOT)
    if not isinstance(root_store, dict):
        return {}
    workflow_store = root_store.get(_runtime_slug(getattr(workflow, "name", ""), "workflow"))
    if not isinstance(workflow_store, dict):
        return {}
    module_store = workflow_store.get(_runtime_slug(getattr(module, "name", ""), "module"))
    return dict(module_store) if isinstance(module_store, dict) else {}


def _full_validation_runtime_scope_key(scene=None, workflow=None, module=None):
    scene_pointer = ""
    scene_name = ""
    workflow_name = ""
    module_name = ""
    try:
        if scene is not None and hasattr(scene, "as_pointer"):
            scene_pointer = str(int(scene.as_pointer()))
    except Exception:
        scene_pointer = ""
    try:
        scene_name = str(getattr(scene, "name_full", "") or getattr(scene, "name", "") or "")
    except Exception:
        scene_name = ""
    try:
        workflow_name = str(getattr(workflow, "name", "") or "")
    except Exception:
        workflow_name = ""
    try:
        module_name = str(getattr(module, "name", "") or "")
    except Exception:
        module_name = ""
    return (
        _runtime_slug(scene_pointer or scene_name, "scene"),
        _runtime_slug(workflow_name, "workflow"),
        _runtime_slug(module_name, "module"),
    )


def _update_module_runtime_store(scene, workflow, module, updates):
    if scene is None:
        return {}
    root_store = scene.get(_MODULE_RUNTIME_STORE_ROOT)
    if not isinstance(root_store, dict):
        root_store = {}
    else:
        root_store = dict(root_store)
    workflow_key = _runtime_slug(getattr(workflow, "name", ""), "workflow")
    module_key = _runtime_slug(getattr(module, "name", ""), "module")
    workflow_store = root_store.get(workflow_key)
    if not isinstance(workflow_store, dict):
        workflow_store = {}
    else:
        workflow_store = dict(workflow_store)
    module_store = workflow_store.get(module_key)
    if not isinstance(module_store, dict):
        module_store = {}
    else:
        module_store = dict(module_store)
    module_store.update(dict(updates or {}))
    workflow_store[module_key] = module_store
    root_store[workflow_key] = workflow_store
    scene[_MODULE_RUNTIME_STORE_ROOT] = root_store
    return module_store


def _full_validation_runtime(module_state=None, scene=None, workflow=None, module=None):
    scope_key = _full_validation_runtime_scope_key(scene=scene, workflow=workflow, module=module)
    runtime = dict(_FULL_VALIDATION_RUNTIME_BY_SCOPE.get(scope_key, _FULL_VALIDATION_RUNTIME_DEFAULTS))
    state_source = {}
    if module_state is not None and hasattr(module_state, "to_dict"):
        state_source = module_state.to_dict()
    elif module_state is not None:
        try:
            state_source = dict(module_state.items())
        except Exception:
            state_source = {}
    if scene is not None and workflow is not None and module is not None:
        scene_state_source = _module_runtime_store_snapshot(scene, workflow, module)
        if scene_state_source:
            merged = dict(state_source)
            merged.update(scene_state_source)
            state_source = merged
    for logical_key, store_key in _FULL_VALIDATION_RUNTIME_KEYS.items():
        if store_key in state_source:
            runtime[logical_key] = state_source.get(store_key)
    for logical_key, default_value in _FULL_VALIDATION_RUNTIME_DEFAULTS.items():
        value = runtime.get(logical_key, default_value)
        if isinstance(default_value, bool):
            runtime[logical_key] = bool(value)
        elif isinstance(default_value, int):
            try:
                runtime[logical_key] = int(value)
            except Exception:
                runtime[logical_key] = int(default_value)
        elif isinstance(default_value, float):
            try:
                runtime[logical_key] = float(value)
            except Exception:
                runtime[logical_key] = float(default_value)
        else:
            runtime[logical_key] = str(value or "")
    _FULL_VALIDATION_RUNTIME_BY_SCOPE[scope_key] = dict(runtime)
    _FULL_VALIDATION_RUNTIME.clear()
    _FULL_VALIDATION_RUNTIME.update(runtime)
    return runtime


def _set_full_validation_runtime(scene, workflow, module, module_state=None, **kwargs):
    persist = bool(kwargs.pop("_persist", True))
    scope_key = _full_validation_runtime_scope_key(scene=scene, workflow=workflow, module=module)
    runtime = dict(_FULL_VALIDATION_RUNTIME_BY_SCOPE.get(scope_key, _FULL_VALIDATION_RUNTIME_DEFAULTS))
    updates = {}
    for logical_key, value in kwargs.items():
        store_key = _FULL_VALIDATION_RUNTIME_KEYS.get(logical_key)
        if not store_key:
            continue
        updates[store_key] = value
        if persist and module_state is not None:
            module_state.set(store_key, value)
    if updates and persist:
        _update_module_runtime_store(scene, workflow, module, updates)
    if updates:
        for logical_key, store_key in _FULL_VALIDATION_RUNTIME_KEYS.items():
            if store_key in updates:
                runtime[logical_key] = updates[store_key]
    if persist:
        runtime = _full_validation_runtime(module_state, scene, workflow, module)
    _FULL_VALIDATION_RUNTIME_BY_SCOPE[scope_key] = dict(runtime)
    _FULL_VALIDATION_RUNTIME.clear()
    _FULL_VALIDATION_RUNTIME.update(runtime)
    return dict(runtime)


def _module_store_value(scene, workflow, module, key, default=None):
    snapshot = _module_runtime_store_snapshot(scene, workflow, module)
    return snapshot.get(key, default)


def _set_module_store_values(scene, workflow, module, module_state=None, **kwargs):
    updates = {}
    for key, value in dict(kwargs or {}).items():
        updates[str(key)] = value
        if module_state is not None:
            module_state.set(str(key), value)
    if updates:
        _update_module_runtime_store(scene, workflow, module, updates)
    return _module_runtime_store_snapshot(scene, workflow, module)


def _clear_module_store_values(scene, workflow, module, module_state=None, *keys):
    keys = [str(key) for key in keys if str(key or "").strip()]
    if not keys:
        return
    root_store = scene.get(_MODULE_RUNTIME_STORE_ROOT) if scene is not None else None
    if isinstance(root_store, dict):
        root_store = dict(root_store)
        workflow_key = _runtime_slug(getattr(workflow, "name", ""), "workflow")
        module_key = _runtime_slug(getattr(module, "name", ""), "module")
        workflow_store = root_store.get(workflow_key)
        if isinstance(workflow_store, dict):
            workflow_store = dict(workflow_store)
            module_store = workflow_store.get(module_key)
            if isinstance(module_store, dict):
                module_store = dict(module_store)
                for key in keys:
                    module_store.pop(key, None)
                workflow_store[module_key] = module_store
                root_store[workflow_key] = workflow_store
                scene[_MODULE_RUNTIME_STORE_ROOT] = root_store
    if module_state is not None:
        for key in keys:
            module_state.set(key, "")


def _full_validation_plan(module_state=None, scene=None, workflow=None, module=None):
    raw = ""
    if module_state is not None:
        raw = str(module_state.get(_FULL_VALIDATION_PLAN_KEY, "") or "").strip()
    if not raw and scene is not None and workflow is not None and module is not None:
        raw = str(_module_store_value(scene, workflow, module, _FULL_VALIDATION_PLAN_KEY, "") or "").strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    if not isinstance(payload.get("segments"), list):
        payload["segments"] = []
    return payload


def _set_full_validation_plan(scene, workflow, module, module_state=None, plan=None):
    payload = dict(plan or {})
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if module_state is not None:
        module_state.set(_FULL_VALIDATION_PLAN_KEY, raw)
    _update_module_runtime_store(scene, workflow, module, {_FULL_VALIDATION_PLAN_KEY: raw})
    return payload


def _clear_full_validation_plan(scene, workflow, module, module_state=None):
    _clear_module_store_values(scene, workflow, module, module_state, _FULL_VALIDATION_PLAN_KEY)


def _iter_candidate_preset_dirs():
    seen = set()
    for raw_path in (globals().get("__file__", ""), getattr(globals().get("module", None), "script_path", "")):
        path = str(raw_path or "").strip()
        if not path:
            continue
        folder = os.path.dirname(os.path.abspath(path))
        if folder and folder not in seen:
            seen.add(folder)
            yield folder
    if DEFAULT_PRESET_DIR not in seen:
        seen.add(DEFAULT_PRESET_DIR)
        yield DEFAULT_PRESET_DIR
    for root in bpy.utils.script_paths():
        try:
            target = os.path.join(root, "extensions", "user_default", "go_workflow", "special_presets")
            if os.path.isfile(os.path.join(target, f"{PRESET_BASENAME}.json")) and target not in seen:
                seen.add(target)
                yield target
        except Exception:
            continue


def _preset_paths():
    state = _reference_runtime_state()
    cached = state.get("preset_paths")
    if isinstance(cached, dict) and cached.get("data_file"):
        return cached
    for preset_dir in _iter_candidate_preset_dirs():
        data_file = os.path.join(preset_dir, f"{PRESET_BASENAME}.json")
        if not os.path.isfile(data_file):
            continue
        cached = {
            "preset_dir": preset_dir,
            "data_file": data_file,
            "image_dir": os.path.join(preset_dir, f"{PRESET_BASENAME}_images"),
            "local_preview_dir": os.path.join(preset_dir, LOCAL_PREVIEW_DIRNAME),
            "panel_static_preview_dir": os.path.join(preset_dir, PANEL_STATIC_PREVIEW_DIRNAME),
            "viewer_state_file": os.path.join(preset_dir, f"{PRESET_BASENAME}_viewer_state_main.json"),
            "detail_viewer_state_file": os.path.join(preset_dir, f"{PRESET_BASENAME}_viewer_state_detail.json"),
            "viewer_script_file": os.path.join(preset_dir, "arkit_reference_viewer.ps1"),
        }
        state["preset_paths"] = cached
        return cached
    cached = {
        "preset_dir": DEFAULT_PRESET_DIR,
        "data_file": os.path.join(DEFAULT_PRESET_DIR, f"{PRESET_BASENAME}.json"),
        "image_dir": os.path.join(DEFAULT_PRESET_DIR, f"{PRESET_BASENAME}_images"),
        "local_preview_dir": os.path.join(DEFAULT_PRESET_DIR, LOCAL_PREVIEW_DIRNAME),
        "panel_static_preview_dir": os.path.join(DEFAULT_PRESET_DIR, PANEL_STATIC_PREVIEW_DIRNAME),
        "viewer_state_file": os.path.join(DEFAULT_PRESET_DIR, f"{PRESET_BASENAME}_viewer_state_main.json"),
        "detail_viewer_state_file": os.path.join(DEFAULT_PRESET_DIR, f"{PRESET_BASENAME}_viewer_state_detail.json"),
        "viewer_script_file": os.path.join(DEFAULT_PRESET_DIR, "arkit_reference_viewer.ps1"),
    }
    state["preset_paths"] = cached
    return cached


def _load_payload():
    state = _reference_runtime_state()
    payload_cache = state.get("payload_cache", {})
    data_file = _preset_paths()["data_file"]
    now = time.perf_counter()
    if (
        payload_cache.get("payload") is not None
        and payload_cache.get("data_file") == data_file
        and (now - float(payload_cache.get("checked_at", 0.0) or 0.0)) < PAYLOAD_STAT_REFRESH_SECONDS
    ):
        return payload_cache["payload"]
    if not os.path.isfile(data_file):
        raise Exception("缺少 ARKit 形态键工作流参考数据文件")
    stat = os.stat(data_file)
    if (
        payload_cache.get("payload") is not None
        and payload_cache.get("data_file") == data_file
        and payload_cache.get("mtime_ns") == stat.st_mtime_ns
        and payload_cache.get("size") == stat.st_size
    ):
        payload_cache["checked_at"] = now
        return payload_cache["payload"]
    with open(data_file, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise Exception("ARKit 形态键工作流参考数据格式无效")
    payload_cache.clear()
    payload_cache.update(
        {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "data_file": data_file,
            "checked_at": now,
            "payload": payload,
            "items": tuple(list(payload.get("items", []) or [])),
        }
    )
    state["payload_cache"] = payload_cache
    try:
        preview_state = _preview_runtime_state()
        for cache_key in ("resolve_cache", "detail_lines_cache", "validation_mix_cache"):
            cache = preview_state.get(cache_key, {})
            if hasattr(cache, "clear"):
                cache.clear()
    except Exception:
        pass
    return payload


def _load_items():
    payload = _load_payload()
    payload_cache = _reference_runtime_state().get("payload_cache", {})
    items = payload_cache.get("items")
    if items is None:
        items = tuple(list(payload.get("items", []) or []))
        payload_cache["items"] = items
    if not items:
        raise Exception("ARKit 形态键工作流参考数据为空")
    return payload, items


def _panel_api():
    return globals().get("panel_api")


def _module_state():
    return globals().get("module_state")


def _settings(module):
    raw = getattr(module, "config_payload", "") or ""
    if not raw.strip():
        return {}
    state = _reference_runtime_state()
    cache = state.get("settings_cache", {})
    if cache.get("raw") == raw and isinstance(cache.get("data"), dict):
        return cache["data"]
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    data = data if isinstance(data, dict) else {}
    state["settings_cache"] = {"raw": raw, "data": data}
    return data


def _save_settings(module, data):
    raw = json.dumps(data or {}, ensure_ascii=False, sort_keys=True)
    module.config_payload = raw
    try:
        _reference_runtime_state()["settings_cache"] = {"raw": raw, "data": dict(data or {})}
    except Exception:
        pass


def _get_setting(module, key, default):
    return _settings(module).get(key, default)


def _set_setting(module, key, value):
    data = _settings(module)
    data[key] = value
    _save_settings(module, data)


def _selected(context):
    return list(getattr(context, "selected_objects", []) or [])


def _target_object(context, panel_api):
    obj = panel_api.get_object("target_object") if panel_api is not None else None
    if obj is None:
        selected = _selected(context)
        obj = selected[0] if selected else getattr(context, "object", None)
    if obj is None:
        raise Exception("请先选择目标物体")
    shape_keys = getattr(getattr(obj.data, "shape_keys", None), "key_blocks", None)
    if shape_keys is None:
        raise Exception("目标物体没有形态键")
    return obj, shape_keys


def _soft_target_error_messages():
    return ("请先选择目标物体", "目标物体没有形态键")


def _report_soft_target_issue(exc, panel_api, module_state):
    message = str(exc or "").strip()
    if not message or message not in _soft_target_error_messages():
        return False
    print(message, file=sys.stderr)
    if panel_api is not None:
        panel_api.set_status(message, level="WARNING")
    if module_state is not None:
        module_state.set("last_result", message)
    return True


def _step_index(panel_api, items):
    stored = panel_api.get_int("step_index", 1) if panel_api is not None else 1
    ui_value = max(1, int(stored))
    return max(0, min(ui_value - 1, len(items) - 1))


def _is_remote_media_path(value):
    text = str(value or "").strip().lower()
    return text.startswith("http://") or text.startswith("https://")


def _resolve_media_paths(item, field_name):
    item_key = _item_cache_key(item)
    state = _preview_runtime_state()
    resolve_cache = state.get("resolve_cache", {})
    cache_key = f"media::{item_key}::{field_name}"
    cached = resolve_cache.get(cache_key)
    if isinstance(cached, tuple):
        return list(cached)
    preset_dir = _preset_paths()["preset_dir"]
    image_dir = _preset_paths()["image_dir"]
    files = []
    for value in list(item.get(field_name, []) or []):
        text = str(value or "").strip()
        if not text:
            continue
        if _is_remote_media_path(text):
            files.append(text)
            continue
        full = value if os.path.isabs(value) else os.path.normpath(os.path.join(preset_dir, value))
        if os.path.isfile(full):
            files.append(full)
    if files or field_name != "media_files":
        resolve_cache[cache_key] = tuple(files)
        return files
    local_path = str(item.get("image_local_path", "") or "").strip()
    if local_path:
        full = local_path if os.path.isabs(local_path) else os.path.normpath(os.path.join(preset_dir, local_path))
        if os.path.isfile(full):
            result = [full]
            resolve_cache[cache_key] = tuple(result)
            return result
    hint = str(item.get("image_hint", "") or "").strip()
    if hint:
        full = os.path.normpath(os.path.join(image_dir, hint))
        if os.path.isfile(full):
            result = [full]
            resolve_cache[cache_key] = tuple(result)
            return result
    resolve_cache[cache_key] = tuple()
    return []


def _media_files(item):
    return _resolve_media_paths(item, "media_files")


def _detail_media_files(item):
    return _resolve_media_paths(item, "detail_media_files")


def _preview_media_files(item):
    return _resolve_media_paths(item, "preview_media_files")


def _detail_preview_media_files(item):
    return _resolve_media_paths(item, "detail_preview_media_files")


def _media_index(panel_api, item):
    files = _media_files(item)
    if not files:
        return 0
    index = panel_api.get_int("media_index", 0) if panel_api is not None else 0
    return max(0, min(int(index), len(files) - 1))


def _detail_media_index(panel_api, item):
    files = _detail_media_files(item)
    if not files:
        return 0
    index = panel_api.get_int("detail_media_index", 0) if panel_api is not None else 0
    return max(0, min(int(index), len(files) - 1))


def _current_item(panel_api, module_state):
    payload, items = _load_items()
    index = _step_index(panel_api, items)
    return payload, items, items[index], index, _media_index(panel_api, items[index])


def _normalize_shape_key_name(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _shape_key_names(items):
    return [str(item.get("shape_key", "") or "").strip() for item in items if str(item.get("shape_key", "") or "").strip()]


def _known_shape_key_map():
    try:
        _payload, items = _load_items()
    except Exception:
        return {}
    return {_normalize_shape_key_name(name): name for name in _shape_key_names(items)}


def _expand_mix_shape_key_name(name, known_map):
    normalized = _normalize_shape_key_name(name)
    if not normalized:
        return []
    direct = known_map.get(normalized)
    if direct:
        return [direct]
    expanded = []
    for side in ("left", "right"):
        side_name = known_map.get(f"{normalized}{side}")
        if side_name:
            expanded.append(side_name)
    return expanded


def _explicit_validation_sequences(item):
    key = _normalize_shape_key_name(item.get("shape_key", ""))
    return [list(seq) for seq in VALIDATION_SEQUENCE_RULES.get(key, [])]


def _clear_validation_preview_state(scene=None, workflow=None, module=None, module_state=None):
    _VALIDATION_PREVIEW_STATE["object_name"] = ""
    _VALIDATION_PREVIEW_STATE["active_index"] = 0
    _VALIDATION_PREVIEW_STATE["values"] = None
    if scene is not None and workflow is not None and module is not None:
        _clear_module_store_values(
            scene,
            workflow,
            module,
            module_state,
            *list(_VALIDATION_PREVIEW_STORE_KEYS.values()),
        )


def _capture_validation_preview_state(obj, key_blocks, scene=None, workflow=None, module=None, module_state=None):
    values = {}
    for index, key_block in enumerate(key_blocks):
        if index == 0:
            continue
        values[key_block.name] = float(getattr(key_block, "value", 0.0) or 0.0)
    action_name = ""
    shape_keys = getattr(obj.data, "shape_keys", None)
    animation_data = getattr(shape_keys, "animation_data", None) if shape_keys is not None else None
    current_action = getattr(animation_data, "action", None) if animation_data is not None else None
    if current_action is not None:
        action_name = str(getattr(current_action, "name_full", "") or getattr(current_action, "name", "") or "").strip()
    _VALIDATION_PREVIEW_STATE["object_name"] = obj.name_full
    _VALIDATION_PREVIEW_STATE["active_index"] = int(getattr(obj, "active_shape_key_index", 0) or 0)
    _VALIDATION_PREVIEW_STATE["values"] = values
    render = getattr(scene, "render", None) if scene is not None else None
    render_fps = int(getattr(render, "fps", 24) or 24) if render is not None else 24
    render_fps_base = float(getattr(render, "fps_base", 1.0) or 1.0) if render is not None else 1.0
    if scene is not None and workflow is not None and module is not None:
        _set_module_store_values(
            scene,
            workflow,
            module,
            module_state,
            **{
                _VALIDATION_PREVIEW_STORE_KEYS["object_name"]: obj.name_full,
                _VALIDATION_PREVIEW_STORE_KEYS["active_index"]: int(getattr(obj, "active_shape_key_index", 0) or 0),
                _VALIDATION_PREVIEW_STORE_KEYS["values_json"]: json.dumps(values, ensure_ascii=False, sort_keys=True),
                _VALIDATION_PREVIEW_STORE_KEYS["action_name"]: action_name,
                _VALIDATION_PREVIEW_STORE_KEYS["frame_start"]: int(getattr(scene, "frame_start", 1) or 1),
                _VALIDATION_PREVIEW_STORE_KEYS["frame_end"]: int(getattr(scene, "frame_end", 250) or 250),
                _VALIDATION_PREVIEW_STORE_KEYS["frame_current"]: int(getattr(scene, "frame_current", 1) or 1),
                _VALIDATION_PREVIEW_STORE_KEYS["render_fps"]: render_fps,
                _VALIDATION_PREVIEW_STORE_KEYS["render_fps_base"]: render_fps_base,
            }
        )


def _restore_validation_preview_state(scene=None, workflow=None, module=None, module_state=None):
    values = _VALIDATION_PREVIEW_STATE.get("values")
    object_name = str(_VALIDATION_PREVIEW_STATE.get("object_name", "") or "").strip()
    active_index = int(_VALIDATION_PREVIEW_STATE.get("active_index", 0) or 0)
    action_name = ""
    frame_start = int(getattr(scene, "frame_start", 1) or 1) if scene is not None else 1
    frame_end = int(getattr(scene, "frame_end", 250) or 250) if scene is not None else 250
    frame_current = int(getattr(scene, "frame_current", 1) or 1) if scene is not None else 1
    render = getattr(scene, "render", None) if scene is not None else None
    render_fps = int(getattr(render, "fps", 24) or 24) if render is not None else 24
    render_fps_base = float(getattr(render, "fps_base", 1.0) or 1.0) if render is not None else 1.0
    if scene is not None and workflow is not None and module is not None:
        stored_object_name = str(_module_store_value(scene, workflow, module, _VALIDATION_PREVIEW_STORE_KEYS["object_name"], "") or "").strip()
        if stored_object_name:
            object_name = stored_object_name
        raw_values = _module_store_value(scene, workflow, module, _VALIDATION_PREVIEW_STORE_KEYS["values_json"], "")
        if raw_values:
            try:
                parsed_values = json.loads(str(raw_values))
            except Exception:
                parsed_values = None
            if isinstance(parsed_values, dict):
                values = {str(name): float(value) for name, value in parsed_values.items()}
        try:
            active_index = int(_module_store_value(scene, workflow, module, _VALIDATION_PREVIEW_STORE_KEYS["active_index"], active_index) or 0)
        except Exception:
            active_index = int(active_index or 0)
        action_name = str(_module_store_value(scene, workflow, module, _VALIDATION_PREVIEW_STORE_KEYS["action_name"], "") or "").strip()
        try:
            frame_start = int(_module_store_value(scene, workflow, module, _VALIDATION_PREVIEW_STORE_KEYS["frame_start"], frame_start) or frame_start)
            frame_end = int(_module_store_value(scene, workflow, module, _VALIDATION_PREVIEW_STORE_KEYS["frame_end"], frame_end) or frame_end)
            frame_current = int(_module_store_value(scene, workflow, module, _VALIDATION_PREVIEW_STORE_KEYS["frame_current"], frame_current) or frame_current)
        except Exception:
            pass
        try:
            render_fps = int(_module_store_value(scene, workflow, module, _VALIDATION_PREVIEW_STORE_KEYS["render_fps"], render_fps) or render_fps)
        except Exception:
            render_fps = int(render_fps or 24)
        try:
            render_fps_base = float(_module_store_value(scene, workflow, module, _VALIDATION_PREVIEW_STORE_KEYS["render_fps_base"], render_fps_base) or render_fps_base)
        except Exception:
            render_fps_base = float(render_fps_base or 1.0)
    if not values or not object_name:
        _clear_validation_preview_state(scene, workflow, module, module_state)
        return False
    obj = bpy.data.objects.get(object_name)
    restored = False
    if obj is not None:
        shape_keys = getattr(obj.data, "shape_keys", None)
        key_blocks = getattr(shape_keys, "key_blocks", None)
        if key_blocks is not None:
            for index, key_block in enumerate(key_blocks):
                if index == 0:
                    continue
                if key_block.name in values:
                    key_block.value = float(values[key_block.name])
            if 0 <= active_index < len(key_blocks):
                obj.active_shape_key_index = active_index
            if shape_keys is not None:
                animation_data = shape_keys.animation_data_create()
                animation_data.action = bpy.data.actions.get(action_name) if action_name else None
            try:
                obj.data.update()
            except Exception:
                pass
            restored = True
    if scene is not None:
        scene_render = getattr(scene, "render", None)
        if scene_render is not None:
            try:
                scene_render.fps = max(1, int(render_fps))
            except Exception:
                pass
            try:
                scene_render.fps_base = max(0.001, float(render_fps_base))
            except Exception:
                pass
        scene.frame_start = int(frame_start)
        scene.frame_end = max(int(frame_start), int(frame_end))
        try:
            scene.frame_set(int(frame_current))
        except Exception:
            scene.frame_current = int(frame_current)
    _clear_validation_preview_state(scene, workflow, module, module_state)
    return restored


def _reset_shape_keys_to_basis(obj, key_blocks, active_index=0):
    if key_blocks is None:
        return
    for index, key_block in enumerate(key_blocks):
        if index == 0:
            continue
        try:
            key_block.value = 0.0
        except Exception:
            pass
    try:
        obj.active_shape_key_index = max(0, int(active_index))
    except Exception:
        pass
    try:
        obj.data.update()
    except Exception:
        pass


def _zero_known_shape_keys(key_blocks, items):
    known = {_normalize_shape_key_name(name): name for name in _shape_key_names(items)}
    for index, key_block in enumerate(key_blocks):
        if index == 0:
            continue
        if _normalize_shape_key_name(getattr(key_block, "name", "")) in known:
            key_block.value = 0.0


def _activate_object(context, obj):
    view_layer = getattr(context, "view_layer", None)
    if view_layer is not None and getattr(view_layer.objects, "active", None) != obj:
        view_layer.objects.active = obj
    if not obj.select_get():
        obj.select_set(True)


def _switch_to_edit_mode(context, obj):
    _activate_object(context, obj)
    if getattr(obj, "mode", "") == "EDIT":
        return
    bpy.ops.object.mode_set(mode="EDIT")


def _switch_to_object_mode(context, obj):
    _activate_object(context, obj)
    if getattr(obj, "mode", "") == "OBJECT":
        return
    bpy.ops.object.mode_set(mode="OBJECT")


def _find_matching_shape_key(key_blocks, shape_key_name):
    target = _normalize_shape_key_name(shape_key_name)
    if not target:
        return None
    for index, key_block in enumerate(key_blocks):
        if index == 0:
            continue
        if _normalize_shape_key_name(getattr(key_block, "name", "")) == target:
            return key_block
    return None


def _resolved_state_active_name(state):
    for entry in list(state.get("weights", []) or []):
        if entry and entry[0]:
            return entry[0]
    for entry in list(state.get("base_weights", []) or []):
        if entry and entry[0]:
            return entry[0]
    return ""


def _set_active_shape_key_index(obj, key_blocks, shape_key_name):
    target = _normalize_shape_key_name(shape_key_name)
    if not target:
        return False
    for index, key_block in enumerate(key_blocks):
        if _normalize_shape_key_name(getattr(key_block, "name", "")) == target:
            obj.active_shape_key_index = index
            return True
    return False


def _report_missing_shape_key_soft(shape_key_name, panel_api, module_state, prefix="目标物体缺少同名形态键"):
    message = f"{prefix}: {shape_key_name}"
    print(message, file=sys.stderr)
    if panel_api is not None:
        panel_api.set_status(message, level="WARNING")
    if module_state is not None:
        module_state.set("last_missing_shape_key", shape_key_name)
        module_state.set("last_result", message)
    return None


def _focus_current_shape_key(context, panel_api, module_state, item):
    try:
        obj, key_blocks = _target_object(context, panel_api)
    except Exception as exc:
        if _report_soft_target_issue(exc, panel_api, module_state):
            return None
        raise
    shape_key_name = str(item.get("shape_key", "") or "").strip()
    if not shape_key_name:
        raise Exception("当前步骤没有对应形态键名称")
    _activate_object(context, obj)
    if not _set_active_shape_key_index(obj, key_blocks, shape_key_name):
        return _report_missing_shape_key_soft(shape_key_name, panel_api, module_state)
    if panel_api is not None:
        panel_api.set_status(f"已定位形态键: {shape_key_name}", level="OK")
    if module_state is not None:
        module_state.set("last_shape_key", shape_key_name)
    return obj, shape_key_name


def _set_step_and_focus(context, scene, workflow, module, panel_api, module_state, items, target_index):
    _stop_validation_animation(scene=scene, workflow=workflow, module=module, module_state=module_state)
    _restore_validation_preview_state(scene=scene, workflow=workflow, module=module, module_state=module_state)
    target_index = max(0, min(int(target_index), len(items) - 1))
    panel_api.set_int("step_index", target_index + 1)
    panel_api.set_int("media_index", 0)
    panel_api.set_int("detail_media_index", 0)
    item = items[target_index]
    _focus_current_shape_key(context, panel_api, module_state, item)
    return item, target_index


def _set_step_and_maybe_validate(context, scene, workflow, module, panel_api, module_state, items, target_index):
    item, target_index = _set_step_and_focus(context, scene, workflow, module, panel_api, module_state, items, target_index)
    auto_validate = bool(panel_api.get_bool("auto_validate_on_step", _get_setting(module, "auto_validate_on_step", False)))
    if auto_validate:
        _start_validation_animation(context, scene, workflow, module, panel_api, module_state, item, items)
    return item, target_index


def _validation_mix_lines(item):
    key = _item_cache_key(item)
    state = _preview_runtime_state()
    cache = state.get("validation_mix_cache", {})
    if key and key in cache:
        return list(cache[key])
    lines = []
    for source in list(item.get("validation_mix", []) or []):
        text = str(source or "").strip()
        if text and text not in lines:
            lines.append(text)
    for source in list(item.get("notes", []) or []):
        text = str(source or "").strip()
        if any(token in text for token in ("可与", "组合", "联动", "混合")) and text not in lines:
            lines.append(text)
    detail_text = str(item.get("detail_ja_zh") or item.get("detail_ja") or "").strip()
    known_shape_key_map = _known_shape_key_map()
    for left_name, right_name in re.findall(r"([A-Za-z][A-Za-z0-9_/-]*)\s*[+＋]\s*([A-Za-z][A-Za-z0-9_/-]*)", detail_text):
        mix_names = []
        for source_name in _expand_mix_shape_key_name(left_name, known_shape_key_map) + _expand_mix_shape_key_name(right_name, known_shape_key_map):
            if source_name and source_name not in mix_names:
                mix_names.append(source_name)
        if len(mix_names) < 2:
            continue
        line = "建议混合验证: " + " + ".join(mix_names)
        if line not in lines:
            lines.append(line)
    if key:
        cache[key] = tuple(lines)
        _trim_small_cache(cache, 256)
    return lines


def _has_validation_mix_hint(item):
    key = _item_cache_key(item)
    cache = _preview_runtime_state().get("validation_mix_cache", {})
    if key and key in cache:
        return bool(cache.get(key))
    if item.get("validation_mix"):
        return True
    for source in list(item.get("notes", []) or []):
        text = str(source or "")
        if any(token in text for token in ("\u53ef\u4e0e", "\u7ec4\u5408", "\u8054\u52a8", "\u6df7\u5408")):
            return True
    detail_text = str(item.get("detail_ja_zh") or item.get("detail_ja") or "")
    if not detail_text or not any(marker in detail_text for marker in ("+", "\uff0b", "\u3001")):
        return False
    known_shape_key_map = _known_shape_key_map()
    for left_name, right_name in re.findall(r"([A-Za-z][A-Za-z0-9_/-]*)\s*[+＋]\s*([A-Za-z][A-Za-z0-9_/-]*)", detail_text):
        mix_names = _expand_mix_shape_key_name(left_name, known_shape_key_map) + _expand_mix_shape_key_name(right_name, known_shape_key_map)
        if len(set(mix_names)) >= 2:
            return True
    return False


def _validation_shape_keys(item, items):
    known_map = {_normalize_shape_key_name(name): name for name in _shape_key_names(items)}
    ordered = []

    def append_name(name, allow_duplicate=False):
        normalized = _normalize_shape_key_name(name)
        if not normalized:
            return
        resolved = known_map.get(normalized, str(name or "").strip())
        if resolved and (allow_duplicate or resolved not in ordered):
            ordered.append(resolved)

    explicit_sequences = _explicit_validation_sequences(item)
    if explicit_sequences:
        for sequence in explicit_sequences:
            for shape_key_name in sequence:
                append_name(shape_key_name, allow_duplicate=True)
        return ordered

    append_name(item.get("shape_key", ""))
    text_sources = []
    text_sources.extend(list(item.get("validation_mix", []) or []))
    text_sources.extend(list(item.get("notes", []) or []))
    text_sources.extend(list(item.get("tips", []) or []))
    text_sources.extend(list(item.get("detail_notes", []) or []))
    text_sources.append(item.get("detail_ja_zh") or item.get("detail_ja") or "")
    for source in text_sources:
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_/-]*", str(source or "")):
            append_name(token)
    return ordered


def _full_validation_shape_keys(items):
    known_map = {_normalize_shape_key_name(name): name for name in _shape_key_names(items)}
    resolved = []
    for group in FULL_VALIDATION_SEQUENCE:
        current = []
        for shape_key_name in group:
            normalized = _normalize_shape_key_name(shape_key_name)
            if not normalized:
                continue
            resolved_name = known_map.get(normalized, str(shape_key_name or "").strip())
            if resolved_name and resolved_name not in current:
                current.append(resolved_name)
        if current:
            resolved.append(current)
    return resolved


def _full_validation_states(items):
    known_map = {_normalize_shape_key_name(name): name for name in _shape_key_names(items)}
    resolved_states = []
    for state in FULL_VALIDATION_STATES:
        resolved_weights = {}
        resolved_base_weights = {}
        for raw_name, raw_value in dict(state.get("weights", {}) or {}).items():
            normalized = _normalize_shape_key_name(raw_name)
            if not normalized:
                continue
            resolved_name = known_map.get(normalized)
            if not resolved_name:
                continue
            try:
                weight = max(0.0, min(1.0, float(raw_value)))
            except Exception:
                continue
            if weight <= 0.0:
                continue
            resolved_weights[resolved_name] = weight
        for raw_name, raw_value in dict(state.get("base_weights", {}) or {}).items():
            normalized = _normalize_shape_key_name(raw_name)
            if not normalized:
                continue
            resolved_name = known_map.get(normalized)
            if not resolved_name:
                continue
            try:
                weight = max(0.0, min(1.0, float(raw_value)))
            except Exception:
                continue
            if weight <= 0.0:
                continue
            resolved_base_weights[resolved_name] = weight
        if resolved_weights or resolved_base_weights:
            resolved_states.append(
                {
                    "name": str(state.get("name", "混合状态") or "混合状态"),
                    "weights": resolved_weights,
                    "base_weights": resolved_base_weights,
                }
            )
    return resolved_states


def _full_validation_state_bucket(state):
    key_text = " ".join(
        list(dict(state.get("weights", {}) or {}).keys())
        + list(dict(state.get("base_weights", {}) or {}).keys())
    ).lower()
    if any(token in key_text for token in ("eyeblink", "eyesquint", "eyelook", "eyewide", "brow")):
        return "upper"
    if any(token in key_text for token in ("cheek", "nose")):
        return "mid"
    if any(token in key_text for token in ("jaw", "mouth", "tongue")):
        return "mouth"
    return "other"


def _mouth_validation_order_key(state, original_index=0):
    weights = dict((state or {}).get("weights", {}) or {})
    base_weights = dict((state or {}).get("base_weights", {}) or {})
    values = {str(name or "").casefold(): float(value or 0.0) for name, value in {**base_weights, **weights}.items()}
    name_text = str((state or {}).get("name", "") or "").casefold()

    def has(*tokens):
        return any(token.casefold() in values for token in tokens) or any(token.casefold() in name_text for token in tokens)

    if has("cheekpuff", "mouthclose", "mouthpressleft", "mouthpressright", "mouthshrugupper", "mouthshruglower"):
        group = 0
    elif has("jawopen", "mouthlowerdownleft", "mouthlowerdownright", "mouthupperupleft", "mouthupperupright") and not has("tongueout"):
        group = 1
    elif has("mouthstretchleft", "mouthstretchright", "mouthsmileleft", "mouthsmileright", "mouthdimpleleft", "mouthdimpleright"):
        group = 2
    elif has("mouthfrownleft", "mouthfrownright"):
        group = 3
    elif has("mouthfunnel", "mouthpucker"):
        group = 4
    elif has("mouthrollupper", "mouthrolllower"):
        group = 5
    elif has("mouthleft", "mouthright", "jawleft", "jawright"):
        group = 6
    elif has("tongueout"):
        group = 7
    else:
        group = 8

    direction_score = 0
    if has("mouthright", "jawright"):
        direction_score += 1
    if has("mouthlowerdownright", "mouthlowerdownleft"):
        direction_score += 2
    if has("mouthleft", "jawleft"):
        direction_score += 4
    if has("mouthupperupright", "mouthupperupleft"):
        direction_score += 8
    return (group, direction_score, int(original_index))


def _interleave_full_validation_states(states):
    buckets = {"upper": [], "mid": [], "mouth": [], "other": []}
    for original_index, state in enumerate(list(states or [])):
        buckets.setdefault(_full_validation_state_bucket(state), []).append((original_index, state))
    buckets["mouth"].sort(key=lambda item: _mouth_validation_order_key(item[1], item[0]))
    order = []
    while any(buckets.get(bucket_name) for bucket_name in ("upper", "mid", "other")):
        for bucket_name in ("upper", "mid", "other", "upper"):
            bucket = buckets.get(bucket_name) or []
            if bucket:
                _original_index, state = bucket.pop(0)
                order.append(state)
    for _original_index, state in buckets.get("mouth") or []:
        order.append(state)
    return order


def _mouth_close_floor(values):
    normalized = dict(values or {})
    mouth_close_raw = normalized.get("MouthClose")
    if mouth_close_raw is None:
        return None
    try:
        mouth_close = max(0.0, min(1.0, float(mouth_close_raw)))
    except Exception:
        return None
    jaw_open_raw = normalized.get("JawOpen")
    try:
        jaw_open = 0.0 if jaw_open_raw is None else max(0.0, min(1.0, float(jaw_open_raw)))
    except Exception:
        jaw_open = 0.0
    if jaw_open < mouth_close:
        return mouth_close
    return None


def _value_key_by_normalized(values, normalized_name):
    normalized_name = str(normalized_name or "").strip()
    for key_name in dict(values or {}).keys():
        if _normalize_shape_key_name(key_name) == normalized_name:
            return key_name
    return ""


def _enforce_mouth_close_not_above_jaw_open(values):
    adjusted = {str(key_name): max(0.0, min(1.0, float(value or 0.0))) for key_name, value in dict(values or {}).items()}
    mouth_close_key = _value_key_by_normalized(adjusted, "mouthclose")
    if not mouth_close_key:
        return adjusted
    jaw_open_key = _value_key_by_normalized(adjusted, "jawopen") or "JawOpen"
    mouth_close = max(0.0, min(1.0, float(adjusted.get(mouth_close_key, 0.0) or 0.0)))
    jaw_open = max(0.0, min(1.0, float(adjusted.get(jaw_open_key, 0.0) or 0.0)))
    if mouth_close > jaw_open:
        jaw_open = mouth_close
        adjusted[jaw_open_key] = jaw_open
    adjusted[mouth_close_key] = min(mouth_close, jaw_open)
    return adjusted


def _blend_full_validation_state_maps(weighted_states):
    mixed_weights = {}
    mixed_base_weights = {}
    for scale, state in list(weighted_states or []):
        if not state:
            continue
        try:
            scale_value = max(0.0, float(scale))
        except Exception:
            scale_value = 0.0
        if scale_value <= 0.0:
            continue
        for name, value in dict(state.get("base_weights", {}) or {}).items():
            try:
                blended = max(0.0, min(1.0, float(value) * scale_value))
            except Exception:
                continue
            if blended <= 0.0:
                continue
            mixed_base_weights[name] = max(float(mixed_base_weights.get(name, 0.0) or 0.0), blended)
        for name, value in dict(state.get("weights", {}) or {}).items():
            try:
                blended = max(0.0, min(1.0, float(value) * scale_value))
            except Exception:
                continue
            if blended <= 0.0:
                continue
            mixed_weights[name] = max(float(mixed_weights.get(name, 0.0) or 0.0), blended)
    mouth_close_floor = _mouth_close_floor({**mixed_base_weights, **mixed_weights})
    if mouth_close_floor is not None:
        mixed_weights["JawOpen"] = max(float(mixed_weights.get("JawOpen", 0.0) or 0.0), float(mouth_close_floor))
    mixed_values = _enforce_mouth_close_not_above_jaw_open({**mixed_base_weights, **mixed_weights})
    for normalized_name in ("jawopen", "mouthclose"):
        value_key = _value_key_by_normalized(mixed_values, normalized_name)
        if value_key:
            mixed_weights[value_key] = mixed_values[value_key]
    return mixed_weights, mixed_base_weights


def _is_reference_full_validation_state(state):
    state_name = str((state or {}).get("name", "") or "").strip()
    return state_name in FULL_VALIDATION_REFERENCE_STATE_NAMES


def _expanded_full_validation_states(resolved_states, target_count=70):
    base_states = _interleave_full_validation_states([state for state in list(resolved_states or []) if state])
    if not base_states:
        return []
    reference_states = [state for state in base_states if _is_reference_full_validation_state(state)]
    reference_order = {name: index for index, name in enumerate(FULL_VALIDATION_REFERENCE_STATE_NAMES)}
    reference_states.sort(key=lambda state: reference_order.get(str(state.get("name", "") or "").strip(), len(reference_order)))
    core_states = [state for state in base_states if not _is_reference_full_validation_state(state)]
    if not core_states:
        return reference_states[: max(1, int(target_count))]
    target_before_reference = max(1, int(target_count) - len(reference_states))
    expanded = list(core_states)
    pair_offset = max(2, len(core_states) // 5)
    cursor = 0
    while len(expanded) < target_before_reference:
        first = core_states[cursor % len(core_states)]
        first_bucket = _full_validation_state_bucket(first)
        second = None
        third = None
        if first_bucket == "mouth":
            for offset in range(1, len(core_states) + 1):
                candidate = core_states[(cursor + offset) % len(core_states)]
                if _full_validation_state_bucket(candidate) == "mouth":
                    second = candidate
                    break
        else:
            for offset in range(1, len(core_states) + 1):
                candidate = core_states[(cursor + offset) % len(core_states)]
                if _full_validation_state_bucket(candidate) != first_bucket:
                    second = candidate
                    break
        if second is None:
            second = core_states[(cursor + pair_offset) % len(core_states)]
        second_bucket = _full_validation_state_bucket(second)
        if first_bucket != "mouth":
            for offset in range(pair_offset + 1, len(core_states) + pair_offset + 1):
                candidate = core_states[(cursor + offset) % len(core_states)]
                candidate_bucket = _full_validation_state_bucket(candidate)
                if candidate_bucket not in {first_bucket, second_bucket}:
                    third = candidate
                    break
        cursor += 1
        weighted_sources = [(1.0, first), (0.42 if first_bucket == "mouth" else 0.58, second)]
        if third is not None:
            weighted_sources.append((0.34, third))
        mixed_weights, mixed_base_weights = _blend_full_validation_state_maps(weighted_sources)
        if mixed_weights or mixed_base_weights:
            first_name = str(first.get("name", "mix") or "mix")
            second_name = str(second.get("name", "mix") or "mix")
            if third is not None:
                third_name = str(third.get("name", "mix") or "mix")
                if len({first_name, second_name, third_name}) > 1:
                    blend_name = f"{first_name} + {second_name} + {third_name}" if third_name not in {first_name, second_name} else f"{first_name} + {second_name}"
                else:
                    blend_name = first_name
            else:
                blend_name = f"{first_name} + {second_name}"
            if blend_name in {first_name, second_name} and first_name == second_name:
                continue
            expanded.append(
                {
                    "name": blend_name,
                    "weights": mixed_weights,
                    "base_weights": mixed_base_weights,
                }
            )
    expanded = expanded[:target_before_reference]
    if len(expanded) < target_before_reference:
        fallback_cycle = [state for state in core_states if _full_validation_state_bucket(state) != "other"] or core_states
        for index, state in enumerate(fallback_cycle):
            if len(expanded) >= target_before_reference:
                break
            anchor = fallback_cycle[(index + 1) % len(fallback_cycle)]
            cloned_weights = dict(state.get("weights", {}) or {})
            cloned_base_weights = dict(state.get("base_weights", {}) or {})
            for name, value in dict(anchor.get("weights", {}) or {}).items():
                cloned_weights[name] = max(float(cloned_weights.get(name, 0.0) or 0.0), float(value) * 0.35)
            for name, value in dict(anchor.get("base_weights", {}) or {}).items():
                cloned_base_weights[name] = max(float(cloned_base_weights.get(name, 0.0) or 0.0), float(value) * 0.25)
            expanded.append(
                {
                    "name": f"{state.get('name', 'mix')} / {anchor.get('name', 'mix')}",
                    "weights": cloned_weights,
                    "base_weights": cloned_base_weights,
                }
            )
    expanded.extend(reference_states)
    return expanded[: max(1, int(target_count))]


def _soften_opposing_target_values(target_values):
    softened = {str(key_name): max(0.0, min(1.0, float(value))) for key_name, value in dict(target_values or {}).items()}
    opposing_pairs = (
        ("BrowOuterUpLeft", "BrowDownLeft"),
        ("BrowOuterUpRight", "BrowDownRight"),
        ("EyeWideLeft", "EyeSquintLeft"),
        ("EyeWideRight", "EyeSquintRight"),
        ("EyeWideLeft", "EyeBlinkLeft"),
        ("EyeWideRight", "EyeBlinkRight"),
        ("MouthSmileLeft", "MouthFrownLeft"),
        ("MouthSmileRight", "MouthFrownRight"),
        ("MouthStretchLeft", "MouthPucker"),
        ("MouthStretchRight", "MouthPucker"),
        ("MouthLowerDownLeft", "MouthPressLeft"),
        ("MouthLowerDownRight", "MouthPressRight"),
    )
    for first_name, second_name in opposing_pairs:
        first_value = float(softened.get(first_name, 0.0) or 0.0)
        second_value = float(softened.get(second_name, 0.0) or 0.0)
        if first_value <= 0.0 or second_value <= 0.0:
            continue
        if first_value >= second_value:
            softened[second_name] = second_value * 0.22
        else:
            softened[first_name] = first_value * 0.22
    return softened


def _blend_target_value_maps(first_values, second_values, factor=0.5):
    mix_factor = max(0.0, min(1.0, float(factor)))
    blended = {}
    for key_name in set(dict(first_values or {}).keys()) | set(dict(second_values or {}).keys()):
        first_value = float(dict(first_values or {}).get(key_name, 0.0) or 0.0)
        second_value = float(dict(second_values or {}).get(key_name, 0.0) or 0.0)
        blended[key_name] = max(0.0, min(1.0, first_value + ((second_value - first_value) * mix_factor)))
    return blended


def _text_mix_bridge_value_map(previous_values, current_values):
    previous_map = dict(previous_values or {})
    current_map = dict(current_values or {})
    next_opens_wide = max(
        float(current_map.get("JawOpen", 0.0) or 0.0),
        float(current_map.get("EyeWideLeft", 0.0) or 0.0),
        float(current_map.get("EyeWideRight", 0.0) or 0.0),
    ) >= 0.60
    next_sad = max(
        float(current_map.get("MouthFrownLeft", 0.0) or 0.0),
        float(current_map.get("MouthFrownRight", 0.0) or 0.0),
        float(current_map.get("BrowDownLeft", 0.0) or 0.0),
        float(current_map.get("BrowDownRight", 0.0) or 0.0),
    ) >= 0.44
    next_disgust = max(
        float(current_map.get("NoseSneerLeft", 0.0) or 0.0),
        float(current_map.get("NoseSneerRight", 0.0) or 0.0),
        float(current_map.get("MouthPressLeft", 0.0) or 0.0),
        float(current_map.get("MouthPressRight", 0.0) or 0.0),
    ) >= 0.34

    def bridge_factor_for_key(key_name):
        normalized = str(key_name or "")
        if next_opens_wide:
            if normalized.startswith("Brow") or normalized.startswith("EyeWide"):
                return 0.58
            if normalized.startswith("Jaw") or normalized.startswith("MouthFunnel"):
                return 0.44
            if normalized.startswith("MouthSmile") or normalized.startswith("MouthDimple") or normalized.startswith("Cheek"):
                return 0.20
            return 0.34
        if next_disgust:
            if normalized.startswith("Nose") or normalized.startswith("MouthPress") or normalized.startswith("MouthUpperUp") or normalized.startswith("MouthRoll"):
                return 0.54
            if normalized.startswith("Brow") or normalized.startswith("Eye"):
                return 0.42
            return 0.48
        if next_sad:
            if normalized.startswith("Jaw") or normalized.startswith("MouthFrown") or normalized.startswith("MouthShrug") or normalized.startswith("MouthLowerDown"):
                return 0.60
            if normalized.startswith("Brow") or normalized.startswith("Eye"):
                return 0.42
            return 0.50
        return 0.46

    blended = {}
    for key_name in set(previous_map) | set(current_map):
        first_value = float(previous_map.get(key_name, 0.0) or 0.0)
        second_value = float(current_map.get(key_name, 0.0) or 0.0)
        factor = bridge_factor_for_key(key_name)
        blended[key_name] = max(0.0, min(1.0, first_value + ((second_value - first_value) * factor)))
    return blended, next_opens_wide


def _smoothed_text_mix_validation_states(resolved_states):
    states = list(resolved_states or [])
    if len(states) <= 1:
        return states

    smoothed_states = [dict(states[0])]
    previous_anchor = dict(states[0])
    previous_values = _state_target_values(previous_anchor)

    for state in states[1:]:
        current_state = dict(state)
        current_values = _state_target_values(current_state)
        union_keys = set(previous_values) | set(current_values)
        delta_sum = sum(abs(float(previous_values.get(key_name, 0.0) or 0.0) - float(current_values.get(key_name, 0.0) or 0.0)) for key_name in union_keys)
        delta_peak = max([abs(float(previous_values.get(key_name, 0.0) or 0.0) - float(current_values.get(key_name, 0.0) or 0.0)) for key_name in union_keys] or [0.0])
        if delta_sum >= 1.55 or delta_peak >= 0.48:
            bridge_values, next_opens_wide = _text_mix_bridge_value_map(previous_values, current_values)
            bridge_seconds = max(0.18, min(0.42, 0.16 + (delta_sum * 0.06)))
            smoothed_states.append(
                {
                    "name": f"{previous_anchor.get('name', 'mix')} Bridge",
                    "seconds": bridge_seconds,
                    "transition_ratio": 0.72 if next_opens_wide else 0.86,
                    "weights": bridge_values,
                    "base_weights": {},
                }
            )
        smoothed_states.append(current_state)
        previous_anchor = current_state
        previous_values = current_values
    return smoothed_states


def _state_target_values(state):
    target_values = dict(state.get("base_weights", {}) or {})
    for key_name, value in dict(state.get("weights", {}) or {}).items():
        target_values[key_name] = float(value)
    target_values = _soften_opposing_target_values(target_values)
    jaw_open = max(0.0, min(1.0, float(target_values.get("JawOpen", 0.0) or 0.0)))
    if "MouthClose" in target_values and jaw_open > 0.0:
        target_values["MouthClose"] = min(
            max(0.0, float(target_values.get("MouthClose", 0.0) or 0.0)),
            jaw_open,
        )
    return _enforce_mouth_close_not_above_jaw_open(target_values)


def _resolve_validation_state_specs_for_object(key_blocks, state_specs, allow_empty=False):
    resolved_states = []
    for state in list(state_specs or []):
        matched_weights = {}
        matched_base_weights = {}
        for shape_key_name, target_value in dict(state.get("base_weights", {}) or {}).items():
            target_key = _find_matching_shape_key(key_blocks, shape_key_name)
            if target_key is not None:
                matched_base_weights[target_key.name] = float(target_value)
        for shape_key_name, target_value in dict(state.get("weights", {}) or {}).items():
            target_key = _find_matching_shape_key(key_blocks, shape_key_name)
            if target_key is not None:
                matched_weights[target_key.name] = float(target_value)
        if matched_weights or matched_base_weights or allow_empty:
            resolved_state = {
                "name": str(state.get("name", "mix") or "mix"),
                "weights": matched_weights,
                "base_weights": matched_base_weights,
            }
            if "seconds" in state:
                resolved_state["seconds"] = max(0.1, float(state.get("seconds", 0.1) or 0.1))
            if "transition_ratio" in state:
                resolved_state["transition_ratio"] = max(0.35, min(0.95, float(state.get("transition_ratio", 0.82) or 0.82)))
            resolved_states.append(resolved_state)
    return resolved_states


def _resolve_text_mix_validation_states_for_object(key_blocks):
    return _smoothed_text_mix_validation_states(
        _resolve_validation_state_specs_for_object(key_blocks, FULL_VALIDATION_TEXT_MIX_STATES, allow_empty=True)
    )


def _resolve_full_validation_states_for_object(key_blocks, items, target_count=70):
    return _resolve_validation_state_specs_for_object(
        key_blocks,
        _expanded_full_validation_states(_full_validation_states(items), target_count=target_count),
    )
    resolved_states = []
    for state in _expanded_full_validation_states(_full_validation_states(items), target_count=target_count):
        matched_weights = {}
        matched_base_weights = {}
        for shape_key_name, target_value in dict(state.get("base_weights", {}) or {}).items():
            target_key = _find_matching_shape_key(key_blocks, shape_key_name)
            if target_key is not None:
                matched_base_weights[target_key.name] = float(target_value)
        for shape_key_name, target_value in dict(state.get("weights", {}) or {}).items():
            target_key = _find_matching_shape_key(key_blocks, shape_key_name)
            if target_key is not None:
                matched_weights[target_key.name] = float(target_value)
        if matched_weights or matched_base_weights:
            resolved_states.append(
                {
                    "name": str(state.get("name", "混合状态") or "混合状态"),
                    "weights": matched_weights,
                    "base_weights": matched_base_weights,
                }
            )
    return resolved_states

def _shape_keys_animation_data(obj):
    shape_keys = getattr(getattr(obj, "data", None), "shape_keys", None)
    if shape_keys is None:
        return None, None
    return shape_keys, shape_keys.animation_data_create()


def _full_validation_action_name(obj):
    return f"GoWorkflow_ARKitFullValidation_{_runtime_slug(getattr(obj, 'name_full', ''), 'Object')}"


def _ensure_full_validation_action(obj):
    shape_keys, animation_data = _shape_keys_animation_data(obj)
    if shape_keys is None or animation_data is None:
        raise Exception("目标物体没有可用于动画的形态键数据")
    action_name = _full_validation_action_name(obj)
    action = bpy.data.actions.get(action_name)
    if action is None:
        action = bpy.data.actions.new(action_name)
    action.use_fake_user = True
    for fcurve in list(action.fcurves):
        action.fcurves.remove(fcurve)
    animation_data.action = action
    return shape_keys, action


def _detach_full_validation_action(obj, plan=None):
    shape_keys, animation_data = _shape_keys_animation_data(obj)
    if animation_data is None:
        return None, None
    expected_names = {_full_validation_action_name(obj)}
    if isinstance(plan, dict):
        plan_action_name = str(plan.get("action_name", "") or "").strip()
        if plan_action_name:
            expected_names.add(plan_action_name)
    current_action = getattr(animation_data, "action", None)
    current_action_name = str(getattr(current_action, "name_full", "") or getattr(current_action, "name", "") or "").strip()
    if current_action_name in expected_names or current_action_name.startswith("GoWorkflow_ARKitFullValidation_"):
        animation_data.action = None
    return shape_keys, animation_data


def _insert_shape_key_frame(key_block_map, key_names, values, frame):
    frame = int(frame)
    values = _enforce_mouth_close_not_above_jaw_open(values)
    for key_name in key_names:
        key_block = key_block_map.get(key_name)
        if key_block is None:
            continue
        key_block.value = max(0.0, min(1.0, float(values.get(key_name, 0.0) or 0.0)))
        key_block.keyframe_insert(data_path="value", frame=frame)


def _smoothstep_factor(value):
    value = max(0.0, min(1.0, float(value)))
    return value * value * (3.0 - (2.0 * value))


def _segment_factor(segment, frame):
    if not segment:
        return 0.0
    start_frame = int(segment.get("start_frame", 0) or 0)
    peak_frame = int(segment.get("peak_frame", start_frame) or start_frame)
    hold_end_frame = int(segment.get("hold_end_frame", peak_frame) or peak_frame)
    end_frame = int(segment.get("end_frame", hold_end_frame) or hold_end_frame)
    frame = float(frame)
    if frame <= start_frame:
        return 0.0
    if peak_frame > start_frame and frame < peak_frame:
        return _smoothstep_factor(float(frame - start_frame) / float(peak_frame - start_frame))
    if frame <= hold_end_frame:
        return 1.0
    if end_frame > hold_end_frame and frame < end_frame:
        return max(0.0, min(1.0, 1.0 - (float(frame - hold_end_frame) / float(end_frame - hold_end_frame))))
    return 0.0


def _segment_interpolated_values(segment, frame):
    if not segment:
        return {}
    from_values = dict(segment.get("from_values", {}) or {})
    target_values = dict(segment.get("target_values", {}) or {})
    start_frame = float(segment.get("start_frame", 0) or 0)
    peak_frame = float(segment.get("peak_frame", start_frame) or start_frame)
    hold_end_frame = float(segment.get("hold_end_frame", peak_frame) or peak_frame)
    frame_value = float(frame)
    if frame_value <= start_frame:
        return _enforce_mouth_close_not_above_jaw_open(from_values)
    if peak_frame <= start_frame:
        return _enforce_mouth_close_not_above_jaw_open(target_values)
    if frame_value >= peak_frame:
        return _enforce_mouth_close_not_above_jaw_open(target_values)
    factor = _smoothstep_factor(float(frame_value - start_frame) / float(peak_frame - start_frame))
    values = {}
    for key_name in set(from_values) | set(target_values):
        start_value = float(from_values.get(key_name, 0.0) or 0.0)
        end_value = float(target_values.get(key_name, 0.0) or 0.0)
        values[key_name] = max(0.0, min(1.0, start_value + ((end_value - start_value) * factor)))
    if frame_value > hold_end_frame:
        return _enforce_mouth_close_not_above_jaw_open(target_values)
    return _enforce_mouth_close_not_above_jaw_open(values)


def _full_validation_states(items):
    known_map = {_normalize_shape_key_name(name): name for name in _shape_key_names(items)}
    resolved_states = []
    for state in FULL_VALIDATION_STATES:
        resolved_weights = {}
        resolved_base_weights = {}
        for source_name, source_value in dict(state.get("weights", {}) or {}).items():
            normalized = _normalize_shape_key_name(source_name)
            if not normalized:
                continue
            resolved_name = known_map.get(normalized)
            if not resolved_name:
                continue
            try:
                weight = max(0.0, min(1.0, float(source_value)))
            except Exception:
                continue
            if weight > 0.0:
                resolved_weights[resolved_name] = weight
        for source_name, source_value in dict(state.get("base_weights", {}) or {}).items():
            normalized = _normalize_shape_key_name(source_name)
            if not normalized:
                continue
            resolved_name = known_map.get(normalized)
            if not resolved_name:
                continue
            try:
                weight = max(0.0, min(1.0, float(source_value)))
            except Exception:
                continue
            if weight > 0.0:
                resolved_base_weights[resolved_name] = weight
        if not resolved_weights and not resolved_base_weights:
            continue
        resolved_state = {
            "name": str(state.get("name", "mix") or "mix"),
            "weights": resolved_weights,
            "base_weights": resolved_base_weights,
        }
        if "seconds" in state:
            try:
                resolved_state["seconds"] = max(0.1, float(state.get("seconds", 0.1) or 0.1))
            except Exception:
                pass
        if "transition_ratio" in state:
            try:
                resolved_state["transition_ratio"] = max(0.35, min(0.95, float(state.get("transition_ratio", 0.82) or 0.82)))
            except Exception:
                pass
        resolved_states.append(resolved_state)
    return resolved_states


def _full_validation_state_seconds(state, default_by_bucket=True):
    if default_by_bucket:
        bucket_defaults = {"upper": 0.17, "mid": 0.20, "mouth": 0.22, "other": 0.18}
        default_value = bucket_defaults.get(_full_validation_state_bucket(state), 0.18)
    else:
        default_value = 0.18
    try:
        return max(0.1, float((state or {}).get("seconds", default_value) or default_value))
    except Exception:
        return default_value


def _full_validation_state_transition_ratio(state):
    bucket_defaults = {"upper": 0.48, "mid": 0.58, "mouth": 0.52, "other": 0.60}
    default_value = bucket_defaults.get(_full_validation_state_bucket(state), 0.60)
    try:
        return max(0.35, min(0.95, float((state or {}).get("transition_ratio", default_value) or default_value)))
    except Exception:
        return default_value


def _full_validation_curve_mode(state):
    state = dict(state or {})
    keys = " ".join(list(dict(state.get("weights", {}) or {}).keys()) + list(dict(state.get("base_weights", {}) or {}).keys())).lower()
    if "bridge" in str(state.get("name", "")).lower():
        return "bridge"
    if "release" in str(state.get("name", "")).lower():
        return "release"
    if any(token in keys for token in ("eyeblink", "eyewide", "eyesquint", "brow")):
        return "upper"
    if any(token in keys for token in ("mouthfunnel", "mouthpucker", "jawopen")):
        return "speech"
    if any(token in keys for token in ("mouthsmile", "mouthfrown", "mouthstretch", "mouthpress", "mouthroll", "tongue")):
        return "mouth"
    return "smooth"


def _full_validation_eased_progress(value, mode):
    x = max(0.0, min(1.0, float(value)))
    if mode == "upper":
        return 1.0 - pow(1.0 - x, 2.4)
    if mode == "speech":
        if x <= 0.0:
            return 0.0
        if x >= 1.0:
            return 1.0
        return pow(x, 0.64)
    if mode == "mouth":
        if x < 0.28:
            y = x / 0.28
            return 0.55 * (y * y)
        if x < 0.78:
            y = (x - 0.28) / 0.50
            return 0.55 + (0.35 * y)
        y = (x - 0.78) / 0.22
        return 0.90 + (0.10 * (y * y * (3.0 - (2.0 * y))))
    if mode == "release":
        return 1.0 - pow(1.0 - x, 1.45)
    if mode == "bridge":
        return x * x * (3.0 - (2.0 * x))
    return x


def _build_full_validation_bridge_state(previous_state, current_state):
    previous_values = _state_target_values(previous_state)
    current_values = _state_target_values(current_state)
    union_keys = set(previous_values) | set(current_values)
    if not union_keys:
        return None
    delta_values = [abs(float(previous_values.get(key_name, 0.0) or 0.0) - float(current_values.get(key_name, 0.0) or 0.0)) for key_name in union_keys]
    delta_sum = sum(delta_values)
    delta_peak = max(delta_values or [0.0])
    previous_bucket = _full_validation_state_bucket(previous_state)
    current_bucket = _full_validation_state_bucket(current_state)
    if delta_sum < 1.0 and delta_peak < 0.40 and previous_bucket == current_bucket:
        return None
    if previous_bucket == "mouth" and current_bucket == "mouth" and delta_sum < 1.35 and delta_peak < 0.48:
        return None
    bridge_factor = 0.42 if current_bucket == "mouth" else 0.50
    if current_bucket == "upper":
        bridge_factor = 0.58
    bridge_values = _blend_target_value_maps(previous_values, current_values, factor=bridge_factor)
    bridge_seconds = max(0.12, min(0.34, (_full_validation_state_seconds(previous_state) + _full_validation_state_seconds(current_state)) * 0.48))
    bridge_ratio = 0.68 if current_bucket == "mouth" else 0.76
    return {
        "name": f"{previous_state.get('name', 'mix')} Bridge",
        "seconds": bridge_seconds,
        "transition_ratio": bridge_ratio,
        "weights": bridge_values,
        "base_weights": {},
    }


def _expanded_full_validation_states(resolved_states, target_count=70):
    base_states = _interleave_full_validation_states([state for state in list(resolved_states or []) if state])
    if not base_states:
        return []
    reference_states = [state for state in base_states if _is_reference_full_validation_state(state)]
    reference_order = {name: index for index, name in enumerate(FULL_VALIDATION_REFERENCE_STATE_NAMES)}
    reference_states.sort(key=lambda state: reference_order.get(str(state.get("name", "") or "").strip(), len(reference_order)))
    core_states = [state for state in base_states if not _is_reference_full_validation_state(state)]
    if not core_states:
        return reference_states[: max(1, int(target_count))]

    target_before_reference = max(1, int(target_count) - len(reference_states))
    expanded = list(core_states)
    pair_offset = max(2, len(core_states) // 5)
    cursor = 0
    while len(expanded) < target_before_reference:
        first = core_states[cursor % len(core_states)]
        first_bucket = _full_validation_state_bucket(first)
        second = None
        third = None
        search_count = len(core_states) + 1
        if first_bucket == "mouth":
            for offset in range(1, search_count):
                candidate = core_states[(cursor + offset) % len(core_states)]
                if _full_validation_state_bucket(candidate) == "mouth":
                    second = candidate
                    break
        else:
            for offset in range(1, search_count):
                candidate = core_states[(cursor + offset) % len(core_states)]
                if _full_validation_state_bucket(candidate) != first_bucket:
                    second = candidate
                    break
        if second is None:
            second = core_states[(cursor + pair_offset) % len(core_states)]
        second_bucket = _full_validation_state_bucket(second)
        if first_bucket != "mouth":
            for offset in range(pair_offset + 1, len(core_states) + pair_offset + 1):
                candidate = core_states[(cursor + offset) % len(core_states)]
                candidate_bucket = _full_validation_state_bucket(candidate)
                if candidate_bucket not in {first_bucket, second_bucket}:
                    third = candidate
                    break
        cursor += 1
        weighted_sources = [(1.0, first), (0.44 if first_bucket == "mouth" else 0.56, second)]
        if third is not None:
            weighted_sources.append((0.28, third))
        mixed_weights, mixed_base_weights = _blend_full_validation_state_maps(weighted_sources)
        if not mixed_weights and not mixed_base_weights:
            continue
        name_parts = [str(first.get("name", "mix") or "mix"), str(second.get("name", "mix") or "mix")]
        if third is not None:
            name_parts.append(str(third.get("name", "mix") or "mix"))
        expanded.append(
            {
                "name": " + ".join(dict.fromkeys(name_parts)),
                "weights": mixed_weights,
                "base_weights": mixed_base_weights,
                "seconds": max(0.12, min(0.30, sum(_full_validation_state_seconds(source_state) for _scale, source_state in weighted_sources) / len(weighted_sources) * 0.82)),
                "transition_ratio": 0.58 if first_bucket == "mouth" else 0.64,
            }
        )
    expanded = expanded[:target_before_reference]
    expanded.extend(reference_states)
    return expanded[: max(1, int(target_count))]


def _state_target_values(state):
    target_values = dict((state or {}).get("base_weights", {}) or {})
    for key_name, value in dict((state or {}).get("weights", {}) or {}).items():
        target_values[key_name] = float(value)
    target_values = _soften_opposing_target_values(target_values)

    jaw_open = max(0.0, min(1.0, float(target_values.get("JawOpen", 0.0) or 0.0)))
    blink_left = max(0.0, min(1.0, float(target_values.get("EyeBlinkLeft", 0.0) or 0.0)))
    blink_right = max(0.0, min(1.0, float(target_values.get("EyeBlinkRight", 0.0) or 0.0)))
    squint_left = max(0.0, min(1.0, float(target_values.get("EyeSquintLeft", 0.0) or 0.0)))
    squint_right = max(0.0, min(1.0, float(target_values.get("EyeSquintRight", 0.0) or 0.0)))
    smile_left = max(0.0, min(1.0, float(target_values.get("MouthSmileLeft", 0.0) or 0.0)))
    smile_right = max(0.0, min(1.0, float(target_values.get("MouthSmileRight", 0.0) or 0.0)))
    funnel = max(0.0, min(1.0, float(target_values.get("MouthFunnel", 0.0) or 0.0)))
    pucker = max(0.0, min(1.0, float(target_values.get("MouthPucker", 0.0) or 0.0)))

    if "EyeWideLeft" in target_values:
        target_values["EyeWideLeft"] = max(0.0, min(1.0, float(target_values.get("EyeWideLeft", 0.0) or 0.0) * (1.0 - (0.92 * blink_left)) * (1.0 - (0.74 * squint_left))))
    if "EyeWideRight" in target_values:
        target_values["EyeWideRight"] = max(0.0, min(1.0, float(target_values.get("EyeWideRight", 0.0) or 0.0) * (1.0 - (0.92 * blink_right)) * (1.0 - (0.74 * squint_right))))
    if "MouthClose" in target_values:
        target_values["MouthClose"] = max(0.0, min(1.0, float(target_values.get("MouthClose", 0.0) or 0.0) * (1.0 - (0.78 * jaw_open))))

    smile_hold_left = smile_left * (0.18 + (0.16 * jaw_open))
    smile_hold_right = smile_right * (0.18 + (0.16 * jaw_open))
    if jaw_open > 0.18:
        target_values["MouthSmileLeft"] = max(smile_left, smile_hold_left)
        target_values["MouthSmileRight"] = max(smile_right, smile_hold_right)
        target_values["CheekSquintLeft"] = max(float(target_values.get("CheekSquintLeft", 0.0) or 0.0), smile_left * 0.18)
        target_values["CheekSquintRight"] = max(float(target_values.get("CheekSquintRight", 0.0) or 0.0), smile_right * 0.18)

    if funnel > 0.0 or pucker > 0.0:
        target_values["JawOpen"] = max(jaw_open, max(funnel * 0.58, pucker * 0.34))

    if jaw_open > 0.0:
        target_values["MouthLowerDownLeft"] = max(float(target_values.get("MouthLowerDownLeft", 0.0) or 0.0), jaw_open * 0.12)
        target_values["MouthLowerDownRight"] = max(float(target_values.get("MouthLowerDownRight", 0.0) or 0.0), jaw_open * 0.12)

    mouth_close_floor = _mouth_close_floor(target_values)
    if mouth_close_floor is not None:
        target_values["JawOpen"] = max(float(target_values.get("JawOpen", 0.0) or 0.0), float(mouth_close_floor))
    return _enforce_mouth_close_not_above_jaw_open(target_values)


def _build_full_validation_plan(resolved_states, start_frame, fps, total_seconds, action_name, object_name, target_frames=FULL_VALIDATION_TARGET_FRAMES, tail_states=None):
    fps = max(1.0, float(fps))
    tail_states = list(tail_states or [])

    prepared_core_states = []
    previous_state = None
    for state in list(resolved_states or []):
        if previous_state is not None:
            bridge_state = _build_full_validation_bridge_state(previous_state, state)
            if bridge_state is not None:
                prepared_core_states.append(bridge_state)
        prepared_core_states.append(state)
        previous_state = state

    if not prepared_core_states:
        prepared_core_states = list(resolved_states or [])

    core_frame_budget = sum(max(4, int(round(_full_validation_state_seconds(state) * fps))) for state in prepared_core_states)
    tail_frame_budget = sum(max(6, int(round(max(0.1, float(state.get("seconds", 1.0) or 1.0)) * fps))) for state in tail_states)
    total_frame_target = max(core_frame_budget + tail_frame_budget + 8, int(round(max(1.0, float(total_seconds)) * fps)), int(target_frames or 0))
    available_core_budget = max(core_frame_budget, total_frame_target - tail_frame_budget)
    scale = float(available_core_budget) / float(core_frame_budget or 1)

    cursor = int(start_frame)
    segments = []
    previous_values = {}

    for index, state in enumerate(prepared_core_states, start=1):
        state_span = max(4, int(round(_full_validation_state_seconds(state) * fps * scale)))
        transition_frames = max(2, min(state_span - 1, int(round(state_span * _full_validation_state_transition_ratio(state)))))
        hold_frames = max(1, state_span - transition_frames)
        target_values = _state_target_values(state)
        peak_frame = cursor + transition_frames
        hold_end_frame = peak_frame + hold_frames
        end_frame = hold_end_frame
        segments.append(
            {
                "index": index,
                "name": str(state.get("name", "mix") or "mix"),
                "start_frame": int(cursor),
                "peak_frame": int(peak_frame),
                "hold_end_frame": int(hold_end_frame),
                "end_frame": int(end_frame),
                "from_values": dict(previous_values),
                "base_weights": dict(state.get("base_weights", {}) or {}),
                "weights": dict(state.get("weights", {}) or {}),
                "target_values": target_values,
                "curve_mode": _full_validation_curve_mode(state),
            }
        )
        previous_values = dict(target_values)
        cursor = int(end_frame) + 1

    if previous_values:
        reset_state = {"name": "Return To Base", "seconds": 0.32, "transition_ratio": 0.82, "weights": {}}
        reset_span = max(6, int(round(_full_validation_state_seconds(reset_state, default_by_bucket=False) * fps)))
        reset_transition = max(3, min(reset_span - 1, int(round(reset_span * _full_validation_state_transition_ratio(reset_state)))))
        reset_hold = max(1, reset_span - reset_transition)
        reset_peak = cursor + reset_transition
        reset_end = reset_peak + reset_hold
        segments.append(
            {
                "index": len(segments) + 1,
                "name": "Return To Base",
                "start_frame": int(cursor),
                "peak_frame": int(reset_peak),
                "hold_end_frame": int(reset_end),
                "end_frame": int(reset_end),
                "from_values": dict(previous_values),
                "base_weights": {},
                "weights": {},
                "target_values": {},
                "curve_mode": "bridge",
            }
        )
        previous_values = {}
        cursor = int(reset_end) + 1

    for state in tail_states:
        tail_span = max(6, int(round(max(0.1, float(state.get("seconds", 1.0) or 1.0)) * fps)))
        tail_transition_ratio = max(0.35, min(0.95, float(state.get("transition_ratio", 0.82) or 0.82)))
        tail_transition_frames = max(3, min(tail_span - 1, int(round(tail_span * tail_transition_ratio))))
        tail_hold_frames = max(1, tail_span - tail_transition_frames)
        target_values = _state_target_values(state)
        peak_frame = cursor + tail_transition_frames
        hold_end_frame = peak_frame + tail_hold_frames
        end_frame = hold_end_frame
        segments.append(
            {
                "index": len(segments) + 1,
                "name": str(state.get("name", "TextMix") or "TextMix"),
                "start_frame": int(cursor),
                "peak_frame": int(peak_frame),
                "hold_end_frame": int(hold_end_frame),
                "end_frame": int(end_frame),
                "from_values": dict(previous_values),
                "base_weights": dict(state.get("base_weights", {}) or {}),
                "weights": dict(state.get("weights", {}) or {}),
                "target_values": target_values,
                "curve_mode": _full_validation_curve_mode(state),
            }
        )
        previous_values = dict(target_values)
        cursor = int(end_frame) + 1

    end_frame = int(segments[-1]["end_frame"]) if segments else int(start_frame)
    frame_span = max(1, end_frame - int(start_frame))
    actual_total_seconds = max(float(total_seconds), float(frame_span) / fps)
    return {
        "object_name": str(object_name or ""),
        "action_name": str(action_name or ""),
        "start_frame": int(start_frame),
        "end_frame": end_frame,
        "total_seconds": float(actual_total_seconds),
        "fps": float(fps),
        "target_frames": int(target_frames or 0),
        "segments": segments,
    }


def _full_validation_eased_progress(value, mode):
    x = max(0.0, min(1.0, float(value)))
    if mode == "upper":
        return 1.0 - pow(1.0 - x, 1.7)
    if mode == "speech":
        return pow(x, 0.64) if x > 0.0 else 0.0
    if mode == "mouth":
        return 1.0 - pow(1.0 - x, 1.28)
    if mode == "bridge":
        return x * x * (3.0 - (2.0 * x))
    return 1.0 - pow(1.0 - x, 1.36)


def _state_target_values(state):
    target_values = dict((state or {}).get("base_weights", {}) or {})
    for key_name, value in dict((state or {}).get("weights", {}) or {}).items():
        target_values[key_name] = float(value)
    target_values = _soften_opposing_target_values(target_values)

    jaw_open = max(0.0, min(1.0, float(target_values.get("JawOpen", 0.0) or 0.0)))
    blink_left = max(0.0, min(1.0, float(target_values.get("EyeBlinkLeft", 0.0) or 0.0)))
    blink_right = max(0.0, min(1.0, float(target_values.get("EyeBlinkRight", 0.0) or 0.0)))
    squint_left = max(0.0, min(1.0, float(target_values.get("EyeSquintLeft", 0.0) or 0.0)))
    squint_right = max(0.0, min(1.0, float(target_values.get("EyeSquintRight", 0.0) or 0.0)))
    smile_left = max(0.0, min(1.0, float(target_values.get("MouthSmileLeft", 0.0) or 0.0)))
    smile_right = max(0.0, min(1.0, float(target_values.get("MouthSmileRight", 0.0) or 0.0)))
    funnel = max(0.0, min(1.0, float(target_values.get("MouthFunnel", 0.0) or 0.0)))
    pucker = max(0.0, min(1.0, float(target_values.get("MouthPucker", 0.0) or 0.0)))

    if "EyeWideLeft" in target_values:
        target_values["EyeWideLeft"] = max(0.0, min(1.0, float(target_values.get("EyeWideLeft", 0.0) or 0.0) * (1.0 - (0.85 * blink_left)) * (1.0 - (0.62 * squint_left))))
    if "EyeWideRight" in target_values:
        target_values["EyeWideRight"] = max(0.0, min(1.0, float(target_values.get("EyeWideRight", 0.0) or 0.0) * (1.0 - (0.85 * blink_right)) * (1.0 - (0.62 * squint_right))))
    if "MouthClose" in target_values:
        mouth_close_value = max(0.0, min(1.0, float(target_values.get("MouthClose", 0.0) or 0.0) * (1.0 - (0.75 * jaw_open))))
        if jaw_open > 0.0:
            mouth_close_value = min(mouth_close_value, jaw_open)
        target_values["MouthClose"] = mouth_close_value

    smile_hold_left = smile_left * 0.20
    smile_hold_right = smile_right * 0.20
    if jaw_open > 0.12:
        target_values["MouthSmileLeft"] = max(smile_left, smile_hold_left)
        target_values["MouthSmileRight"] = max(smile_right, smile_hold_right)
        target_values["CheekSquintLeft"] = max(float(target_values.get("CheekSquintLeft", 0.0) or 0.0), smile_left * 0.12)
        target_values["CheekSquintRight"] = max(float(target_values.get("CheekSquintRight", 0.0) or 0.0), smile_right * 0.12)

    if funnel > 0.0 or pucker > 0.0:
        target_values["JawOpen"] = max(jaw_open, max(funnel * 0.50, pucker * 0.30))
        if "CheekPuff" in target_values and jaw_open > 0.0:
            target_values["CheekPuff"] = min(float(target_values.get("CheekPuff", 0.0) or 0.0), max(0.0, float(target_values.get("CheekPuff", 0.0) or 0.0) * (1.0 - (0.55 * jaw_open))))

    if jaw_open > 0.0:
        target_values["MouthLowerDownLeft"] = max(float(target_values.get("MouthLowerDownLeft", 0.0) or 0.0), jaw_open * 0.12)
        target_values["MouthLowerDownRight"] = max(float(target_values.get("MouthLowerDownRight", 0.0) or 0.0), jaw_open * 0.12)
        target_values["MouthStretchLeft"] = max(float(target_values.get("MouthStretchLeft", 0.0) or 0.0), jaw_open * 0.08)
        target_values["MouthStretchRight"] = max(float(target_values.get("MouthStretchRight", 0.0) or 0.0), jaw_open * 0.08)

    mouth_close_floor = _mouth_close_floor(target_values)
    if mouth_close_floor is not None:
        target_values["JawOpen"] = max(float(target_values.get("JawOpen", 0.0) or 0.0), float(mouth_close_floor))
        if "MouthClose" in target_values:
            target_values["MouthClose"] = min(float(target_values.get("MouthClose", 0.0) or 0.0), float(target_values.get("JawOpen", 0.0) or 0.0))
    return _enforce_mouth_close_not_above_jaw_open(target_values)


def _build_full_validation_plan(resolved_states, start_frame, fps, total_seconds, action_name, object_name, target_frames=FULL_VALIDATION_TARGET_FRAMES, tail_states=None):
    fps = max(1.0, float(fps))
    total_frames = max(int(round(max(1.0, float(total_seconds)) * fps)), int(target_frames or 0), len(resolved_states) * 20)
    state_span = max(20, int(round(float(total_frames) / max(1, len(resolved_states) + 1))))
    cursor = int(start_frame)
    segments = []
    previous_values = {}
    for index, state in enumerate(resolved_states, start=1):
        current_span = max(state_span, int(round(max(float((state or {}).get("seconds", 0.0) or 0.0), float(state_span) / fps) * fps)))
        transition_ratio = max(0.36, min(0.74, float((state or {}).get("transition_ratio", 0.62) or 0.62)))
        transition_frames = max(5, min(current_span - 6, int(round(current_span * transition_ratio))))
        hold_frames = max(10, current_span - transition_frames)
        target_values = _state_target_values(state)
        peak_frame = cursor + transition_frames
        hold_end_frame = peak_frame + hold_frames
        end_frame = hold_end_frame
        segments.append(
            {
                "index": index,
                "name": str(state.get("name", "mix") or "mix"),
                "start_frame": int(cursor),
                "peak_frame": int(peak_frame),
                "hold_end_frame": int(hold_end_frame),
                "end_frame": int(end_frame),
                "from_values": dict(previous_values),
                "base_weights": dict(state.get("base_weights", {}) or {}),
                "weights": dict(state.get("weights", {}) or {}),
                "target_values": target_values,
                "curve_mode": _full_validation_curve_mode(state),
            }
        )
        previous_values = dict(target_values)
        cursor = int(end_frame) + 1
    segments.append(
        {
            "index": len(segments) + 1,
            "name": "Return To Zero",
            "start_frame": int(cursor),
            "peak_frame": int(cursor + 4),
            "hold_end_frame": int(cursor + 8),
            "end_frame": int(cursor + 12),
            "from_values": dict(previous_values),
            "base_weights": {},
            "weights": {},
            "target_values": {},
            "curve_mode": "bridge",
        }
    )
    end_frame = int(segments[-1]["end_frame"]) if segments else int(start_frame)
    frame_span = max(1, end_frame - int(start_frame))
    actual_total_seconds = max(float(total_seconds), float(frame_span) / fps)
    return {
        "object_name": str(object_name or ""),
        "action_name": str(action_name or ""),
        "start_frame": int(start_frame),
        "end_frame": end_frame,
        "total_seconds": float(actual_total_seconds),
        "fps": float(fps),
        "target_frames": int(target_frames or 0),
        "segments": segments,
    }


def _capture_profile_idle_state():
    profile = dict(FULL_VALIDATION_CAPTURE_PROFILE or {})
    idle_weights = dict(profile.get("idle", {}) or {})
    return {
        "name": "Capture Neutral Base",
        "seconds": 0.40,
        "transition_ratio": 0.74,
        "weights": idle_weights,
    }


def _capture_profile_motif_states():
    profile = dict(FULL_VALIDATION_CAPTURE_PROFILE or {})
    intensity_scale = max(0.1, float(profile.get("intensity_scale", 1.0) or 1.0))
    motif_states = []
    for motif in list(profile.get("motifs", []) or []):
        weights = {}
        for key_name, value in dict(motif.get("weights", {}) or {}).items():
            weights[key_name] = max(0.0, min(1.0, float(value or 0.0) * intensity_scale))
        motif_states.append(
            {
                "name": str(motif.get("name", "Capture Motif") or "Capture Motif"),
                "seconds": max(0.12, float(motif.get("seconds", 0.28) or 0.28)),
                "transition_ratio": max(0.35, min(0.95, float(motif.get("transition_ratio", 0.58) or 0.58))),
                "weights": weights,
            }
        )
    return motif_states


def _capture_profile_peak_event_states():
    profile = dict(FULL_VALIDATION_CAPTURE_PROFILE or {})
    fps = max(1.0, float(profile.get("target_fps", FULL_VALIDATION_PLAYBACK_FPS) or FULL_VALIDATION_PLAYBACK_FPS))
    intensity_scale = max(0.1, float(profile.get("intensity_scale", 1.0) or 1.0))
    event_states = []
    for event in list(profile.get("peak_events", []) or []):
        event_name = str(event.get("name", "Capture Peak") or "Capture Peak")
        peak_weights = {}
        for key_name, value in dict(event.get("weights", {}) or {}).items():
            peak_weights[key_name] = max(0.0, min(1.0, float(value or 0.0) * intensity_scale))
        attack_seconds = max(2.0, float(event.get("attack", 6) or 6.0)) / fps
        hold_seconds = max(1.0, float(event.get("hold", 2) or 2.0)) / fps
        release_seconds = max(2.0, float(event.get("release", 6) or 6.0)) / fps
        lead_weights = {key_name: value * 0.38 for key_name, value in peak_weights.items()}
        settle_weights = {key_name: value * 0.30 for key_name, value in peak_weights.items()}
        event_states.extend(
            [
                {
                    "name": f"{event_name} Lead",
                    "seconds": attack_seconds,
                    "transition_ratio": 0.52,
                    "weights": lead_weights,
                },
                {
                    "name": f"{event_name} Peak",
                    "seconds": hold_seconds,
                    "transition_ratio": 0.42,
                    "weights": peak_weights,
                },
                {
                    "name": f"{event_name} Release",
                    "seconds": release_seconds,
                    "transition_ratio": 0.78,
                    "weights": settle_weights,
                },
            ]
        )
    return event_states


def _capture_profile_tail_states():
    tail_states = []
    for state in list(FULL_VALIDATION_TEXT_MIX_STATES or []):
        tail_states.append(
            {
                "name": str(state.get("name", "TextMix Tail") or "TextMix Tail"),
                "seconds": max(0.12, float(state.get("seconds", 0.32) or 0.32)),
                "transition_ratio": max(0.35, min(0.95, float(state.get("transition_ratio", 0.72) or 0.72))),
                "weights": dict(state.get("weights", {}) or {}),
                "base_weights": dict(state.get("base_weights", {}) or {}),
            }
        )
    return tail_states


def _capture_profile_full_validation_states():
    base_state = _capture_profile_idle_state()
    motif_states = _capture_profile_motif_states()
    peak_states = _capture_profile_peak_event_states()
    states = [base_state]
    states.extend(motif_states[:4])
    states.extend(peak_states[:6])
    states.extend(motif_states[4:])
    states.extend(peak_states[6:])
    states.extend(_capture_profile_tail_states())
    states.append(
        {
            "name": "Capture Neutral Recover",
            "seconds": 0.46,
            "transition_ratio": 0.84,
            "weights": dict(base_state.get("weights", {}) or {}),
        }
    )
    return states


def _resolve_full_validation_states_for_object(key_blocks, items, target_count=70):
    capture_states = _capture_profile_full_validation_states()
    resolved_capture = _resolve_validation_state_specs_for_object(key_blocks, capture_states, allow_empty=False)
    if len(resolved_capture) >= 12:
        return resolved_capture
    return _resolve_validation_state_specs_for_object(
        key_blocks,
        _expanded_full_validation_states(_full_validation_states(items), target_count=target_count),
    )


def _segment_factor(segment, frame):
    if not segment:
        return 0.0
    start_frame = float(segment.get("start_frame", 0) or 0)
    peak_frame = float(segment.get("peak_frame", start_frame) or start_frame)
    hold_end_frame = float(segment.get("hold_end_frame", peak_frame) or peak_frame)
    end_frame = float(segment.get("end_frame", hold_end_frame) or hold_end_frame)
    frame = float(frame)
    if frame <= start_frame:
        return 0.0
    if peak_frame > start_frame and frame < peak_frame:
        normalized = float(frame - start_frame) / float(peak_frame - start_frame)
        return _full_validation_eased_progress(normalized, str(segment.get("curve_mode", "smooth") or "smooth"))
    if frame <= hold_end_frame:
        return 1.0
    if end_frame > hold_end_frame and frame < end_frame:
        normalized = float(frame - hold_end_frame) / float(end_frame - hold_end_frame)
        return max(0.0, min(1.0, 1.0 - _full_validation_eased_progress(normalized, "bridge")))
    return 0.0


def _segment_interpolated_values(segment, frame):
    if not segment:
        return {}
    from_values = dict(segment.get("from_values", {}) or {})
    target_values = dict(segment.get("target_values", {}) or {})
    start_frame = float(segment.get("start_frame", 0) or 0)
    peak_frame = float(segment.get("peak_frame", start_frame) or start_frame)
    hold_end_frame = float(segment.get("hold_end_frame", peak_frame) or peak_frame)
    end_frame = float(segment.get("end_frame", hold_end_frame) or hold_end_frame)
    frame_value = float(frame)

    if frame_value <= start_frame:
        return _enforce_mouth_close_not_above_jaw_open(from_values)
    if peak_frame <= start_frame:
        return _enforce_mouth_close_not_above_jaw_open(target_values)
    if frame_value <= peak_frame:
        normalized = float(frame_value - start_frame) / float(peak_frame - start_frame)
        factor = _full_validation_eased_progress(normalized, str(segment.get("curve_mode", "smooth") or "smooth"))
        values = {}
        for key_name in set(from_values) | set(target_values):
            start_value = float(from_values.get(key_name, 0.0) or 0.0)
            end_value = float(target_values.get(key_name, 0.0) or 0.0)
            values[key_name] = max(0.0, min(1.0, start_value + ((end_value - start_value) * factor)))
        return _enforce_mouth_close_not_above_jaw_open(values)
    if frame_value <= hold_end_frame:
        return _enforce_mouth_close_not_above_jaw_open(target_values)
    if end_frame <= hold_end_frame:
        return _enforce_mouth_close_not_above_jaw_open(target_values)
    normalized = float(frame_value - hold_end_frame) / float(end_frame - hold_end_frame)
    factor = 1.0 - _full_validation_eased_progress(normalized, "bridge")
    values = {}
    for key_name in set(from_values) | set(target_values):
        base_value = float(target_values.get(key_name, 0.0) or 0.0)
        next_value = float(from_values.get(key_name, 0.0) or 0.0)
        values[key_name] = max(0.0, min(1.0, next_value + ((base_value - next_value) * factor)))
    return _enforce_mouth_close_not_above_jaw_open(values)


def _set_scene_frame(scene, frame, subframe=None):
    if scene is None:
        return False
    frame_value = float(frame)
    if subframe is None:
        base_frame = int(math.floor(frame_value))
        subframe_value = float(frame_value - float(base_frame))
    else:
        base_frame = int(frame_value)
        subframe_value = float(subframe)
    if subframe_value >= 0.9995:
        base_frame += 1
        subframe_value = 0.0
    elif subframe_value < 0.0:
        frame_offset = int(math.floor(subframe_value))
        base_frame += frame_offset
        subframe_value -= float(frame_offset)
    subframe_value = max(0.0, min(0.999999, subframe_value))
    try:
        scene.frame_set(frame=base_frame, subframe=subframe_value)
    except Exception:
        try:
            scene.frame_current = base_frame
            if hasattr(scene, "frame_subframe"):
                scene.frame_subframe = subframe_value
        except Exception:
            return False
    return True


def _configure_full_validation_scene_timing(scene):
    render = getattr(scene, "render", None) if scene is not None else None
    if render is None:
        return
    try:
        render.fps = int(round(FULL_VALIDATION_PLAYBACK_FPS))
    except Exception:
        pass
    try:
        render.fps_base = 1.0
    except Exception:
        pass


def _find_plan_segment(plan, frame):
    segments = list((plan or {}).get("segments", []) or [])
    frame_value = float(frame)
    for segment in segments:
        if float(segment.get("start_frame", 0) or 0) <= frame_value <= float(segment.get("end_frame", 0) or 0):
            return segment
    if segments and frame_value > float(segments[-1].get("end_frame", 0) or 0):
        return segments[-1]
    return None


def _build_full_validation_plan(resolved_states, start_frame, fps, total_seconds, action_name, object_name, target_frames=FULL_VALIDATION_TARGET_FRAMES, tail_states=None):
    fps = max(1.0, float(fps))
    tail_states = list(tail_states or [])
    tail_frame_budget = sum(max(6, int(round(max(0.1, float(state.get("seconds", 1.0) or 1.0)) * fps))) for state in tail_states)
    total_frames = max(
        (len(resolved_states) * 4) + tail_frame_budget,
        int(round(max(1.0, float(total_seconds)) * fps)),
        int(target_frames or 0),
    )
    core_frame_budget = max(len(resolved_states) * 4, int(total_frames - tail_frame_budget))
    state_span = max(4, int(round(float(core_frame_budget) / max(1, len(resolved_states)))))
    transition_frames = max(2, int(round(state_span * 0.78)))
    hold_frames = max(1, state_span - transition_frames)
    cursor = int(start_frame)
    segments = []
    previous_values = {}
    for index, state in enumerate(resolved_states, start=1):
        target_values = _state_target_values(state)
        peak_frame = cursor + transition_frames
        hold_end_frame = peak_frame + hold_frames
        end_frame = hold_end_frame
        segments.append(
            {
                "index": index,
                "name": str(state.get("name", "混合状态") or "混合状态"),
                "start_frame": int(cursor),
                "peak_frame": int(peak_frame),
                "hold_end_frame": int(hold_end_frame),
                "end_frame": int(end_frame),
                "from_values": dict(previous_values),
                "base_weights": dict(state.get("base_weights", {}) or {}),
                "weights": dict(state.get("weights", {}) or {}),
                "target_values": target_values,
            }
        )
        previous_values = dict(target_values)
        cursor = int(end_frame) + 1
    if previous_values:
        reset_peak = cursor + max(3, int(round(state_span * 0.9)))
        segments.append(
            {
                "index": len(segments) + 1,
                "name": "回到基型",
                "start_frame": int(cursor),
                "peak_frame": int(reset_peak),
                "hold_end_frame": int(reset_peak),
                "end_frame": int(reset_peak),
                "from_values": dict(previous_values),
                "base_weights": {},
                "weights": {},
                "target_values": {},
            }
        )
        previous_values = {}
        cursor = int(reset_peak) + 1
    for state in tail_states:
        tail_span = max(6, int(round(max(0.1, float(state.get("seconds", 1.0) or 1.0)) * fps)))
        tail_transition_ratio = max(0.35, min(0.95, float(state.get("transition_ratio", 0.82) or 0.82)))
        tail_transition_frames = max(3, int(round(tail_span * tail_transition_ratio)))
        tail_hold_frames = max(1, tail_span - tail_transition_frames)
        target_values = _state_target_values(state)
        peak_frame = cursor + tail_transition_frames
        hold_end_frame = peak_frame + tail_hold_frames
        end_frame = hold_end_frame
        segments.append(
            {
                "index": len(segments) + 1,
                "name": str(state.get("name", "TextMix") or "TextMix"),
                "start_frame": int(cursor),
                "peak_frame": int(peak_frame),
                "hold_end_frame": int(hold_end_frame),
                "end_frame": int(end_frame),
                "from_values": dict(previous_values),
                "base_weights": dict(state.get("base_weights", {}) or {}),
                "weights": dict(state.get("weights", {}) or {}),
                "target_values": target_values,
            }
        )
        previous_values = dict(target_values)
        cursor = int(end_frame) + 1
    end_frame = int(segments[-1]["end_frame"]) if segments else int(start_frame)
    frame_span = max(1, end_frame - int(start_frame))
    actual_total_seconds = max(float(total_seconds), float(frame_span) / fps)
    return {
        "object_name": str(object_name or ""),
        "action_name": str(action_name or ""),
        "start_frame": int(start_frame),
        "end_frame": end_frame,
        "total_seconds": float(actual_total_seconds),
        "fps": float(fps),
        "target_frames": int(target_frames or 0),
        "segments": segments,
    }


def _bake_full_validation_action(obj, key_blocks, resolved_states, tail_states, start_frame=1):
    key_block_map = {str(getattr(key_block, "name", "") or ""): key_block for index, key_block in enumerate(key_blocks) if index != 0}
    shape_keys, action = _ensure_full_validation_action(obj)
    plan = _build_full_validation_plan(
        resolved_states,
        start_frame,
        FULL_VALIDATION_PLAYBACK_FPS,
        FULL_VALIDATION_TOTAL_SECONDS,
        getattr(action, "name_full", action.name),
        obj.name_full,
        tail_states=tail_states,
    )
    used_key_names = sorted(
        {
            key_name
            for segment in list(plan.get("segments", []) or [])
            for key_name in set(dict(segment.get("from_values", {}) or {})) | set(dict(segment.get("target_values", {}) or {}))
        },
        key=str.casefold,
    )
    for segment in list(plan.get("segments", []) or []):
        from_values = _enforce_mouth_close_not_above_jaw_open(segment.get("from_values", {}) or {})
        target_values = _enforce_mouth_close_not_above_jaw_open(segment.get("target_values", {}) or {})
        segment["from_values"] = from_values
        segment["target_values"] = target_values
        _insert_shape_key_frame(key_block_map, used_key_names, from_values, int(segment.get("start_frame", start_frame) or start_frame))
        _insert_shape_key_frame(key_block_map, used_key_names, target_values, int(segment.get("peak_frame", start_frame) or start_frame))
        _insert_shape_key_frame(key_block_map, used_key_names, target_values, int(segment.get("hold_end_frame", start_frame) or start_frame))
    for fcurve in action.fcurves:
        for keyframe_point in fcurve.keyframe_points:
            keyframe_point.interpolation = "BEZIER"
            try:
                keyframe_point.handle_left_type = "AUTO_CLAMPED"
                keyframe_point.handle_right_type = "AUTO_CLAMPED"
            except Exception:
                pass
    for key_name in used_key_names:
        if key_name in key_block_map:
            key_block_map[key_name].value = 0.0
    try:
        shape_keys.update_tag()
    except Exception:
        pass
    return shape_keys, action, plan, used_key_names


def _full_validation_live_status(scene, workflow, module, module_state=None):
    plan = _full_validation_plan(module_state, scene, workflow, module)
    if not plan:
        return {"plan": {}, "segment": None, "frame": int(getattr(scene, "frame_current", 1) or 1), "group_index": 0, "group_total": 0, "factor": 0.0, "values": []}
    frame = int(getattr(scene, "frame_current", 1) or 1)
    segments = list(plan.get("segments", []) or [])
    active_segment = None
    for segment in segments:
        if int(segment.get("start_frame", 0) or 0) <= frame <= int(segment.get("end_frame", 0) or 0):
            active_segment = segment
            break
    if active_segment is None and segments and frame > int(segments[-1].get("end_frame", 0) or 0):
        group_index = len(segments)
    elif active_segment is None:
        group_index = 0
    else:
        group_index = int(active_segment.get("index", 0) or 0)
    object_name = str(plan.get("object_name", "") or "").strip()
    obj = bpy.data.objects.get(object_name) if object_name else None
    values = []
    key_blocks = getattr(getattr(getattr(obj, "data", None), "shape_keys", None), "key_blocks", None)
    if key_blocks is not None:
        for index, key_block in enumerate(key_blocks):
            if index == 0:
                continue
            value = float(getattr(key_block, "value", 0.0) or 0.0)
            if abs(value) > 0.001:
                values.append(f"{key_block.name}={value:.3f}")
        values.sort(key=lambda text: (-float(text.rsplit("=", 1)[-1]), text.casefold()))
    return {
        "plan": plan,
        "segment": active_segment,
        "frame": frame,
        "group_index": int(group_index),
        "group_total": len(segments),
        "factor": _segment_factor(active_segment, frame),
        "values": values,
        "object": obj,
    }


def _validation_animation_mode(item):
    text_sources = []
    text_sources.extend(list(item.get("validation_mix", []) or []))
    text_sources.extend(list(item.get("notes", []) or []))
    text_sources.extend(list(item.get("tips", []) or []))
    text_sources.extend(list(item.get("detail_notes", []) or []))
    text_sources.append(item.get("detail_ja_zh") or item.get("detail_ja") or "")
    text = " | ".join(str(source or "") for source in text_sources).strip()
    if not text:
        return "sequence"
    simultaneous_tokens = ("同时", "一起", "同時", "左右", "both", "両方", "组合", "組み合わせ", "联动", "叠加", "+", "＋")
    sequential_tokens = ("顺序", "依次", "逐步", "先", "再", "然后")
    if any(token in text for token in simultaneous_tokens):
        return "simultaneous"
    if any(token in text for token in sequential_tokens):
        return "sequence"
    return "sequence"


def _apply_shape_key_values(context, scene, workflow, module, panel_api, module_state, item, items, mode):
    _stop_validation_animation(scene=scene, workflow=workflow, module=module, module_state=module_state)
    _restore_validation_preview_state(scene=scene, workflow=workflow, module=module, module_state=module_state)
    try:
        obj, key_blocks = _target_object(context, panel_api)
    except Exception as exc:
        if _report_soft_target_issue(exc, panel_api, module_state):
            return None
        raise
    shape_key_names = _validation_shape_keys(item, items)
    if not shape_key_names:
        raise Exception("当前步骤没有可用于混合验证的形态键")
    _switch_to_object_mode(context, obj)
    _capture_validation_preview_state(obj, key_blocks, scene=scene, workflow=workflow, module=module, module_state=module_state)
    _zero_known_shape_keys(key_blocks, items)
    matched = []
    for order, shape_key_name in enumerate(shape_key_names, start=1):
        target_key = _find_matching_shape_key(key_blocks, shape_key_name)
        if target_key is None:
            continue
        target_key.value = 1.0 if mode == "direct" else min(1.0, order * 0.1)
        matched.append((shape_key_name, target_key.value))
    if not matched:
        if panel_api is not None:
            panel_api.set_status("文档提到的形态键在当前物体上都没有找到，请查看 Blender 控制台日志", level="WARNING")
        if module_state is not None:
            module_state.set("last_result", "文档提到的形态键在当前物体上都没有找到")
        return None
    _set_active_shape_key_index(obj, key_blocks, matched[0][0])
    if module_state is not None:
        module_state.set("last_result", "；".join(f"{name}={value:.1f}" for name, value in matched[:10]))
    if panel_api is not None:
        panel_api.set_status(
            f"已应用只读混合预览：{len(matched)} 个形态键，模式={'全部设为1' if mode == 'direct' else '按顺序递增'}；切步骤或重置时会恢复原值",
            level="OK",
        )
    return matched


def _start_full_validation_animation_legacy(context, scene, workflow, module, panel_api, module_state, items):
    _stop_validation_animation(scene=scene, workflow=workflow, module=module, module_state=module_state)
    _restore_validation_preview_state(scene=scene, workflow=workflow, module=module, module_state=module_state)
    _clear_full_validation_plan(scene, workflow, module, module_state)
    try:
        obj, key_blocks = _target_object(context, panel_api)
    except Exception as exc:
        if _report_soft_target_issue(exc, panel_api, module_state):
            return None
        raise

    _detach_full_validation_action(obj)
    resolved_states = _resolve_full_validation_states_for_object(key_blocks, items, target_count=len(FULL_VALIDATION_STATES))
    if not resolved_states:
        message = "当前物体没有匹配到可用于全面混合验证的 ARKit 形态键，请先确认物体上至少有一部分对应形态键"
        if panel_api is not None:
            panel_api.set_status(message, level="WARNING")
        if module_state is not None:
            module_state.set("last_result", message)
        return None

    _switch_to_object_mode(context, obj)
    _capture_validation_preview_state(obj, key_blocks, scene=scene, workflow=workflow, module=module, module_state=module_state)
    _zero_known_shape_keys(key_blocks, items)
    plan_fps_base = float(getattr(getattr(scene, "render", None), "fps_base", 1.0) or 1.0)
    plan_fps_value = float(getattr(getattr(scene, "render", None), "fps", 24) or 24.0)
    plan_fps = plan_fps_value / plan_fps_base if plan_fps_base not in {0.0, -0.0} else plan_fps_value
    text_mix_states = _resolve_text_mix_validation_states_for_object(key_blocks)
    plan = _build_full_validation_plan(
        resolved_states,
        1,
        plan_fps,
        FULL_VALIDATION_TOTAL_SECONDS,
        "",
        obj.name_full,
        tail_states=text_mix_states,
    )
    _set_full_validation_plan(scene, workflow, module, module_state, plan)
    first_segment = _find_plan_segment(plan, int(plan.get("start_frame", 1) or 1))
    first_target_values = dict((first_segment or {}).get("target_values", {}) or {})
    if first_target_values:
        dominant_name = max(first_target_values.items(), key=lambda item: float(item[1]))[0]
        _set_active_shape_key_index(obj, key_blocks, dominant_name)
    runtime = _full_validation_runtime(module_state, scene, workflow, module)
    full_token = int(runtime.get("token", 0)) + 1
    _set_full_validation_runtime(
        scene,
        workflow,
        module,
        module_state,
        running=True,
        paused=False,
        token=full_token,
        current_label="",
        current_values="",
        status="running",
        paused_at=0.0,
        pause_accumulated=0.0,
        current_index=1,
        current_factor=0.0,
        total=len(list(plan.get("segments", []) or [])),
    )

    total_seconds = max(1.0, float(plan.get("total_seconds", FULL_VALIDATION_TOTAL_SECONDS) or FULL_VALIDATION_TOTAL_SECONDS))
    _ANIMATION_STATE["token"] = full_token
    _ANIMATION_STATE["running"] = True
    _ANIMATION_STATE["paused"] = False
    _suspend_nodepreview_playback_updates()
    _suspend_global_undo()
    token = int(_ANIMATION_STATE["token"])
    object_name = obj.name_full
    plan_start_frame = float(plan.get("start_frame", 1) or 1)
    plan_end_frame = float(plan.get("end_frame", plan_start_frame) or plan_start_frame)
    state = {
        "started_at": time.perf_counter(),
        "last_status_at": 0.0,
        "last_segment_index": -1,
        "key_map_object_name": "",
        "key_map": {},
        "last_values": {},
    }

    def _active_key_map():
        current_obj = bpy.data.objects.get(object_name)
        current_shape_keys = getattr(getattr(current_obj, "data", None), "shape_keys", None) if current_obj is not None else None
        current_key_blocks = getattr(current_shape_keys, "key_blocks", None) if current_shape_keys is not None else None
        if current_obj is None or current_key_blocks is None:
            return None, {}
        current_object_name = str(getattr(current_obj, "name_full", "") or getattr(current_obj, "name", "") or "")
        cached_key_map = dict(state.get("key_map", {}) or {})
        cached_object_name = str(state.get("key_map_object_name", "") or "")
        if cached_key_map and cached_object_name == current_object_name:
            return current_obj, cached_key_map
        key_map = {getattr(key_block, "name", ""): key_block for index, key_block in enumerate(current_key_blocks) if index != 0}
        state["key_map_object_name"] = current_object_name
        state["key_map"] = key_map
        return current_obj, key_map

    used_key_names = sorted(
        {key_name for segment in list(plan.get("segments", []) or []) for key_name in set(dict(segment.get("from_values", {}) or {})) | set(dict(segment.get("target_values", {}) or {}))},
        key=str.casefold,
    )

    def _apply_value_map(values):
        current_obj, key_map = _active_key_map()
        if not key_map:
            return False
        last_values = dict(state.get("last_values", {}) or {})
        changed = False
        for shape_key_name in used_key_names:
            target_key = key_map.get(shape_key_name)
            if target_key is None:
                continue
            next_value = max(0.0, min(1.0, float(values.get(shape_key_name, 0.0) or 0.0)))
            previous_value = float(last_values.get(shape_key_name, 0.0) or 0.0)
            if abs(previous_value - next_value) <= 0.0005:
                continue
            target_key.value = next_value
            last_values[shape_key_name] = next_value
            changed = True
        if not changed:
            return True
        state["last_values"] = last_values
        return True

    def _tick():
        try:
            runtime_now = _full_validation_runtime(module_state, scene, workflow, module)
            if not _ANIMATION_STATE["running"] or token != _ANIMATION_STATE["token"]:
                _restore_global_undo()
                _release_validation_timer(_tick)
                _restore_nodepreview_playback_updates()
                _set_full_validation_runtime(scene, workflow, module, module_state, running=False, paused=False, status="", paused_at=0.0, pause_accumulated=0.0, current_index=0, current_factor=0.0)
                return None
            if full_token != int(runtime_now.get("token", 0)):
                _restore_global_undo()
                _release_validation_timer(_tick)
                _restore_nodepreview_playback_updates()
                _set_full_validation_runtime(scene, workflow, module, module_state, running=False, paused=False, status="", paused_at=0.0, pause_accumulated=0.0, current_index=0, current_factor=0.0)
                return None
            if bool(_ANIMATION_STATE.get("paused")) or bool(runtime_now.get("paused")):
                _set_full_validation_runtime(scene, workflow, module, module_state, status="paused", _persist=False)
                return ANIMATION_TIMER_INTERVAL
            paused_at = float(runtime_now.get("paused_at") or 0.0)
            if paused_at > 0.0:
                resumed_at = time.perf_counter()
                paused_duration = max(0.0, resumed_at - paused_at)
                runtime_now = _set_full_validation_runtime(scene, workflow, module, module_state, pause_accumulated=float(runtime_now.get("pause_accumulated") or 0.0) + paused_duration, paused_at=0.0, status="running")
            else:
                runtime_now = _set_full_validation_runtime(scene, workflow, module, module_state, status="running", _persist=False)
            elapsed = max(0.0, time.perf_counter() - float(state["started_at"]) - float(runtime_now.get("pause_accumulated") or 0.0))
            if elapsed >= total_seconds:
                _ANIMATION_STATE["running"] = False
                _ANIMATION_STATE["paused"] = False
                _apply_value_map({})
                _set_full_validation_runtime(scene, workflow, module, module_state, running=False, paused=False, status="finished", paused_at=0.0, pause_accumulated=0.0, current_index=len(list(plan.get("segments", []) or [])), current_factor=1.0, total=len(list(plan.get("segments", []) or [])))
                _restore_global_undo()
                _release_validation_timer(_tick)
                if panel_api is not None:
                    panel_api.set_status(f"已完成 ARKit 全面混合验证，共 {len(list(plan.get('segments', []) or []))} 段连续动画", level="OK")
                if module_state is not None:
                    module_state.set("last_result", f"已完成 ARKit 全面混合验证，共 {len(list(plan.get('segments', []) or []))} 段连续动画")
                return None

            frame_span = max(1.0, plan_end_frame - plan_start_frame)
            virtual_frame = plan_start_frame + ((elapsed / total_seconds) * frame_span)
            current_segment = _find_plan_segment(plan, virtual_frame)
            if current_segment is None:
                current_segment = (list(plan.get("segments", []) or []) or [None])[-1]
            current_values = _segment_interpolated_values(current_segment, virtual_frame)
            _apply_value_map(current_values)
            current_name = str((current_segment or {}).get("name", "混合状态") or "混合状态")
            current_index = int((current_segment or {}).get("index", 0) or 0)
            phase = _segment_factor(current_segment, int(round(virtual_frame)))
            non_zero_values = [(name, value) for name, value in current_values.items() if abs(float(value)) > 0.001]
            non_zero_values.sort(key=lambda item: (-float(item[1]), item[0].casefold()))
            current_values_text = ", ".join(f"{name}={float(value):.2f}" for name, value in non_zero_values[:16])
            _set_full_validation_runtime(
                scene,
                workflow,
                module,
                module_state,
                current_label=current_name,
                current_values=current_values_text,
                current_index=current_index,
                current_factor=phase,
                total=len(list(plan.get("segments", []) or [])),
                _persist=False,
            )
            now = time.perf_counter()
            if current_index != int(state.get("last_segment_index", -1) or -1):
                state["last_segment_index"] = current_index
            if module_state is not None and (state["last_status_at"] <= 0.0 or (now - float(state["last_status_at"])) >= ANIMATION_STATUS_INTERVAL):
                state["last_status_at"] = now
            return ANIMATION_TIMER_INTERVAL
        except ReferenceError:
            _ANIMATION_STATE["running"] = False
            _ANIMATION_STATE["paused"] = False
            _restore_global_undo()
            _release_validation_timer(_tick)
            _restore_nodepreview_playback_updates()
            _set_full_validation_runtime(scene, workflow, module, module_state, running=False, paused=False, status="stopped", paused_at=0.0, pause_accumulated=0.0, current_index=0, current_factor=0.0)
            return None
        except Exception as exc:
            _ANIMATION_STATE["running"] = False
            _ANIMATION_STATE["paused"] = False
            _restore_global_undo()
            _release_validation_timer(_tick)
            _restore_nodepreview_playback_updates()
            _set_full_validation_runtime(scene, workflow, module, module_state, running=False, paused=False, status="error", paused_at=0.0, pause_accumulated=0.0, current_index=0, current_factor=0.0)
            if panel_api is not None:
                panel_api.set_status(f"Full validation interrupted: {exc}", level="ERROR")
            if module_state is not None:
                module_state.set("last_result", f"Full validation interrupted: {exc}")
            return None

    _register_validation_timer(_tick)
    bpy.app.timers.register(_tick, first_interval=0.0)
    if panel_api is not None:
        panel_api.set_status(f"已开始 ARKit 全面混合验证：{len(list(plan.get('segments', []) or []))} 段连续动画，控制版与关键帧版效果一致", level="OK")
    return plan


def _start_full_validation_animation(context, scene, workflow, module, panel_api, module_state, items):
    _stop_validation_animation(scene=scene, workflow=workflow, module=module, module_state=module_state)
    _restore_validation_preview_state(scene=scene, workflow=workflow, module=module, module_state=module_state)
    _clear_full_validation_plan(scene, workflow, module, module_state)
    try:
        obj, key_blocks = _target_object(context, panel_api)
    except Exception as exc:
        if _report_soft_target_issue(exc, panel_api, module_state):
            return None
        raise

    _detach_full_validation_action(obj)
    resolved_states = _resolve_full_validation_states_for_object(key_blocks, items, target_count=len(FULL_VALIDATION_STATES))
    if not resolved_states:
        message = "No matched ARKit shape keys for full validation"
        if panel_api is not None:
            panel_api.set_status(message, level="WARNING")
        if module_state is not None:
            module_state.set("last_result", message)
        return None

    _switch_to_object_mode(context, obj)
    _capture_validation_preview_state(obj, key_blocks, scene=scene, workflow=workflow, module=module, module_state=module_state)
    _configure_full_validation_scene_timing(scene)
    _zero_known_shape_keys(key_blocks, items)
    text_mix_states = _resolve_text_mix_validation_states_for_object(key_blocks)
    plan = _build_full_validation_plan(
        resolved_states,
        1,
        FULL_VALIDATION_PLAYBACK_FPS,
        FULL_VALIDATION_TOTAL_SECONDS,
        "",
        obj.name_full,
        tail_states=text_mix_states,
    )
    used_key_names = sorted(
        {
            key_name
            for segment in list(plan.get("segments", []) or [])
            for key_name in set(dict(segment.get("from_values", {}) or {})) | set(dict(segment.get("target_values", {}) or {}))
        },
        key=str.casefold,
    )
    if not used_key_names:
        message = "No writable ARKit shape keys for full validation"
        if panel_api is not None:
            panel_api.set_status(message, level="WARNING")
        if module_state is not None:
            module_state.set("last_result", message)
        return None

    _set_full_validation_plan(scene, workflow, module, module_state, plan)
    first_segment = _find_plan_segment(plan, int(plan.get("start_frame", 1) or 1))
    first_target_values = dict((first_segment or {}).get("target_values", {}) or {})
    if first_target_values:
        dominant_name = max(first_target_values.items(), key=lambda item: float(item[1]))[0]
        _set_active_shape_key_index(obj, key_blocks, dominant_name)
    scene.frame_start = int(plan.get("start_frame", 1) or 1)
    scene.frame_end = max(scene.frame_start, int(plan.get("end_frame", scene.frame_start) or scene.frame_start))
    try:
        scene.frame_current = int(scene.frame_start)
        if hasattr(scene, "frame_subframe"):
            scene.frame_subframe = 0.0
    except Exception:
        pass

    runtime = _full_validation_runtime(module_state, scene, workflow, module)
    full_token = int(runtime.get("token", 0)) + 1
    _set_full_validation_runtime(
        scene,
        workflow,
        module,
        module_state,
        running=True,
        paused=False,
        token=full_token,
        current_label="",
        current_values="",
        status="running",
        paused_at=0.0,
        pause_accumulated=0.0,
        current_index=1,
        current_factor=0.0,
        total=len(list(plan.get("segments", []) or [])),
    )

    total_seconds = max(1.0, float(plan.get("total_seconds", FULL_VALIDATION_TOTAL_SECONDS) or FULL_VALIDATION_TOTAL_SECONDS))
    _ANIMATION_STATE["token"] = full_token
    _ANIMATION_STATE["running"] = True
    _ANIMATION_STATE["paused"] = False
    _suspend_nodepreview_playback_updates()
    _suspend_global_undo()
    token = int(_ANIMATION_STATE["token"])
    object_name = obj.name_full
    plan_start_frame = float(plan.get("start_frame", 1) or 1)
    plan_end_frame = float(plan.get("end_frame", plan_start_frame) or plan_start_frame)
    state = {
        "started_at": time.perf_counter(),
        "last_status_at": 0.0,
        "last_segment_index": -1,
        "key_map_object_name": "",
        "key_map": {},
        "last_values": {},
    }

    def _active_key_map():
        current_obj = bpy.data.objects.get(object_name)
        current_shape_keys = getattr(getattr(current_obj, "data", None), "shape_keys", None) if current_obj is not None else None
        current_key_blocks = getattr(current_shape_keys, "key_blocks", None) if current_shape_keys is not None else None
        if current_obj is None or current_key_blocks is None:
            return None, {}
        current_object_name = str(getattr(current_obj, "name_full", "") or getattr(current_obj, "name", "") or "")
        cached_key_map = dict(state.get("key_map", {}) or {})
        cached_object_name = str(state.get("key_map_object_name", "") or "")
        if cached_key_map and cached_object_name == current_object_name:
            return current_obj, cached_key_map
        key_map = {getattr(key_block, "name", ""): key_block for index, key_block in enumerate(current_key_blocks) if index != 0}
        state["key_map_object_name"] = current_object_name
        state["key_map"] = key_map
        return current_obj, key_map

    def _apply_value_map(values):
        current_obj, key_map = _active_key_map()
        if not key_map:
            return False
        last_values = dict(state.get("last_values", {}) or {})
        changed = False
        for shape_key_name in used_key_names:
            target_key = key_map.get(shape_key_name)
            if target_key is None:
                continue
            next_value = max(0.0, min(1.0, float(values.get(shape_key_name, 0.0) or 0.0)))
            previous_value = float(last_values.get(shape_key_name, 0.0) or 0.0)
            if abs(previous_value - next_value) <= 0.0005:
                continue
            target_key.value = next_value
            last_values[shape_key_name] = next_value
            changed = True
        if not changed:
            return True
        state["last_values"] = last_values
        return True

    def _tick():
        try:
            runtime_now = _full_validation_runtime(module_state, scene, workflow, module)
            if not _ANIMATION_STATE["running"] or token != _ANIMATION_STATE["token"]:
                _restore_global_undo()
                _release_validation_timer(_tick)
                _restore_nodepreview_playback_updates()
                _set_full_validation_runtime(scene, workflow, module, module_state, running=False, paused=False, status="", paused_at=0.0, pause_accumulated=0.0, current_index=0, current_factor=0.0)
                return None
            if full_token != int(runtime_now.get("token", 0)):
                _restore_global_undo()
                _release_validation_timer(_tick)
                _restore_nodepreview_playback_updates()
                _set_full_validation_runtime(scene, workflow, module, module_state, running=False, paused=False, status="", paused_at=0.0, pause_accumulated=0.0, current_index=0, current_factor=0.0)
                return None
            if bpy.data.objects.get(object_name) is None:
                _ANIMATION_STATE["running"] = False
                _ANIMATION_STATE["paused"] = False
                _set_full_validation_runtime(scene, workflow, module, module_state, running=False, paused=False, status="stopped", paused_at=0.0, pause_accumulated=0.0, current_index=0, current_factor=0.0)
                _restore_global_undo()
                _release_validation_timer(_tick)
                _restore_nodepreview_playback_updates()
                return None
            if bool(_ANIMATION_STATE.get("paused")) or bool(runtime_now.get("paused")):
                _set_full_validation_runtime(scene, workflow, module, module_state, status="paused", _persist=False)
                return FULL_VALIDATION_TIMER_INTERVAL
            paused_at = float(runtime_now.get("paused_at") or 0.0)
            if paused_at > 0.0:
                resumed_at = time.perf_counter()
                paused_duration = max(0.0, resumed_at - paused_at)
                runtime_now = _set_full_validation_runtime(
                    scene,
                    workflow,
                    module,
                    module_state,
                    pause_accumulated=float(runtime_now.get("pause_accumulated") or 0.0) + paused_duration,
                    paused_at=0.0,
                    status="running",
                )
            else:
                runtime_now = _set_full_validation_runtime(scene, workflow, module, module_state, status="running", _persist=False)

            elapsed = max(0.0, time.perf_counter() - float(state["started_at"]) - float(runtime_now.get("pause_accumulated") or 0.0))
            if elapsed >= total_seconds:
                _ANIMATION_STATE["running"] = False
                _ANIMATION_STATE["paused"] = False
                _apply_value_map({})
                try:
                    scene.frame_current = int(plan_end_frame)
                    if hasattr(scene, "frame_subframe"):
                        scene.frame_subframe = 0.0
                except Exception:
                    pass
                _set_full_validation_runtime(
                    scene,
                    workflow,
                    module,
                    module_state,
                    running=False,
                    paused=False,
                    status="finished",
                    paused_at=0.0,
                    pause_accumulated=0.0,
                    current_index=len(list(plan.get("segments", []) or [])),
                    current_factor=1.0,
                    total=len(list(plan.get("segments", []) or [])),
                )
                _restore_global_undo()
                _release_validation_timer(_tick)
                _restore_nodepreview_playback_updates()
                if panel_api is not None:
                    panel_api.set_status(f"Full validation finished: {len(list(plan.get('segments', []) or []))} segments", level="OK")
                if module_state is not None:
                    module_state.set("last_result", f"Full validation finished: {len(list(plan.get('segments', []) or []))} segments")
                return None

            frame_span = max(1.0, plan_end_frame - plan_start_frame)
            virtual_frame = min(plan_end_frame, plan_start_frame + ((elapsed / total_seconds) * frame_span))
            current_segment = _find_plan_segment(plan, virtual_frame)
            if current_segment is None:
                current_segment = (list(plan.get("segments", []) or []) or [None])[-1]
            current_values = _segment_interpolated_values(current_segment, virtual_frame)
            _apply_value_map(current_values)
            current_name = str((current_segment or {}).get("name", "mix") or "mix")
            current_index = int((current_segment or {}).get("index", 0) or 0)
            phase = _segment_factor(current_segment, virtual_frame)
            non_zero_values = [(name, value) for name, value in current_values.items() if abs(float(value)) > 0.001]
            non_zero_values.sort(key=lambda item: (-float(item[1]), item[0].casefold()))
            current_values_text = ", ".join(f"{name}={float(value):.2f}" for name, value in non_zero_values[:16])
            _set_full_validation_runtime(
                scene,
                workflow,
                module,
                module_state,
                current_label=current_name,
                current_values=current_values_text,
                current_index=current_index,
                current_factor=phase,
                total=len(list(plan.get("segments", []) or [])),
                _persist=False,
            )
            now = time.perf_counter()
            if current_index != int(state.get("last_segment_index", -1) or -1):
                state["last_segment_index"] = current_index
            if module_state is not None and (state["last_status_at"] <= 0.0 or (now - float(state["last_status_at"])) >= ANIMATION_STATUS_INTERVAL):
                state["last_status_at"] = now
            return FULL_VALIDATION_TIMER_INTERVAL
        except ReferenceError:
            _ANIMATION_STATE["running"] = False
            _ANIMATION_STATE["paused"] = False
            _restore_global_undo()
            _release_validation_timer(_tick)
            _restore_nodepreview_playback_updates()
            _set_full_validation_runtime(scene, workflow, module, module_state, running=False, paused=False, status="stopped", paused_at=0.0, pause_accumulated=0.0, current_index=0, current_factor=0.0)
            return None
        except Exception as exc:
            _ANIMATION_STATE["running"] = False
            _ANIMATION_STATE["paused"] = False
            _restore_global_undo()
            _release_validation_timer(_tick)
            _restore_nodepreview_playback_updates()
            _set_full_validation_runtime(scene, workflow, module, module_state, running=False, paused=False, status="error", paused_at=0.0, pause_accumulated=0.0, current_index=0, current_factor=0.0)
            if panel_api is not None:
                panel_api.set_status(f"Full validation interrupted: {exc}", level="ERROR")
            if module_state is not None:
                module_state.set("last_result", f"Full validation interrupted: {exc}")
            return None

    _register_validation_timer(_tick)
    bpy.app.timers.register(_tick, first_interval=0.0)
    if panel_api is not None:
        panel_api.set_status(f"Full validation started: {len(list(plan.get('segments', []) or []))} segments in live no-keyframe mode", level="OK")
    return plan


def _current_area_context(context):
    window = getattr(context, "window", None)
    screen = getattr(window, "screen", None) if window is not None else None
    area = getattr(context, "area", None)
    region = getattr(context, "region", None)
    return {
        "window": window,
        "screen": screen,
        "area": area,
        "region": region,
        "scene": getattr(context, "scene", None),
        "view_layer": getattr(context, "view_layer", None),
    }


def _clear_full_validation_runtime_state(scene, workflow, module, module_state=None):
    scope_key = _full_validation_runtime_scope_key(scene=scene, workflow=workflow, module=module)
    _set_full_validation_runtime(
        scene,
        workflow,
        module,
        module_state,
        running=False,
        paused=False,
        current_label="",
        current_values="",
        status="",
        paused_at=0.0,
        pause_accumulated=0.0,
        current_index=0,
        current_factor=0.0,
        total=0,
    )
    _FULL_VALIDATION_RUNTIME_BY_SCOPE.pop(scope_key, None)


def _register_validation_timer(callback):
    _purge_validation_timer_registry(unregister=True)
    _VALIDATION_TIMER_STATE["callback"] = callback
    try:
        registry = bpy.app.driver_namespace.setdefault(_VALIDATION_TIMER_REGISTRY_KEY, set())
        registry.add(callback)
    except Exception:
        pass


def _release_validation_timer(callback):
    if _VALIDATION_TIMER_STATE.get("callback") is callback:
        _VALIDATION_TIMER_STATE["callback"] = None
    try:
        registry = bpy.app.driver_namespace.get(_VALIDATION_TIMER_REGISTRY_KEY)
        if registry is not None:
            registry.discard(callback)
            if not registry:
                bpy.app.driver_namespace.pop(_VALIDATION_TIMER_REGISTRY_KEY, None)
    except Exception:
        pass


def _purge_validation_timer_registry(unregister=False):
    removed = False
    callbacks = []
    try:
        registry = bpy.app.driver_namespace.get(_VALIDATION_TIMER_REGISTRY_KEY)
        if registry is not None:
            callbacks.extend(list(registry))
    except Exception:
        pass
    current_callback = _VALIDATION_TIMER_STATE.get("callback")
    if current_callback is not None:
        callbacks.append(current_callback)
    seen = set()
    for callback in callbacks:
        if callback is None:
            continue
        callback_id = id(callback)
        if callback_id in seen:
            continue
        seen.add(callback_id)
        if unregister:
            try:
                bpy.app.timers.unregister(callback)
            except Exception:
                pass
        removed = True
    try:
        bpy.app.driver_namespace.pop(_VALIDATION_TIMER_REGISTRY_KEY, None)
    except Exception:
        pass
    _VALIDATION_TIMER_STATE["callback"] = None
    return removed


def _cancel_validation_timer():
    return bool(_purge_validation_timer_registry(unregister=True))


def cleanup_runtime(scene=None, workflow=None, module=None, module_state=None):
    _cancel_validation_timer()
    _ANIMATION_STATE["running"] = False
    _ANIMATION_STATE["paused"] = False
    _ANIMATION_STATE["token"] += 1
    _restore_global_undo()
    _restore_nodepreview_playback_updates()
    _clear_reference_runtime_state()
    _release_preview_images(force=True, min_age_seconds=0.0)
    _clear_full_validation_runtime_state(scene, workflow, module, module_state)
    _clear_full_validation_plan(scene, workflow, module, module_state)
    _run_memory_cleanup()
    _tag_redraw_all()
    return True


def _remove_action_if_matches(action_name):
    action_name = str(action_name or "").strip()
    if not action_name:
        return False
    action = bpy.data.actions.get(action_name)
    if action is None:
        return False
    try:
        for fcurve in list(action.fcurves):
            action.fcurves.remove(fcurve)
    except Exception:
        pass
    try:
        action.use_fake_user = False
    except Exception:
        pass
    try:
        bpy.data.actions.remove(action)
        return True
    except Exception:
        return False


def _remove_full_validation_actions_for_object(obj, plan=None):
    removed = 0
    expected_names = {_full_validation_action_name(obj)}
    if isinstance(plan, dict):
        plan_action_name = str(plan.get("action_name", "") or "").strip()
        if plan_action_name:
            expected_names.add(plan_action_name)
    for action in list(bpy.data.actions):
        action_name = str(getattr(action, "name_full", "") or getattr(action, "name", "") or "").strip()
        if action_name not in expected_names and not action_name.startswith("GoWorkflow_ARKitFullValidation_"):
            continue
        try:
            for fcurve in list(action.fcurves):
                action.fcurves.remove(fcurve)
        except Exception:
            pass
        try:
            action.use_fake_user = False
        except Exception:
            pass
        try:
            bpy.data.actions.remove(action)
            removed += 1
        except Exception:
            pass
    return removed


def _start_full_validation_native_animation(context, scene, workflow, module, panel_api, module_state, items):
    _stop_validation_animation(scene=scene, workflow=workflow, module=module, module_state=module_state)
    _restore_validation_preview_state(scene=scene, workflow=workflow, module=module, module_state=module_state)
    _clear_full_validation_plan(scene, workflow, module, module_state)
    try:
        obj, key_blocks = _target_object(context, panel_api)
    except Exception as exc:
        if _report_soft_target_issue(exc, panel_api, module_state):
            return None
        raise

    key_block_map = {str(getattr(key_block, "name", "") or ""): key_block for index, key_block in enumerate(key_blocks) if index != 0}
    resolved_states = _resolve_full_validation_states_for_object(key_blocks, items, target_count=len(FULL_VALIDATION_STATES))
    if not resolved_states:
        message = "当前物体没有匹配到可用于全面混合验证的 ARKit 形态键，请先确认物体上至少有一部分对应形态键"
        if panel_api is not None:
            panel_api.set_status(message, level="WARNING")
        if module_state is not None:
            module_state.set("last_result", message)
        return None

    _switch_to_object_mode(context, obj)
    _capture_validation_preview_state(obj, key_blocks, scene=scene, workflow=workflow, module=module, module_state=module_state)
    _configure_full_validation_scene_timing(scene)
    start_frame = 1
    shape_keys, action, plan, used_key_names = _bake_full_validation_action(obj, key_blocks, resolved_states, [], start_frame=start_frame)
    if not used_key_names:
        message = "当前物体没有可写入关键帧的匹配 ARKit 形态键"
        if panel_api is not None:
            panel_api.set_status(message, level="WARNING")
        if module_state is not None:
            module_state.set("last_result", message)
        return None

    _set_full_validation_plan(scene, workflow, module, module_state, plan)
    for key_name in used_key_names:
        if key_name in key_block_map:
            key_block_map[key_name].value = 0.0
    _set_active_shape_key_index(obj, key_blocks, used_key_names[0])
    scene.frame_start = int(start_frame)
    scene.frame_end = max(int(start_frame), int(plan.get("end_frame", start_frame) or start_frame))
    _set_scene_frame(scene, int(start_frame))
    _set_full_validation_runtime(
        scene,
        workflow,
        module,
        module_state,
        running=False,
        paused=False,
        token=int(_full_validation_runtime(module_state, scene, workflow, module).get("token", 0) or 0),
        current_label=str((plan.get("segments") or [{}])[0].get("name", "") or ""),
        current_values="",
        status="generated",
        paused_at=0.0,
        pause_accumulated=0.0,
        current_index=0,
        current_factor=0.0,
        total=len(list(plan.get("segments", []) or [])),
    )
    _tag_redraw_all()
    message = f"已生成 ARKit 全面混合验证关键帧：{len(list(plan.get('segments', []) or []))} 组，结束帧 {int(plan.get('end_frame', start_frame) or start_frame)}"
    if panel_api is not None:
        panel_api.set_status(message, level="OK")
    if module_state is not None:
        module_state.set("last_result", message)
    return plan


def _reset_full_validation_to_preview(scene, workflow, module, panel_api=None, module_state=None):
    _stop_validation_animation(scene=scene, workflow=workflow, module=module, module_state=module_state)
    plan = _full_validation_plan(module_state, scene, workflow, module)
    restored = _restore_validation_preview_state(scene=scene, workflow=workflow, module=module, module_state=module_state)
    obj = bpy.data.objects.get(str(plan.get("object_name", "") or "").strip()) if plan else None
    if obj is None:
        try:
            obj, _key_blocks = _target_object(getattr(bpy, "context", None), panel_api)
        except Exception:
            obj = None
    if obj is not None:
        shape_keys, animation_data = _detach_full_validation_action(obj, plan)
        _remove_full_validation_actions_for_object(obj, plan)
        key_blocks = getattr(shape_keys, "key_blocks", None) if shape_keys is not None else None
        _reset_shape_keys_to_basis(obj, key_blocks, active_index=0)
    _clear_full_validation_plan(scene, workflow, module, module_state)
    _clear_full_validation_runtime_state(scene, workflow, module, module_state)
    _tag_redraw_all()
    if restored:
        message = "已重置到全面混合验证之前的状态"
        if panel_api is not None:
            panel_api.set_status(message, level="OK")
        if module_state is not None:
            module_state.set("last_result", message)
        return True
    if plan:
        message = "已清除全面混合验证动画计划，但没有找到可恢复的预览前状态"
        if panel_api is not None:
            panel_api.set_status(message, level="WARNING")
        if module_state is not None:
            module_state.set("last_result", message)
        return True
    return False


def _stop_validation_animation(scene=None, workflow=None, module=None, module_state=None):
    _cancel_validation_timer()
    was_running = bool(_ANIMATION_STATE["running"])
    _ANIMATION_STATE["running"] = False
    _ANIMATION_STATE["paused"] = False
    _ANIMATION_STATE["token"] += 1
    _restore_global_undo()
    _restore_nodepreview_playback_updates()
    _set_full_validation_runtime(
        scene if scene is not None else globals().get("scene"),
        workflow if workflow is not None else globals().get("workflow"),
        module if module is not None else globals().get("module"),
        module_state if module_state is not None else globals().get("module_state"),
        running=False,
        paused=False,
        current_label="",
        current_values="",
        status="",
        paused_at=0.0,
        pause_accumulated=0.0,
        current_index=0,
        current_factor=0.0,
        total=0,
    )
    if was_running or bool(_full_validation_runtime(scene=scene, workflow=workflow, module=module).get("running")):
        _restore_validation_preview_state(scene=scene, workflow=workflow, module=module, module_state=module_state)
    _tag_redraw_all()


def _start_validation_animation(context, scene, workflow, module, panel_api, module_state, item, items):
    _stop_validation_animation(scene=scene, workflow=workflow, module=module, module_state=module_state)
    _restore_validation_preview_state(scene=scene, workflow=workflow, module=module, module_state=module_state)
    try:
        obj, key_blocks = _target_object(context, panel_api)
    except Exception as exc:
        if _report_soft_target_issue(exc, panel_api, module_state):
            return None
        raise
    shape_key_names = _validation_shape_keys(item, items)
    if not shape_key_names:
        raise Exception("当前步骤没有可用于混合验证的形态键")

    explicit_sequences = _explicit_validation_sequences(item)
    rule_key = _normalize_shape_key_name(item.get("shape_key", ""))
    cumulative_explicit_sequences = bool(explicit_sequences) and rule_key in CUMULATIVE_VALIDATION_SEQUENCE_KEYS
    matched = []
    reset_between_segments = bool(explicit_sequences) and not cumulative_explicit_sequences
    reset_target_keys = []
    reset_target_names = set()
    animated_target_names = set()
    if explicit_sequences:
        for sequence in explicit_sequences:
            current_keys = []
            current_names = []
            current_normalized = set()
            for shape_key_name in sequence:
                normalized = _normalize_shape_key_name(shape_key_name)
                if not normalized or normalized in current_normalized:
                    continue
                target_key = _find_matching_shape_key(key_blocks, shape_key_name)
                if target_key is None:
                    continue
                current_names.append(str(shape_key_name or "").strip())
                current_normalized.add(normalized)
                if normalized not in reset_target_names:
                    reset_target_names.add(normalized)
                    reset_target_keys.append(target_key)
                if cumulative_explicit_sequences and normalized in animated_target_names:
                    continue
                current_keys.append(target_key)
                if cumulative_explicit_sequences:
                    animated_target_names.add(normalized)
            if current_keys:
                matched.append((" + ".join(current_names), current_keys))
    else:
        consumed = set()
        for shape_key_name in shape_key_names:
            normalized = _normalize_shape_key_name(shape_key_name)
            if normalized in consumed:
                continue
            target_key = _find_matching_shape_key(key_blocks, shape_key_name)
            if target_key is None:
                continue
            if normalized.endswith("left"):
                pair_normalized = normalized[:-4] + "right"
                pair_name = next((candidate for candidate in shape_key_names if _normalize_shape_key_name(candidate) == pair_normalized), "")
                pair_key = _find_matching_shape_key(key_blocks, pair_name) if pair_name else None
                if pair_key is not None:
                    matched.append((f"{shape_key_name} + {pair_name}", [target_key, pair_key]))
                    consumed.add(normalized)
                    consumed.add(pair_normalized)
                    continue
            if normalized.endswith("right"):
                pair_normalized = normalized[:-5] + "left"
                if pair_normalized in consumed:
                    continue
            matched.append((shape_key_name, [target_key]))
            consumed.add(normalized)
    if not matched:
        if panel_api is not None:
            panel_api.set_status("文档提到的形态键在当前物体上都没有找到，请查看 Blender 控制台日志", level="WARNING")
        return None

    _switch_to_object_mode(context, obj)
    _capture_validation_preview_state(obj, key_blocks, scene=scene, workflow=workflow, module=module, module_state=module_state)
    _zero_known_shape_keys(key_blocks, items)
    _set_active_shape_key_index(obj, key_blocks, matched[0][0].split(" + ", 1)[0])
    keys_to_zero = reset_target_keys or [target_key for _shape_key_name, target_keys in matched for target_key in target_keys]
    for target_key in keys_to_zero:
        target_key.value = 0.0
    duration_seconds = _animation_duration_seconds(panel_api)
    animation_mode = "sequence" if explicit_sequences else _validation_animation_mode(item)

    _ANIMATION_STATE["running"] = True
    _suspend_nodepreview_playback_updates()
    _suspend_global_undo()
    token = int(_ANIMATION_STATE["token"])
    state = {
        "target_index": 0,
        "started_at": time.perf_counter(),
        "last_value": -1.0,
        "last_status_at": 0.0,
    }

    def _tick():
        if not _ANIMATION_STATE["running"] or token != _ANIMATION_STATE["token"]:
            _restore_global_undo()
            _release_validation_timer(_tick)
            _restore_nodepreview_playback_updates()
            return None
        target_index = int(state["target_index"])
        if target_index >= len(matched):
            _ANIMATION_STATE["running"] = False
            _restore_global_undo()
            _release_validation_timer(_tick)
            _restore_nodepreview_playback_updates()
            if panel_api is not None:
                panel_api.set_status(
                    f"已完成只读递增预览：{len(matched)} 个形态键当前保持在预览值，模式={'同时递增' if animation_mode == 'simultaneous' else '顺序递增'}；切步骤、重新验证或点重置都会恢复原值",
                    level="OK",
                )
            if module_state is not None:
                module_state.set(
                    "last_result",
                    f"已完成只读递增预览：{len(matched)} 个形态键当前保持在预览值，模式={'同时递增' if animation_mode == 'simultaneous' else '顺序递增'}",
                )
            return None
        elapsed = max(0.0, time.perf_counter() - float(state["started_at"]))
        value = min(1.0, elapsed / float(duration_seconds))
        try:
            if animation_mode == "simultaneous":
                current_name = " + ".join(name for name, _target in matched[:4])
                if len(matched) > 4:
                    current_name = f"{current_name} 等"
                if abs(value - float(state["last_value"])) >= 0.01 or value >= 1.0:
                    for _shape_key_name, target_keys in matched:
                        for target_key in target_keys:
                            target_key.value = value
                    state["last_value"] = value
            else:
                current_name, current_keys = matched[target_index]
                if reset_between_segments and float(state["last_value"]) < 0.0:
                    for target_key in keys_to_zero:
                        target_key.value = 0.0
                if abs(value - float(state["last_value"])) >= 0.01 or value >= 1.0:
                    for current_key in current_keys:
                        current_key.value = value
                    state["last_value"] = value
            now = time.perf_counter()
            if module_state is not None and (
                state["last_status_at"] <= 0.0
                or (now - float(state["last_status_at"])) >= ANIMATION_STATUS_INTERVAL
                or value >= 1.0
            ):
                state["last_status_at"] = now
        except Exception:
            _ANIMATION_STATE["running"] = False
            _restore_global_undo()
            _release_validation_timer(_tick)
            _restore_nodepreview_playback_updates()
            return None
        if animation_mode == "simultaneous":
            if value >= 1.0:
                for _shape_key_name, target_keys in matched:
                    for target_key in target_keys:
                        target_key.value = 1.0
                state["target_index"] = len(matched)
            return ANIMATION_TIMER_INTERVAL
        if value >= 1.0:
            for current_key in current_keys:
                current_key.value = 1.0
            state["target_index"] = target_index + 1
            state["started_at"] = time.perf_counter()
            state["last_value"] = -1.0
            state["last_status_at"] = 0.0
            if state["target_index"] < len(matched):
                if not reset_between_segments:
                    for next_key in matched[state["target_index"]][1]:
                        next_key.value = 0.0
        return ANIMATION_TIMER_INTERVAL

    _register_validation_timer(_tick)
    bpy.app.timers.register(_tick, first_interval=0.0)
    if panel_api is not None:
        panel_api.set_status(
            f"已开始只读递增预览：{len(matched)} 个形态键会按{'同时' if animation_mode == 'simultaneous' else '顺序'}模式，在每个 {duration_seconds:.2f} 秒内线性从 0 过渡到 1",
            level="OK",
        )
    return matched


def _reset_all_steps(context, scene, workflow, module, panel_api, module_state):
    _stop_validation_animation(scene=scene, workflow=workflow, module=module, module_state=module_state)
    if _restore_validation_preview_state(scene=scene, workflow=workflow, module=module, module_state=module_state):
        return {"mode": "restored", "count": 0}
    try:
        obj, key_blocks = _target_object(context, panel_api)
    except Exception as exc:
        if _report_soft_target_issue(exc, panel_api, module_state):
            return None
        raise
    _switch_to_object_mode(context, obj)
    _payload, items = _load_items()
    _zero_known_shape_keys(key_blocks, items)
    obj.active_shape_key_index = 0
    return {"mode": "zeroed", "count": len(_shape_key_names(items))}


def _change_media_index(panel_api, item, delta):
    files = _media_files(item)
    if not files:
        raise Exception("当前步骤没有可切换的参考媒体")
    index = (_media_index(panel_api, item) + int(delta)) % len(files)
    panel_api.set_int("media_index", index)
    state = _preview_runtime_state()
    state["last_media_switch_at"] = time.perf_counter()
    state["preload_queue"] = []
    _schedule_preview_switch_redraw()
    if len(state.get("image_cache", {}) or {}) > PREVIEW_MAX_CACHE_IMAGES:
        _schedule_idle_preview_release()
    return index, len(files)


def _change_detail_media_index(panel_api, item, delta):
    files = _detail_media_files(item)
    if not files:
        raise Exception("当前步骤没有可切换的补充参考图")
    index = (_detail_media_index(panel_api, item) + int(delta)) % len(files)
    panel_api.set_int("detail_media_index", index)
    state = _preview_runtime_state()
    state["last_detail_media_switch_at"] = time.perf_counter()
    state["preload_queue"] = []
    _schedule_preview_switch_redraw()
    if len(state.get("image_cache", {}) or {}) > PREVIEW_MAX_CACHE_IMAGES:
        _schedule_idle_preview_release()
    return index, len(files)


def _set_media_switch_status(panel_api, current_index, total, detail=False):
    if panel_api is None:
        return
    state = _preview_runtime_state()
    key = "last_detail_media_status_at" if detail else "last_media_status_at"
    now = time.perf_counter()
    last_at = float(state.get(key, 0.0) or 0.0)
    if last_at > 0.0 and (now - last_at) < MEDIA_STATUS_THROTTLE_SECONDS:
        return
    state[key] = now
    if detail:
        panel_api.set_status(f"\u5df2\u5207\u6362\u8865\u5145\u53c2\u8003 {current_index + 1} / {total}", level="OK")
    else:
        panel_api.set_status(f"\u5df2\u5207\u6362\u53c2\u8003\u5a92\u4f53 {current_index + 1} / {total}", level="OK")


def _recent_preview_switch(detail=False):
    state = _preview_runtime_state()
    key = "last_detail_media_switch_at" if detail else "last_media_switch_at"
    last_at = float(state.get(key, 0.0) or 0.0)
    return last_at > 0.0 and (time.perf_counter() - last_at) < PREVIEW_DRAW_DEBOUNCE_SECONDS


def _schedule_preview_switch_redraw():
    state = _preview_runtime_state()
    if state.get("preview_redraw_registered", False):
        return

    def _redraw_when_switch_settles():
        state = _preview_runtime_state()
        last_switch_at = max(
            float(state.get("last_media_switch_at", 0.0) or 0.0),
            float(state.get("last_detail_media_switch_at", 0.0) or 0.0),
        )
        remaining = PREVIEW_DRAW_DEBOUNCE_SECONDS - (time.perf_counter() - last_switch_at)
        if remaining > 0.0:
            return max(0.03, remaining)
        state["preview_redraw_registered"] = False
        _tag_redraw_all()
        return None

    state["preview_redraw_registered"] = True
    try:
        bpy.app.timers.register(_redraw_when_switch_settles, first_interval=PREVIEW_DRAW_DEBOUNCE_SECONDS)
    except Exception:
        state["preview_redraw_registered"] = False


def _drawer_store_key(key):
    return f"arkit_drawer_{str(key or '').strip()}"


def _drawer_open(panel_api, key, default=False, module_state=None):
    store_key = _drawer_store_key(key)
    if module_state is not None:
        value = module_state.get(store_key, None)
        if value is not None:
            return bool(value)
    if panel_api is None:
        return bool(default)
    return bool(panel_api.get_bool(store_key, default))


def _set_drawer_open(panel_api, key, value, module_state=None):
    store_key = _drawer_store_key(key)
    if module_state is not None:
        module_state.set(store_key, bool(value))
    if panel_api is not None:
        panel_api.set_bool(store_key, bool(value))


def _animation_duration_seconds(panel_api):
    if panel_api is None:
        return float(ANIMATION_DURATION_PER_KEY)
    try:
        value = float(panel_api.get_float("validation_duration_seconds", ANIMATION_DURATION_PER_KEY))
    except Exception:
        value = float(ANIMATION_DURATION_PER_KEY)
    return max(0.1, value)


def _persist_runtime_settings(module, panel_api):
    if panel_api is None:
        return
    _set_setting(
        module,
        "validation_duration_seconds",
        max(0.1, float(panel_api.get_float("validation_duration_seconds", ANIMATION_DURATION_PER_KEY))),
    )
    for key, default in (
        ("auto_validate_on_step", False),
        ("auto_zero_others", True),
        ("auto_edit_mode", True),
        ("auto_open_reference", True),
    ):
        _set_setting(module, key, bool(panel_api.get_bool(key, default)))


def _draw_drawer_header(layout, panel_api, key, title, default=False, module_state=None):
    expanded = _drawer_open(panel_api, key, default, module_state=module_state)
    row = layout.row(align=True)
    panel_api.draw_button(row, f"TOGGLE_DRAWER::{key}", title, icon="TRIA_DOWN" if expanded else "TRIA_RIGHT")
    return expanded


def _draw_full_text_block(layout, text, icon="INFO", width=54):
    lines = _cached_wrap_lines(text, width)
    if not lines:
        return
    col = layout.column(align=True)
    first = True
    for line in lines:
        col.label(text=line, icon=icon if first else "NONE")
        first = False


def _panel_wrap_width(context, fallback=54):
    region = getattr(context, "region", None)
    region_width = int(getattr(region, "width", 0) or 0)
    if region_width <= 0:
        return fallback
    usable_width = max(120, region_width - 48)
    estimated = int(usable_width / 8.8)
    return max(18, min(72, estimated))


def _detail_lines(item):
    key = _item_cache_key(item)
    state = _preview_runtime_state()
    cache = state.get("detail_lines_cache", {})
    if key and key in cache:
        return list(cache[key])
    lines = []
    for entry in list(item.get("detail_notes", []) or []):
        text = str(entry or "").strip()
        if text and text not in lines:
            lines.append(text)
    detail_text = str(item.get("detail_ja_zh") or item.get("detail_ja") or "").strip()
    if detail_text and detail_text not in lines:
        lines.append(detail_text)
    if key:
        cache[key] = tuple(lines)
        _trim_small_cache(cache, 256)
    return lines


def _has_detail_hint(item):
    key = _item_cache_key(item)
    cache = _preview_runtime_state().get("detail_lines_cache", {})
    if key and key in cache and cache.get(key):
        return True
    if item.get("detail_notes"):
        return True
    if str(item.get("detail_ja_zh") or item.get("detail_ja") or "").strip():
        return True
    return bool(item.get("detail_media_files") or item.get("detail_preview_media_files"))


def _tag_redraw_all():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for window in getattr(wm, "windows", []) or []:
        screen = getattr(window, "screen", None)
        if screen is None:
            continue
        for area in getattr(screen, "areas", []) or []:
            try:
                area.tag_redraw()
            except Exception:
                pass


def _open_reference_window(item, panel_api, module_state, step_index, total_steps):
    payload = _viewer_payload(item, panel_api, module_state, step_index, total_steps)
    _launch_reference_viewer(payload, viewer_kind="main")
    if panel_api is not None:
        panel_api.set_status("\u5df2\u6253\u5f00\u7f6e\u9876\u53c2\u8003\u56fe\u7a97\u53e3", level="OK")
    if module_state is not None and payload["media_files"]:
        module_state.set("last_reference_image", payload["media_files"][payload["media_index"]])
    return payload


def _open_detail_reference_window(item, panel_api, module_state, step_index, total_steps):
    payload = _detail_viewer_payload(item, panel_api, module_state, step_index, total_steps)
    _launch_reference_viewer(payload, viewer_kind="detail")
    if panel_api is not None:
        panel_api.set_status("\u5df2\u6253\u5f00\u7f6e\u9876\u8865\u5145\u53c2\u8003\u56fe\u7a97\u53e3", level="OK")
    if module_state is not None and payload["media_files"]:
        module_state.set("last_detail_reference_image", payload["media_files"][payload["media_index"]])
    return payload


def _normalized_image_path(path):
    value = str(path or "").strip()
    if not value:
        return ""
    try:
        return os.path.normcase(os.path.abspath(value))
    except Exception:
        return ""


def _preview_cache_dir():
    folder = _preset_paths().get("panel_static_preview_dir", "")
    if not folder:
        return ""
    try:
        os.makedirs(folder, exist_ok=True)
    except Exception:
        return ""
    return folder


def _cached_preview_filename(media_path):
    normalized = _normalized_image_path(media_path)
    if not normalized:
        return ""
    digest = hashlib.sha1(normalized.encode("utf-8", errors="replace")).hexdigest()[:16]
    stem = os.path.splitext(os.path.basename(normalized))[0]
    safe_stem = re.sub(r"[^0-9A-Za-z_.-]+", "_", stem).strip("._") or "preview"
    return f"{safe_stem}_{digest}.png"


def _source_file_signature(path):
    normalized = _normalized_image_path(path)
    if not normalized or not os.path.isfile(normalized):
        return (0, 0)
    try:
        stat = os.stat(normalized)
        return (int(stat.st_mtime_ns), int(stat.st_size))
    except Exception:
        return (0, 0)


def _preview_runtime_state():
    try:
        store = bpy.app.driver_namespace.get(_PREVIEW_RUNTIME_STATE_KEY)
        if not isinstance(store, dict):
            store = {}
            bpy.app.driver_namespace[_PREVIEW_RUNTIME_STATE_KEY] = store
    except Exception:
        store = {}
    store.setdefault("resolve_cache", {})
    store.setdefault("image_cache", {})
    store.setdefault("icon_cache", {})
    store.setdefault("pending_release", {})
    store.setdefault("release_retry_registered", False)
    store.setdefault("idle_release_registered", False)
    store.setdefault("idle_keep_paths", [])
    store.setdefault("preload_registered", False)
    store.setdefault("preload_queue", [])
    store.setdefault("preview_redraw_registered", False)
    store.setdefault("last_media_switch_at", 0.0)
    store.setdefault("last_detail_media_switch_at", 0.0)
    store.setdefault("last_media_status_at", 0.0)
    store.setdefault("last_detail_media_status_at", 0.0)
    store.setdefault("text_wrap_cache", {})
    store.setdefault("detail_lines_cache", {})
    store.setdefault("validation_mix_cache", {})
    return store


def _reference_runtime_state():
    try:
        store = bpy.app.driver_namespace.get(_REFERENCE_RUNTIME_STATE_KEY)
        if not isinstance(store, dict):
            store = {}
            bpy.app.driver_namespace[_REFERENCE_RUNTIME_STATE_KEY] = store
    except Exception:
        store = {}
    store.setdefault("preset_paths", None)
    store.setdefault("payload_cache", {})
    return store


def _clear_reference_runtime_state():
    state = _reference_runtime_state()
    state["preset_paths"] = None
    payload_cache = state.get("payload_cache", {})
    if isinstance(payload_cache, dict):
        payload_cache.clear()
    else:
        state["payload_cache"] = {}


def _trim_small_cache(cache, limit):
    if not isinstance(cache, dict):
        return
    extra = len(cache) - int(limit)
    if extra <= 0:
        return
    for key in list(cache.keys())[:extra]:
        cache.pop(key, None)


def _wrap_text_lines(text, width):
    value = str(text or "").strip()
    if not value:
        return ()
    lines = []
    current = ""
    for token in re.findall(r"[A-Za-z0-9_./:+-]+|\s+|[^\x00-\x7F]|.", value):
        if token.isspace():
            if current and not current.endswith(" "):
                current += " "
            continue
        token = token.strip()
        if not token:
            continue
        candidate = (current + token).strip()
        visual_width = sum(1 if ord(ch) < 128 else 2 for ch in candidate)
        if current and visual_width > max(8, int(width)):
            lines.append(current.rstrip())
            current = token
        else:
            current = candidate
    if current:
        lines.append(current.rstrip())
    return tuple(lines)


def _cached_wrap_lines(text, width):
    state = _preview_runtime_state()
    cache = state.get("text_wrap_cache", {})
    key = (str(text or ""), int(width))
    lines = cache.get(key)
    if lines is None:
        lines = _wrap_text_lines(text, width)
        cache[key] = lines
        _trim_small_cache(cache, 512)
    return lines


def _run_memory_cleanup():
    try:
        return gc.collect()
    except Exception:
        return 0


def _generate_gif_first_frame_preview(media_path, preview_path, max_size=192):
    if not media_path or not preview_path:
        return False
    script = r"""
Add-Type -AssemblyName System.Drawing
$src = $env:GO_WORKFLOW_PREVIEW_SRC
$dst = $env:GO_WORKFLOW_PREVIEW_DST
$maxSize = [int]$env:GO_WORKFLOW_PREVIEW_MAX
$img = [System.Drawing.Image]::FromFile($src)
try {
    if ([System.IO.Path]::GetExtension($src).ToLowerInvariant() -eq '.gif') {
        $img.SelectActiveFrame([System.Drawing.Imaging.FrameDimension]::Time, 0) | Out-Null
    }
    $ratio = [Math]::Min($maxSize / [double]$img.Width, $maxSize / [double]$img.Height)
    if ($ratio -gt 1.0) { $ratio = 1.0 }
    $newW = [Math]::Max(1, [int][Math]::Round($img.Width * $ratio))
    $newH = [Math]::Max(1, [int][Math]::Round($img.Height * $ratio))
    $bmp = New-Object System.Drawing.Bitmap $newW, $newH
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    try {
        $g.Clear([System.Drawing.Color]::Transparent)
        $g.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $g.DrawImage($img, 0, 0, $newW, $newH)
        $bmp.Save($dst, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $g.Dispose()
        $bmp.Dispose()
    }
} finally {
    $img.Dispose()
}
"""
    env = os.environ.copy()
    env["GO_WORKFLOW_PREVIEW_SRC"] = media_path
    env["GO_WORKFLOW_PREVIEW_DST"] = preview_path
    env["GO_WORKFLOW_PREVIEW_MAX"] = str(int(max_size))
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=8,
            check=False,
        )
    except Exception:
        return False
    return result.returncode == 0 and os.path.isfile(preview_path)


def _panel_preview_path(media_path):
    if _is_remote_media_path(media_path):
        return ""
    normalized = _normalized_image_path(media_path)
    if not normalized:
        return ""
    state = _preview_runtime_state()
    resolve_cache = state.get("resolve_cache", {})
    direct_cache_key = f"preview::{normalized}"
    direct_cached = resolve_cache.get(direct_cache_key)
    if isinstance(direct_cached, str):
        return direct_cached
    if not os.path.isfile(normalized):
        resolve_cache[direct_cache_key] = ""
        return ""
    ext = os.path.splitext(normalized)[1].lower()
    if ext not in {".gif", ".png", ".jpg", ".jpeg", ".bmp"}:
        return ""
    if ext != ".gif":
        resolve_cache[direct_cache_key] = normalized
        return normalized
    source_signature = _source_file_signature(normalized)
    cached = resolve_cache.get(normalized)
    if cached and tuple(cached.get("signature", ())) == source_signature:
        preview_path = _normalized_image_path(cached.get("preview_path", ""))
        if cached.get("available", False) and preview_path and os.path.isfile(preview_path):
            return preview_path
        if not cached.get("available", False):
            return ""
    preview_dir = _preview_cache_dir()
    if not preview_dir:
        return ""
    candidate = os.path.join(preview_dir, _cached_preview_filename(normalized))
    candidate = _normalized_image_path(candidate)
    if candidate and os.path.isfile(candidate):
        try:
            if os.path.getmtime(candidate) >= os.path.getmtime(normalized):
                resolve_cache[normalized] = {
                    "signature": source_signature,
                    "preview_path": candidate,
                    "available": True,
                }
                return candidate
        except Exception:
            resolve_cache[normalized] = {
                "signature": source_signature,
                "preview_path": candidate,
                "available": True,
            }
            return candidate
    if _generate_gif_first_frame_preview(normalized, candidate):
        resolve_cache[normalized] = {
            "signature": source_signature,
            "preview_path": candidate,
            "available": True,
        }
        return candidate
    resolve_cache[normalized] = {
        "signature": source_signature,
        "preview_path": "",
        "available": False,
    }
    return ""


def _cleanup_panel_preview_cache():
    removed = _release_preview_images(force=True, min_age_seconds=0.0)
    state = _preview_runtime_state()
    state.get("resolve_cache", {}).clear()
    state.get("icon_cache", {}).clear()
    folder = _preview_cache_dir()
    if not folder or not os.path.isdir(folder):
        return removed
    for name in os.listdir(folder):
        path = os.path.join(folder, name)
        try:
            if os.path.isfile(path):
                os.remove(path)
                removed += 1
        except Exception:
            pass
    _run_memory_cleanup()
    return removed


def _cleanup_reference_cache_images():
    _clear_reference_runtime_state()
    return _cleanup_panel_preview_cache()


def _preview_file_signature(path):
    normalized = _normalized_image_path(path)
    if not normalized or not os.path.isfile(normalized):
        return (0, 0)
    try:
        stat = os.stat(normalized)
        return (int(stat.st_mtime_ns), int(stat.st_size))
    except Exception:
        return (0, 0)


def _load_panel_preview_image(path):
    normalized = _normalized_image_path(path)
    if not normalized:
        return None
    state = _preview_runtime_state()
    image_cache = state.get("image_cache", {})
    cached = image_cache.get(normalized)
    if cached:
        try:
            image_name = str(cached.get("image_name", "") or "")
            image = bpy.data.images.get(image_name)
            if image is not None and _normalized_image_path(getattr(image, "filepath", "")) == normalized:
                cached["last_used_at"] = time.perf_counter()
                _perf_increment("preview_cache_hits")
                return image
        except Exception:
            image_cache.pop(normalized, None)
    if not os.path.isfile(normalized):
        image_cache.pop(normalized, None)
        _perf_increment("preview_load_failures")
        return None
    _perf_increment("preview_cache_misses")
    try:
        image = bpy.data.images.load(normalized, check_existing=True)
    except Exception:
        _perf_increment("preview_load_failures")
        return None
    image_cache[normalized] = {
        "image_name": str(getattr(image, "name", "") or ""),
        "last_used_at": time.perf_counter(),
    }
    if len(image_cache) > PREVIEW_HARD_CACHE_IMAGES:
        _schedule_idle_preview_release(keep_paths=[normalized])
    try:
        image.preview_ensure()
    except Exception:
        pass
    _perf_push_sample(
        "preview_events",
        {
            "t": round(time.perf_counter(), 4),
            "event": "load",
            "path": normalized,
            "cache_items": len(image_cache),
        },
    )
    return image


def _native_preview_icon_id(image):
    if image is None:
        return 0
    state = _preview_runtime_state()
    icon_cache = state.get("icon_cache", {})
    normalized = _normalized_image_path(getattr(image, "filepath", ""))
    image_name = str(getattr(image, "name", "") or "")
    image_ptr = 0
    try:
        image_ptr = int(image.as_pointer())
    except Exception:
        image_ptr = 0
    cached = icon_cache.get(normalized or image_name)
    if (
        isinstance(cached, dict)
        and cached.get("image_name") == image_name
        and int(cached.get("image_ptr", 0) or 0) == image_ptr
        and int(cached.get("icon_id", 0) or 0) > 0
    ):
        return int(cached.get("icon_id", 0) or 0)
    try:
        image.preview_ensure()
    except Exception:
        pass
    preview = getattr(image, "preview", None)
    icon_id = int(getattr(preview, "icon_id", 0) or 0) if preview is not None else 0
    if icon_id > 0:
        icon_cache[normalized or image_name] = {
            "image_name": image_name,
            "image_ptr": image_ptr,
            "icon_id": icon_id,
        }
    return icon_id


def _draw_native_image_preview(layout, image, fallback="", scale=PANEL_PREVIEW_SCALE):
    icon_id = _native_preview_icon_id(image)
    if icon_id > 0:
        layout.template_icon(icon_value=icon_id, scale=max(1.0, float(scale)))
        return True
    if fallback:
        layout.label(text=str(fallback), icon="FILE_IMAGE")
    return False


def _touch_preview_cache_entry(path):
    normalized = _normalized_image_path(path)
    if not normalized:
        return
    state = _preview_runtime_state()
    cache_entry = state.get("image_cache", {}).get(normalized)
    if isinstance(cache_entry, dict):
        cache_entry["last_used_at"] = time.perf_counter()


def _schedule_idle_preview_release(keep_paths=None):
    state = _preview_runtime_state()
    state["idle_keep_paths"] = [
        _normalized_image_path(path) for path in list(keep_paths or []) if _normalized_image_path(path)
    ]
    if state.get("idle_release_registered", False):
        return

    def _release_when_idle():
        state = _preview_runtime_state()
        state["idle_release_registered"] = False
        keep = list(state.get("idle_keep_paths", []) or [])
        _release_preview_images(keep_paths=keep)
        return None

    state["idle_release_registered"] = True
    try:
        bpy.app.timers.register(_release_when_idle, first_interval=PREVIEW_IDLE_RELEASE_SECONDS)
    except Exception:
        state["idle_release_registered"] = False


def _enforce_preview_cache_budget(keep_paths=None):
    state = _preview_runtime_state()
    image_cache = state.get("image_cache", {})
    if len(image_cache) <= PREVIEW_MAX_CACHE_IMAGES:
        return
    keep = {_normalized_image_path(path) for path in list(keep_paths or []) if _normalized_image_path(path)}
    ordered = sorted(
        (
            (key, float((entry or {}).get("last_used_at", 0.0) or 0.0))
            for key, entry in image_cache.items()
            if key not in keep
        ),
        key=lambda item: item[1],
    )
    remove_count = max(0, len(image_cache) - PREVIEW_MAX_CACHE_IMAGES)
    if remove_count <= 0:
        return
    victims = {key for key, _stamp in ordered[:remove_count]}
    survivor_keep = list(keep.union(set(image_cache.keys()) - victims))
    _schedule_idle_preview_release(keep_paths=survivor_keep)


def _queue_preview_preload(paths):
    normalized_paths = []
    for path in list(paths or []):
        normalized = _normalized_image_path(path)
        if normalized and os.path.isfile(normalized):
            normalized_paths.append(normalized)
    if not normalized_paths:
        return
    state = _preview_runtime_state()
    queue = list(state.get("preload_queue", []) or [])
    known = set(queue)
    for value in normalized_paths:
        if value not in known:
            queue.append(value)
            known.add(value)
    state["preload_queue"] = queue[:PREVIEW_PRELOAD_QUEUE_LIMIT]
    if state.get("preload_registered", False):
        return

    def _consume_preload_queue():
        state = _preview_runtime_state()
        last_switch_at = max(
            float(state.get("last_media_switch_at", 0.0) or 0.0),
            float(state.get("last_detail_media_switch_at", 0.0) or 0.0),
        )
        if time.perf_counter() - last_switch_at < 0.30:
            return 0.30
        queue = list(state.get("preload_queue", []) or [])
        if not queue:
            state["preload_registered"] = False
            return None
        batch = queue[:PREVIEW_PRELOAD_BATCH_SIZE]
        state["preload_queue"] = queue[PREVIEW_PRELOAD_BATCH_SIZE:]
        for candidate in batch:
            try:
                _load_panel_preview_image(candidate)
            except Exception:
                pass
        _enforce_preview_cache_budget()
        if state.get("preload_queue"):
            return 0.14
        state["preload_registered"] = False
        return None

    state["preload_registered"] = True
    try:
        bpy.app.timers.register(_consume_preload_queue, first_interval=0.18)
    except Exception:
        state["preload_registered"] = False


def _schedule_preview_release_retry():
    state = _preview_runtime_state()
    if state.get("release_retry_registered", False):
        return

    def _retry():
        state = _preview_runtime_state()
        pending = dict(state.get("pending_release", {}) or {})
        if not pending:
            state["release_retry_registered"] = False
            return None
        state["release_retry_registered"] = True
        _release_preview_images()
        pending = dict(state.get("pending_release", {}) or {})
        if not pending:
            state["release_retry_registered"] = False
            return None
        state["release_retry_registered"] = True
        return PREVIEW_RELEASE_RETRY_SECONDS

    state["release_retry_registered"] = True
    try:
        bpy.app.timers.register(_retry, first_interval=PREVIEW_RELEASE_RETRY_SECONDS)
    except Exception:
        state["release_retry_registered"] = False


def _release_preview_images(keep_paths=None, min_age_seconds=PREVIEW_RELEASE_GRACE_SECONDS, force=False):
    keep = {_normalized_image_path(path) for path in list(keep_paths or []) if _normalized_image_path(path)}
    state = _preview_runtime_state()
    image_cache = state.get("image_cache", {})
    icon_cache = state.get("icon_cache", {})
    pending_release = state.get("pending_release", {})
    now = time.perf_counter()
    min_age = 0.0 if force else max(0.0, float(min_age_seconds or 0.0))
    removed = 0
    for key in list(image_cache.keys()):
        if key in keep:
            pending_release.pop(key, None)
            continue
        cache_entry = image_cache.get(key, {}) or {}
        last_used_at = float(cache_entry.get("last_used_at", 0.0) or 0.0)
        if last_used_at > 0.0 and (now - last_used_at) < min_age:
            pending_release[key] = {
                "image_name": str(cache_entry.get("image_name", "") or ""),
                "last_users": -1,
                "reason": "recent",
            }
            continue
        image_name = str(cache_entry.get("image_name", "") or "")
        image = bpy.data.images.get(image_name) if image_name else None
        removed_now = False
        try:
            if image is not None:
                if getattr(image, "users", 0) > 0:
                    pending_release[key] = {"image_name": image_name, "last_users": int(getattr(image, "users", 0) or 0)}
                    continue
                bpy.data.images.remove(image)
            removed_now = True
        except Exception:
            pending_release[key] = {"image_name": image_name, "last_users": int(getattr(image, "users", 0) or 0) if image is not None else 0}
            continue
        if removed_now:
            image_cache.pop(key, None)
            icon_cache.pop(key, None)
            pending_release.pop(key, None)
            removed += 1
    for key in list(image_cache.keys()):
        image_name = str(image_cache.get(key, {}).get("image_name", "") or "")
        if not image_name or bpy.data.images.get(image_name) is None:
            image_cache.pop(key, None)
            icon_cache.pop(key, None)
            pending_release.pop(key, None)
    if pending_release:
        _schedule_preview_release_retry()
    if removed:
        _perf_increment("preview_release_removed", removed)
        _perf_push_sample(
            "preview_events",
            {
                "t": round(time.perf_counter(), 4),
                "event": "release",
                "removed": int(removed),
                "cache_items": len(image_cache),
                "pending": len(pending_release),
            },
        )
    return removed


def _draw_runtime_icon_button(layout, panel_api, action, icon):
    op = layout.operator("bworkflow.module_runtime_action", text="", icon=icon)
    op.workflow_name = panel_api.workflow_name
    op.module_name = panel_api.module_name
    op.action_name = str(action)
    return op


def _draw_preview_with_side_arrows(layout, panel_api, image, fallback, prev_action, next_action, scale=PANEL_PREVIEW_SCALE):
    row = layout.row(align=True)
    left = row.column(align=True)
    left.separator(factor=3.2)
    left_button = left.row(align=True)
    left_button.scale_y = 2.8
    _draw_runtime_icon_button(left_button, panel_api, prev_action, "TRIA_LEFT")
    left.separator(factor=3.2)
    center = row.column(align=True)
    _draw_native_image_preview(center, image, fallback=fallback, scale=max(1.0, float(scale)))
    right = row.column(align=True)
    right.separator(factor=3.2)
    right_button = right.row(align=True)
    right_button.scale_y = 2.8
    _draw_runtime_icon_button(right_button, panel_api, next_action, "TRIA_RIGHT")
    right.separator(factor=3.2)


def _load_preview_image_for_draw(preview_path, detail=False):
    normalized = _normalized_image_path(preview_path)
    if not normalized:
        return None
    state = _preview_runtime_state()
    if _recent_preview_switch(detail=detail) and normalized not in (state.get("image_cache", {}) or {}):
        return None
    return _load_panel_preview_image(normalized)


def _draw_step_indicator(layout, index, total_steps):
    row = layout.row(align=True)
    split = row.split(factor=0.35, align=True)
    split.label(text="\u6b65\u9aa4\u7f16\u53f7")
    split.label(text=str(index + 1))
    layout.label(text=f"\u5f53\u524d\u6b65\u9aa4: {index + 1} / {total_steps}", icon="INFO")


def _draw_preview(layout, item, panel_api):
    media_files = _media_files(item)
    if not media_files:
        return ""
    preview_files = _preview_media_files(item)
    preview_box = layout.box()
    preview_box.label(text="\u53c2\u8003\u9884\u89c8", icon="IMAGE_REFERENCE")
    media_index = _media_index(panel_api, item)
    media_path = media_files[media_index]
    preview_path = ""
    if preview_files:
        preview_index = max(0, min(media_index, len(preview_files) - 1))
        preview_path = _panel_preview_path(preview_files[preview_index])
    preview_image = _load_preview_image_for_draw(preview_path, detail=False)
    _touch_preview_cache_entry(preview_path)
    fallback = os.path.basename(preview_path or media_path)
    _draw_preview_with_side_arrows(preview_box, panel_api, preview_image, fallback, "PREV_MEDIA", "NEXT_MEDIA")
    return preview_path


def _draw_detail_preview(layout, item, panel_api):
    media_files = _detail_media_files(item)
    if not media_files:
        return ""
    preview_files = _detail_preview_media_files(item)
    detail_index = _detail_media_index(panel_api, item)
    media_path = media_files[detail_index]
    preview_path = ""
    if preview_files:
        preview_index = max(0, min(detail_index, len(preview_files) - 1))
        preview_path = _panel_preview_path(preview_files[preview_index])
    detail_image = _load_preview_image_for_draw(preview_path, detail=True)
    _touch_preview_cache_entry(preview_path)
    layout.label(text=f"\u8865\u5145\u53c2\u8003\u56fe: {detail_index + 1} / {len(media_files)}", icon="IMAGE_REFERENCE")
    layout.label(text=os.path.basename(media_path), icon="FILE_IMAGE")
    _draw_preview_with_side_arrows(
        layout,
        panel_api,
        detail_image,
        os.path.basename(preview_path or media_path),
        "PREV_DETAIL_MEDIA",
        "NEXT_DETAIL_MEDIA",
    )
    panel_api.draw_button(layout, "OPEN_DETAIL_REFERENCE_WINDOW", "\u7f6e\u9876\u8865\u5145\u53c2\u8003\u56fe", icon="IMAGE_REFERENCE")
    return preview_path


def _append_unique_preload_path(preload, value):
    text = str(value or "").strip()
    if not text or text in preload:
        return
    preload.append(text)


def _viewer_step_preload_paths(items, step_index, file_getter, preferred_index=0):
    preload = []
    for near_step in (step_index - 1, step_index + 1):
        if 0 <= near_step < len(items):
            near_files = list(file_getter(items[near_step]) or [])
            if not near_files:
                continue
            clipped_index = max(0, min(int(preferred_index), len(near_files) - 1))
            _append_unique_preload_path(preload, near_files[clipped_index])
            _append_unique_preload_path(preload, near_files[0])
    return preload


def _queue_step_preview_preload(panel_api, preview_files, media_index, detail=False):
    state = _preview_runtime_state()
    request_key = "last_detail_preload_request" if detail else "last_preview_preload_request"
    try:
        _payload, items, _item, step_index, _current_media_index = _current_item(panel_api, None)
    except Exception:
        items = []
        step_index = -1
    request_signature = f"{step_index}:{media_index}:{len(preview_files)}:{1 if detail else 0}"
    if state.get(request_key) == request_signature:
        return
    state[request_key] = request_signature
    preload_paths = []
    for delta in (-1, 1):
        near_index = media_index + delta
        if 0 <= near_index < len(preview_files):
            near_path = _panel_preview_path(preview_files[near_index])
            if near_path:
                preload_paths.append(near_path)
    if items and step_index >= 0:
        getter = _detail_preview_media_files if detail else _preview_media_files
        for value in _viewer_step_preload_paths(items, step_index, getter, preferred_index=media_index):
            near_path = _panel_preview_path(value)
            if near_path:
                preload_paths.append(near_path)
    _queue_preview_preload(preload_paths)


def _viewer_preload_media_files(items, step_index, item, media_index):
    preload = []

    current_files = _media_files(item)
    if current_files and media_index - 1 >= 0:
        _append_unique_preload_path(preload, current_files[media_index - 1])
    if current_files and media_index + 1 < len(current_files):
        _append_unique_preload_path(preload, current_files[media_index + 1])
    for value in _viewer_step_preload_paths(items, step_index, _media_files, preferred_index=media_index):
        _append_unique_preload_path(preload, value)
    return preload


def _viewer_payload(item, panel_api, module_state, step_index, total_steps):
    _payload, items = _load_items()
    files = _media_files(item)
    media_index = _media_index(panel_api, item)
    blender_exe = str(getattr(bpy.app, "binary_path", "") or "")
    payload = {
        "title": "ARKit 形态键工作流参考",
        "step_label": f"步骤 {step_index + 1} / {total_steps}",
        "shape_key": item.get("shape_key", ""),
        "name_bilingual": item.get("name_bilingual") or item.get("shape_key", ""),
        "category": item.get("category", ""),
        "summary": item.get("summary", ""),
        "notes": list(item.get("notes", []) or []),
        "tips": list(item.get("tips", []) or []),
        "detail_note": "\n".join(_detail_lines(item)),
        "detail_media_files": _detail_media_files(item),
        "validation_mix": _validation_mix_lines(item),
        "media_files": files,
        "preload_media_files": _viewer_preload_media_files(items, step_index, item, media_index),
        "media_index": media_index,
        "topmost": True,
        "window_icon_path": blender_exe if os.path.isfile(blender_exe) else "",
    }
    if module_state is not None:
        module_state.set("last_reference_media_count", len(files))
        module_state.set("last_reference_media_index", media_index)
    return payload


def _detail_viewer_payload(item, panel_api, module_state, step_index, total_steps):
    _payload, items = _load_items()
    files = _detail_media_files(item)
    media_index = _detail_media_index(panel_api, item)
    blender_exe = str(getattr(bpy.app, "binary_path", "") or "")
    preload_media_files = []
    if media_index - 1 >= 0:
        _append_unique_preload_path(preload_media_files, files[media_index - 1])
    if media_index + 1 < len(files):
        _append_unique_preload_path(preload_media_files, files[media_index + 1])
    for value in _viewer_step_preload_paths(items, step_index, _detail_media_files, preferred_index=media_index):
        _append_unique_preload_path(preload_media_files, value)
    payload = {
        "title": "ARKit 补充参考图",
        "step_label": f"补充参考 {step_index + 1} / {total_steps}",
        "shape_key": item.get("shape_key", ""),
        "name_bilingual": item.get("name_bilingual") or item.get("shape_key", ""),
        "category": item.get("category", ""),
        "summary": item.get("summary", ""),
        "notes": list(item.get("notes", []) or []),
        "tips": list(item.get("tips", []) or []),
        "detail_note": "\n".join(_detail_lines(item)),
        "detail_media_files": files,
        "validation_mix": _validation_mix_lines(item),
        "media_files": files,
        "preload_media_files": preload_media_files,
        "media_index": media_index,
        "topmost": True,
        "window_icon_path": blender_exe if os.path.isfile(blender_exe) else "",
    }
    if module_state is not None:
        module_state.set("last_detail_reference_media_count", len(files))
        module_state.set("last_detail_reference_media_index", media_index)
    return payload


def _viewer_files(viewer_kind):
    paths = _preset_paths()
    if str(viewer_kind or "").strip().lower() == "detail":
        state_file = paths["detail_viewer_state_file"]
    else:
        state_file = paths["viewer_state_file"]
    return {
        "viewer_script_file": paths["viewer_script_file"],
        "viewer_state_file": state_file,
        "viewer_pid_file": os.path.splitext(state_file)[0] + ".pid",
        "viewer_log_file": os.path.splitext(state_file)[0] + ".log",
    }


def _unique_viewer_files(viewer_kind):
    files = _viewer_files(viewer_kind)
    state_file = files["viewer_state_file"]
    folder = os.path.dirname(state_file)
    stem, ext = os.path.splitext(os.path.basename(state_file))
    unique_stamp = time.time_ns() if hasattr(time, "time_ns") else int(time.time() * 1000000000)
    unique = f"{stem}.{os.getpid()}.{unique_stamp}{ext or '.json'}"
    unique_state_file = os.path.join(folder, unique)
    return {
        "viewer_script_file": files["viewer_script_file"],
        "viewer_state_file": unique_state_file,
        "viewer_pid_file": os.path.splitext(unique_state_file)[0] + ".pid",
        "viewer_log_file": os.path.splitext(unique_state_file)[0] + ".log",
    }


def _cleanup_old_viewer_runtime_files(viewer_kind, max_age_seconds=86400.0):
    try:
        base = _viewer_files(viewer_kind)["viewer_state_file"]
        folder = os.path.dirname(base)
        stem = os.path.splitext(os.path.basename(base))[0]
        if not os.path.isdir(folder):
            return 0
        now = time.time()
        removed = 0
        for name in os.listdir(folder):
            if not name.startswith(stem + "."):
                continue
            if not (name.endswith(".json") or name.endswith(".pid") or name.endswith(".log")):
                continue
            path = os.path.join(folder, name)
            try:
                if now - os.path.getmtime(path) < float(max_age_seconds):
                    continue
                os.remove(path)
                removed += 1
            except Exception:
                pass
        return removed
    except Exception:
        return 0


def draw_panel(layout, context, scene, workflow, module, panel_api, module_state):
    draw_started_at = time.perf_counter()
    perf_open = False
    try:
        _payload, items, item, index, media_index = _current_item(panel_api, module_state)
    except Exception as exc:
        layout.label(text=str(exc), icon="ERROR")
        return

    media_files = _media_files(item)
    keep_preview_paths = []
    wrap_width = _panel_wrap_width(context, fallback=54)
    box = panel_api.section(layout, "ARKit \u5f62\u6001\u952e\u5de5\u4f5c\u6d41\u53c2\u8003", icon="SHAPEKEY_DATA")
    target_obj = panel_api.get_object("target_object") if panel_api is not None else None
    panel_api.label(box, f"\u76ee\u6807: {getattr(target_obj, 'name', '') or '-'}", icon="OBJECT_DATA")
    panel_api.draw_active_object_capture(box, "target_object", "\u5438\u53d6\u5f53\u524d\u9009\u4e2d", icon="EYEDROPPER")
    _draw_step_indicator(box, index, len(items))
    nav = panel_api.row(box, align=True)
    panel_api.draw_button(nav, "PREV", "\u4e0a\u4e00\u6b65", icon="TRIA_LEFT")
    panel_api.draw_button(nav, "NEXT", "\u4e0b\u4e00\u6b65", icon="TRIA_RIGHT")
    panel_api.draw_button(nav, "OPEN_REFERENCE_WINDOW", "\u7f6e\u9876\u53c2\u8003\u56fe", icon="IMAGE_REFERENCE")
    panel_api.label(box, item.get("name_bilingual") or item.get("shape_key", ""), icon="SHAPEKEY_DATA")
    panel_api.label(box, f"\u5206\u7c7b: {item.get('category', '-')}", icon="OUTLINER_COLLECTION")
    if _draw_drawer_header(box, panel_api, "summary", "\u6b65\u9aa4\u8bf4\u660e", default=False, module_state=module_state):
        _draw_full_text_block(box, item.get("summary", ""), icon="INFO", width=wrap_width)
    if media_files and _draw_drawer_header(box, panel_api, "media", "\u53c2\u8003\u5a92\u4f53", default=False, module_state=module_state):
        media_box = panel_api.section(box, "\u53c2\u8003\u5a92\u4f53", icon="IMAGE_REFERENCE")
        preview_path = _draw_preview(media_box, item, panel_api)
        if preview_path:
            keep_preview_paths.append(preview_path)
    else:
        if not media_files:
            panel_api.label(box, "\u5f53\u524d\u6b65\u9aa4\u8fd8\u6ca1\u6709\u53c2\u8003\u5a92\u4f53", icon="ERROR")
    if _draw_drawer_header(box, panel_api, "tips", "\u6ce8\u610f\u91cd\u70b9", default=False, module_state=module_state):
        tips_box = panel_api.section(box, "\u6ce8\u610f\u91cd\u70b9", icon="LIGHT")
        for note in item.get("notes", []):
            _draw_full_text_block(tips_box, note, icon="ERROR", width=wrap_width)
        for tip in item.get("tips", []):
            _draw_full_text_block(tips_box, tip, icon="CHECKMARK", width=wrap_width)
    if _has_detail_hint(item):
        if _draw_drawer_header(box, panel_api, "detail", "\u8865\u5145\u8bf4\u660e", default=False, module_state=module_state):
            detail_lines = _detail_lines(item)
            detail_media_files = _detail_media_files(item)
            detail_box = panel_api.section(box, "\u8865\u5145\u8bf4\u660e", icon="BOOKMARKS")
            for detail_line in detail_lines:
                _draw_full_text_block(detail_box, detail_line, icon="INFO", width=wrap_width)
            if detail_media_files:
                preview_path = _draw_detail_preview(detail_box, item, panel_api)
                if preview_path:
                    keep_preview_paths.append(preview_path)
    if _has_validation_mix_hint(item) and _draw_drawer_header(box, panel_api, "mix", "\u6df7\u5408\u9a8c\u8bc1\u8bf4\u660e", default=False, module_state=module_state):
        mix_lines = _validation_mix_lines(item)
        if mix_lines:
            mix_box = panel_api.section(box, "\u6df7\u5408\u9a8c\u8bc1\u8bf4\u660e", icon="MOD_MESHDEFORM")
            for line in mix_lines:
                _draw_full_text_block(mix_box, line, icon="INFO", width=wrap_width)
    actions = panel_api.row(box, align=True)
    panel_api.draw_button(actions, "FOCUS_SHAPE_KEY", "\u5b9a\u4f4d\u540c\u540d\u5f62\u6001\u952e", icon="RESTRICT_SELECT_OFF")
    panel_api.draw_button(actions, "APPLY_VALIDATION_ONE", "\u9a8c\u8bc1\u952e\u8bbe\u4e3a1", icon="PLAY")
    panel_api.draw_button(actions, "APPLY_VALIDATION_SEQUENCE", "\u9a8c\u8bc1\u952e\u9012\u589e", icon="IPO_EASE_IN_OUT")
    panel_api.draw_button(actions, "RESET_ALL", "\u91cd\u7f6e\u5168\u90e8\u5f62\u6001\u952e", icon="LOOP_BACK")
    full_actions = panel_api.row(box, align=True)
    panel_api.draw_button(full_actions, "APPLY_FULL_VALIDATION_NATIVE", "\u5168\u9762\u6df7\u5408\u9a8c\u8bc1(\u5173\u952e\u5e27)", icon="SEQ_PREVIEW")
    panel_api.draw_button(full_actions, "TOGGLE_FULL_VALIDATION_PAUSE", "\u91cd\u7f6e\u5168\u9762\u9a8c\u8bc1", icon="LOOP_BACK")
    if _draw_drawer_header(box, panel_api, "settings", "\u8fd0\u884c\u9009\u9879", default=False, module_state=module_state):
        settings = panel_api.section(box, "\u8fd0\u884c\u9009\u9879", icon="TOOL_SETTINGS")
        panel_api.draw_object_picker(settings, "target_object", "\u76ee\u6807\u7269\u4f53")
        panel_api.draw_float_input(settings, "validation_duration_seconds", "\u9a8c\u8bc1\u952e\u9012\u589e\u79d2\u6570", default=_get_setting(module, "validation_duration_seconds", ANIMATION_DURATION_PER_KEY))
        panel_api.draw_toggle(settings, "auto_validate_on_step", "\u4e0a\u4e00\u6b65/\u4e0b\u4e00\u6b65\u540e\u81ea\u52a8\u9a8c\u8bc1\u952e\u9012\u589e", default=_get_setting(module, "auto_validate_on_step", False))
        panel_api.draw_toggle(settings, "auto_zero_others", "\u5207\u6362\u6b65\u9aa4\u65f6\u6e05\u96f6\u5176\u4ed6\u53c2\u8003\u952e", default=True)
        panel_api.draw_toggle(settings, "auto_edit_mode", "\u5e94\u7528\u540e\u81ea\u52a8\u8fdb\u5165\u7f16\u8f91\u6a21\u5f0f", default=True)
        panel_api.draw_toggle(settings, "auto_open_reference", "\u5e94\u7528\u540e\u81ea\u52a8\u6253\u5f00\u7f6e\u9876\u53c2\u8003\u56fe", default=True)
        panel_api.label(settings, "\u5f53\u524d\u5de5\u4f5c\u6d41\u53ea\u4f7f\u7528\u72ec\u7acb\u7f6e\u9876\u53c2\u8003\u7a97\uff0c\u4e0d\u518d\u56de\u9000\u5230 Blender \u5185\u90e8\u53c2\u8003\u7a97\u3002", icon="INFO")
        tools = panel_api.row(settings, align=True)
        panel_api.draw_button(tools, "CLEAR_REFERENCE_CACHE", "\u6e05\u7406GIF\u53c2\u8003\u7f13\u5b58", icon="TRASH")
        panel_api.draw_button(tools, "CLEAR_PANEL_PREVIEW_CACHE", "\u6e05\u7406\u9762\u677f\u9884\u89c8\u7f13\u5b58", icon="TRASH")
        panel_api.draw_button(tools, "EXPORT_PERF_LOG", "\u5bfc\u51fa\u6027\u80fd\u65e5\u5fd7", icon="EXPORT")
        panel_api.draw_button(tools, "RESET_PERF_LOG", "\u6e05\u7a7a\u6027\u80fd\u7edf\u8ba1", icon="LOOP_BACK")
        perf_open = _draw_drawer_header(settings, panel_api, "perf", "\u6027\u80fd\u8bca\u65ad", default=False, module_state=module_state)
        if perf_open:
            perf_box = panel_api.section(settings, "\u6027\u80fd\u8bca\u65ad", icon="TIME")
            summary = _perf_capture_summary(
                extra={
                    "current_step": int(index + 1),
                    "total_steps": int(len(items)),
                    "current_media_index": int(media_index + 1),
                    "current_media_total": int(len(media_files)),
                }
            )
            panel_api.label(perf_box, f"\u6700\u8fd1Draw\u5747\u503c: {summary['avg_draw_ms_recent']:.3f} ms", icon="INFO")
            panel_api.label(perf_box, f"Draw\u5cf0\u503c: {summary['max_draw_ms']:.3f} ms", icon="ORPHAN_DATA")
            panel_api.label(perf_box, f"\u9884\u89c8\u7f13\u5b58: {summary['preview_cache_items']} / \u5f85\u91ca\u653e {summary['preview_pending_release']}", icon="IMAGE_DATA")
            panel_api.label(perf_box, f"\u9884\u52a0\u8f7d\u961f\u5217: {summary['preview_preload_queue']}", icon="PREVIEW_RANGE")
            panel_api.label(perf_box, f"\u5185\u5b58(MB): {summary['memory_mb_current']:.2f} / peak {summary['memory_mb_peak']:.2f}", icon="MEMORY")
    panel_api.draw_run_button(box, "\u8fd0\u884c\u6a21\u5757", icon="PLAY")
    panel_api.draw_status(box)
    if keep_preview_paths:
        _enforce_preview_cache_budget(keep_paths=keep_preview_paths)
    if perf_open:
        draw_elapsed_ms = (time.perf_counter() - draw_started_at) * 1000.0
        _perf_increment("draw_calls")
        perf_state = _perf_runtime_state()
        now = time.perf_counter()
        if (now - float(perf_state.get("last_draw_sample_at", 0.0) or 0.0)) >= 0.25:
            perf_state["last_draw_sample_at"] = now
            _perf_push_sample(
                "draw_samples",
                {
                    "t": round(now, 4),
                    "ms": round(draw_elapsed_ms, 4),
                    "step_index": int(index),
                    "media_index": int(media_index),
                    "preview_cache_items": len(_preview_runtime_state().get("image_cache", {}) or {}),
                    "memory_mb": round(float(perf_state.get("memory_mb_current", 0.0) or 0.0), 2),
                },
            )


def on_panel_collapse(scene=None, workflow=None, module=None, module_state=None):
    _release_preview_images(force=True, min_age_seconds=0.0)
    _run_memory_cleanup()


def _launch_reference_viewer(payload, viewer_kind="main"):
    _cleanup_old_viewer_runtime_files(viewer_kind)
    viewer_files = _unique_viewer_files(viewer_kind)
    viewer_script_file = viewer_files["viewer_script_file"]
    viewer_state_file = viewer_files["viewer_state_file"]
    viewer_pid_file = viewer_files["viewer_pid_file"]
    viewer_log_file = viewer_files["viewer_log_file"]
    if not os.path.isfile(viewer_script_file):
        raise Exception("\u7f3a\u5c11 ARKit \u53c2\u8003\u7a97\u811a\u672c")
    with open(viewer_state_file, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _viewer_pid_alive(pid):
        if int(pid or 0) <= 0:
            return False
        if os.name == "nt":
            try:
                import ctypes
                process_handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
                if process_handle:
                    ctypes.windll.kernel32.CloseHandle(process_handle)
                    return True
            except Exception:
                return False
            return False
        try:
            os.kill(int(pid), 0)
            return True
        except Exception:
            return False

    def _read_viewer_pid():
        if not os.path.isfile(viewer_pid_file):
            return 0
        try:
            with open(viewer_pid_file, "r", encoding="utf-8") as handle:
                return int((handle.read() or "").strip() or "0")
        except Exception:
            return 0

    stale_pid = _read_viewer_pid()
    if stale_pid and not _viewer_pid_alive(stale_pid):
        try:
            os.remove(viewer_pid_file)
        except Exception:
            pass

    command = ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", viewer_script_file, viewer_state_file]
    try:
        with open(viewer_log_file, "w", encoding="utf-8", errors="replace") as log_handle:
            kwargs = {"stdout": log_handle, "stderr": subprocess.STDOUT}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            process = subprocess.Popen(command, **kwargs)
    except Exception as exc:
        raise Exception(f"\u542f\u52a8\u7f6e\u9876\u53c2\u8003\u7a97\u5931\u8d25: {exc}")
    deadline = time.time() + 2.0
    while time.time() < deadline:
        ready_pid = _read_viewer_pid()
        if ready_pid and _viewer_pid_alive(ready_pid):
            return
        if process.poll() is not None:
            break
        time.sleep(0.05)
    if process.poll() is None:
        return
    message = "\u542f\u52a8\u7f6e\u9876\u53c2\u8003\u7a97\u5931\u8d25"
    try:
        if os.path.isfile(viewer_log_file):
            with open(viewer_log_file, "r", encoding="utf-8", errors="replace") as handle:
                lines = [line.strip() for line in handle.readlines() if line.strip()]
            if lines:
                message = f"{message}: {lines[-1]}"
    except Exception:
        pass
    raise Exception(message)



def on_panel_action(action, context, scene, workflow, module, panel_api, module_state):
    _persist_runtime_settings(module, panel_api)
    if action.startswith("TOGGLE_DRAWER::"):
        key = action.split("::", 1)[1]
        _set_drawer_open(panel_api, key, not _drawer_open(panel_api, key, default=False, module_state=module_state), module_state)
        return {"FINISHED"}
    if action == "TOGGLE_FULL_VALIDATION_PAUSE":
        if not _reset_full_validation_to_preview(scene, workflow, module, panel_api=panel_api, module_state=module_state):
            panel_api.set_status("\u5f53\u524d\u6ca1\u6709\u53ef\u91cd\u7f6e\u7684\u5168\u9762\u6df7\u5408\u9a8c\u8bc1", level="WARNING")
            _tag_redraw_all()
            return {"FINISHED"}
        _tag_redraw_all()
        return {"FINISHED"}

    _payload, items, item, index, _media_index_value = _current_item(panel_api, module_state)
    if action == "PREV":
        _set_step_and_maybe_validate(context, scene, workflow, module, panel_api, module_state, items, index - 1)
        return {"FINISHED"}
    if action == "NEXT":
        _set_step_and_maybe_validate(context, scene, workflow, module, panel_api, module_state, items, index + 1)
        return {"FINISHED"}
    if action == "FOCUS_SHAPE_KEY":
        _focus_current_shape_key(context, panel_api, module_state, item)
        return {"FINISHED"}
    if action == "PREV_MEDIA":
        current_index, total = _change_media_index(panel_api, item, -1)
        _set_media_switch_status(panel_api, current_index, total, detail=False)
        return {"FINISHED"}
    if action == "NEXT_MEDIA":
        current_index, total = _change_media_index(panel_api, item, 1)
        _set_media_switch_status(panel_api, current_index, total, detail=False)
        return {"FINISHED"}
    if action == "PREV_DETAIL_MEDIA":
        current_index, total = _change_detail_media_index(panel_api, item, -1)
        _set_media_switch_status(panel_api, current_index, total, detail=True)
        return {"FINISHED"}
    if action == "NEXT_DETAIL_MEDIA":
        current_index, total = _change_detail_media_index(panel_api, item, 1)
        _set_media_switch_status(panel_api, current_index, total, detail=True)
        return {"FINISHED"}
    if action == "OPEN_DETAIL_REFERENCE_WINDOW":
        payload = _open_detail_reference_window(item, panel_api, module_state, index, len(items))
        panel_api.set_status(f"\u5df2\u6253\u5f00\u7f6e\u9876\u8865\u5145\u53c2\u8003\u56fe: {payload['media_index'] + 1} / {len(payload['media_files'])}", level="OK")
        return {"FINISHED"}
    if action == "OPEN_REFERENCE_WINDOW":
        payload = _open_reference_window(item, panel_api, module_state, index, len(items))
        panel_api.set_status(f"\u5df2\u6253\u5f00\u7f6e\u9876\u53c2\u8003\u7a97: {payload['media_index'] + 1} / {len(payload['media_files'])}", level="OK")
        return {"FINISHED"}
    if action == "APPLY_VALIDATION_ONE":
        _apply_shape_key_values(context, scene, workflow, module, panel_api, module_state, item, items, mode="direct")
        return {"FINISHED"}
    if action == "APPLY_VALIDATION_SEQUENCE":
        _start_validation_animation(context, scene, workflow, module, panel_api, module_state, item, items)
        return {"FINISHED"}
    if action == "APPLY_FULL_VALIDATION":
        _start_full_validation_native_animation(context, scene, workflow, module, panel_api, module_state, items)
        return {"FINISHED"}
    if action == "APPLY_FULL_VALIDATION_NATIVE":
        _start_full_validation_native_animation(context, scene, workflow, module, panel_api, module_state, items)
        return {"FINISHED"}
    if action == "FIELD_WRITE::validation_duration_seconds":
        value = max(0.1, float(panel_api.get_float("validation_duration_seconds", ANIMATION_DURATION_PER_KEY)))
        panel_api.set_float("validation_duration_seconds", value)
        _set_setting(module, "validation_duration_seconds", value)
        panel_api.set_status(f"\u5df2\u66f4\u65b0\u9a8c\u8bc1\u952e\u9012\u589e\u79d2\u6570\uff1a{value:.2f} \u79d2", level="OK")
        if module_state is not None:
            module_state.set("last_result", f"\u5df2\u66f4\u65b0\u9a8c\u8bc1\u952e\u9012\u589e\u79d2\u6570\uff1a{value:.2f} \u79d2")
        return {"FINISHED"}
    if action == "CLEAR_REFERENCE_CACHE":
        removed = _cleanup_reference_cache_images()
        panel_api.set_status(f"\u5df2\u6e05\u7406 {removed} \u4e2a\u672a\u4f7f\u7528\u7684GIF\u53c2\u8003\u7f13\u5b58", level="OK")
        if module_state is not None:
            module_state.set("last_result", f"\u5df2\u6e05\u7406 {removed} \u4e2a\u672a\u4f7f\u7528\u7684GIF\u53c2\u8003\u7f13\u5b58")
        return {"FINISHED"}
    if action == "CLEAR_PANEL_PREVIEW_CACHE":
        removed = _cleanup_panel_preview_cache()
        panel_api.set_status(f"\u5df2\u6e05\u7406 {removed} \u4e2a\u9762\u677f\u9884\u89c8\u7f13\u5b58\u9879", level="OK")
        if module_state is not None:
            module_state.set("last_result", f"\u5df2\u6e05\u7406 {removed} \u4e2a\u9762\u677f\u9884\u89c8\u7f13\u5b58\u9879")
        return {"FINISHED"}
    if action == "EXPORT_PERF_LOG":
        path = _export_perf_log(module_state=module_state)
        panel_api.set_status(f"\u5df2\u5bfc\u51fa\u6027\u80fd\u65e5\u5fd7: {os.path.basename(path)}", level="OK")
        if module_state is not None:
            module_state.set("last_result", f"\u5df2\u5bfc\u51fa\u6027\u80fd\u65e5\u5fd7: {path}")
        return {"FINISHED"}
    if action == "RESET_PERF_LOG":
        _reset_perf_stats()
        panel_api.set_status("\u5df2\u6e05\u7a7a\u6027\u80fd\u7edf\u8ba1", level="OK")
        if module_state is not None:
            module_state.set("last_result", "\u5df2\u6e05\u7a7a\u6027\u80fd\u7edf\u8ba1")
        return {"FINISHED"}
    if action == "RESET_ALL":
        result = _reset_all_steps(context, scene, workflow, module, panel_api, module_state)
        _clear_full_validation_plan(scene, workflow, module, module_state)
        _clear_full_validation_runtime_state(scene, workflow, module, module_state)
        if result is None:
            return {"FINISHED"}
        if result.get("mode") == "restored":
            panel_api.set_status("\u5df2\u6062\u590d\u6df7\u5408\u9884\u89c8\u524d\u7684\u539f\u59cb\u5f62\u6001\u952e\u6ed1\u6761\u503c", level="OK")
            if module_state is not None:
                module_state.set("last_result", "\u5df2\u6062\u590d\u6df7\u5408\u9884\u89c8\u524d\u7684\u539f\u59cb\u5f62\u6001\u952e\u6ed1\u6761\u503c")
            return {"FINISHED"}
        count = int(result.get("count", 0) or 0)
        panel_api.set_status(f"\u5df2\u91cd\u7f6e {count} \u4e2a\u53c2\u8003\u5f62\u6001\u952e", level="OK")
        if module_state is not None:
            module_state.set("last_result", f"\u5df2\u91cd\u7f6e {count} \u4e2a\u53c2\u8003\u5f62\u6001\u952e")
        return {"FINISHED"}
    return {"FINISHED"}


def _full_validation_rule_state(shape_key_name, alias_name=None, side="C"):
    key_name = str(shape_key_name or "").strip()
    label = str(alias_name or key_name or "Validation").strip()
    weights = {key_name: 1.0} if key_name else {}
    normalized = _normalize_shape_key_name(key_name)

    if normalized in {"jawopen"}:
        weights["MouthClose"] = 0.25
        weights["MouthLowerDownLeft"] = 0.18
        weights["MouthLowerDownRight"] = 0.18
        weights["MouthStretchLeft"] = 0.10
        weights["MouthStretchRight"] = 0.10
    elif normalized in {"mouthclose"}:
        weights["JawOpen"] = 1.0
    elif normalized in {"mouthpucker"}:
        weights["MouthFunnel"] = 0.58
        weights["JawOpen"] = 0.14
    elif normalized in {"mouthfunnel"}:
        weights["MouthPucker"] = 0.42
        weights["JawOpen"] = 0.24
    elif normalized in {"mouthsmileleft"}:
        weights["CheekSquintLeft"] = 0.12
        weights["CheekSquintRight"] = 0.04
        weights["MouthSmileRight"] = 0.18
    elif normalized in {"mouthsmileright"}:
        weights["CheekSquintRight"] = 0.12
        weights["CheekSquintLeft"] = 0.04
        weights["MouthSmileLeft"] = 0.18
    elif normalized in {"mouthfrownleft"}:
        weights["MouthShrugLower"] = 0.16
        weights["BrowInnerUp"] = 0.08
        weights["MouthFrownRight"] = 0.16
    elif normalized in {"mouthfrownright"}:
        weights["MouthShrugLower"] = 0.16
        weights["BrowInnerUp"] = 0.08
        weights["MouthFrownLeft"] = 0.16
    elif normalized in {"eyesquintleft"}:
        weights["CheekSquintLeft"] = 0.16
    elif normalized in {"eyesquintright"}:
        weights["CheekSquintRight"] = 0.16
    elif normalized in {"eyeblinkleft"}:
        weights["EyeSquintLeft"] = 0.16
    elif normalized in {"eyeblinkright"}:
        weights["EyeSquintRight"] = 0.16
    elif normalized in {"eyewideleft"}:
        weights["BrowOuterUpLeft"] = 0.18
    elif normalized in {"eyewideright"}:
        weights["BrowOuterUpRight"] = 0.18
    elif normalized in {"cheekpuff"}:
        weights["MouthPucker"] = 0.22
        weights["JawOpen"] = 0.12
    elif normalized in {"nosesneerleft"}:
        weights["MouthUpperUpLeft"] = 0.18
    elif normalized in {"nosesneerright"}:
        weights["MouthUpperUpRight"] = 0.18

    if side == "L":
        if "MouthStretchRight" in weights and normalized not in {"mouthstretchright", "jawopen"}:
            weights["MouthStretchRight"] *= 0.45
        if "CheekSquintRight" in weights:
            weights["CheekSquintRight"] *= 0.45
    elif side == "R":
        if "MouthStretchLeft" in weights and normalized not in {"mouthstretchleft", "jawopen"}:
            weights["MouthStretchLeft"] *= 0.45
        if "CheekSquintLeft" in weights:
            weights["CheekSquintLeft"] *= 0.45

    return {
        "name": f"{label} Validation",
        "seconds": 0.18,
        "transition_ratio": 0.52,
        "weights": weights,
    }


def _full_validation_rule_bridge(previous_name, previous_values, current_values, previous_side="C", current_side="C"):
    bridge_values = _blend_target_value_maps(previous_values, current_values, factor=0.52)
    jaw_open = max(0.0, min(1.0, float(current_values.get("JawOpen", 0.0) or 0.0)))
    blink_left = max(0.0, min(1.0, float(current_values.get("EyeBlinkLeft", 0.0) or 0.0)))
    blink_right = max(0.0, min(1.0, float(current_values.get("EyeBlinkRight", 0.0) or 0.0)))
    smile = max(
        float(current_values.get("MouthSmileLeft", 0.0) or 0.0),
        float(current_values.get("MouthSmileRight", 0.0) or 0.0),
    )
    pucker = max(
        float(current_values.get("MouthPucker", 0.0) or 0.0),
        float(current_values.get("MouthFunnel", 0.0) or 0.0),
    )

    if jaw_open > 0.0:
        bridge_values["MouthClose"] = min(max(0.0, 1.0 - (0.75 * jaw_open)), jaw_open)
        bridge_values["MouthLowerDownLeft"] = max(float(bridge_values.get("MouthLowerDownLeft", 0.0) or 0.0), jaw_open * 0.14)
        bridge_values["MouthLowerDownRight"] = max(float(bridge_values.get("MouthLowerDownRight", 0.0) or 0.0), jaw_open * 0.14)
        bridge_values["MouthStretchLeft"] = max(float(bridge_values.get("MouthStretchLeft", 0.0) or 0.0), jaw_open * 0.10)
        bridge_values["MouthStretchRight"] = max(float(bridge_values.get("MouthStretchRight", 0.0) or 0.0), jaw_open * 0.10)

    if pucker > 0.0:
        bridge_values["MouthFunnel"] = max(float(bridge_values.get("MouthFunnel", 0.0) or 0.0), pucker * 0.62)
        if "JawOpen" in bridge_values:
            bridge_values["JawOpen"] = min(float(bridge_values.get("JawOpen", 0.0) or 0.0), max(0.18, float(bridge_values.get("JawOpen", 0.0) or 0.0) * 0.78))

    if smile > 0.0 and jaw_open > 0.0:
        bridge_values["MouthSmileLeft"] = max(float(bridge_values.get("MouthSmileLeft", 0.0) or 0.0), smile * 0.20)
        bridge_values["MouthSmileRight"] = max(float(bridge_values.get("MouthSmileRight", 0.0) or 0.0), smile * 0.20)
        bridge_values["CheekSquintLeft"] = max(float(bridge_values.get("CheekSquintLeft", 0.0) or 0.0), smile * 0.12)
        bridge_values["CheekSquintRight"] = max(float(bridge_values.get("CheekSquintRight", 0.0) or 0.0), smile * 0.12)

    if blink_left > 0.0:
        bridge_values["EyeWideLeft"] = float(bridge_values.get("EyeWideLeft", 0.0) or 0.0) * (1.0 - (0.85 * blink_left))
        bridge_values["EyeSquintLeft"] = max(float(bridge_values.get("EyeSquintLeft", 0.0) or 0.0), blink_left * 0.16)
    if blink_right > 0.0:
        bridge_values["EyeWideRight"] = float(bridge_values.get("EyeWideRight", 0.0) or 0.0) * (1.0 - (0.85 * blink_right))
        bridge_values["EyeSquintRight"] = max(float(bridge_values.get("EyeSquintRight", 0.0) or 0.0), blink_right * 0.16)

    if previous_side != current_side and current_side in {"L", "R"}:
        damp_key = "MouthSmileRight" if current_side == "L" else "MouthSmileLeft"
        if damp_key in bridge_values:
            bridge_values[damp_key] = float(bridge_values.get(damp_key, 0.0) or 0.0) * 0.55

    return {
        "name": f"{previous_name} Blend",
        "seconds": 0.12,
        "transition_ratio": 0.70,
        "weights": _soften_opposing_target_values(_enforce_mouth_close_not_above_jaw_open(bridge_values)),
    }


def _full_validation_rule_states(items):
    groups = _full_validation_shape_keys(items)
    resolved_states = []
    previous_values = {}
    previous_name = ""
    previous_side = "C"
    for group in groups:
        for index, key_name in enumerate(group):
            side = "L" if key_name.lower().endswith("left") else ("R" if key_name.lower().endswith("right") else "C")
            state = _full_validation_rule_state(key_name, alias_name=key_name, side=side)
            current_values = _state_target_values(state)
            if previous_values:
                resolved_states.append(_full_validation_rule_bridge(previous_name, previous_values, current_values, previous_side=previous_side, current_side=side))
            resolved_states.append(state)
            previous_values = current_values
            previous_name = key_name
            previous_side = side
        if len(group) > 1:
            combo_weights = {}
            for key_name in group:
                combo_weights[str(key_name)] = 1.0
            combo_name = " + ".join(group)
            combo_state = {
                "name": f"{combo_name} Combo Validation",
                "seconds": 0.16,
                "transition_ratio": 0.50,
                "weights": combo_weights,
            }
            combo_values = _state_target_values(combo_state)
            if previous_values:
                resolved_states.append(_full_validation_rule_bridge(previous_name, previous_values, combo_values, previous_side=previous_side, current_side="C"))
            resolved_states.append(combo_state)
            previous_values = combo_values
            previous_name = combo_name
            previous_side = "C"
    return resolved_states


def _resolve_full_validation_states_for_object(key_blocks, items, target_count=70):
    del target_count
    return _resolve_validation_state_specs_for_object(
        key_blocks,
        _full_validation_rule_states(items),
        allow_empty=False,
    )


def _start_full_validation_animation(context, scene, workflow, module, panel_api, module_state, items):
    return _start_full_validation_native_animation(context, scene, workflow, module, panel_api, module_state, items)


def _full_validation_rule_bucket(shape_key_name):
    normalized = _normalize_shape_key_name(shape_key_name)
    if normalized.startswith("eye") or normalized.startswith("brow"):
        return "eye"
    if normalized.startswith("jaw") or normalized.startswith("mouth"):
        return "mouth"
    if normalized.startswith("cheek") or normalized.startswith("nose"):
        return "mid"
    return "other"


def _full_validation_rule_timing(shape_key_name, stage="main"):
    bucket = _full_validation_rule_bucket(shape_key_name)
    if bucket == "eye":
        table = {
            "lead": (0.08, 0.42),
            "main": (0.10, 0.46),
            "release": (0.14, 0.78),
            "combo": (0.12, 0.48),
            "bridge": (0.10, 0.70),
        }
    elif bucket == "mouth":
        table = {
            "lead": (0.10, 0.44),
            "main": (0.14, 0.50),
            "release": (0.16, 0.76),
            "combo": (0.16, 0.52),
            "bridge": (0.12, 0.68),
        }
    elif bucket == "mid":
        table = {
            "lead": (0.09, 0.44),
            "main": (0.12, 0.48),
            "release": (0.15, 0.76),
            "combo": (0.14, 0.50),
            "bridge": (0.11, 0.68),
        }
    else:
        table = {
            "lead": (0.09, 0.44),
            "main": (0.12, 0.50),
            "release": (0.14, 0.76),
            "combo": (0.14, 0.52),
            "bridge": (0.10, 0.68),
        }
    return table.get(stage, table["main"])


def _full_validation_rule_lead_state(shape_key_name, side="C"):
    key_name = str(shape_key_name or "").strip()
    normalized = _normalize_shape_key_name(key_name)
    seconds, transition_ratio = _full_validation_rule_timing(key_name, "lead")
    weights = {}
    if normalized in {"jawopen", "mouthclose", "mouthpucker", "mouthfunnel", "mouthstretchleft", "mouthstretchright", "mouthsmileleft", "mouthsmileright", "mouthfrownleft", "mouthfrownright"}:
        weights["BrowInnerUp"] = 0.10
        if side == "L":
            weights["EyeLookOutLeft"] = 0.12
            weights["EyeLookInRight"] = 0.10
        elif side == "R":
            weights["EyeLookInLeft"] = 0.10
            weights["EyeLookOutRight"] = 0.12
        else:
            weights["EyeWideLeft"] = 0.08
            weights["EyeWideRight"] = 0.08
        if normalized in {"mouthpucker", "mouthfunnel"}:
            weights["MouthFunnel"] = 0.26
            weights["MouthPucker"] = 0.18
        if normalized in {"jawopen", "mouthclose"}:
            weights["MouthLowerDownLeft"] = 0.10
            weights["MouthLowerDownRight"] = 0.10
    elif normalized in {"eyeblinkleft", "eyeblinkright", "eyesquintleft", "eyesquintright", "eyewideleft", "eyewideright"}:
        if normalized.endswith("left"):
            weights["BrowOuterUpLeft"] = 0.12
            weights["EyeSquintLeft"] = 0.08 if "blink" in normalized else 0.0
        elif normalized.endswith("right"):
            weights["BrowOuterUpRight"] = 0.12
            weights["EyeSquintRight"] = 0.08 if "blink" in normalized else 0.0
        else:
            weights["BrowInnerUp"] = 0.10
    if not weights:
        return None
    return {
        "name": f"{key_name} Lead",
        "seconds": seconds,
        "transition_ratio": transition_ratio,
        "weights": {name: value for name, value in weights.items() if value > 0.0},
    }


def _full_validation_rule_release_state(shape_key_name, side="C"):
    key_name = str(shape_key_name or "").strip()
    normalized = _normalize_shape_key_name(key_name)
    seconds, transition_ratio = _full_validation_rule_timing(key_name, "release")
    weights = {}
    if normalized in {"jawopen", "mouthclose"}:
        weights["JawOpen"] = 0.14
        weights["MouthClose"] = 0.06
        weights["MouthLowerDownLeft"] = 0.06
        weights["MouthLowerDownRight"] = 0.06
    elif normalized in {"mouthsmileleft", "mouthsmileright"}:
        if normalized.endswith("left"):
            weights["MouthSmileLeft"] = 0.18
            weights["CheekSquintLeft"] = 0.12
            weights["MouthSmileRight"] = 0.06
        else:
            weights["MouthSmileRight"] = 0.18
            weights["CheekSquintRight"] = 0.12
            weights["MouthSmileLeft"] = 0.06
    elif normalized in {"mouthpucker", "mouthfunnel"}:
        weights["MouthPucker"] = 0.14
        weights["MouthFunnel"] = 0.18
        weights["JawOpen"] = 0.06
    elif normalized in {"eyeblinkleft", "eyeblinkright"}:
        if normalized.endswith("left"):
            weights["EyeSquintLeft"] = 0.12
        else:
            weights["EyeSquintRight"] = 0.12
    elif normalized in {"eyewideleft", "eyewideright"}:
        if normalized.endswith("left"):
            weights["EyeWideLeft"] = 0.18
        else:
            weights["EyeWideRight"] = 0.18
    if not weights:
        return None
    return {
        "name": f"{key_name} Release",
        "seconds": seconds,
        "transition_ratio": transition_ratio,
        "weights": weights,
    }


def _full_validation_rule_state(shape_key_name, alias_name=None, side="C"):
    key_name = str(shape_key_name or "").strip()
    label = str(alias_name or key_name or "Validation").strip()
    seconds, transition_ratio = _full_validation_rule_timing(key_name, "main")
    weights = {key_name: 1.0} if key_name else {}
    normalized = _normalize_shape_key_name(key_name)

    if normalized in {"jawopen"}:
        weights["MouthClose"] = 0.18
        weights["MouthLowerDownLeft"] = 0.18
        weights["MouthLowerDownRight"] = 0.18
        weights["MouthStretchLeft"] = 0.10
        weights["MouthStretchRight"] = 0.10
    elif normalized in {"mouthclose"}:
        weights["JawOpen"] = 0.34
        weights["MouthPressLeft"] = 0.42
        weights["MouthPressRight"] = 0.42
    elif normalized in {"mouthpucker"}:
        weights["MouthFunnel"] = 0.58
        weights["JawOpen"] = 0.10
    elif normalized in {"mouthfunnel"}:
        weights["MouthPucker"] = 0.42
        weights["JawOpen"] = 0.18
    elif normalized in {"mouthsmileleft"}:
        weights["CheekSquintLeft"] = 0.12
        weights["CheekSquintRight"] = 0.04
        weights["MouthSmileRight"] = 0.18
    elif normalized in {"mouthsmileright"}:
        weights["CheekSquintRight"] = 0.12
        weights["CheekSquintLeft"] = 0.04
        weights["MouthSmileLeft"] = 0.18
    elif normalized in {"mouthfrownleft"}:
        weights["MouthShrugLower"] = 0.16
        weights["BrowInnerUp"] = 0.08
        weights["MouthFrownRight"] = 0.16
    elif normalized in {"mouthfrownright"}:
        weights["MouthShrugLower"] = 0.16
        weights["BrowInnerUp"] = 0.08
        weights["MouthFrownLeft"] = 0.16
    elif normalized in {"eyesquintleft"}:
        weights["CheekSquintLeft"] = 0.16
    elif normalized in {"eyesquintright"}:
        weights["CheekSquintRight"] = 0.16
    elif normalized in {"eyeblinkleft"}:
        weights["EyeSquintLeft"] = 0.16
    elif normalized in {"eyeblinkright"}:
        weights["EyeSquintRight"] = 0.16
    elif normalized in {"eyewideleft"}:
        weights["BrowOuterUpLeft"] = 0.18
    elif normalized in {"eyewideright"}:
        weights["BrowOuterUpRight"] = 0.18
    elif normalized in {"cheekpuff"}:
        weights["MouthPucker"] = 0.22
        weights["JawOpen"] = 0.12
    elif normalized in {"nosesneerleft"}:
        weights["MouthUpperUpLeft"] = 0.18
    elif normalized in {"nosesneerright"}:
        weights["MouthUpperUpRight"] = 0.18

    if side == "L":
        if "MouthStretchRight" in weights and normalized not in {"mouthstretchright", "jawopen"}:
            weights["MouthStretchRight"] *= 0.45
        if "CheekSquintRight" in weights:
            weights["CheekSquintRight"] *= 0.45
    elif side == "R":
        if "MouthStretchLeft" in weights and normalized not in {"mouthstretchleft", "jawopen"}:
            weights["MouthStretchLeft"] *= 0.45
        if "CheekSquintLeft" in weights:
            weights["CheekSquintLeft"] *= 0.45

    return {
        "name": f"{label} Validation",
        "seconds": seconds,
        "transition_ratio": transition_ratio,
        "weights": weights,
    }


def _full_validation_rule_bridge(previous_name, previous_values, current_values, previous_side="C", current_side="C"):
    bridge_values = _blend_target_value_maps(previous_values, current_values, factor=0.52)
    seconds = 0.12
    transition_ratio = 0.68
    jaw_open = max(0.0, min(1.0, float(current_values.get("JawOpen", 0.0) or 0.0)))
    blink_left = max(0.0, min(1.0, float(current_values.get("EyeBlinkLeft", 0.0) or 0.0)))
    blink_right = max(0.0, min(1.0, float(current_values.get("EyeBlinkRight", 0.0) or 0.0)))
    smile = max(
        float(current_values.get("MouthSmileLeft", 0.0) or 0.0),
        float(current_values.get("MouthSmileRight", 0.0) or 0.0),
    )
    pucker = max(
        float(current_values.get("MouthPucker", 0.0) or 0.0),
        float(current_values.get("MouthFunnel", 0.0) or 0.0),
    )

    if jaw_open > 0.0:
        seconds, transition_ratio = _full_validation_rule_timing("JawOpen", "bridge")
        if "MouthClose" in bridge_values:
            bridge_values["MouthClose"] = min(
                max(0.0, float(bridge_values.get("MouthClose", 0.0) or 0.0)),
                jaw_open,
            )
        bridge_values["MouthLowerDownLeft"] = max(float(bridge_values.get("MouthLowerDownLeft", 0.0) or 0.0), jaw_open * 0.14)
        bridge_values["MouthLowerDownRight"] = max(float(bridge_values.get("MouthLowerDownRight", 0.0) or 0.0), jaw_open * 0.14)
        bridge_values["MouthStretchLeft"] = max(float(bridge_values.get("MouthStretchLeft", 0.0) or 0.0), jaw_open * 0.10)
        bridge_values["MouthStretchRight"] = max(float(bridge_values.get("MouthStretchRight", 0.0) or 0.0), jaw_open * 0.10)

    if pucker > 0.0:
        bridge_values["MouthFunnel"] = max(float(bridge_values.get("MouthFunnel", 0.0) or 0.0), pucker * 0.62)
        bridge_values["JawOpen"] = min(float(bridge_values.get("JawOpen", 0.0) or 0.0), max(0.18, float(bridge_values.get("JawOpen", 0.0) or 0.0) * 0.78))
        bridge_values["CheekPuff"] = min(float(bridge_values.get("CheekPuff", 0.0) or 0.0), max(0.0, float(bridge_values.get("CheekPuff", 0.0) or 0.0) * 0.68))

    if smile > 0.0 and jaw_open > 0.0:
        bridge_values["MouthSmileLeft"] = max(float(bridge_values.get("MouthSmileLeft", 0.0) or 0.0), smile * 0.20)
        bridge_values["MouthSmileRight"] = max(float(bridge_values.get("MouthSmileRight", 0.0) or 0.0), smile * 0.20)
        bridge_values["CheekSquintLeft"] = max(float(bridge_values.get("CheekSquintLeft", 0.0) or 0.0), smile * 0.12)
        bridge_values["CheekSquintRight"] = max(float(bridge_values.get("CheekSquintRight", 0.0) or 0.0), smile * 0.12)
        bridge_values["MouthPucker"] = min(float(bridge_values.get("MouthPucker", 0.0) or 0.0), max(0.0, float(bridge_values.get("MouthPucker", 0.0) or 0.0) * 0.72))

    if blink_left > 0.0:
        bridge_values["EyeWideLeft"] = float(bridge_values.get("EyeWideLeft", 0.0) or 0.0) * (1.0 - (0.85 * blink_left))
        bridge_values["EyeSquintLeft"] = max(float(bridge_values.get("EyeSquintLeft", 0.0) or 0.0), blink_left * 0.16)
    if blink_right > 0.0:
        bridge_values["EyeWideRight"] = float(bridge_values.get("EyeWideRight", 0.0) or 0.0) * (1.0 - (0.85 * blink_right))
        bridge_values["EyeSquintRight"] = max(float(bridge_values.get("EyeSquintRight", 0.0) or 0.0), blink_right * 0.16)

    if previous_side != current_side and current_side in {"L", "R"}:
        damp_key = "MouthSmileRight" if current_side == "L" else "MouthSmileLeft"
        if damp_key in bridge_values:
            bridge_values[damp_key] = float(bridge_values.get(damp_key, 0.0) or 0.0) * 0.55

    final_jaw_open = max(0.0, min(1.0, float(bridge_values.get("JawOpen", 0.0) or 0.0)))
    if "MouthClose" in bridge_values and final_jaw_open > 0.0:
        bridge_values["MouthClose"] = min(
            max(0.0, float(bridge_values.get("MouthClose", 0.0) or 0.0)),
            final_jaw_open,
        )
    bridge_values = _enforce_mouth_close_not_above_jaw_open(bridge_values)

    return {
        "name": f"{previous_name} Blend",
        "seconds": seconds,
        "transition_ratio": transition_ratio,
        "weights": _soften_opposing_target_values(bridge_values),
    }


def _full_validation_rule_states(items):
    groups = _full_validation_shape_keys(items)
    resolved_states = []
    previous_values = {}
    previous_name = ""
    previous_side = "C"
    for group in groups:
        for key_name in group:
            side = "L" if key_name.lower().endswith("left") else ("R" if key_name.lower().endswith("right") else "C")
            lead_state = _full_validation_rule_lead_state(key_name, side=side)
            if lead_state is not None:
                lead_values = _state_target_values(lead_state)
                if previous_values:
                    resolved_states.append(_full_validation_rule_bridge(previous_name, previous_values, lead_values, previous_side=previous_side, current_side=side))
                resolved_states.append(lead_state)
                previous_values = lead_values
                previous_name = str(lead_state.get("name", key_name) or key_name)
                previous_side = side

            state = _full_validation_rule_state(key_name, alias_name=key_name, side=side)
            current_values = _state_target_values(state)
            if previous_values:
                resolved_states.append(_full_validation_rule_bridge(previous_name, previous_values, current_values, previous_side=previous_side, current_side=side))
            resolved_states.append(state)
            previous_values = current_values
            previous_name = key_name
            previous_side = side

            release_state = _full_validation_rule_release_state(key_name, side=side)
            if release_state is not None:
                release_values = _state_target_values(release_state)
                resolved_states.append(_full_validation_rule_bridge(previous_name, previous_values, release_values, previous_side=previous_side, current_side=side))
                resolved_states.append(release_state)
                previous_values = release_values
                previous_name = str(release_state.get("name", key_name) or key_name)
                previous_side = side

        if len(group) > 1:
            combo_weights = {str(key_name): 1.0 for key_name in group}
            combo_name = " + ".join(group)
            combo_seconds, combo_ratio = _full_validation_rule_timing(group[0], "combo")
            combo_state = {
                "name": f"{combo_name} Combo Validation",
                "seconds": combo_seconds,
                "transition_ratio": combo_ratio,
                "weights": combo_weights,
            }
            combo_values = _state_target_values(combo_state)
            if previous_values:
                resolved_states.append(_full_validation_rule_bridge(previous_name, previous_values, combo_values, previous_side=previous_side, current_side="C"))
            resolved_states.append(combo_state)
            previous_values = combo_values
            previous_name = combo_name
            previous_side = "C"
    return resolved_states


def _legacy_style_full_validation_states(items):
    return _full_validation_states(items)


def _resolve_full_validation_states_for_object(key_blocks, items, target_count=70):
    del target_count
    return _resolve_validation_state_specs_for_object(
        key_blocks,
        _legacy_style_full_validation_states(items),
        allow_empty=False,
    )


def _build_full_validation_plan(resolved_states, start_frame, fps, total_seconds, action_name, object_name, target_frames=FULL_VALIDATION_TARGET_FRAMES, tail_states=None):
    fps = max(1.0, float(fps))
    tail_states = []
    tail_frame_budget = sum(max(6, int(round(max(0.1, float(state.get("seconds", 1.0) or 1.0)) * fps))) for state in tail_states)
    total_frames = max(
        (len(resolved_states) * 4) + tail_frame_budget,
        int(round(max(1.0, float(total_seconds)) * fps)),
        int(target_frames or 0),
    )
    core_frame_budget = max(len(resolved_states) * 4, int(total_frames - tail_frame_budget))
    state_span = max(24, int(round(float(core_frame_budget) / max(1, len(resolved_states)))))
    cursor = int(start_frame)
    segments = []
    previous_values = {}
    for index, state in enumerate(resolved_states, start=1):
        state_seconds = max(float(state_span) / fps, float((state or {}).get("seconds", 0.0) or 0.0))
        current_span = max(24, int(round(state_seconds * fps)))
        transition_ratio = max(0.28, min(0.72, float((state or {}).get("transition_ratio", 0.78) or 0.78)))
        transition_frames = max(6, min(current_span - 10, int(round(current_span * transition_ratio))))
        hold_frames = max(12, current_span - transition_frames)
        target_values = _enforce_mouth_close_not_above_jaw_open(_state_target_values(state))
        peak_frame = cursor + transition_frames
        hold_end_frame = peak_frame + hold_frames
        end_frame = hold_end_frame
        segments.append(
            {
                "index": index,
                "name": str(state.get("name", "mix") or "mix"),
                "start_frame": int(cursor),
                "peak_frame": int(peak_frame),
                "hold_end_frame": int(hold_end_frame),
                "end_frame": int(end_frame),
                "from_values": _enforce_mouth_close_not_above_jaw_open(previous_values),
                "base_weights": dict(state.get("base_weights", {}) or {}),
                "weights": dict(state.get("weights", {}) or {}),
                "target_values": target_values,
                "curve_mode": _full_validation_curve_mode(state),
            }
        )
        previous_values = _enforce_mouth_close_not_above_jaw_open(target_values)
        cursor = int(end_frame) + 1
    if previous_values:
        reset_span = max(20, int(round(state_span * 0.84)))
        reset_transition = max(6, int(round(reset_span * 0.56)))
        reset_hold = max(10, reset_span - reset_transition)
        reset_peak = cursor + reset_transition
        reset_end = reset_peak + reset_hold
        segments.append(
            {
                "index": len(segments) + 1,
                "name": "Return To Base",
                "start_frame": int(cursor),
                "peak_frame": int(reset_peak),
                "hold_end_frame": int(reset_end),
                "end_frame": int(reset_end),
                "from_values": _enforce_mouth_close_not_above_jaw_open(previous_values),
                "base_weights": {},
                "weights": {},
                "target_values": {},
                "curve_mode": "bridge",
            }
        )
        previous_values = {}
        cursor = int(reset_end) + 1
    for state in tail_states:
        tail_span = max(8, int(round(max(0.1, float(state.get("seconds", 1.0) or 1.0)) * fps)))
        tail_transition_ratio = max(0.42, min(0.92, float(state.get("transition_ratio", 0.82) or 0.82)))
        tail_transition_frames = max(3, min(tail_span - 2, int(round(tail_span * tail_transition_ratio))))
        tail_hold_frames = max(2, tail_span - tail_transition_frames)
        target_values = _enforce_mouth_close_not_above_jaw_open(_state_target_values(state))
        peak_frame = cursor + tail_transition_frames
        hold_end_frame = peak_frame + tail_hold_frames
        end_frame = hold_end_frame
        segments.append(
            {
                "index": len(segments) + 1,
                "name": str(state.get("name", "TextMix") or "TextMix"),
                "start_frame": int(cursor),
                "peak_frame": int(peak_frame),
                "hold_end_frame": int(hold_end_frame),
                "end_frame": int(end_frame),
                "from_values": _enforce_mouth_close_not_above_jaw_open(previous_values),
                "base_weights": dict(state.get("base_weights", {}) or {}),
                "weights": dict(state.get("weights", {}) or {}),
                "target_values": target_values,
                "curve_mode": _full_validation_curve_mode(state),
            }
        )
        previous_values = _enforce_mouth_close_not_above_jaw_open(target_values)
        cursor = int(end_frame) + 1
    end_frame = int(segments[-1]["end_frame"]) if segments else int(start_frame)
    frame_span = max(1, end_frame - int(start_frame))
    actual_total_seconds = max(float(total_seconds), float(frame_span) / fps)
    return {
        "object_name": str(object_name or ""),
        "action_name": str(action_name or ""),
        "start_frame": int(start_frame),
        "end_frame": end_frame,
        "total_seconds": float(actual_total_seconds),
        "fps": float(fps),
        "target_frames": int(target_frames or 0),
        "segments": segments,
    }
