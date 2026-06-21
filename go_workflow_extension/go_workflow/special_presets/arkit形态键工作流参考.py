import json
import os
import re
import subprocess
import sys
import time

import bpy


DEFAULT_PRESET_DIR = os.path.dirname(os.path.abspath(__file__))
PRESET_BASENAME = os.path.splitext(os.path.basename(os.path.abspath(__file__)))[0]
PANEL_PREVIEW_SCALE = 10.0
_PRESET_PATH_CACHE = None
_PAYLOAD_CACHE = {"mtime_ns": None, "size": None, "payload": None}
_ANIMATION_STATE = {"running": False, "token": 0, "paused": False}
_VALIDATION_PREVIEW_STATE = {"object_name": "", "active_index": 0, "values": None}
_VALIDATION_TIMER_STATE = {"callback": None}
_VALIDATION_TIMER_REGISTRY_KEY = "go_workflow.validation_timer_callbacks"
ANIMATION_TIMER_INTERVAL = 0.04
ANIMATION_DURATION_PER_KEY = 1.0
ANIMATION_STATUS_INTERVAL = 0.2
FULL_VALIDATION_TOTAL_SECONDS = 15.0
FULL_VALIDATION_TARGET_FRAMES = 700
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
    runtime = dict(_FULL_VALIDATION_RUNTIME_DEFAULTS)
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
    return runtime


def _set_full_validation_runtime(scene, workflow, module, module_state=None, **kwargs):
    updates = {}
    for logical_key, value in kwargs.items():
        store_key = _FULL_VALIDATION_RUNTIME_KEYS.get(logical_key)
        if not store_key:
            continue
        updates[store_key] = value
        if module_state is not None:
            module_state.set(store_key, value)
    if updates:
        _update_module_runtime_store(scene, workflow, module, updates)
    _FULL_VALIDATION_RUNTIME.update(_full_validation_runtime(module_state, scene, workflow, module))
    return dict(_FULL_VALIDATION_RUNTIME)


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
    global _PRESET_PATH_CACHE
    if _PRESET_PATH_CACHE is not None:
        return _PRESET_PATH_CACHE
    for preset_dir in _iter_candidate_preset_dirs():
        data_file = os.path.join(preset_dir, f"{PRESET_BASENAME}.json")
        if not os.path.isfile(data_file):
            continue
        _PRESET_PATH_CACHE = {
            "preset_dir": preset_dir,
            "data_file": data_file,
            "image_dir": os.path.join(preset_dir, f"{PRESET_BASENAME}_images"),
            "viewer_state_file": os.path.join(preset_dir, f"{PRESET_BASENAME}_viewer_state_main.json"),
            "detail_viewer_state_file": os.path.join(preset_dir, f"{PRESET_BASENAME}_viewer_state_detail.json"),
            "viewer_script_file": os.path.join(preset_dir, "arkit_reference_viewer.ps1"),
        }
        return _PRESET_PATH_CACHE
    _PRESET_PATH_CACHE = {
        "preset_dir": DEFAULT_PRESET_DIR,
        "data_file": os.path.join(DEFAULT_PRESET_DIR, f"{PRESET_BASENAME}.json"),
        "image_dir": os.path.join(DEFAULT_PRESET_DIR, f"{PRESET_BASENAME}_images"),
        "viewer_state_file": os.path.join(DEFAULT_PRESET_DIR, f"{PRESET_BASENAME}_viewer_state_main.json"),
        "detail_viewer_state_file": os.path.join(DEFAULT_PRESET_DIR, f"{PRESET_BASENAME}_viewer_state_detail.json"),
        "viewer_script_file": os.path.join(DEFAULT_PRESET_DIR, "arkit_reference_viewer.ps1"),
    }
    return _PRESET_PATH_CACHE


def _load_payload():
    data_file = _preset_paths()["data_file"]
    if not os.path.isfile(data_file):
        raise Exception("缺少 ARKit 形态键工作流参考数据文件")
    stat = os.stat(data_file)
    if (
        _PAYLOAD_CACHE["payload"] is not None
        and _PAYLOAD_CACHE["mtime_ns"] == stat.st_mtime_ns
        and _PAYLOAD_CACHE["size"] == stat.st_size
    ):
        return _PAYLOAD_CACHE["payload"]
    with open(data_file, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise Exception("ARKit 形态键工作流参考数据格式无效")
    _PAYLOAD_CACHE.update({"mtime_ns": stat.st_mtime_ns, "size": stat.st_size, "payload": payload})
    return payload


def _load_items():
    payload = _load_payload()
    items = list(payload.get("items", []) or [])
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


def _resolve_media_paths(item, field_name):
    preset_dir = _preset_paths()["preset_dir"]
    image_dir = _preset_paths()["image_dir"]
    files = []
    for value in list(item.get(field_name, []) or []):
        full = value if os.path.isabs(value) else os.path.normpath(os.path.join(preset_dir, value))
        if os.path.isfile(full):
            files.append(full)
    if files or field_name != "media_files":
        return files
    local_path = str(item.get("image_local_path", "") or "").strip()
    if local_path:
        full = local_path if os.path.isabs(local_path) else os.path.normpath(os.path.join(preset_dir, local_path))
        if os.path.isfile(full):
            return [full]
    hint = str(item.get("image_hint", "") or "").strip()
    if hint:
        full = os.path.normpath(os.path.join(image_dir, hint))
        if os.path.isfile(full):
            return [full]
    return []


def _media_files(item):
    return _resolve_media_paths(item, "media_files")


def _detail_media_files(item):
    return _resolve_media_paths(item, "detail_media_files")


def _media_index(panel_api, item):
    files = _media_files(item)
    if not files:
        return 0
    index = panel_api.get_int("media_index", 0) if panel_api is not None else 0
    index = max(0, min(int(index), len(files) - 1))
    if panel_api is not None:
        panel_api.set_int("media_index", index)
    return index


def _detail_media_index(panel_api, item):
    files = _detail_media_files(item)
    if not files:
        return 0
    index = panel_api.get_int("detail_media_index", 0) if panel_api is not None else 0
    index = max(0, min(int(index), len(files) - 1))
    if panel_api is not None:
        panel_api.set_int("detail_media_index", index)
    return index


def _current_item(panel_api, module_state):
    payload, items = _load_items()
    if module_state is not None:
        module_state.set("arkit_meta", payload.get("meta", {}))
    index = _step_index(panel_api, items)
    if panel_api is not None:
        panel_api.set_int("step_index", index + 1)
    return payload, items, items[index], index, _media_index(panel_api, items[index])


def _normalize_shape_key_name(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower())


def _shape_key_names(items):
    return [str(item.get("shape_key", "") or "").strip() for item in items if str(item.get("shape_key", "") or "").strip()]


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
    lines = []
    for sequence in _explicit_validation_sequences(item):
        text = "顺序验证: " + " -> ".join(str(name or "").strip() for name in sequence if str(name or "").strip())
        if text not in lines:
            lines.append(text)
    for source in list(item.get("validation_mix", []) or []):
        text = str(source or "").strip()
        if text and text not in lines:
            lines.append(text)
    for source in list(item.get("notes", []) or []):
        text = str(source or "").strip()
        if any(token in text for token in ("可与", "组合", "联动", "混合")) and text not in lines:
            lines.append(text)
    detail_text = str(item.get("detail_ja_zh") or item.get("detail_ja") or "").strip()
    for left_name, right_name in re.findall(r"([A-Za-z][A-Za-z0-9_/-]*)\s*[+＋]\s*([A-Za-z][A-Za-z0-9_/-]*)", detail_text):
        line = f"建议混合验证: {left_name} + {right_name}"
        if line not in lines:
            lines.append(line)
    return lines


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


def _interleave_full_validation_states(states):
    buckets = {"upper": [], "mid": [], "mouth": [], "other": []}
    for state in list(states or []):
        buckets.setdefault(_full_validation_state_bucket(state), []).append(state)
    order = []
    while any(buckets.values()):
        for bucket_name in ("upper", "mid", "mouth", "other", "mouth", "upper", "mouth"):
            bucket = buckets.get(bucket_name) or []
            if bucket:
                order.append(bucket.pop(0))
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
        for offset in range(1, len(core_states) + 1):
            candidate = core_states[(cursor + offset) % len(core_states)]
            if _full_validation_state_bucket(candidate) != first_bucket:
                second = candidate
                break
        if second is None:
            second = core_states[(cursor + pair_offset) % len(core_states)]
        second_bucket = _full_validation_state_bucket(second)
        for offset in range(pair_offset + 1, len(core_states) + pair_offset + 1):
            candidate = core_states[(cursor + offset) % len(core_states)]
            candidate_bucket = _full_validation_state_bucket(candidate)
            if candidate_bucket not in {first_bucket, second_bucket}:
                third = candidate
                break
        cursor += 1
        weighted_sources = [(1.0, first), (0.58, second)]
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


def _state_target_values(state):
    target_values = dict(state.get("base_weights", {}) or {})
    for key_name, value in dict(state.get("weights", {}) or {}).items():
        target_values[key_name] = float(value)
    mouth_close_floor = _mouth_close_floor(target_values)
    if mouth_close_floor is not None:
        target_values["JawOpen"] = max(float(target_values.get("JawOpen", 0.0) or 0.0), float(mouth_close_floor))
    return target_values


def _resolve_full_validation_states_for_object(key_blocks, items, target_count=70):
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
    for key_name in key_names:
        key_block = key_block_map.get(key_name)
        if key_block is None:
            continue
        key_block.value = max(0.0, min(1.0, float(values.get(key_name, 0.0) or 0.0)))
        key_block.keyframe_insert(data_path="value", frame=frame)


def _segment_factor(segment, frame):
    if not segment:
        return 0.0
    start_frame = int(segment.get("start_frame", 0) or 0)
    peak_frame = int(segment.get("peak_frame", start_frame) or start_frame)
    hold_end_frame = int(segment.get("hold_end_frame", peak_frame) or peak_frame)
    end_frame = int(segment.get("end_frame", hold_end_frame) or hold_end_frame)
    frame = int(frame)
    if frame <= start_frame:
        return 0.0
    if peak_frame > start_frame and frame < peak_frame:
        return max(0.0, min(1.0, float(frame - start_frame) / float(peak_frame - start_frame)))
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
        return dict(from_values)
    if peak_frame <= start_frame:
        return dict(target_values)
    if frame_value >= peak_frame:
        return dict(target_values)
    factor = max(0.0, min(1.0, float(frame_value - start_frame) / float(peak_frame - start_frame)))
    values = {}
    for key_name in set(from_values) | set(target_values):
        start_value = float(from_values.get(key_name, 0.0) or 0.0)
        end_value = float(target_values.get(key_name, 0.0) or 0.0)
        values[key_name] = max(0.0, min(1.0, start_value + ((end_value - start_value) * factor)))
    if frame_value > hold_end_frame:
        return dict(target_values)
    return values


def _find_plan_segment(plan, frame):
    segments = list((plan or {}).get("segments", []) or [])
    frame_value = float(frame)
    for segment in segments:
        if float(segment.get("start_frame", 0) or 0) <= frame_value <= float(segment.get("end_frame", 0) or 0):
            return segment
    if segments and frame_value > float(segments[-1].get("end_frame", 0) or 0):
        return segments[-1]
    return None


def _build_full_validation_plan(resolved_states, start_frame, fps, total_seconds, action_name, object_name):
    total_frames = max(len(resolved_states) * 4, int(round(max(1.0, float(total_seconds)) * max(1.0, float(fps)))))
    state_span = max(4, int(round(float(total_frames) / max(1, len(resolved_states)))))
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
    return {
        "object_name": str(object_name or ""),
        "action_name": str(action_name or ""),
        "start_frame": int(start_frame),
        "end_frame": int(segments[-1]["end_frame"]) if segments else int(start_frame),
        "total_seconds": float(total_seconds),
        "fps": float(fps),
        "segments": segments,
    }


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
    plan = _build_full_validation_plan(
        resolved_states,
        1,
        plan_fps,
        FULL_VALIDATION_TOTAL_SECONDS,
        "",
        obj.name_full,
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

    total_seconds = max(1.0, float(FULL_VALIDATION_TOTAL_SECONDS))
    _ANIMATION_STATE["token"] = full_token
    _ANIMATION_STATE["running"] = True
    _ANIMATION_STATE["paused"] = False
    token = int(_ANIMATION_STATE["token"])
    object_name = obj.name_full
    plan_start_frame = float(plan.get("start_frame", 1) or 1)
    plan_end_frame = float(plan.get("end_frame", plan_start_frame) or plan_start_frame)
    state = {"started_at": time.perf_counter(), "last_status_at": 0.0, "last_segment_index": -1}

    def _active_key_map():
        current_obj = bpy.data.objects.get(object_name)
        current_shape_keys = getattr(getattr(current_obj, "data", None), "shape_keys", None) if current_obj is not None else None
        current_key_blocks = getattr(current_shape_keys, "key_blocks", None) if current_shape_keys is not None else None
        if current_obj is None or current_key_blocks is None:
            return None, {}
        return current_obj, {getattr(key_block, "name", ""): key_block for index, key_block in enumerate(current_key_blocks) if index != 0}

    used_key_names = sorted(
        {key_name for segment in list(plan.get("segments", []) or []) for key_name in set(dict(segment.get("from_values", {}) or {})) | set(dict(segment.get("target_values", {}) or {}))},
        key=str.casefold,
    )

    def _apply_value_map(values):
        current_obj, key_map = _active_key_map()
        if not key_map:
            return False
        for shape_key_name in used_key_names:
            target_key = key_map.get(shape_key_name)
            if target_key is not None:
                target_key.value = max(0.0, min(1.0, float(values.get(shape_key_name, 0.0) or 0.0)))
        try:
            current_obj.data.update()
        except Exception:
            pass
        return True

    def _tick():
        try:
            runtime_now = _full_validation_runtime(module_state, scene, workflow, module)
            if not _ANIMATION_STATE["running"] or token != _ANIMATION_STATE["token"]:
                _set_full_validation_runtime(scene, workflow, module, module_state, running=False, paused=False, status="", paused_at=0.0, pause_accumulated=0.0, current_index=0, current_factor=0.0)
                return None
            if full_token != int(runtime_now.get("token", 0)):
                _set_full_validation_runtime(scene, workflow, module, module_state, running=False, paused=False, status="", paused_at=0.0, pause_accumulated=0.0, current_index=0, current_factor=0.0)
                return None
            if bool(_ANIMATION_STATE.get("paused")) or bool(runtime_now.get("paused")):
                _set_full_validation_runtime(scene, workflow, module, module_state, status="paused")
                return ANIMATION_TIMER_INTERVAL
            paused_at = float(runtime_now.get("paused_at") or 0.0)
            if paused_at > 0.0:
                resumed_at = time.perf_counter()
                paused_duration = max(0.0, resumed_at - paused_at)
                runtime_now = _set_full_validation_runtime(scene, workflow, module, module_state, pause_accumulated=float(runtime_now.get("pause_accumulated") or 0.0) + paused_duration, paused_at=0.0, status="running")
            else:
                runtime_now = _set_full_validation_runtime(scene, workflow, module, module_state, status="running")
            elapsed = max(0.0, time.perf_counter() - float(state["started_at"]) - float(runtime_now.get("pause_accumulated") or 0.0))
            if elapsed >= total_seconds:
                _ANIMATION_STATE["running"] = False
                _ANIMATION_STATE["paused"] = False
                _apply_value_map({})
                _set_full_validation_runtime(scene, workflow, module, module_state, running=False, paused=False, status="finished", paused_at=0.0, pause_accumulated=0.0, current_index=len(list(plan.get("segments", []) or [])), current_factor=1.0, total=len(list(plan.get("segments", []) or [])))
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
            _set_full_validation_runtime(scene, workflow, module, module_state, current_label=current_name, current_values=current_values_text, current_index=current_index, current_factor=phase, total=len(list(plan.get("segments", []) or [])))
            now = time.perf_counter()
            if current_index != int(state.get("last_segment_index", -1) or -1):
                dominant_values = dict((current_segment or {}).get("target_values", {}) or {})
                if dominant_values:
                    dominant_name = max(dominant_values.items(), key=lambda item: float(item[1]))[0]
                    current_obj, current_key_map = _active_key_map()
                    current_key_blocks = getattr(getattr(getattr(current_obj, "data", None), "shape_keys", None), "key_blocks", None) if current_obj is not None else None
                    if current_key_blocks is not None:
                        _set_active_shape_key_index(current_obj, current_key_blocks, dominant_name)
                state["last_segment_index"] = current_index
            if module_state is not None and (state["last_status_at"] <= 0.0 or (now - float(state["last_status_at"])) >= ANIMATION_STATUS_INTERVAL):
                module_state.set("last_result", f"全面混合验证进行中：{current_name}（第 {current_index} / {len(list(plan.get('segments', []) or []))} 段） | {current_values_text}")
                state["last_status_at"] = now
            return ANIMATION_TIMER_INTERVAL
        except ReferenceError:
            _ANIMATION_STATE["running"] = False
            _ANIMATION_STATE["paused"] = False
            _release_validation_timer(_tick)
            _set_full_validation_runtime(scene, workflow, module, module_state, running=False, paused=False, status="stopped", paused_at=0.0, pause_accumulated=0.0, current_index=0, current_factor=0.0)
            return None
        except Exception as exc:
            _ANIMATION_STATE["running"] = False
            _ANIMATION_STATE["paused"] = False
            _release_validation_timer(_tick)
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


def _register_validation_timer(callback):
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


def _cancel_validation_timer():
    callback = _VALIDATION_TIMER_STATE.get("callback")
    if callback is None:
        return False
    try:
        bpy.app.timers.unregister(callback)
    except Exception:
        pass
    try:
        registry = bpy.app.driver_namespace.get(_VALIDATION_TIMER_REGISTRY_KEY)
        if registry is not None:
            registry.discard(callback)
            if not registry:
                bpy.app.driver_namespace.pop(_VALIDATION_TIMER_REGISTRY_KEY, None)
    except Exception:
        pass
    _VALIDATION_TIMER_STATE["callback"] = None
    return True


def cleanup_runtime(scene=None, workflow=None, module=None, module_state=None):
    _cancel_validation_timer()
    _ANIMATION_STATE["running"] = False
    _ANIMATION_STATE["paused"] = False
    _ANIMATION_STATE["token"] += 1
    _clear_full_validation_runtime_state(scene, workflow, module, module_state)
    _clear_full_validation_plan(scene, workflow, module, module_state)
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
    return True


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
            action.use_fake_user = False
        except Exception:
            pass
        removed += 1
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
    shape_keys, action = _ensure_full_validation_action(obj)
    fps_base = float(getattr(getattr(scene, "render", None), "fps_base", 1.0) or 1.0)
    fps_value = float(getattr(getattr(scene, "render", None), "fps", 24) or 24.0)
    fps = fps_value / fps_base if fps_base not in {0.0, -0.0} else fps_value
    start_frame = 1
    plan = _build_full_validation_plan(
        resolved_states,
        start_frame,
        fps,
        FULL_VALIDATION_TOTAL_SECONDS,
        getattr(action, "name_full", action.name),
        obj.name_full,
    )
    used_key_names = sorted(
        {key_name for segment in list(plan.get("segments", []) or []) for key_name in dict(segment.get("target_values", {}) or {}).keys()},
        key=str.casefold,
    )
    if not used_key_names:
        message = "当前物体没有可写入关键帧的匹配 ARKit 形态键"
        if panel_api is not None:
            panel_api.set_status(message, level="WARNING")
        if module_state is not None:
            module_state.set("last_result", message)
        return None

    for segment in list(plan.get("segments", []) or []):
        from_values = dict(segment.get("from_values", {}) or {})
        target_values = dict(segment.get("target_values", {}) or {})
        _insert_shape_key_frame(key_block_map, used_key_names, from_values, int(segment.get("start_frame", start_frame) or start_frame))
        _insert_shape_key_frame(key_block_map, used_key_names, target_values, int(segment.get("peak_frame", start_frame) or start_frame))
        _insert_shape_key_frame(key_block_map, used_key_names, target_values, int(segment.get("hold_end_frame", start_frame) or start_frame))
    for fcurve in action.fcurves:
        for keyframe_point in fcurve.keyframe_points:
            keyframe_point.interpolation = "LINEAR"

    _set_full_validation_plan(scene, workflow, module, module_state, plan)
    for key_name in used_key_names:
        if key_name in key_block_map:
            key_block_map[key_name].value = 0.0
    _set_active_shape_key_index(obj, key_blocks, used_key_names[0])
    scene.frame_start = int(start_frame)
    scene.frame_end = max(int(start_frame), int(plan.get("end_frame", start_frame) or start_frame))
    try:
        scene.frame_set(int(start_frame))
    except Exception:
        scene.frame_current = int(start_frame)
    try:
        shape_keys.update_tag()
    except Exception:
        pass
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
    token = int(_ANIMATION_STATE["token"])
    state = {
        "target_index": 0,
        "started_at": time.perf_counter(),
        "last_value": -1.0,
        "last_status_at": 0.0,
    }

    def _tick():
        if not _ANIMATION_STATE["running"] or token != _ANIMATION_STATE["token"]:
            return None
        target_index = int(state["target_index"])
        if target_index >= len(matched):
            _ANIMATION_STATE["running"] = False
            _release_validation_timer(_tick)
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
                module_state.set(
                    "last_result",
                    f"递增验证进行中：{current_name} {value:.2f}（第 {target_index + 1} / {len(matched)} 个）",
                )
                state["last_status_at"] = now
        except Exception:
            _ANIMATION_STATE["running"] = False
            _release_validation_timer(_tick)
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
    return index, len(files)


def _change_detail_media_index(panel_api, item, delta):
    files = _detail_media_files(item)
    if not files:
        raise Exception("当前步骤没有可切换的补充参考图")
    index = (_detail_media_index(panel_api, item) + int(delta)) % len(files)
    panel_api.set_int("detail_media_index", index)
    return index, len(files)


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
    value = str(text or "").strip()
    if not value:
        return
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
    lines = []
    for entry in list(item.get("detail_notes", []) or []):
        text = str(entry or "").strip()
        if text and text not in lines:
            lines.append(text)
    detail_text = str(item.get("detail_ja_zh") or item.get("detail_ja") or "").strip()
    if detail_text and detail_text not in lines:
        lines.append(detail_text)
    return lines


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


def _load_image(path):
    try:
        return bpy.data.images.load(path, check_existing=True)
    except Exception:
        return None


def _draw_runtime_icon_button(layout, panel_api, action, icon):
    op = layout.operator("bworkflow.module_runtime_action", text="", icon=icon)
    op.workflow_name = panel_api.workflow_name
    op.module_name = panel_api.module_name
    op.action_name = str(action)
    return op


def _draw_preview_with_side_arrows(layout, panel_api, image, fallback, prev_action, next_action, scale=PANEL_PREVIEW_SCALE):
    row = layout.row(align=True)
    left = row.column(align=True)
    left.separator(factor=5.0)
    left.scale_y = 3.0
    _draw_runtime_icon_button(left, panel_api, prev_action, "TRIA_LEFT")
    center = row.column(align=True)
    panel_api.draw_image_preview(center, image, label="", scale=scale, fallback=fallback)
    right = row.column(align=True)
    right.separator(factor=5.0)
    right.scale_y = 3.0
    _draw_runtime_icon_button(right, panel_api, next_action, "TRIA_RIGHT")


def _draw_step_indicator(layout, index, total_steps):
    row = layout.row(align=True)
    split = row.split(factor=0.35, align=True)
    split.label(text="\u6b65\u9aa4\u7f16\u53f7")
    split.label(text=str(index + 1))
    layout.label(text=f"\u5f53\u524d\u6b65\u9aa4: {index + 1} / {total_steps}", icon="INFO")


def _draw_preview(layout, item, panel_api):
    media_files = _media_files(item)
    if not media_files:
        return
    preview_box = layout.box()
    preview_box.label(text="\u53c2\u8003\u9884\u89c8", icon="IMAGE_REFERENCE")
    media_index = _media_index(panel_api, item)
    preview_image = _load_image(media_files[media_index])
    fallback = os.path.basename(media_files[media_index])
    _draw_preview_with_side_arrows(preview_box, panel_api, preview_image, fallback, "PREV_MEDIA", "NEXT_MEDIA")
    if media_files[media_index].lower().endswith(".gif"):
        preview_box.label(text="\u9762\u677f\u5185\u53ea\u663e\u793a GIF \u9996\u5e27\u9884\u89c8", icon="INFO")
        preview_box.label(text="\u70b9\u51fb\u201c\u7f6e\u9876\u53c2\u8003\u56fe\u201d\u4f1a\u5728\u72ec\u7acb\u7a97\u53e3\u64ad\u653e\u5b8c\u6574 GIF", icon="INFO")


def _draw_detail_preview(layout, item, panel_api):
    media_files = _detail_media_files(item)
    if not media_files:
        return
    detail_index = _detail_media_index(panel_api, item)
    detail_image = _load_image(media_files[detail_index])
    layout.label(text=f"\u8865\u5145\u53c2\u8003\u56fe: {detail_index + 1} / {len(media_files)}", icon="IMAGE_REFERENCE")
    layout.label(text=os.path.basename(media_files[detail_index]), icon="FILE_IMAGE")
    _draw_preview_with_side_arrows(
        layout,
        panel_api,
        detail_image,
        os.path.basename(media_files[detail_index]),
        "PREV_DETAIL_MEDIA",
        "NEXT_DETAIL_MEDIA",
    )
    panel_api.draw_button(layout, "OPEN_DETAIL_REFERENCE_WINDOW", "\u7f6e\u9876\u8865\u5145\u53c2\u8003\u56fe", icon="IMAGE_REFERENCE")


def _viewer_payload(item, panel_api, module_state, step_index, total_steps):
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
        "media_index": media_index,
        "topmost": True,
        "window_icon_path": blender_exe if os.path.isfile(blender_exe) else "",
    }
    if module_state is not None:
        module_state.set("last_reference_media_count", len(files))
        module_state.set("last_reference_media_index", media_index)
    return payload


def _detail_viewer_payload(item, panel_api, module_state, step_index, total_steps):
    files = _detail_media_files(item)
    media_index = _detail_media_index(panel_api, item)
    blender_exe = str(getattr(bpy.app, "binary_path", "") or "")
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


def draw_panel(layout, context, scene, workflow, module, panel_api, module_state):
    try:
        _payload, items, item, index, media_index = _current_item(panel_api, module_state)
    except Exception as exc:
        layout.label(text=str(exc), icon="ERROR")
        return

    media_files = _media_files(item)
    wrap_width = _panel_wrap_width(context, fallback=54)
    box = panel_api.section(layout, "ARKit \u5f62\u6001\u952e\u5de5\u4f5c\u6d41\u53c2\u8003", icon="SHAPEKEY_DATA")
    panel_api.draw_object_picker(box, "target_object", "\u76ee\u6807\u7269\u4f53")
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
    if media_files:
        media_box = panel_api.section(box, "\u53c2\u8003\u5a92\u4f53", icon="IMAGE_REFERENCE")
        panel_api.label(media_box, f"\u5f53\u524d\u5a92\u4f53: {media_index + 1} / {len(media_files)}", icon="INFO")
        panel_api.label(media_box, os.path.basename(media_files[media_index]), icon="FILE_IMAGE")
        _draw_preview(media_box, item, panel_api)
    else:
        panel_api.label(box, "\u5f53\u524d\u6b65\u9aa4\u8fd8\u6ca1\u6709\u53c2\u8003\u5a92\u4f53", icon="ERROR")
    if _draw_drawer_header(box, panel_api, "tips", "\u6ce8\u610f\u91cd\u70b9", default=False, module_state=module_state):
        tips_box = panel_api.section(box, "\u6ce8\u610f\u91cd\u70b9", icon="LIGHT")
        for note in item.get("notes", []):
            _draw_full_text_block(tips_box, note, icon="ERROR", width=wrap_width)
        for tip in item.get("tips", []):
            _draw_full_text_block(tips_box, tip, icon="CHECKMARK", width=wrap_width)
    detail_lines = _detail_lines(item)
    detail_media_files = _detail_media_files(item)
    if detail_lines or detail_media_files:
        if _draw_drawer_header(box, panel_api, "detail", "\u8865\u5145\u8bf4\u660e", default=False, module_state=module_state):
            detail_box = panel_api.section(box, "\u8865\u5145\u8bf4\u660e", icon="BOOKMARKS")
            for detail_line in detail_lines:
                _draw_full_text_block(detail_box, detail_line, icon="INFO", width=wrap_width)
            if detail_media_files:
                _draw_detail_preview(detail_box, item, panel_api)
    mix_lines = _validation_mix_lines(item)
    if mix_lines and _draw_drawer_header(box, panel_api, "mix", "\u6df7\u5408\u9a8c\u8bc1\u8bf4\u660e", default=False, module_state=module_state):
        mix_box = panel_api.section(box, "\u6df7\u5408\u9a8c\u8bc1\u8bf4\u660e", icon="MOD_MESHDEFORM")
        for line in mix_lines:
            _draw_full_text_block(mix_box, line, icon="INFO", width=wrap_width)
    actions = panel_api.row(box, align=True)
    panel_api.draw_button(actions, "FOCUS_SHAPE_KEY", "\u5b9a\u4f4d\u540c\u540d\u5f62\u6001\u952e", icon="RESTRICT_SELECT_OFF")
    panel_api.draw_button(actions, "APPLY_VALIDATION_ONE", "\u9a8c\u8bc1\u952e\u8bbe\u4e3a1", icon="PLAY")
    panel_api.draw_button(actions, "APPLY_VALIDATION_SEQUENCE", "\u9a8c\u8bc1\u952e\u9012\u589e", icon="IPO_EASE_IN_OUT")
    panel_api.draw_button(actions, "RESET_ALL", "\u91cd\u7f6e\u5168\u90e8\u5f62\u6001\u952e", icon="LOOP_BACK")
    full_actions = panel_api.row(box, align=True)
    panel_api.draw_button(full_actions, "APPLY_FULL_VALIDATION", "\u5168\u9762\u6df7\u5408\u9a8c\u8bc1(\u63a7\u5236)", icon="PLAY")
    panel_api.draw_button(full_actions, "APPLY_FULL_VALIDATION_NATIVE", "\u5168\u9762\u6df7\u5408\u9a8c\u8bc1(\u5173\u952e\u5e27)", icon="SEQ_PREVIEW")
    panel_api.draw_button(full_actions, "TOGGLE_FULL_VALIDATION_PAUSE", "\u91cd\u7f6e\u5168\u9762\u9a8c\u8bc1", icon="LOOP_BACK")
    settings = panel_api.section(box, "\u8fd0\u884c\u9009\u9879", icon="TOOL_SETTINGS")
    panel_api.draw_float_input(settings, "validation_duration_seconds", "\u9a8c\u8bc1\u952e\u9012\u589e\u79d2\u6570", default=_get_setting(module, "validation_duration_seconds", ANIMATION_DURATION_PER_KEY))
    panel_api.draw_toggle(settings, "auto_validate_on_step", "\u4e0a\u4e00\u6b65/\u4e0b\u4e00\u6b65\u540e\u81ea\u52a8\u9a8c\u8bc1\u952e\u9012\u589e", default=_get_setting(module, "auto_validate_on_step", False))
    panel_api.draw_toggle(settings, "auto_zero_others", "\u5207\u6362\u6b65\u9aa4\u65f6\u6e05\u96f6\u5176\u4ed6\u53c2\u8003\u952e", default=True)
    panel_api.draw_toggle(settings, "auto_edit_mode", "\u5e94\u7528\u540e\u81ea\u52a8\u8fdb\u5165\u7f16\u8f91\u6a21\u5f0f", default=True)
    panel_api.draw_toggle(settings, "auto_open_reference", "\u5e94\u7528\u540e\u81ea\u52a8\u6253\u5f00\u7f6e\u9876\u53c2\u8003\u56fe", default=True)
    panel_api.label(settings, "\u5f53\u524d\u5de5\u4f5c\u6d41\u53ea\u4f7f\u7528\u72ec\u7acb\u7f6e\u9876\u53c2\u8003\u7a97\uff0c\u4e0d\u518d\u56de\u9000\u5230 Blender \u5185\u90e8\u53c2\u8003\u7a97\u3002", icon="INFO")
    tools = panel_api.row(settings, align=True)
    panel_api.draw_button(tools, "CLEAR_REFERENCE_CACHE", "\u6e05\u7406GIF\u53c2\u8003\u7f13\u5b58", icon="TRASH")
    panel_api.draw_run_button(box, "\u8fd0\u884c\u6a21\u5757", icon="PLAY")
    panel_api.draw_status(box)


def _launch_reference_viewer(payload, viewer_kind="main"):
    viewer_files = _viewer_files(viewer_kind)
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
    if stale_pid and _viewer_pid_alive(stale_pid):
        return
    if stale_pid and not _viewer_pid_alive(stale_pid):
        try:
            os.remove(viewer_pid_file)
        except Exception:
            pass

    command = ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", viewer_script_file, viewer_state_file]
    try:
        with open(viewer_log_file, "w", encoding="utf-8", errors="replace") as log_handle:
            process = subprocess.Popen(command, stdout=log_handle, stderr=subprocess.STDOUT)
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
        panel_api.set_status(f"\u5df2\u5207\u6362\u53c2\u8003\u5a92\u4f53 {current_index + 1} / {total}", level="OK")
        return {"FINISHED"}
    if action == "NEXT_MEDIA":
        current_index, total = _change_media_index(panel_api, item, 1)
        panel_api.set_status(f"\u5df2\u5207\u6362\u53c2\u8003\u5a92\u4f53 {current_index + 1} / {total}", level="OK")
        return {"FINISHED"}
    if action == "PREV_DETAIL_MEDIA":
        current_index, total = _change_detail_media_index(panel_api, item, -1)
        panel_api.set_status(f"\u5df2\u5207\u6362\u8865\u5145\u53c2\u8003 {current_index + 1} / {total}", level="OK")
        return {"FINISHED"}
    if action == "NEXT_DETAIL_MEDIA":
        current_index, total = _change_detail_media_index(panel_api, item, 1)
        panel_api.set_status(f"\u5df2\u5207\u6362\u8865\u5145\u53c2\u8003 {current_index + 1} / {total}", level="OK")
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
        _start_full_validation_animation(context, scene, workflow, module, panel_api, module_state, items)
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
