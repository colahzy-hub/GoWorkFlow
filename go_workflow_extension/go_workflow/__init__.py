bl_info = {
    "name": "Go工作流 / Go Workflow",
    "author": "OpenAI Codex",
    "version": (0, 7, 2),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar > Go工作流",
    "description": "基于工作流的 N 面板筛选与自定义脚本模块工具 / Workflow panel filter and script module manager",
    "category": "3D View",
}

__version__ = (0, 7, 2)

import json
import hashlib
import inspect
import os
import re
import shutil
import subprocess
import time
import traceback
import uuid
from datetime import datetime

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup, UIList
from bpy_extras.io_utils import ExportHelper, ImportHelper

from .panel_scan import (
    discover_sidebar_panels as shared_discover_sidebar_panels,
    is_registered_panel_class as shared_is_registered_panel_class,
    iter_panel_subclasses as shared_iter_panel_subclasses,
    iter_registered_panel_classes as shared_iter_registered_panel_classes,
    panel_display_category as shared_panel_display_category,
    panel_registration_order_map as shared_panel_registration_order_map,
)
from .text_utils import (
    is_placeholder_ui_text as shared_is_placeholder_ui_text,
    is_suspicious_ui_text as shared_is_suspicious_ui_text,
    normalize_text_value as shared_normalize_text_value,
    normalize_workflow_description as shared_normalize_workflow_description,
    normalize_workflow_name as shared_normalize_workflow_name,
    unique_name_from_existing as shared_unique_name_from_existing,
)
from .workflow_counts import (
    drawer_count_for_entries as shared_drawer_count_for_entries,
    panel_count_label as shared_panel_count_label,
)
from .native_reference import BWFLOW_OT_native_reference_cleanup, BWFLOW_OT_native_reference_viewer


WORKFLOW_CATEGORY = "Go工作流"
SCHEMA_VERSION = 5
CURRENT_WORKFLOW_PRESET_KIND = "active_workflow"
PRESET_FILE_EXTENSION = ".goworkflow"
PRESET_FILE_FILTER = "*.goworkflow"
LEGACY_PRESET_FILE_FILTER = "*.bworkflow"
SCRIPT_LIBRARY_DOC_URL = "https://docs.qq.com/sheet/DRUNEZ0RFUXdtU2FS"
WORKFLOW_SWITCHER_COLUMNS = 4
AI_DOC_MAX_CHARS = 6200
AI_DOC_DESCRIPTION_MAX_CHARS = 900
UI_LABEL_MAX_CHARS = 56
UI_BUTTON_MAX_CHARS = 24
MAX_PRESET_FILE_BYTES = 50 * 1024 * 1024
MAX_GLOBAL_STATE_FILE_BYTES = 12 * 1024 * 1024
DEFAULT_WORKFLOW_NAME = "默认工作流"
DEFAULT_WORKFLOW_DESCRIPTION = "这是默认的工作流配置，可自定义面板与脚本模板"
SUPPORTED_SPACE_TYPES = (
    "VIEW_3D",
    "IMAGE_EDITOR",
    "NODE_EDITOR",
)
SPACE_LABELS = {
    "VIEW_3D": "3D视图",
    "IMAGE_EDITOR": "图像/UV编辑器",
    "NODE_EDITOR": "节点/着色器编辑器",
}
SPACE_STATE_PROP_NAMES = {
    "VIEW_3D": "bworkflow_state_view3d",
    "IMAGE_EDITOR": "bworkflow_state_image_editor",
    "NODE_EDITOR": "bworkflow_state_node_editor",
}
GLOBAL_WORKFLOW_FILENAME = "go_workflow_global_state.json"
SPECIAL_PRESET_ARKIT_52 = "arkit_52_reference"

SETTINGS_TABS = [
    ("WORKFLOWS", "工作流", "管理工作流列表与默认工作流"),
    ("PANELS", "面板库", "按工作流勾选需要显示的第三方 N 面板"),
    ("MODULES", "脚本模板", "维护工作流自定义脚本模块模板"),
    ("SCRIPTS", "脚本库", "存放可复用的脚本模板与说明"),
    ("PRESETS", "预设", "导入导出共享工作流"),
    ("GLOBAL", "全局", "配置 Go工作流 的显示与调试选项"),
]

PANEL_CLASS_CACHE_BY_SPACE = {}
PANEL_CLASS_REGISTRY_BY_SPACE = {}
PANEL_POLL_CALLERS = {}
PANEL_POLL_ORIGINALS = {}
PANEL_POLL_TARGETS = {}
PANEL_BL_ORDER_ORIGINALS = {}
UNREGISTERED_PANEL_IDS = set()
ACTIVE_ALLOWED_PANEL_IDS_BY_SPACE = {}
PANEL_GROUP_INDEX_CACHE_BY_SPACE = {}
PANEL_REGISTRY_LOOKUP_CACHE_BY_SPACE = {}
PANEL_LIBRARY_GROUPS_CACHE_BY_SPACE = {}
PANEL_LIBRARY_GROUP_ENTRY_CACHE_BY_SPACE = {}
SELECTED_PANEL_GROUPS_CACHE_BY_SPACE = {}
PANEL_ORDER_SIGNATURE_BY_SPACE = {}
PANEL_FILTER_SIGNATURE_BY_SPACE = {}
FILE_TEXT_CACHE = {}
FILE_JSON_CACHE = {}
BUILTIN_SCRIPT_LIBRARY_PAYLOAD_CACHE = {"signature": None, "payloads": []}
LOAD_HANDLER_REGISTERED = False
IS_INITIALIZING_ADDON = False
PANEL_POLL_MISSING = object()
PANEL_BL_ORDER_MISSING = object()
DEFERRED_REFRESH_INTERVALS = (0.25,)
DEFERRED_REFRESH_TOKENS = {}
DEFERRED_REFRESH_PENDING_KEYS = set()
DEFERRED_SAVE_INTERVAL = 0.35
DEFERRED_SAVE_PENDING_SCENES = set()
DOUBLE_CLICK_SECONDS = 1.0
DOUBLE_CLICK_CLICK_COUNT = 2
TRACKED_ONE_SHOT_TIMER_CALLBACKS = set()
MODULE_RUNTIME_CLEANUP_CACHE = {}
BUILTIN_DEFAULT_PANEL_PREFIXES = (
    "bl_ui.",
    "bpy_types",
)
BUILTIN_PANEL_CATEGORY_MAP = {
    "VIEW3D_PT_context_properties": "Item",
    "VIEW3D_PT_view3d_properties": "View",
    "VIEW3D_PT_view3d_lock": "View",
    "VIEW3D_PT_view3d_cursor": "View",
    "VIEW3D_PT_grease_pencil": "View",
    "VIEW3D_PT_collections": "View",
    "VIEW3D_PT_active_tool": "Tool",
    "VIEW3D_PT_tools_object_options": "Tool",
    "VIEW3D_PT_tools_object_options_transform": "Tool",
    "WORKSPACE_PT_main": "Tool",
    "WORKSPACE_PT_custom_props": "Tool",
    "WORKSPACE_PT_addons": "Tool",
    "NODE_PT_backdrop": "View",
    "NODE_PT_annotation": "View",
    "NODE_PT_active_tool": "Tool",
    "IMAGE_PT_view_display": "View",
    "IMAGE_PT_uv_cursor": "View",
    "IMAGE_PT_annotation": "View",
    "IMAGE_PT_active_tool": "Tool",
    "NODE_WORLD_PT_viewport_display": "Options",
    "NODE_PT_quality": "Options",
    "NODE_MATERIAL_PT_viewport": "Options",
    "NODE_EEVEE_MATERIAL_PT_settings": "Options",
    "NODE_DATA_PT_light": "Options",
    "NODE_DATA_PT_EEVEE_light": "Options",
    "NODE_CYCLES_WORLD_PT_settings_volume": "Options",
    "NODE_CYCLES_WORLD_PT_settings_surface": "Options",
    "NODE_CYCLES_WORLD_PT_settings": "Options",
    "NODE_CYCLES_WORLD_PT_ray_visibility": "Options",
    "NODE_CYCLES_MATERIAL_PT_settings_volume": "Options",
    "NODE_CYCLES_MATERIAL_PT_settings_surface": "Options",
    "NODE_CYCLES_MATERIAL_PT_settings": "Options",
    "NODE_CYCLES_LIGHT_PT_light": "Options",
    "NODE_CYCLES_LIGHT_PT_beam_shape": "Options",
}
# 统一占位符/乱码检测
PLACEHOLDER_UI_TEXTS = {
    "ok",
    "好的",
    "确认",
}
SUSPICIOUS_TEXT_TOKENS = (
    "?" * 4,
    "\u95bf?",
    "\u95b5?",
    "\u95c2?",
    "\u9420?",
    "\u95b9?",
    "\u7f02?",
    "\u95b8?",
    "\u6fe1?",
    "\u7f01?",
    "\u745c?",
    "\u59d2?",
    "\u95b3?",
    "\u95c1?",
    "\u6fde?",
    "\u5a34?",
    "\u95bb?",
    "\u7f01?",
    "\u95ba?",
    "\u5a62?",
    "\u95bc?",
    "\u93bc?",
    "\u9353?",
)


def _register_one_shot_timer(callback, first_interval=0.0):
    def _wrapped(callback=callback):
        try:
            return callback()
        finally:
            TRACKED_ONE_SHOT_TIMER_CALLBACKS.discard(_wrapped)

    TRACKED_ONE_SHOT_TIMER_CALLBACKS.add(_wrapped)
    bpy.app.timers.register(_wrapped, first_interval=first_interval)
    return _wrapped


def _cancel_tracked_one_shot_timers():
    callbacks = list(TRACKED_ONE_SHOT_TIMER_CALLBACKS)
    TRACKED_ONE_SHOT_TIMER_CALLBACKS.clear()
    removed = 0
    for callback in callbacks:
        try:
            bpy.app.timers.unregister(callback)
        except Exception:
            pass
        removed += 1
    return removed


def module_runtime_cleanup_cache_key(scene, workflow, module):
    return (id(scene) if scene is not None else 0, id(workflow) if workflow is not None else 0, id(module) if module is not None else 0)


def cache_module_runtime_cleanup(scene, workflow, module, cleanup_fn, module_state=None):
    if not callable(cleanup_fn):
        return False
    MODULE_RUNTIME_CLEANUP_CACHE[module_runtime_cleanup_cache_key(scene, workflow, module)] = {
        "cleanup_runtime": cleanup_fn,
        "module_state": module_state,
    }
    return True


def pop_module_runtime_cleanup(scene, workflow, module):
    return MODULE_RUNTIME_CLEANUP_CACHE.pop(module_runtime_cleanup_cache_key(scene, workflow, module), None)


def iter_panel_subclasses(base_cls):
    yield from shared_iter_panel_subclasses(base_cls)


def iter_registered_panel_classes():
    yield from shared_iter_registered_panel_classes()


def panel_registration_order_map(space_type=None):
    return shared_panel_registration_order_map(space_type=space_type)


def is_registered_panel_class(cls):
    return shared_is_registered_panel_class(cls)


def is_builtin_default_source_module(source_module):
    source_module = (source_module or "").strip()
    return bool(source_module) and source_module.startswith(BUILTIN_DEFAULT_PANEL_PREFIXES)


def is_builtin_default_panel_class(cls):
    return is_builtin_default_source_module(getattr(cls, "__module__", ""))


def builtin_default_panel_category(panel_id):
    return BUILTIN_PANEL_CATEGORY_MAP.get(panel_id, "")


def is_builtin_default_panel_name(panel_id):
    return panel_id in BUILTIN_PANEL_CATEGORY_MAP


def discover_sidebar_panels(space_type="VIEW_3D"):
    panels, registry = shared_discover_sidebar_panels(
        space_type=space_type,
        is_builtin_default_panel_class_fn=is_builtin_default_panel_class,
        is_builtin_default_panel_name_fn=is_builtin_default_panel_name,
        clean_panel_title_fn=clean_panel_title,
    )
    PANEL_CLASS_REGISTRY_BY_SPACE[space_type] = registry
    return panels


def panel_display_category(panel_id, cls, space_type=None):
    return shared_panel_display_category(
        panel_id,
        cls,
        builtin_default_panel_category_fn=builtin_default_panel_category,
        get_panel_cache_fn=get_panel_cache,
        space_type=space_type,
    )


def clean_panel_title(title, fallback):
    text = (title or "").strip()
    return normalize_text_value(text, fallback)


def is_placeholder_ui_text(text):
    return shared_is_placeholder_ui_text(text, PLACEHOLDER_UI_TEXTS)


def normalize_text_value(text, fallback=""):
    return shared_normalize_text_value(
        text,
        fallback,
        placeholder_texts=PLACEHOLDER_UI_TEXTS,
        suspicious_tokens=SUSPICIOUS_TEXT_TOKENS,
    )


def is_suspicious_ui_text(text):
    return shared_is_suspicious_ui_text(text, PLACEHOLDER_UI_TEXTS, SUSPICIOUS_TEXT_TOKENS)


def normalize_workflow_name(name, fallback):
    return shared_normalize_workflow_name(
        name,
        fallback,
        placeholder_texts=PLACEHOLDER_UI_TEXTS,
        suspicious_tokens=SUSPICIOUS_TEXT_TOKENS,
    )


def unique_workflow_name_from_existing(base_name, existing_names, fallback="新工作流"):
    return shared_unique_name_from_existing(
        base_name,
        existing_names,
        fallback=fallback,
        normalize_name_fn=lambda value, default: normalize_workflow_name(value, default),
    )


def unique_workflow_name(state, base_name, fallback="新工作流", exclude_index=None):
    existing = []
    for index, workflow in enumerate(getattr(state, "workflows", [])):
        if exclude_index is not None and index == exclude_index:
            continue
        existing.append(getattr(workflow, "name", ""))
    return unique_workflow_name_from_existing(base_name, existing, fallback=fallback)


def unique_workflow_name_across_spaces(scene, base_name, fallback="新工作流"):
    existing = []
    for space_type in SUPPORTED_SPACE_TYPES:
        state = get_state(scene=scene, space_type=space_type)
        existing.extend(getattr(workflow, "name", "") for workflow in getattr(state, "workflows", []))
    return unique_workflow_name_from_existing(base_name, existing, fallback=fallback)


def normalize_workflow_description(description, fallback):
    return shared_normalize_workflow_description(
        description,
        fallback,
        placeholder_texts=PLACEHOLDER_UI_TEXTS,
        suspicious_tokens=SUSPICIOUS_TEXT_TOKENS,
    )


def normalize_workflow_texts(state):
    if state is None:
        return False

    changed = False
    used_names = []
    for index, workflow in enumerate(state.workflows):
        if workflow.is_default:
            new_name = unique_workflow_name_from_existing(
                normalize_workflow_name(workflow.name, DEFAULT_WORKFLOW_NAME),
                used_names,
                fallback=DEFAULT_WORKFLOW_NAME,
            )
            new_description = normalize_workflow_description(workflow.description, DEFAULT_WORKFLOW_DESCRIPTION)
            if workflow.name != new_name:
                workflow.name = new_name
                changed = True
            if workflow.description != new_description:
                workflow.description = new_description
                changed = True
            used_names.append(new_name)
            continue

        fallback_name = f"工作流{index + 1}"
        new_name = unique_workflow_name_from_existing(
            normalize_workflow_name(workflow.name, fallback_name),
            used_names,
            fallback=fallback_name,
        )
        new_description = normalize_workflow_description(workflow.description, "自定义工作流，可在面板库中配置显示的面板")
        if workflow.name != new_name:
            workflow.name = new_name
            changed = True
        if workflow.description != new_description:
            workflow.description = new_description
            changed = True
        used_names.append(new_name)
    return changed


def panel_plugin_key_from_module(source_module):
    module_name = (source_module or "").strip()
    if not module_name:
        return ""
    parts = [part for part in module_name.split(".") if part]
    if not parts:
        return ""
    if parts[0] == "bl_ext" and len(parts) >= 3:
        return parts[2]
    if parts[0] in {"user_default", "extensions", "blender_org"} and len(parts) >= 2:
        return parts[1]
    return parts[0]


def panel_plugin_key(panel_id):
    cls = None
    for registry in PANEL_CLASS_REGISTRY_BY_SPACE.values():
        cls = registry.get(panel_id)
        if cls is not None:
            break
    if cls is None:
        for cache in PANEL_CLASS_CACHE_BY_SPACE.values():
            cls = cache.get(panel_id)
            if cls is not None:
                break
    if cls is None:
        return ""
    return panel_plugin_key_from_module(getattr(cls, "__module__", ""))


def infer_plugin_key_from_panel_id(panel_id):
    value = (panel_id or "").strip()
    if not value:
        return ""
    for marker in ("_PT_", "_MT_", "_UL_", "_HT_"):
        if marker in value:
            return value.split(marker, 1)[0]
    if "_" in value:
        return value.split("_", 1)[0]
    return value


def panel_plugin_label_from_key(plugin_key):
    if not plugin_key:
        return "未分类插件"
    tail = plugin_key.split(".")[-1]
    return tail.replace("_", " ").strip().title() or plugin_key


def is_usable_family_label(text):
    normalized = normalize_panel_family_text(text)
    if not normalized:
        return False
    if is_suspicious_ui_text(normalized):
        return False
    question_count = normalized.count("?")
    if question_count >= 2:
        return False
    if "\ufffd" in normalized:
        return False
    return True


def normalize_panel_family_text(text):
    value = re.sub(r"[_\-.]+", " ", str(text or "")).strip()
    value = re.sub(r"\s+", " ", value)
    return value


def panel_family_title_from_candidates(candidates):
    normalized = [
        normalize_panel_family_text(candidate)
        for candidate in candidates
        if str(candidate or "").strip() and is_usable_family_label(candidate)
    ]
    for label in normalized:
        if not label:
            continue
        prefix = re.split(r"\s*[:：|/]\s*", label, maxsplit=1)[0].strip()
        return prefix or label
    return "未分类插件"


def panel_family_key(title):
    return slugify_filename(normalize_panel_family_text(title).casefold(), "panel_family")


def panel_family_aliases(family_title):
    words = [word for word in re.split(r"\s+", normalize_panel_family_text(family_title)) if word]
    aliases = []
    plain = " ".join(words)
    if plain:
        aliases.append(plain)
    compact = re.sub(r"[^0-9a-zA-Z]+", "", plain)
    if compact:
        aliases.append(compact)
    if len(words) >= 2:
        initials = "".join(word[0] for word in words if word)
        if initials:
            aliases.append(initials)
    return unique_panel_ids(alias for alias in aliases if alias)


def strip_family_prefix(text, family_title):
    value = normalize_panel_family_text(text)
    if not value:
        return ""
    for alias in panel_family_aliases(family_title):
        alias_pattern = r"\s*".join(re.escape(char) for char in alias if char.strip())
        patterns = [
            rf"^{re.escape(alias)}\s*[:：\-_/|]\s*(.+)$",
            rf"^{alias_pattern}\s*[:：\-_/|]\s*(.+)$",
            rf"^{re.escape(alias)}\s+(.+)$",
            rf"^{alias_pattern}\s+(.+)$",
        ]
        for pattern in patterns:
            match = re.match(pattern, value, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()
    return value


def panel_family_title(state, record_or_panel_id):
    record = record_or_panel_id
    if isinstance(record_or_panel_id, str):
        record = find_registry_record(state, record_or_panel_id) if state is not None else None
    panel_id = getattr(record, "panel_id", record_or_panel_id if isinstance(record_or_panel_id, str) else "")
    plugin_title = panel_plugin_title(state, panel_id)
    plugin_key_title = panel_plugin_label_from_key(panel_plugin_key_for_record(record) or panel_plugin_key(panel_id))
    return panel_family_title_from_candidates([plugin_title, plugin_key_title, getattr(record, "category", ""), getattr(record, "title", ""), panel_id])


def panel_component_title(state, record_or_panel_id, root_id=None):
    record = record_or_panel_id
    if isinstance(record_or_panel_id, str):
        record = find_registry_record(state, record_or_panel_id) if state is not None else None
    panel_id = getattr(record, "panel_id", record_or_panel_id if isinstance(record_or_panel_id, str) else "")
    root_id = root_id or panel_tree_root_id(panel_id, space_type=getattr(state, "space_type", "VIEW_3D") if state is not None else "VIEW_3D")
    root_record = find_registry_record(state, root_id) if state is not None else None
    family_title = panel_family_title(state, record or panel_id)
    candidates = [
        getattr(root_record, "title", ""),
        getattr(record, "title", ""),
        getattr(record, "category", ""),
        panel_plugin_title(state, panel_id),
    ]
    for candidate in candidates:
        stripped = strip_family_prefix(candidate, family_title)
        if not stripped:
            continue
        if stripped.casefold() != normalize_panel_family_text(family_title).casefold():
            return stripped
    return family_title or "未分类插件"


def panel_component_key(title):
    return slugify_filename(normalize_panel_family_text(title).casefold(), "panel_component")


def panel_group_index_signature(state, space_type):
    if state is None:
        return ()
    return tuple(
        (
            record.panel_id,
            record.title,
            record.category,
            record.source_module,
            bool(record.discovered),
            panel_parent_id(record.panel_id, space_type=space_type),
        )
        for record in getattr(state, "panel_registry", [])
    )


def get_panel_group_index(state):
    if state is None:
        return {
            "title_by_panel": {},
            "ids_by_panel": {},
            "key_by_panel": {},
            "members_by_key": {},
            "record_count_by_panel": {},
        }
    space_type = getattr(state, "space_type", "VIEW_3D")
    signature = panel_group_index_signature(state, space_type)
    cached = PANEL_GROUP_INDEX_CACHE_BY_SPACE.get(space_type)
    if cached is not None and cached.get("signature") == signature:
        return cached["index"]

    raw_components = {}
    for record in getattr(state, "panel_registry", []):
        if record.panel_id == "BWFLOW_PT_workflow":
            continue
        family_title = panel_family_title(state, record)
        family_key = panel_family_key(family_title)
        root_id = panel_tree_root_id(record.panel_id, space_type=space_type)
        component_title = panel_component_title(state, record, root_id=root_id)
        component_key = panel_component_key(component_title)
        raw_key = (family_key, component_key)
        component = raw_components.setdefault(
            raw_key,
            {
                "family_key": family_key,
                "family_title": family_title,
                "component_title": component_title,
                "root_ids": [],
                "records": [],
            },
        )
        component["root_ids"].append(root_id)
        component["records"].append(record)

    compact_groups = {}
    for component in raw_components.values():
        root_ids = unique_panel_ids(component["root_ids"])
        compact_title = component["family_title"] if len(root_ids) <= 1 else component["component_title"]
        compact_key = (component["family_key"], panel_component_key(compact_title))
        target = compact_groups.setdefault(
            compact_key,
            {
                "family_title": component["family_title"],
                "component_title": compact_title,
                "root_ids": [],
                "records": [],
            },
        )
        target["root_ids"].extend(root_ids)
        target["records"].extend(component["records"])

    index = {
        "title_by_panel": {},
        "ids_by_panel": {},
        "key_by_panel": {},
        "members_by_key": {},
        "record_count_by_panel": {},
    }
    for (family_key, component_key), group in compact_groups.items():
        group_key = f"component:{family_key}:{component_key}"
        root_ids = unique_panel_ids(group["root_ids"])
        record_count = len(unique_panel_ids(record.panel_id for record in group["records"]))
        index["members_by_key"][group_key] = root_ids
        for record in group["records"]:
            index["title_by_panel"][record.panel_id] = group["component_title"]
            index["ids_by_panel"][record.panel_id] = root_ids
            index["key_by_panel"][record.panel_id] = group_key
            index["record_count_by_panel"][record.panel_id] = record_count

    PANEL_GROUP_INDEX_CACHE_BY_SPACE[space_type] = {"signature": signature, "index": index}
    return index


def panel_registry_lookup_signature(state):
    if state is None:
        return ()
    return tuple(
        (
            record.panel_id,
            bool(record.discovered),
        )
        for record in getattr(state, "panel_registry", [])
    )


def panel_library_groups_signature(state, workflow):
    if state is None:
        return ()
    return (
        panel_registry_lookup_signature(state),
        workflow_panel_membership_signature(workflow),
    )


def selected_panel_groups_signature(state, workflow):
    if state is None:
        return ()
    return (
        panel_registry_lookup_signature(state),
        workflow_panel_membership_signature(workflow),
        clamp_index(getattr(workflow, "active_panel_index", 0), len(getattr(workflow, "panels", []))) if workflow is not None else 0,
    )


def get_panel_registry_lookup(state):
    if state is None:
        return {
            "record_by_id": {},
            "records_by_drawer": {},
            "index_by_id": {},
        }

    space_type = getattr(state, "space_type", "VIEW_3D")
    signature = panel_registry_lookup_signature(state)
    cached = PANEL_REGISTRY_LOOKUP_CACHE_BY_SPACE.get(space_type)
    if cached is not None and cached.get("signature") == signature:
        return cached["lookup"]

    records = list(getattr(state, "panel_registry", []))
    record_by_id = {}
    index_by_id = {}
    records_by_drawer = {}

    for index, record in enumerate(records):
        panel_id = getattr(record, "panel_id", "")
        if not panel_id:
            continue
        record_by_id.setdefault(panel_id, record)
        index_by_id.setdefault(panel_id, index)
        drawer_id = panel_drawer_root_id(panel_id, space_type=space_type) or panel_id
        records_by_drawer.setdefault(drawer_id, []).append(record)

    lookup = {
        "record_by_id": record_by_id,
        "records_by_drawer": records_by_drawer,
        "index_by_id": index_by_id,
    }
    PANEL_REGISTRY_LOOKUP_CACHE_BY_SPACE[space_type] = {"signature": signature, "lookup": lookup}
    return lookup


def compact_component_title(state, panel_id):
    title = get_panel_group_index(state)["title_by_panel"].get(panel_id)
    if title:
        return title
    return panel_family_title(state, panel_id)


def compact_component_workflow_ids(state, panel_id):
    if not panel_id or panel_id == "BWFLOW_PT_workflow":
        return []
    ids = get_panel_group_index(state)["ids_by_panel"].get(panel_id)
    if ids:
        return list(ids)
    return panel_component_workflow_ids(state, panel_id)


def compact_component_panel_count(state, panel_id):
    if not panel_id or panel_id == "BWFLOW_PT_workflow":
        return 0
    count = get_panel_group_index(state)["record_count_by_panel"].get(panel_id, 0)
    if count:
        return count
    ids = compact_component_workflow_ids(state, panel_id)
    return max(1, len(ids))


def drawer_count_for_entries(entries):
    return shared_drawer_count_for_entries(entries, unique_panel_ids)


def panel_count_label(count):
    return shared_panel_count_label(count)


def panel_plugin_title(state, panel_id):
    record = find_registry_record(state, panel_id) if state is not None else None
    source_module = getattr(record, "source_module", "") if record is not None else ""
    plugin_key = panel_plugin_key_from_module(source_module) or panel_plugin_key(panel_id)
    return panel_plugin_label_from_key(plugin_key)


def panel_plugin_key_for_record(record):
    if record is None:
        return ""
    return panel_plugin_key_from_module(getattr(record, "source_module", "")) or panel_plugin_key(record.panel_id)


def panel_parent_id(panel_id, space_type=None):
    cache = get_panel_cache(space_type)
    registry = get_panel_registry(space_type)
    cls = registry.get(panel_id) or cache.get(panel_id)
    if cls is None:
        return ""
    return getattr(cls, "bl_parent_id", "") or ""


def panel_child_depth(panel_id, space_type=None):
    depth = 0
    seen = set()
    current_id = panel_id
    cache = get_panel_cache(space_type)
    registry = get_panel_registry(space_type)
    while current_id and current_id not in seen:
        seen.add(current_id)
        cls = registry.get(current_id) or cache.get(current_id)
        parent_id = getattr(cls, "bl_parent_id", "") if cls else ""
        if not parent_id:
            break
        depth += 1
        current_id = parent_id
    return depth


def current_space_type(context=None):
    area = getattr(context, "area", None) if context is not None else None
    area_type = getattr(area, "type", "") if area is not None else ""
    if area_type in SUPPORTED_SPACE_TYPES:
        return area_type
    return "VIEW_3D"


def space_state_prop_name(space_type):
    return SPACE_STATE_PROP_NAMES.get(space_type, SPACE_STATE_PROP_NAMES["VIEW_3D"])


def get_panel_cache(space_type=None):
    return PANEL_CLASS_CACHE_BY_SPACE.get(space_type or "VIEW_3D", {})


def get_panel_registry(space_type=None):
    return PANEL_CLASS_REGISTRY_BY_SPACE.get(space_type or "VIEW_3D", {})


def iter_registries(space_type=None):
    if space_type:
        yield space_type, get_panel_registry(space_type)
        return
    for item_space_type in iter_supported_space_types():
        yield item_space_type, get_panel_registry(item_space_type)


def resolve_panel_class(panel_id, space_type=None):
    for _space_type, registry in iter_registries(space_type):
        cls = registry.get(panel_id)
        if cls is not None:
            return cls
    return None


def resolve_panel_class_anywhere(panel_id, space_type=None):
    cls = resolve_panel_class(panel_id, space_type=space_type)
    if cls is not None:
        return cls

    for cls in iter_panel_subclasses(bpy.types.Panel):
        candidate_id = getattr(cls, "bl_idname", "") or getattr(cls, "__name__", "")
        if candidate_id != panel_id:
            continue
        if space_type and getattr(cls, "bl_space_type", None) != space_type:
            continue
        if getattr(cls, "bl_region_type", None) != "UI":
            continue
        return cls
    return None


def panel_category_key(panel_id, space_type=None):
    cls = get_panel_registry(space_type).get(panel_id) or get_panel_cache(space_type).get(panel_id)
    category = panel_display_category(panel_id, cls, space_type=space_type) if cls is not None else ""
    return category or f"__panel__:{panel_id}"


def safe_context_scene():
    try:
        return bpy.context.scene
    except Exception:
        return None


def iter_available_scenes():
    try:
        return list(bpy.data.scenes)
    except Exception:
        return []


def tag_redraw_all():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return
    for window in wm.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type in SUPPORTED_SPACE_TYPES:
                area.tag_redraw()


def clamp_index(index, size):
    if size <= 0:
        return 0
    return max(0, min(index, size - 1))


def get_state(context=None, scene=None, space_type=None):
    target_scene = scene
    if target_scene is None and context is not None:
        try:
            target_scene = context.scene
        except Exception:
            target_scene = None
    if target_scene is None:
        target_scene = safe_context_scene()
    if target_scene is None:
        return None

    target_space = space_type or current_space_type(context=context)
    prop_name = space_state_prop_name(target_space)
    if not hasattr(target_scene, prop_name):
        return None
    return getattr(target_scene, prop_name)


def iter_supported_space_types():
    return tuple(SUPPORTED_SPACE_TYPES)


def get_active_workflow(state):
    if state is None or not state.workflows:
        return None
    return state.workflows[clamp_index(state.active_workflow_index, len(state.workflows))]


def resolve_workflow_module(scene, workflow_name="", module_name=""):
    workflow_name = str(workflow_name or "").strip()
    module_name = str(module_name or "").strip()
    if scene is None:
        return None, None
    for space_type in iter_supported_space_types():
        state = get_state(scene=scene, space_type=space_type)
        if state is None:
            continue
        for workflow in state.workflows:
            if workflow_name and workflow.name != workflow_name:
                continue
            if not module_name:
                return workflow, None
            for module in workflow.modules:
                if module.name == module_name:
                    return workflow, module
    return None, None


def find_registry_record(state, panel_id):
    if state is None or not panel_id:
        return None
    return get_panel_registry_lookup(state)["record_by_id"].get(panel_id)


def is_builtin_default_panel_id(panel_id, space_type=None):
    if not panel_id or panel_id == "BWFLOW_PT_workflow":
        return False
    cls = get_panel_registry(space_type).get(panel_id) or get_panel_cache(space_type).get(panel_id)
    return cls is not None and is_builtin_default_panel_class(cls)


def is_builtin_default_panel_record(record):
    if record is None or record.panel_id == "BWFLOW_PT_workflow":
        return False
    if is_builtin_default_source_module(getattr(record, "source_module", "")):
        return True
    return is_builtin_default_panel_id(getattr(record, "panel_id", ""))


def unique_panel_ids(panel_ids):
    ordered = []
    seen = set()
    for panel_id in panel_ids:
        if panel_id and panel_id not in seen:
            ordered.append(panel_id)
            seen.add(panel_id)
    return ordered


def workflow_module_signature(module):
    if module is None:
        return ()
    return (
        getattr(module, "name", ""),
        bool(getattr(module, "enabled", False)),
        bool(getattr(module, "use_custom_panel", False)),
        bool(getattr(module, "runtime_panel_expanded", True)),
        getattr(module, "panel_title", ""),
        getattr(module, "panel_description", ""),
        getattr(module, "script_path", ""),
        getattr(module, "description", ""),
        getattr(module, "script_source", ""),
        getattr(module, "config_payload", ""),
        getattr(module, "ai_doc", ""),
    )


def workflow_signature(workflow):
    if workflow is None:
        return ()
    return (
        getattr(workflow, "name", ""),
        bool(getattr(workflow, "is_default", False)),
        getattr(workflow, "description", ""),
        getattr(workflow, "tag_filter", ""),
        tuple(unique_panel_ids(item.panel_id for item in getattr(workflow, "panels", []))),
        tuple(workflow_module_signature(module) for module in getattr(workflow, "modules", [])),
    )


def workflow_panel_membership_signature(workflow):
    if workflow is None:
        return ()
    return (
        bool(getattr(workflow, "is_default", False)),
        tuple(unique_panel_ids(item.panel_id for item in getattr(workflow, "panels", []))),
    )


def dedupe_panel_registry(state):
    if state is None:
        return False

    records = []
    seen = {}
    changed = False
    for item in state.panel_registry:
        panel_id = (item.panel_id or "").strip()
        if not panel_id:
            changed = True
            continue
        payload = {
            "panel_id": panel_id,
            "title": item.title,
            "category": item.category,
            "tags": item.tags,
            "source_module": item.source_module,
            "discovered": bool(item.discovered),
        }
        if panel_id not in seen:
            seen[panel_id] = payload
            records.append(payload)
            continue
        changed = True
        existing = seen[panel_id]
        if payload["discovered"]:
            existing["discovered"] = True
        for key in ("title", "category", "tags", "source_module"):
            if not existing.get(key) and payload.get(key):
                existing[key] = payload[key]

    if not changed:
        return False

    selected_panel_id = ""
    if state.panel_registry and 0 <= state.panel_registry_index < len(state.panel_registry):
        selected_panel_id = state.panel_registry[state.panel_registry_index].panel_id

    clear_collection(state.panel_registry)
    for payload in records:
        item = state.panel_registry.add()
        item.panel_id = payload["panel_id"]
        item.title = payload["title"]
        item.category = payload["category"]
        item.tags = payload["tags"]
        item.source_module = payload["source_module"]
        item.discovered = payload["discovered"]

    state.panel_registry_index = 0
    if selected_panel_id:
        for index, item in enumerate(state.panel_registry):
            if item.panel_id == selected_panel_id:
                state.panel_registry_index = index
                break
    return True


def dedupe_workflow_panels(workflow):
    if workflow is None:
        return False
    before = [item.panel_id for item in workflow.panels]
    after = unique_panel_ids(before)
    if before == after:
        return False
    replace_workflow_panels(workflow, after)
    return True


def dedupe_workflows(state):
    if state is None:
        return False

    changed = False
    for workflow in state.workflows:
        if dedupe_workflow_panels(workflow):
            changed = True

    signatures = set()
    keep_indexes = []
    for index, workflow in enumerate(state.workflows):
        signature = workflow_signature(workflow)
        if signature in signatures:
            changed = True
            continue
        signatures.add(signature)
        keep_indexes.append(index)

    if not changed:
        return False

    active_signature = ()
    if state.workflows and 0 <= state.active_workflow_index < len(state.workflows):
        active_signature = workflow_signature(state.workflows[state.active_workflow_index])

    snapshot = []
    for index in keep_indexes:
        workflow = state.workflows[index]
        snapshot.append(
            {
                "name": workflow.name,
                "is_default": workflow.is_default,
                "description": workflow.description,
                "tag_filter": workflow.tag_filter,
                "panels": [item.panel_id for item in workflow.panels],
                "modules": [
                    {
                        "name": module.name,
                        "enabled": module.enabled,
                        "use_custom_panel": module.use_custom_panel,
                        "runtime_panel_expanded": getattr(module, "runtime_panel_expanded", True),
                        "panel_title": module.panel_title,
                        "panel_description": module.panel_description,
                        "script_path": module.script_path,
                        "description": module.description,
                        "text_block_name": module.text_block_name,
                        "script_source": module.script_source,
                        "config_payload": getattr(module, "config_payload", ""),
                        "ai_doc": module.ai_doc,
                    }
                    for module in workflow.modules
                ],
            }
        )

    clear_collection(state.workflows)
    for workflow_data in snapshot:
        workflow = state.workflows.add()
        workflow.name = workflow_data["name"]
        workflow.is_default = workflow_data["is_default"]
        workflow.description = workflow_data["description"]
        workflow.tag_filter = workflow_data["tag_filter"]
        for panel_id in workflow_data["panels"]:
            item = workflow.panels.add()
            item.panel_id = panel_id
        for module_data in workflow_data["modules"]:
            module = workflow.modules.add()
            module.name = module_data["name"]
            module.enabled = module_data["enabled"]
            module.use_custom_panel = module_data["use_custom_panel"]
            module.runtime_panel_expanded = module_data.get("runtime_panel_expanded", True)
            module.panel_title = module_data["panel_title"]
            module.panel_description = module_data["panel_description"]
            module.script_path = module_data["script_path"]
            module.description = module_data["description"]
            module.text_block_name = module_data["text_block_name"]
            module.script_source = module_data["script_source"]
            module.config_payload = module_data.get("config_payload", "")
            module.ai_doc = module_data["ai_doc"]

    state.active_workflow_index = 0
    if active_signature:
        for index, workflow in enumerate(state.workflows):
            if workflow_signature(workflow) == active_signature:
                state.active_workflow_index = index
                break
    return True


def normalize_state_data(state):
    if state is None:
        return False
    changed = False
    before_registry_ids = [item.panel_id for item in getattr(state, "panel_registry", [])]
    before_workflow_panels = [[item.panel_id for item in workflow.panels] for workflow in getattr(state, "workflows", [])]
    purge_builtin_default_panels(state)
    after_registry_ids = [item.panel_id for item in getattr(state, "panel_registry", [])]
    after_workflow_panels = [[item.panel_id for item in workflow.panels] for workflow in getattr(state, "workflows", [])]
    if before_registry_ids != after_registry_ids or before_workflow_panels != after_workflow_panels:
        changed = True
    if dedupe_panel_registry(state):
        changed = True
    if dedupe_workflows(state):
        changed = True
    if normalize_workflow_texts(state):
        changed = True
    ensure_one_default_workflow(state)
    ensure_go_workflow_panel_entry(state)
    return changed


def referenced_panel_ids_in_state(state):
    panel_ids = set()
    if state is None:
        return panel_ids

    for workflow in state.workflows:
        for item in workflow.panels:
            panel_id = (item.panel_id or "").strip()
            if not panel_id or panel_id == "BWFLOW_PT_workflow":
                continue
            panel_ids.add(panel_id)
    return panel_ids


def validate_panel_registry_against_runtime(state, space_type=None, prune_missing_unreferenced=True):
    if state is None:
        return {
            "changed": False,
            "removed_empty": 0,
            "removed_duplicates": 0,
            "removed_stale": 0,
            "added_runtime": 0,
        }

    target_space = space_type or getattr(state, "space_type", "VIEW_3D") or "VIEW_3D"
    registry = get_panel_registry(target_space)
    cache = get_panel_cache(target_space)
    runtime_ids = set(registry.keys()) | set(cache.keys())
    referenced_ids = referenced_panel_ids_in_state(state)

    changed = False
    removed_empty = 0
    removed_duplicates = 0
    removed_stale = 0
    added_runtime = 0

    selected_panel_id = ""
    if state.panel_registry and 0 <= state.panel_registry_index < len(state.panel_registry):
        selected_panel_id = state.panel_registry[state.panel_registry_index].panel_id

    records = []
    seen = {}
    for item in state.panel_registry:
        panel_id = (item.panel_id or "").strip()
        if not panel_id or panel_id == "BWFLOW_PT_workflow":
            removed_empty += 1
            changed = True
            continue
        runtime_cls = registry.get(panel_id) or cache.get(panel_id)
        if runtime_cls is None and prune_missing_unreferenced and panel_id not in referenced_ids:
            removed_stale += 1
            changed = True
            continue

        payload = {
            "panel_id": panel_id,
            "title": clean_panel_title(getattr(runtime_cls, "bl_label", "") if runtime_cls is not None else item.title, panel_id),
            "category": panel_display_category(panel_id, runtime_cls, space_type=target_space) if runtime_cls is not None else item.category,
            "tags": item.tags,
            "source_module": getattr(runtime_cls, "__module__", "") if runtime_cls is not None else item.source_module,
            "discovered": runtime_cls is not None,
        }

        existing = seen.get(panel_id)
        if existing is None:
            seen[panel_id] = payload
            records.append(payload)
            continue

        removed_duplicates += 1
        changed = True
        if payload["discovered"]:
            existing["discovered"] = True
        for key in ("title", "category", "tags", "source_module"):
            if not existing.get(key) and payload.get(key):
                existing[key] = payload[key]

    for panel_id in sorted(runtime_ids):
        if panel_id in seen:
            continue
        runtime_cls = registry.get(panel_id) or cache.get(panel_id)
        if runtime_cls is None:
            continue
        records.append(
            {
                "panel_id": panel_id,
                "title": clean_panel_title(getattr(runtime_cls, "bl_label", panel_id), panel_id),
                "category": panel_display_category(panel_id, runtime_cls, space_type=target_space),
                "tags": "",
                "source_module": getattr(runtime_cls, "__module__", ""),
                "discovered": True,
            }
        )
        added_runtime += 1
        changed = True

    if not changed:
        return {
            "changed": False,
            "removed_empty": 0,
            "removed_duplicates": 0,
            "removed_stale": 0,
            "added_runtime": 0,
        }

    clear_collection(state.panel_registry)
    for payload in records:
        item = state.panel_registry.add()
        item.panel_id = payload["panel_id"]
        item.title = payload["title"]
        item.category = payload["category"]
        item.tags = payload["tags"]
        item.source_module = payload["source_module"]
        item.discovered = payload["discovered"]

    state.panel_registry_index = 0
    if selected_panel_id:
        for index, item in enumerate(state.panel_registry):
            if item.panel_id == selected_panel_id:
                state.panel_registry_index = index
                break

    return {
        "changed": True,
        "removed_empty": removed_empty,
        "removed_duplicates": removed_duplicates,
        "removed_stale": removed_stale,
        "added_runtime": added_runtime,
    }


def coerce_text_value(value, fallback=""):
    if value is None:
        return fallback
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return fallback


def coerce_bool_value(value, fallback=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on", "启用", "是"}:
            return True
        if normalized in {"0", "false", "no", "off", "禁用", "否"}:
            return False
    return fallback


def coerce_int_value(value, fallback=0):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except Exception:
            return fallback
    return fallback


def coerce_list_value(value):
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def coerce_dict_value(value):
    return value if isinstance(value, dict) else {}


def sanitize_module_payload(module_data):
    if not isinstance(module_data, dict):
        return None
    return {
        "name": normalize_workflow_name(coerce_text_value(module_data.get("name", "")), "默认脚本模板"),
        "enabled": coerce_bool_value(module_data.get("enabled", True), True),
        "use_custom_panel": coerce_bool_value(module_data.get("use_custom_panel", False), False),
        "runtime_panel_expanded": coerce_bool_value(module_data.get("runtime_panel_expanded", True), True),
        "panel_title": normalize_text_value(coerce_text_value(module_data.get("panel_title", "")), ""),
        "panel_description": normalize_text_value(coerce_text_value(module_data.get("panel_description", "")), ""),
        "script_path": coerce_text_value(module_data.get("script_path", "")),
        "description": normalize_text_value(coerce_text_value(module_data.get("description", "")), ""),
        "text_block_name": normalize_text_value(coerce_text_value(module_data.get("text_block_name", "")), ""),
        "script_source": coerce_text_value(module_data.get("script_source", "")),
        "config_payload": coerce_text_value(module_data.get("config_payload", "")),
        "ai_doc": coerce_text_value(module_data.get("ai_doc", "")),
    }


def sanitize_script_library_payload(item_data):
    if not isinstance(item_data, dict):
        return None
    return {
        "name": normalize_workflow_name(coerce_text_value(item_data.get("name", "")), "脚本模板"),
        "description": normalize_text_value(coerce_text_value(item_data.get("description", "")), ""),
        "tags": coerce_text_value(item_data.get("tags", "")),
        "use_custom_panel": coerce_bool_value(item_data.get("use_custom_panel", False), False),
        "panel_title": normalize_text_value(coerce_text_value(item_data.get("panel_title", "")), ""),
        "panel_description": normalize_text_value(coerce_text_value(item_data.get("panel_description", "")), ""),
        "script_path": coerce_text_value(item_data.get("script_path", "")),
        "text_block_name": normalize_text_value(coerce_text_value(item_data.get("text_block_name", "")), ""),
        "script_source": coerce_text_value(item_data.get("script_source", "")),
        "config_payload": coerce_text_value(item_data.get("config_payload", "")),
        "ai_doc": coerce_text_value(item_data.get("ai_doc", "")),
    }


def sanitize_workflow_payload(workflow_data):
    if not isinstance(workflow_data, dict):
        return None
    return {
        "name": normalize_workflow_name(coerce_text_value(workflow_data.get("name", "")), "自定义工作流"),
        "is_default": coerce_bool_value(workflow_data.get("is_default", False), False),
        "description": normalize_workflow_description(coerce_text_value(workflow_data.get("description", "")), ""),
        "tag_filter": coerce_text_value(workflow_data.get("tag_filter", "")),
        "panels": unique_panel_ids(coerce_text_value(panel_id) for panel_id in coerce_list_value(workflow_data.get("panels", []))),
        "modules": [
            module_payload
            for module_payload in (sanitize_module_payload(item) for item in coerce_list_value(workflow_data.get("modules", [])))
            if module_payload is not None
        ],
    }


def sanitize_space_payload(space_payload):
    if not isinstance(space_payload, dict):
        return None

    record_map = {}
    ordered_records = []
    for record_data in coerce_list_value(space_payload.get("panel_registry", [])):
        if not isinstance(record_data, dict):
            continue
        panel_id = coerce_text_value(record_data.get("panel_id", "")).strip()
        if not panel_id:
            continue
        payload = {
            "panel_id": panel_id,
            "title": clean_panel_title(coerce_text_value(record_data.get("title", "")), panel_id),
            "category": normalize_text_value(coerce_text_value(record_data.get("category", "")), ""),
            "tags": coerce_text_value(record_data.get("tags", "")),
            "source_module": normalize_text_value(coerce_text_value(record_data.get("source_module", "")), ""),
        }
        if panel_id not in record_map:
            record_map[panel_id] = payload
            ordered_records.append(payload)
            continue
        existing = record_map[panel_id]
        for key in ("title", "category", "tags", "source_module"):
            if not existing.get(key) and payload.get(key):
                existing[key] = payload[key]

    workflows = []
    seen_signatures = set()
    for workflow_data in coerce_list_value(space_payload.get("workflows", [])):
        payload = sanitize_workflow_payload(workflow_data)
        if payload is None:
            continue
        signature = (
            payload["name"],
            bool(payload["is_default"]),
            payload["description"],
            payload["tag_filter"],
            tuple(payload["panels"]),
            tuple(
                (
                    module_item["name"],
                    module_item["enabled"],
                    module_item["use_custom_panel"],
                    module_item["runtime_panel_expanded"],
                    module_item["panel_title"],
                    module_item["panel_description"],
                    module_item["script_path"],
                    module_item["description"],
                    module_item["text_block_name"],
                    module_item["script_source"],
                    module_item.get("config_payload", ""),
                    module_item["ai_doc"],
                )
                for module_item in payload["modules"]
            ),
        )
        if signature in seen_signatures:
            continue
        seen_signatures.add(signature)
        workflows.append(payload)

    script_library = []
    seen_library = set()
    for item_data in coerce_list_value(space_payload.get("script_library", [])):
        payload = sanitize_script_library_payload(item_data)
        if payload is None:
            continue
        signature = (
            payload["name"],
            payload["description"],
            payload["tags"],
            payload["use_custom_panel"],
            payload["panel_title"],
            payload["panel_description"],
            payload["script_path"],
            payload["text_block_name"],
            payload["script_source"],
            payload.get("config_payload", ""),
            payload["ai_doc"],
        )
        if signature in seen_library:
            continue
        seen_library.add(signature)
        script_library.append(payload)

    return {
        "label": coerce_text_value(space_payload.get("label", "")),
        "active_workflow_index": coerce_int_value(space_payload.get("active_workflow_index", 0), 0),
        "settings": dict(coerce_dict_value(space_payload.get("settings", {}))),
        "panel_registry": ordered_records,
        "script_library": script_library,
        "workflows": workflows,
    }


def sanitize_full_payload(payload):
    if not isinstance(payload, dict):
        return None
    space_payloads = payload.get("space_states")
    if not isinstance(space_payloads, dict):
        return payload

    cleaned = {
        "schema_version": payload.get("schema_version", SCHEMA_VERSION),
        "exported_at": payload.get("exported_at", ""),
        "space_states": {},
    }
    for space_type, space_payload in space_payloads.items():
        space_type = coerce_text_value(space_type, "").strip()
        if not space_type:
            continue
        cleaned_payload = sanitize_space_payload(space_payload)
        if cleaned_payload is not None:
            cleaned["space_states"][space_type] = cleaned_payload
    return cleaned


def sanitize_current_workflow_preset_payload(payload):
    if not isinstance(payload, dict):
        return None
    workflow_payload = sanitize_workflow_payload(payload.get("workflow"))
    if workflow_payload is None:
        return None
    space_payload = sanitize_space_payload(
        {
            "label": payload.get("space_label", ""),
            "active_workflow_index": 0,
            "settings": payload.get("settings", {}),
            "panel_registry": payload.get("panel_registry", []),
            "script_library": payload.get("script_library", []),
            "workflows": [workflow_payload],
        }
    )
    if space_payload is None:
        return None
    return {
        "schema_version": payload.get("schema_version", SCHEMA_VERSION),
        "preset_kind": CURRENT_WORKFLOW_PRESET_KIND,
        "exported_at": payload.get("exported_at", ""),
        "space_type": payload.get("space_type", "VIEW_3D"),
        "space_label": payload.get("space_label", ""),
        "workflow_name": workflow_payload.get("name", ""),
        "settings": space_payload.get("settings", {}),
        "panel_registry": space_payload.get("panel_registry", []),
        "script_library": space_payload.get("script_library", []),
        "workflow": workflow_payload,
    }


def is_current_workflow_preset_payload(payload):
    if not isinstance(payload, dict):
        return False
    return payload.get("preset_kind") == CURRENT_WORKFLOW_PRESET_KIND or isinstance(payload.get("workflow"), dict)


def parse_tags(text):
    tags = []
    for raw_tag in (text or "").split(","):
        tag = raw_tag.strip().lower()
        if tag:
            tags.append(tag)
    return tags


def workflow_visible_panel_ids(state, workflow):
    if state is None or workflow is None:
        return []
    if workflow.is_default:
        return list(get_panel_cache(getattr(state, "space_type", "VIEW_3D")).keys())

    space_type = getattr(state, "space_type", "VIEW_3D")
    ordered = unique_panel_ids(
        panel_drawer_root_id(item.panel_id, space_type=space_type)
        for item in workflow.panels
        if item.panel_id != "BWFLOW_PT_workflow" and not is_builtin_default_panel_id(item.panel_id, space_type=space_type)
    )
    seen = set(ordered)

    workflow_tags = set(parse_tags(workflow.tag_filter))
    if workflow_tags:
        for record in state.panel_registry:
            record_tags = set(parse_tags(record.tags))
            drawer_id = panel_drawer_root_id(record.panel_id, space_type=space_type)
            if drawer_id not in seen and workflow_tags.intersection(record_tags):
                ordered.append(drawer_id)
                seen.add(drawer_id)
    return ordered


def panel_tree_root_id(panel_id, space_type=None):
    current_id = panel_id
    seen = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        parent_id = panel_parent_id(current_id, space_type=space_type)
        if not parent_id or parent_id == current_id:
            break
        current_id = parent_id
    return current_id or panel_id


def panel_drawer_root_id(panel_id, space_type=None):
    return panel_tree_root_id(panel_id, space_type=space_type)


def panel_drawer_ids_for_records(records, space_type=None):
    return unique_panel_ids(panel_drawer_root_id(record.panel_id, space_type=space_type) for record in records if record is not None)


def panel_drawer_records(state, drawer_id):
    if state is None or not drawer_id:
        return []
    return list(get_panel_registry_lookup(state)["records_by_drawer"].get(drawer_id, []))


def panel_drawer_record(state, drawer_id):
    if state is None:
        return None
    direct = find_registry_record(state, drawer_id)
    if direct is not None:
        return direct
    records = panel_drawer_records(state, drawer_id)
    for record in records:
        if record.discovered:
            return record
    return records[0] if records else None


def panel_drawer_discovered(state, drawer_id):
    if state is None or not drawer_id:
        return False
    for record in panel_drawer_records(state, drawer_id):
        if record.discovered:
            return True
    space_type = getattr(state, "space_type", "VIEW_3D")
    return drawer_id in get_panel_registry(space_type) or drawer_id in get_panel_cache(space_type)


def panel_drawer_title(state, drawer_id):
    record = panel_drawer_record(state, drawer_id)
    if record is not None:
        return clean_panel_title(record.title, record.panel_id)
    return drawer_id or "未命名面板"


def panel_drawer_entry_for_id(state, drawer_id, selected_ids=None, active_panel_id="", fallback_index=-1):
    selected_ids = selected_ids or set()
    record = panel_drawer_record(state, drawer_id)
    discovered = panel_drawer_discovered(state, drawer_id)
    index = fallback_index
    if state is not None and record is not None:
        for item_index, item in enumerate(getattr(state, "panel_registry", [])):
            if item.panel_id == drawer_id:
                index = item_index
                break
    title = panel_drawer_title(state, drawer_id)
    if not discovered and record is not None:
        family_title = panel_family_title(state, record)
        panel_title = clean_panel_title(getattr(record, "title", ""), getattr(record, "panel_id", drawer_id))
        if family_title and panel_title and panel_title.casefold() != family_title.casefold():
            title = f"{family_title} / {panel_title}"
        elif panel_title:
            title = panel_title
    return {
        "index": index,
        "record": record,
        "panel_id": drawer_id,
        "title": title,
        "depth": 0,
        "selected": drawer_id in selected_ids,
        "discovered": discovered,
        "is_active": drawer_id == active_panel_id,
    }


def panel_descendant_ids(panel_ids, space_type=None):
    expanded = set(panel_ids)
    children_map = {}
    cache = get_panel_cache(space_type)
    registry = get_panel_registry(space_type)

    for candidate_id in list(cache.keys()) + list(registry.keys()):
        parent_id = panel_parent_id(candidate_id, space_type=space_type)
        if parent_id and parent_id != candidate_id:
            children_map.setdefault(parent_id, []).append(candidate_id)

    stack = list(panel_ids)
    while stack:
        panel_id = stack.pop()
        for child_id in children_map.get(panel_id, []):
            if child_id in expanded:
                continue
            expanded.add(child_id)
            stack.append(child_id)
    return expanded


def build_panel_library_groups(state, workflow):
    cache_key = getattr(state, "space_type", "VIEW_3D") if state is not None else "VIEW_3D"
    signature = panel_library_groups_signature(state, workflow)
    cached = PANEL_LIBRARY_GROUPS_CACHE_BY_SPACE.get(cache_key)
    if cached is not None and cached.get("signature") == signature:
        return cached.get("groups", [])

    selected_ids = {item.panel_id for item in workflow.panels} if workflow is not None else set()
    space_type = getattr(state, "space_type", "VIEW_3D") if state is not None else "VIEW_3D"
    registry = list(getattr(state, "panel_registry", []))
    drawer_root_cache = {}

    def cached_drawer_root(panel_id):
        cached = drawer_root_cache.get(panel_id)
        if cached is not None:
            return cached
        cached = panel_drawer_root_id(panel_id, space_type=space_type)
        drawer_root_cache[panel_id] = cached
        return cached

    selected_drawer_ids = {cached_drawer_root(panel_id) for panel_id in selected_ids}
    direct_record_lookup = get_panel_registry_lookup(state)["record_by_id"] if state is not None else {}
    panel_cache = get_panel_cache(space_type)
    groups = {}
    for index, record in enumerate(registry):
        if record.panel_id == "BWFLOW_PT_workflow":
            continue
        if is_builtin_default_panel_record(record):
            continue
        family_title = panel_family_title(state, record)
        family_key = panel_family_key(family_title)
        drawer_id = cached_drawer_root(record.panel_id)
        group_key = f"family:{family_key}"
        group = groups.setdefault(
            group_key,
            {
                "key": group_key,
                "title": family_title or "未分类插件",
                "records": [],
                "drawer_ids": [],
                "drawer_indexes": {},
            },
        )
        group["records"].append(record)
        group["drawer_ids"].append(drawer_id)
        group["drawer_indexes"].setdefault(drawer_id, index)

    ordered_groups = []
    for group in groups.values():
        drawer_ids = unique_panel_ids(group["drawer_ids"])
        header_items = []
        for drawer_id in drawer_ids:
            record = direct_record_lookup.get(drawer_id)
            if record is None:
                drawer_records = [
                    item
                    for item in registry
                    if item.panel_id == drawer_id or cached_drawer_root(item.panel_id) == drawer_id
                ]
                record = next((item for item in drawer_records if item.discovered), drawer_records[0] if drawer_records else None)
            discovered = bool(record and record.discovered)
            if not discovered:
                discovered = drawer_id in panel_cache or drawer_id in direct_record_lookup
            title = clean_panel_title(record.title, record.panel_id) if record is not None else (drawer_id or "未命名面板")
            header_items.append(
                {
                    "panel_id": drawer_id,
                    "index": group["drawer_indexes"].get(drawer_id, -1),
                    "record": record,
                    "title": title,
                    "selected": drawer_id in selected_drawer_ids,
                    "discovered": discovered,
                }
            )
        header_items.sort(key=lambda entry: (entry["title"].casefold(), entry["panel_id"]))
        if not header_items:
            continue

        ordered_groups.append(
            {
                "key": group["key"],
                "title": group["title"] or "未分类插件",
                "drawer_ids": [entry["panel_id"] for entry in header_items],
                "first_panel_index": header_items[0]["index"],
                "selected_count": sum(1 for entry in header_items if entry["selected"]),
                "panel_count": len(header_items),
                "tree_selected": any(entry["selected"] for entry in header_items),
            }
        )

    ordered_groups.sort(key=lambda item: (item["title"].casefold(), item["key"]))
    PANEL_LIBRARY_GROUPS_CACHE_BY_SPACE[cache_key] = {"signature": signature, "groups": ordered_groups}
    return ordered_groups


def parse_expanded_group_keys(text):
    return {item for item in (text or "").split("|") if item}


def serialize_expanded_group_keys(keys):
    return "|".join(sorted({item for item in keys if item}))


def is_group_expanded(state, group_key, selected=False):
    if state is None or not group_key:
        return True
    prop_name = "selected_group_expanded_keys" if selected else "panel_group_expanded_keys"
    keys = parse_expanded_group_keys(getattr(state, prop_name, ""))
    return group_key in keys


def set_group_expanded(state, group_key, expanded, selected=False):
    if state is None or not group_key:
        return
    prop_name = "selected_group_expanded_keys" if selected else "panel_group_expanded_keys"
    keys = parse_expanded_group_keys(getattr(state, prop_name, ""))
    if expanded:
        keys.add(group_key)
    else:
        keys.discard(group_key)
    setattr(state, prop_name, serialize_expanded_group_keys(keys))


def toggle_group_expanded(state, group_key, selected=False):
    expanded = is_group_expanded(state, group_key, selected=selected)
    set_group_expanded(state, group_key, not expanded, selected=selected)


def clear_panel_library_click_state(state):
    if state is None:
        return
    state.panel_library_last_click_index = -1
    state.panel_library_last_click_time = 0.0
    state.panel_library_last_click_target = ""

def register_click_target(state, click_target):
    now = time.monotonic()
    clicked_same = getattr(state, "panel_library_last_click_target", "") == click_target
    clicked_recently = (now - getattr(state, "panel_library_last_click_time", 0.0)) <= DOUBLE_CLICK_SECONDS
    state.panel_library_last_click_target = click_target
    state.panel_library_last_click_time = now
    return clicked_same and clicked_recently


def event_indicates_double_click(event):
    if event is None:
        return False
    for attr in ("is_double_click", "double_click"):
        if getattr(event, attr, False):
            return True
    if getattr(event, "click_count", 0) >= DOUBLE_CLICK_CLICK_COUNT:
        return True
    return getattr(event, "type", "") == "DOUBLE_CLICK" or getattr(event, "value", "") == "DOUBLE_CLICK"


def is_double_click(state, click_target, event=None):
    if event_indicates_double_click(event):
        return True
    return register_scoped_click_target(state, click_target)


def should_treat_as_double_click(state, click_target, event=None):
    return is_double_click(state, click_target, event=event)


def click_target_scope(click_target):
    prefix = str(click_target or "").split(":", 1)[0]
    if prefix in {"panel", "selected", "group"}:
        return prefix
    return "misc"


def register_scoped_click_target(state, click_target):
    scope = click_target_scope(click_target)
    last_target = getattr(state, "panel_library_last_click_target", "")
    if click_target_scope(last_target) != scope:
        clear_panel_library_click_state(state)
    return register_click_target(state, click_target)


def workflow_panel_registry_index(state, workflow, panel_id):
    if state is None or workflow is None or not panel_id:
        return -1
    space_type = getattr(state, "space_type", "VIEW_3D")
    drawer_id = panel_drawer_root_id(panel_id, space_type=space_type) or panel_id
    lookup = get_panel_registry_lookup(state)
    for candidate_id in (panel_id, drawer_id):
        index = lookup["index_by_id"].get(candidate_id)
        if index is not None:
            return index
    drawer_records = lookup["records_by_drawer"].get(drawer_id, [])
    if drawer_records:
        first_panel_id = getattr(drawer_records[0], "panel_id", "")
        return lookup["index_by_id"].get(first_panel_id, -1)
    return -1


def build_selected_panel_groups(state, workflow):
    if state is None or workflow is None:
        return []
    cache_key = getattr(state, "space_type", "VIEW_3D")
    signature = selected_panel_groups_signature(state, workflow)
    cached = SELECTED_PANEL_GROUPS_CACHE_BY_SPACE.get(cache_key)
    if cached is not None and cached.get("signature") == signature:
        return cached.get("groups", [])

    active_panel_id = ""
    if workflow.panels:
        active_index = clamp_index(workflow.active_panel_index, len(workflow.panels))
        active_panel_id = workflow.panels[active_index].panel_id

    selected_entries = []
    seen_panel_ids = set()
    space_type = getattr(state, "space_type", "VIEW_3D")
    selected_ids = {item.panel_id for item in workflow.panels}
    selected_drawer_ids = {
        panel_drawer_root_id(panel_id, space_type=space_type)
        for panel_id in selected_ids
    }
    selected_lookup_ids = selected_ids.union(selected_drawer_ids)
    for index, item in enumerate(workflow.panels):
        if item.panel_id == "BWFLOW_PT_workflow":
            continue
        panel_id = item.panel_id
        if is_builtin_default_panel_id(panel_id, space_type=space_type):
            continue
        if panel_id in seen_panel_ids:
            continue
        seen_panel_ids.add(panel_id)
        entry = panel_drawer_entry_for_id(
            state,
            panel_id,
            selected_ids=selected_lookup_ids,
            active_panel_id=active_panel_id,
            fallback_index=workflow_panel_registry_index(state, workflow, item.panel_id),
        )
        entry["workflow_index"] = index
        selected_entries.append(entry)

    groups = {}
    for entry in selected_entries:
        group_key = workflow_group_key_for_panel(state, entry["panel_id"])
        family_title = workflow_family_group_title(state, entry["panel_id"])
        group = groups.setdefault(
            group_key,
            {
                "key": group_key,
                "title": family_title or "未分类插件",
                "entries": [],
            },
        )
        group["entries"].append(entry)

    ordered_groups = []
    for group in groups.values():
        entries = sorted(group["entries"], key=lambda entry: (entry["workflow_index"], entry["title"].casefold(), entry["panel_id"]))
        group_panel_id = entries[0]["panel_id"] if entries else ""

        ordered_groups.append(
            {
                "key": group["key"],
                "title": group["title"] or "未分类插件",
                "panel_id": group_panel_id,
                "panel_count": drawer_count_for_entries(entries),
                "plugin_title": panel_count_label(drawer_count_for_entries(entries)),
                "entries": entries,
                "is_active": any(entry.get("is_active", False) for entry in entries),
            }
        )

    ordered_groups.sort(
        key=lambda item: (
            min((entry["workflow_index"] for entry in item["entries"]), default=999999),
            item["title"].casefold(),
            item["key"],
        )
    )
    SELECTED_PANEL_GROUPS_CACHE_BY_SPACE[cache_key] = {"signature": signature, "groups": ordered_groups}
    return ordered_groups


def panel_library_group_entries(state, workflow, group):
    if state is None or workflow is None or not isinstance(group, dict):
        return []
    cache_key = getattr(state, "space_type", "VIEW_3D")
    signature = (
        panel_registry_lookup_signature(state),
        workflow_panel_membership_signature(workflow),
        group.get("key", ""),
        tuple(group.get("drawer_ids", ())),
    )
    group_cache = PANEL_LIBRARY_GROUP_ENTRY_CACHE_BY_SPACE.setdefault(cache_key, {})
    cached = group_cache.get(group.get("key", ""))
    if cached is not None and cached.get("signature") == signature:
        return [dict(entry) for entry in cached.get("entries", ())]

    selected_ids = {item.panel_id for item in workflow.panels}
    space_type = getattr(state, "space_type", "VIEW_3D")
    selected_drawer_ids = {panel_drawer_root_id(panel_id, space_type=space_type) for panel_id in selected_ids}
    direct_record_lookup = get_panel_registry_lookup(state)["record_by_id"]
    panel_cache = get_panel_cache(space_type)
    registry = list(getattr(state, "panel_registry", []))
    entries = []

    for drawer_id in group.get("drawer_ids", []):
        record = direct_record_lookup.get(drawer_id)
        if record is None:
            drawer_records = panel_drawer_records(state, drawer_id)
            record = next((item for item in drawer_records if item.discovered), drawer_records[0] if drawer_records else None)
        discovered = bool(record and record.discovered)
        if not discovered:
            discovered = drawer_id in panel_cache or drawer_id in direct_record_lookup
        title = clean_panel_title(record.title, record.panel_id) if record is not None else (drawer_id or "未命名面板")
        entries.append(
            {
                "index": workflow_panel_registry_index(state, workflow, drawer_id),
                "record": record,
                "panel_id": drawer_id,
                "title": title,
                "depth": 0,
                "selected": drawer_id in selected_drawer_ids,
                "discovered": discovered,
                "is_active": False,
                "component_title": title,
            }
        )

    entries.sort(key=lambda entry: (entry["title"].casefold(), entry["panel_id"]))
    group_cache[group.get("key", "")] = {
        "signature": signature,
        "entries": [dict(entry) for entry in entries],
    }
    return entries


def workflow_toggleable_panel_ids(state, panel_ids):
    space_type = getattr(state, "space_type", "VIEW_3D") if state is not None else "VIEW_3D"
    return [
        panel_id
        for panel_id in unique_panel_ids(panel_ids)
        if panel_id and not is_builtin_default_panel_id(panel_id, space_type=space_type)
    ]


def workflow_family_drawer_ids(state, workflow, group_key):
    if state is None or workflow is None or not group_key:
        return []
    space_type = getattr(state, "space_type", "VIEW_3D")
    drawer_ids = []
    for item in getattr(workflow, "panels", []):
        panel_id = getattr(item, "panel_id", "")
        if not panel_id or panel_id == "BWFLOW_PT_workflow":
            continue
        if workflow_group_key_for_panel(state, panel_id) != group_key:
            continue
        drawer_ids.append(panel_drawer_root_id(panel_id, space_type=space_type) or panel_id)
    return workflow_toggleable_panel_ids(state, drawer_ids)


def workflow_family_order_groups(state, workflow):
    if state is None or workflow is None:
        return []

    groups = []
    group_map = {}
    for index, item in enumerate(getattr(workflow, "panels", [])):
        panel_id = item.panel_id
        if panel_id == "BWFLOW_PT_workflow":
            continue
        if is_builtin_default_panel_id(panel_id, space_type=getattr(state, "space_type", "VIEW_3D")):
            continue
        family_title = workflow_family_group_title(state, panel_id)
        group_key = f"family:{panel_family_key(family_title)}"
        group = group_map.get(group_key)
        if group is None:
            group = {
                "key": group_key,
                "title": family_title or "未分类插件",
                "ids": [],
                "first_index": index,
            }
            group_map[group_key] = group
            groups.append(group)
        if panel_id not in group["ids"]:
            group["ids"].append(panel_id)
    return groups


def panel_drawer_default_order_index(state, drawer_id, space_type=None):
    if not drawer_id:
        return 999999

    target_space = space_type or (getattr(state, "space_type", "VIEW_3D") if state is not None else "VIEW_3D")
    registration_order = panel_registration_order_map(space_type=target_space)
    for index, record in enumerate(getattr(state, "panel_registry", [])):
        record_id = getattr(record, "panel_id", "")
        if not record_id:
            continue
        if record_id == drawer_id or panel_drawer_root_id(record_id, space_type=target_space) == drawer_id:
            return index

    for index, panel_id in enumerate(get_panel_registry(target_space).keys()):
        if panel_id == drawer_id or panel_drawer_root_id(panel_id, space_type=target_space) == drawer_id:
            return registration_order.get(panel_id, 100000 + index)
    return 999999


def workflow_family_group_title(state, panel_id):
    if not panel_id or panel_id == "BWFLOW_PT_workflow":
        return "Go工作流"
    space_type = getattr(state, "space_type", "VIEW_3D") if state is not None else "VIEW_3D"
    drawer_id = panel_drawer_root_id(panel_id, space_type=space_type) or panel_id
    record = None
    if state is not None:
        record = panel_drawer_record(state, drawer_id) or find_registry_record(state, drawer_id) or find_registry_record(state, panel_id)
    return panel_family_title(state, record or drawer_id) if state is not None else drawer_id


def workflow_group_key_for_panel(state, panel_id):
    if not panel_id or panel_id == "BWFLOW_PT_workflow":
        return ""
    return f"family:{panel_family_key(workflow_family_group_title(state, panel_id))}"


def workflow_family_member_indices(state, workflow, group_key=""):
    if state is None or workflow is None:
        return []
    indices = []
    for index, item in enumerate(getattr(workflow, "panels", [])):
        panel_id = getattr(item, "panel_id", "")
        if not panel_id or panel_id == "BWFLOW_PT_workflow":
            continue
        if group_key and workflow_group_key_for_panel(state, panel_id) != group_key:
            continue
        indices.append(index)
    return indices


def workflow_auto_tag_panel_ids(state, workflow):
    if state is None or workflow is None:
        return []
    workflow_tags = set(parse_tags(workflow.tag_filter))
    if not workflow_tags:
        return []

    panel_ids = []
    space_type = getattr(state, "space_type", "VIEW_3D")
    for record in state.panel_registry:
        record_tags = set(parse_tags(record.tags))
        if workflow_tags.intersection(record_tags):
            panel_ids.append(panel_drawer_root_id(record.panel_id, space_type=space_type))
    return unique_panel_ids(panel_ids)


def workflow_explicit_panel_ids(workflow):
    if workflow is None:
        return []
    return unique_panel_ids([item.panel_id for item in workflow.panels if item.panel_id != "BWFLOW_PT_workflow"])


def workflow_missing_panel_ids(state, workflow):
    if state is None or workflow is None:
        return []

    missing_ids = []
    space_type = getattr(state, "space_type", "VIEW_3D")
    for panel_id in workflow_explicit_panel_ids(workflow):
        drawer_id = panel_drawer_root_id(panel_id, space_type=space_type)
        if panel_drawer_discovered(state, drawer_id):
            continue
        missing_ids.append(drawer_id or panel_id)
    return unique_panel_ids(missing_ids)


def workflow_missing_records(state, workflow):
    records = []
    for panel_id in workflow_missing_panel_ids(state, workflow):
        record = panel_drawer_record(state, panel_id) or find_registry_record(state, panel_id)
        records.append(record)
    return records


def workflow_effective_panel_count(state, workflow):
    return len(workflow_visible_panel_ids(state, workflow))


def append_panel_ids_to_workflow(workflow, panel_ids):
    current_ids = {item.panel_id for item in workflow.panels}
    added = 0
    for panel_id in panel_ids:
        if not panel_id or panel_id in current_ids:
            continue
        item = workflow.panels.add()
        item.panel_id = panel_id
        current_ids.add(panel_id)
        added += 1
    if workflow.panels:
        workflow.active_panel_index = clamp_index(len(workflow.panels) - 1, len(workflow.panels))
    return added


def all_available_drawer_panel_ids(state):
    if state is None:
        return []
    space_type = getattr(state, "space_type", "VIEW_3D")
    drawer_ids = []
    for record in getattr(state, "panel_registry", []):
        if record.panel_id.startswith("BWFLOW_"):
            continue
        if not record.discovered:
            continue
        if is_builtin_default_panel_record(record):
            continue
        drawer_id = panel_drawer_root_id(record.panel_id, space_type=space_type)
        if drawer_id and panel_drawer_discovered(state, drawer_id):
            drawer_ids.append(drawer_id)
    return unique_panel_ids(drawer_ids)


def create_synced_workflow_for_all_spaces(scene, name):
    workflow_name = unique_workflow_name_across_spaces(scene, name, fallback="新工作流")
    created = []
    global IS_INITIALIZING_ADDON
    previous_initializing = IS_INITIALIZING_ADDON
    IS_INITIALIZING_ADDON = True
    try:
        for space_type in iter_supported_space_types():
            rebuild_panel_cache(scene=scene, space_type=space_type)
            state = get_state(scene=scene, space_type=space_type)
            if state is None:
                continue
            workflow = state.workflows.add()
            workflow.name = workflow_name
            workflow.is_default = False
            workflow.description = ""
            if space_type != "VIEW_3D":
                append_panel_ids_to_workflow(workflow, all_available_drawer_panel_ids(state))
            state.active_workflow_index = len(state.workflows) - 1
            ensure_one_default_workflow(state)
            ensure_go_workflow_panel_entry(state)
            normalize_workflow_active_panel_index(workflow)
            created.append((space_type, workflow))
    finally:
        IS_INITIALIZING_ADDON = previous_initializing
    return created


def related_plugin_panel_ids(state, panel_id):
    if not panel_id or panel_id == "BWFLOW_PT_workflow":
        return []
    space_type = getattr(state, "space_type", "VIEW_3D") if state is not None else "VIEW_3D"
    root_id = panel_tree_root_id(panel_id, space_type=space_type)
    related = []
    cache = get_panel_cache(space_type)
    registry = get_panel_registry(space_type)
    candidate_ids = unique_panel_ids(list(cache.keys()) + list(registry.keys()) + [panel_id])
    for candidate_id in candidate_ids:
        if panel_tree_root_id(candidate_id, space_type=space_type) == root_id:
            related.append(candidate_id)
    return unique_panel_ids(related or [panel_id])


def panel_tree_workflow_ids(state, panel_id):
    if not panel_id or panel_id == "BWFLOW_PT_workflow":
        return []
    space_type = getattr(state, "space_type", "VIEW_3D") if state is not None else "VIEW_3D"
    root_id = panel_tree_root_id(panel_id, space_type=space_type)
    return [root_id or panel_id]


def panel_drawer_workflow_ids(state, panel_id):
    if not panel_id or panel_id == "BWFLOW_PT_workflow":
        return []
    space_type = getattr(state, "space_type", "VIEW_3D") if state is not None else "VIEW_3D"
    return [panel_drawer_root_id(panel_id, space_type=space_type) or panel_id]


def panel_component_workflow_ids(state, panel_id):
    if not panel_id or panel_id == "BWFLOW_PT_workflow":
        return []
    record = find_registry_record(state, panel_id) if state is not None else None
    if record is None:
        return panel_tree_workflow_ids(state, panel_id)

    space_type = getattr(state, "space_type", "VIEW_3D") if state is not None else "VIEW_3D"
    family = panel_family_title(state, record)
    component = panel_component_title(state, record, root_id=panel_tree_root_id(panel_id, space_type=space_type))
    family_key = panel_family_key(family)
    component_key = panel_component_key(component)
    root_ids = []
    for candidate in getattr(state, "panel_registry", []):
        if candidate.panel_id == "BWFLOW_PT_workflow":
            continue
        candidate_root = panel_tree_root_id(candidate.panel_id, space_type=space_type)
        if panel_family_key(panel_family_title(state, candidate)) != family_key:
            continue
        if panel_component_key(panel_component_title(state, candidate, root_id=candidate_root)) != component_key:
            continue
        root_ids.append(candidate_root)
    return unique_panel_ids(root_ids or panel_tree_workflow_ids(state, panel_id))


def workflow_panel_order_ids(workflow):
    if workflow is None:
        return []
    return unique_panel_ids([item.panel_id for item in workflow.panels])


def workflow_editable_panel_indices(workflow):
    if workflow is None:
        return []
    return [index for index, item in enumerate(workflow.panels) if item.panel_id != "BWFLOW_PT_workflow"]


def editable_workflow_panel_count(workflow):
    return len(workflow_editable_panel_indices(workflow))


def workflow_panel_group_key(state, panel_id):
    if not panel_id or panel_id == "BWFLOW_PT_workflow":
        return "fixed:go_workflow"
    space_type = getattr(state, "space_type", "VIEW_3D") if state is not None else "VIEW_3D"
    drawer_id = panel_drawer_root_id(panel_id, space_type=space_type)
    if drawer_id and drawer_id != panel_id:
        panel_id = drawer_id
    record = find_registry_record(state, panel_id) if state is not None else None
    if record is None:
        return f"panel:{panel_id}"
    return "drawer:{family}:{drawer}".format(
        family=panel_family_key(panel_family_title(state, record)),
        drawer=panel_component_key(panel_drawer_title(state, panel_id)),
    )


def workflow_panel_group_title(state, panel_id):
    space_type = getattr(state, "space_type", "VIEW_3D") if state is not None else "VIEW_3D"
    drawer_id = panel_drawer_root_id(panel_id, space_type=space_type)
    if drawer_id:
        panel_id = drawer_id
    record = find_registry_record(state, panel_id) if state is not None else None
    if record is None:
        return panel_id or "缺失面板"
    family_title = panel_family_title(state, record)
    drawer_title = panel_drawer_title(state, panel_id)
    if panel_component_key(family_title) == panel_component_key(drawer_title):
        return family_title
    return f"{family_title} / {drawer_title}"


def workflow_order_groups(state, workflow):
    groups = []
    group_map = {}
    for index, item in enumerate(getattr(workflow, "panels", [])):
        panel_id = item.panel_id
        if panel_id == "BWFLOW_PT_workflow":
            continue
        key = workflow_panel_group_key(state, panel_id)
        group = group_map.get(key)
        if group is None:
            group = {
                "key": key,
                "title": workflow_panel_group_title(state, panel_id),
                "ids": [],
                "first_index": index,
            }
            group_map[key] = group
            groups.append(group)
        drawer_id = panel_drawer_root_id(panel_id, space_type=getattr(state, "space_type", "VIEW_3D") if state is not None else "VIEW_3D")
        if drawer_id not in group["ids"]:
            group["ids"].append(drawer_id)
    return groups


def active_workflow_group_key(state, workflow):
    if workflow is None or not workflow.panels:
        return ""
    active_index = clamp_index(workflow.active_panel_index, len(workflow.panels))
    panel_id = workflow.panels[active_index].panel_id
    if panel_id == "BWFLOW_PT_workflow":
        normalize_workflow_active_panel_index(workflow)
        active_index = clamp_index(workflow.active_panel_index, len(workflow.panels))
        panel_id = workflow.panels[active_index].panel_id if workflow.panels else ""
    return workflow_panel_group_key(state, panel_id) if panel_id else ""


def active_workflow_family_group_key(state, workflow):
    if workflow is None or not workflow.panels:
        return ""
    active_index = clamp_index(workflow.active_panel_index, len(workflow.panels))
    panel_id = workflow.panels[active_index].panel_id
    if panel_id == "BWFLOW_PT_workflow":
        normalize_workflow_active_panel_index(workflow)
        active_index = clamp_index(workflow.active_panel_index, len(workflow.panels))
        panel_id = workflow.panels[active_index].panel_id if workflow.panels else ""
    return workflow_group_key_for_panel(state, panel_id) if panel_id else ""


def set_active_workflow_panel_by_id(workflow, panel_id):
    for index, item in enumerate(getattr(workflow, "panels", [])):
        if item.panel_id == panel_id:
            workflow.active_panel_index = index
            return True
    normalize_workflow_active_panel_index(workflow)
    return False


def replace_workflow_groups(workflow, groups, active_panel_id=""):
    ordered_ids = []
    for group in groups:
        ordered_ids.extend(group.get("ids", []))
    replace_workflow_panels(workflow, ordered_ids)
    if active_panel_id:
        set_active_workflow_panel_by_id(workflow, active_panel_id)


def normalize_workflow_active_panel_index(workflow):
    if workflow is None or not workflow.panels:
        return
    current_index = clamp_index(workflow.active_panel_index, len(workflow.panels))
    if workflow.panels[current_index].panel_id != "BWFLOW_PT_workflow":
        workflow.active_panel_index = current_index
        return

    for index, item in enumerate(workflow.panels):
        if item.panel_id != "BWFLOW_PT_workflow":
            workflow.active_panel_index = index
            return

    workflow.active_panel_index = 0


def replace_workflow_panels(workflow, panel_ids):
    clear_collection(workflow.panels)
    ordered_ids = unique_panel_ids(panel_ids)
    if workflow is not None and not workflow.is_default and "BWFLOW_PT_workflow" not in ordered_ids:
        ordered_ids.insert(0, "BWFLOW_PT_workflow")
    for panel_id in ordered_ids:
        item = workflow.panels.add()
        item.panel_id = panel_id
    workflow.active_panel_index = clamp_index(workflow.active_panel_index, len(workflow.panels))
    normalize_workflow_active_panel_index(workflow)


def ensure_go_workflow_panel_entry(state):
    if state is None:
        return
    for workflow in state.workflows:
        if workflow.is_default:
            continue
        has_entry = any(item.panel_id == "BWFLOW_PT_workflow" for item in workflow.panels)
        if not has_entry:
            item = workflow.panels.add()
            item.panel_id = "BWFLOW_PT_workflow"
        normalize_workflow_active_panel_index(workflow)


def purge_builtin_default_panels(state):
    if state is None:
        return

    removed_ids = set()
    for index in range(len(state.panel_registry) - 1, -1, -1):
        record = state.panel_registry[index]
        if not is_builtin_default_panel_record(record):
            continue
        removed_ids.add(record.panel_id)
        state.panel_registry.remove(index)

    if not removed_ids:
        return

    for workflow in state.workflows:
        kept_ids = []
        for item in workflow.panels:
            panel_id = item.panel_id
            if panel_id in removed_ids or is_builtin_default_panel_id(panel_id):
                continue
            kept_ids.append(panel_id)
        replace_workflow_panels(workflow, kept_ids)


def expand_panel_family(panel_ids, space_type=None):
    expanded = set(panel_ids)
    cache = get_panel_cache(space_type)
    changed = True
    while changed:
        changed = False
        for panel_id, cls in cache.items():
            parent_id = getattr(cls, "bl_parent_id", "")
            if panel_id in expanded and parent_id and parent_id not in expanded:
                expanded.add(parent_id)
                changed = True
    return expanded


def compute_allowed_panel_ids(state):
    workflow = get_active_workflow(state)
    if workflow is None or workflow.is_default:
        return None
    visible_ids = workflow_visible_panel_ids(state, workflow)
    space_type = getattr(state, "space_type", "VIEW_3D")
    allowed_ids = expand_panel_family(panel_descendant_ids(visible_ids, space_type=space_type), space_type=space_type)
    builtin_ids = {
        panel_id
        for panel_id, cls in get_panel_registry(space_type).items()
        if is_builtin_default_panel_id(panel_id, space_type=space_type)
        and getattr(cls, "bl_region_type", None) == "UI"
    }
    return allowed_ids.union(builtin_ids)


def panel_filter_signature(state):
    workflow = get_active_workflow(state) if state is not None else None
    if workflow is None or workflow.is_default:
        return None
    allowed_ids = compute_allowed_panel_ids(state)
    if allowed_ids is None:
        return None
    ordered_ids = workflow_ordered_panel_ids(state, workflow)
    return (
        tuple(sorted(allowed_ids)),
        tuple(ordered_ids),
    )


def workflow_ordered_panel_ids(state, workflow):
    if workflow is None or workflow.is_default:
        return list(get_panel_registry(getattr(state, "space_type", "VIEW_3D")).keys())

    space_type = getattr(state, "space_type", "VIEW_3D")
    explicit_workflow_ids = [
        panel_id
        for panel_id in workflow_panel_order_ids(workflow)
        if panel_id != "BWFLOW_PT_workflow" and not is_builtin_default_panel_id(panel_id, space_type=space_type)
    ]
    panel_order_ids = unique_panel_ids(
        panel_drawer_root_id(panel_id, space_type=space_type)
        for panel_id in explicit_workflow_ids
    )
    explicit_ids = [panel_id for panel_id in panel_order_ids if panel_id != "BWFLOW_PT_workflow"]
    auto_ids = [panel_id for panel_id in workflow_auto_tag_panel_ids(state, workflow) if panel_id not in explicit_ids]
    visible_ids = explicit_ids + auto_ids
    allowed_ids = expand_panel_family(panel_descendant_ids(visible_ids, space_type=space_type), space_type=space_type)

    registry = get_panel_registry(space_type)
    explicit_member_ids = []
    for panel_id in explicit_workflow_ids:
        drawer_id = panel_drawer_root_id(panel_id, space_type=space_type)
        if drawer_id not in visible_ids:
            continue
        if panel_id in allowed_ids and panel_id in registry:
            explicit_member_ids.append(panel_id)
        elif drawer_id in allowed_ids and drawer_id in registry:
            explicit_member_ids.append(drawer_id)

    # 先尊重右侧“当前勾选”的抽屉顺序，再把每个抽屉展开到真实注册的子面板。
    ordered_drawer_member_ids = []
    for drawer_id in visible_ids:
        preferred_members = [
            panel_id
            for panel_id in explicit_member_ids
            if panel_drawer_root_id(panel_id, space_type=space_type) == drawer_id
        ]
        members = [
            panel_id
            for panel_id in registry.keys()
            if panel_id in allowed_ids and panel_drawer_root_id(panel_id, space_type=space_type) == drawer_id
        ]
        members = unique_panel_ids(preferred_members + members)
        if drawer_id in registry and drawer_id not in members:
            members.insert(0, drawer_id)
        ordered_drawer_member_ids.extend(members)

    trailing_ids = [panel_id for panel_id in registry.keys() if panel_id in allowed_ids and panel_id not in visible_ids]
    preferred_ids = [panel_id for panel_id in ordered_drawer_member_ids if panel_id in registry]
    preferred_ids.extend(panel_id for panel_id in trailing_ids if panel_id not in preferred_ids)
    return ordered_panel_ids_for_register(preferred_ids, space_type=space_type)


def clear_panel_filter():
    restore_default_n_panel_state(disable_filters=True)


def addon_module_name():
    return __package__ or __name__


def addon_module_candidates():
    candidates = []
    for value in (addon_module_name(), __name__, __package__, "go_workflow"):
        if value and value not in candidates:
            candidates.append(value)
    try:
        for module_name in bpy.context.preferences.addons.keys():
            if module_name not in candidates and module_name.endswith("go_workflow"):
                candidates.append(module_name)
    except Exception:
        pass
    return candidates


def panel_depth(panel_id, space_type=None):
    depth = 0
    seen = set()
    current_id = panel_id
    registry = get_panel_registry(space_type)
    while current_id and current_id not in seen:
        seen.add(current_id)
        cls = registry.get(current_id)
        parent_id = getattr(cls, "bl_parent_id", "") if cls else ""
        if not parent_id:
            break
        depth += 1
        current_id = parent_id
    return depth


def sorted_panel_ids_for_unregister(panel_ids, space_type=None):
    return sorted(panel_ids, key=lambda panel_id: panel_depth(panel_id, space_type=space_type), reverse=True)


def sorted_panel_ids_for_register(panel_ids, space_type=None):
    return sorted(panel_ids, key=lambda panel_id: panel_depth(panel_id, space_type=space_type))


def _safe_unregister_panel_class(cls):
    if cls is None:
        return False
    original = getattr(cls, "unregister", None)
    try:
        if callable(original):
            setattr(cls, "unregister", lambda: None)
        bpy.utils.unregister_class(cls)
        return True
    except Exception:
        return False
    finally:
        if callable(original):
            try:
                setattr(cls, "unregister", original)
            except Exception:
                pass


def _safe_register_panel_class(cls):
    if cls is None:
        return False
    original = getattr(cls, "register", None)
    try:
        if callable(original):
            setattr(cls, "register", lambda: None)
        bpy.utils.register_class(cls)
        return True
    except TypeError:
        try:
            bpy.utils.register_class(cls, False)
            return True
        except Exception:
            return False
    except Exception:
        return False
    finally:
        if callable(original):
            try:
                setattr(cls, "register", original)
            except Exception:
                pass


def panel_parent_id_anywhere(panel_id, space_type=None):
    cls = resolve_panel_class_anywhere(panel_id, space_type=space_type)
    parent_id = getattr(cls, "bl_parent_id", "") if cls is not None else ""
    return parent_id if parent_id and parent_id != panel_id else ""


def ordered_panel_ids_for_register(panel_ids, space_type=None):
    wanted = set(panel_ids)
    ordered = []
    visiting = set()
    visited = set()

    def visit(panel_id):
        if panel_id in visited or panel_id in visiting:
            return
        visiting.add(panel_id)
        parent_id = panel_parent_id_anywhere(panel_id, space_type=space_type)
        if parent_id in wanted:
            visit(parent_id)
        visiting.remove(panel_id)
        visited.add(panel_id)
        ordered.append(panel_id)

    for panel_id in panel_ids:
        visit(panel_id)
    return ordered


def call_original_panel_poll(panel_id, cls, context):
    original = PANEL_POLL_ORIGINALS.get(panel_id, PANEL_POLL_MISSING)
    if original is PANEL_POLL_MISSING:
        return True
    try:
        if isinstance(original, classmethod):
            return bool(original.__func__(cls, context))
        if isinstance(original, staticmethod):
            return bool(original.__func__(context))
        return bool(original(cls, context))
    except TypeError:
        try:
            bound = getattr(original, "__get__", None)
            if callable(bound):
                candidate = bound(cls, type(cls))
                if callable(candidate):
                    try:
                        return bool(candidate(context))
                    except TypeError:
                        pass
            return bool(original(context))
        except Exception:
            traceback.print_exc()
            return False
    except Exception:
        traceback.print_exc()
        return False


def bworkflow_panel_poll(cls, context):
    panel_id = getattr(cls, "_bworkflow_poll_panel_id", "") or getattr(cls, "bl_idname", "") or getattr(cls, "__name__", "")
    allowed_ids = ACTIVE_ALLOWED_PANEL_IDS_BY_SPACE.get(getattr(cls, "bl_space_type", "VIEW_3D"))
    if allowed_ids is not None and panel_id not in allowed_ids:
        return False
    return call_original_panel_poll(panel_id, cls, context)


def make_panel_poll_wrapper(_panel_id):
    return classmethod(bworkflow_panel_poll)


def default_panel_poll(cls, _context):
    return True


def install_panel_poll_overrides():
    for registry in PANEL_CLASS_REGISTRY_BY_SPACE.values():
        for panel_id, cls in registry.items():
            target_cls = PANEL_POLL_TARGETS.get(panel_id)
            if target_cls is not None and target_cls is not cls and panel_id in PANEL_POLL_CALLERS:
                uninstall_panel_poll_overrides([panel_id])
            if panel_id not in PANEL_POLL_CALLERS:
                original_attr = inspect.getattr_static(cls, "poll", PANEL_POLL_MISSING)
                PANEL_POLL_ORIGINALS[panel_id] = original_attr
                wrapped = make_panel_poll_wrapper(panel_id)
                setattr(cls, "_bworkflow_poll_panel_id", panel_id)
                setattr(cls, "poll", wrapped)
                PANEL_POLL_CALLERS[panel_id] = wrapped
                PANEL_POLL_TARGETS[panel_id] = cls


def uninstall_panel_poll_overrides(panel_ids=None):
    target_ids = list(PANEL_POLL_CALLERS.keys()) if panel_ids is None else list(panel_ids)
    for panel_id in target_ids:
        cls = PANEL_POLL_TARGETS.get(panel_id)
        if cls is None or panel_id not in PANEL_POLL_CALLERS:
            continue
        original = PANEL_POLL_ORIGINALS.get(panel_id, PANEL_POLL_MISSING)
        if original is PANEL_POLL_MISSING:
            try:
                delattr(cls, "poll")
            except Exception:
                setattr(cls, "poll", classmethod(default_panel_poll))
        else:
            setattr(cls, "poll", original)
        try:
            delattr(cls, "_bworkflow_poll_panel_id")
        except Exception:
            pass
        PANEL_POLL_CALLERS.pop(panel_id, None)
        PANEL_POLL_ORIGINALS.pop(panel_id, None)
        PANEL_POLL_TARGETS.pop(panel_id, None)


def apply_panel_order_overrides(ordered_panel_ids, space_type=None):
    registries = [(space_type, get_panel_registry(space_type))] if space_type else list(iter_registries())
    for target_space, registry in registries:
        ordered_signature = tuple(panel_id for panel_id in ordered_panel_ids if panel_id in registry)
        ordered_map = {panel_id: index for index, panel_id in enumerate(ordered_signature)}
        if not ordered_signature:
            continue
        for panel_id in ordered_signature:
            cls = registry.get(panel_id)
            if cls is None:
                continue
            if panel_id not in PANEL_BL_ORDER_ORIGINALS:
                PANEL_BL_ORDER_ORIGINALS[panel_id] = cls.__dict__.get("bl_order", PANEL_BL_ORDER_MISSING)
            cls.bl_order = ordered_map[panel_id] + 100
        if ordered_signature and PANEL_ORDER_SIGNATURE_BY_SPACE.get(target_space) != ordered_signature:
            reorder_registered_panels(ordered_signature, space_type=target_space)
        PANEL_ORDER_SIGNATURE_BY_SPACE[target_space] = ordered_signature
    enforce_go_workflow_panel_order()


def enforce_go_workflow_panel_order():
    pinned = (
        BWFLOW_PT_workflow,
        BWFLOW_PT_workflow_image_editor,
        BWFLOW_PT_workflow_node_editor,
    )
    for cls in pinned:
        try:
            cls.bl_order = -1000
        except Exception:
            pass


def clear_panel_order_overrides(panel_ids=None, space_type=None):
    if space_type is None:
        PANEL_ORDER_SIGNATURE_BY_SPACE.clear()
        PANEL_FILTER_SIGNATURE_BY_SPACE.clear()
    else:
        PANEL_ORDER_SIGNATURE_BY_SPACE.pop(space_type, None)
        PANEL_FILTER_SIGNATURE_BY_SPACE.pop(space_type, None)
    target_ids = list(PANEL_BL_ORDER_ORIGINALS.keys()) if panel_ids is None else list(panel_ids)
    if space_type is not None and panel_ids is None:
        target_ids = [
            panel_id
            for panel_id in target_ids
            if panel_id in get_panel_registry(space_type) or panel_id in get_panel_cache(space_type)
        ]
    for panel_id in target_ids:
        original = PANEL_BL_ORDER_ORIGINALS.get(panel_id, PANEL_BL_ORDER_MISSING)
        cls = resolve_panel_class(panel_id, space_type=space_type)
        if cls is None:
            continue
        if original is PANEL_BL_ORDER_MISSING:
            try:
                delattr(cls, "bl_order")
            except Exception:
                pass
        else:
            cls.bl_order = original
        PANEL_BL_ORDER_ORIGINALS.pop(panel_id, None)


def restore_panel_registry_order(panel_ids=None, space_type=None):
    if panel_ids is None:
        panel_ids = list(PANEL_BL_ORDER_ORIGINALS.keys())
    if not panel_ids:
        return
    registries = [(space_type, get_panel_registry(space_type))] if space_type else list(iter_registries())
    for target_space, registry in registries:
        if not registry:
            continue
        ordered_ids = [panel_id for panel_id in panel_ids if panel_id in registry]
        if not ordered_ids:
            continue
        if PANEL_ORDER_SIGNATURE_BY_SPACE.get(target_space) != tuple(ordered_ids):
            reorder_registered_panels(ordered_ids, space_type=target_space)
        for panel_id in ordered_ids:
            cls = registry.get(panel_id)
            if cls is None:
                continue
            original = PANEL_BL_ORDER_ORIGINALS.get(panel_id, PANEL_BL_ORDER_MISSING)
            if original is PANEL_BL_ORDER_MISSING:
                try:
                    delattr(cls, "bl_order")
                except Exception:
                    pass
            else:
                cls.bl_order = original


def poll_override_panel_ids_for_space(space_type):
    panel_ids = []
    for panel_id, cls in list(PANEL_POLL_TARGETS.items()):
        if getattr(cls, "bl_space_type", "VIEW_3D") == space_type:
            panel_ids.append(panel_id)
    return panel_ids


def has_runtime_unregistered_panels(space_type=None):
    if not UNREGISTERED_PANEL_IDS:
        return False
    if space_type is None:
        return True
    for panel_id in UNREGISTERED_PANEL_IDS:
        cls = resolve_panel_class_anywhere(panel_id, space_type=space_type)
        if cls is not None and getattr(cls, "bl_space_type", None) == space_type:
            return True
    return False


def restore_space_default_n_panel_state(scene=None, space_type="VIEW_3D", disable_filters=True, sync_registry_after_restore=True):
    if disable_filters:
        ACTIVE_ALLOWED_PANEL_IDS_BY_SPACE.pop(space_type, None)
    PANEL_ORDER_SIGNATURE_BY_SPACE.pop(space_type, None)
    PANEL_FILTER_SIGNATURE_BY_SPACE.pop(space_type, None)
    PANEL_LIBRARY_GROUPS_CACHE_BY_SPACE.pop(space_type, None)
    PANEL_LIBRARY_GROUP_ENTRY_CACHE_BY_SPACE.pop(space_type, None)
    SELECTED_PANEL_GROUPS_CACHE_BY_SPACE.pop(space_type, None)
    try:
        restore_panel_registry_order(space_type=space_type)
    except Exception:
        traceback.print_exc()
    try:
        uninstall_panel_poll_overrides(poll_override_panel_ids_for_space(space_type))
    except Exception:
        traceback.print_exc()
    try:
        restore_unregistered_panels(space_type=space_type)
    except Exception:
        traceback.print_exc()
    try:
        clear_panel_order_overrides(space_type=space_type)
    except Exception:
        traceback.print_exc()
    PANEL_GROUP_INDEX_CACHE_BY_SPACE.pop(space_type, None)
    PANEL_LIBRARY_GROUPS_CACHE_BY_SPACE.pop(space_type, None)
    PANEL_LIBRARY_GROUP_ENTRY_CACHE_BY_SPACE.pop(space_type, None)
    SELECTED_PANEL_GROUPS_CACHE_BY_SPACE.pop(space_type, None)
    target_scene = scene or safe_context_scene()
    if sync_registry_after_restore and target_scene is not None:
        try:
            PANEL_CLASS_CACHE_BY_SPACE[space_type] = discover_sidebar_panels(space_type)
            sync_registry(target_scene, space_type=space_type)
        except Exception:
            traceback.print_exc()
    tag_redraw_all()


def switch_state_to_default_workflow(state):
    if state is None or not getattr(state, "workflows", None):
        return False
    ensure_one_default_workflow(state)
    for index, workflow in enumerate(state.workflows):
        if workflow.is_default:
            if state.active_workflow_index != index:
                state.active_workflow_index = index
                return True
            return False
    return False


def switch_all_states_to_default_workflow(scene=None):
    target_scene = scene or safe_context_scene()
    if target_scene is None:
        return False
    changed = False
    for space_type in iter_supported_space_types():
        state = get_state(scene=target_scene, space_type=space_type)
        if switch_state_to_default_workflow(state):
            changed = True
    return changed


def restore_default_n_panel_state(scene=None, disable_filters=True, switch_workflow=True, sync_registry_after_restore=True):
    if switch_workflow:
        switch_all_states_to_default_workflow(scene=scene)
    if disable_filters:
        ACTIVE_ALLOWED_PANEL_IDS_BY_SPACE.clear()
    PANEL_GROUP_INDEX_CACHE_BY_SPACE.clear()
    PANEL_ORDER_SIGNATURE_BY_SPACE.clear()
    PANEL_FILTER_SIGNATURE_BY_SPACE.clear()
    PANEL_LIBRARY_GROUPS_CACHE_BY_SPACE.clear()
    PANEL_LIBRARY_GROUP_ENTRY_CACHE_BY_SPACE.clear()
    SELECTED_PANEL_GROUPS_CACHE_BY_SPACE.clear()
    try:
        restore_panel_registry_order()
    except Exception:
        traceback.print_exc()
    for space_type in iter_supported_space_types():
        try:
            restore_space_default_n_panel_state(
                scene=scene,
                space_type=space_type,
                disable_filters=disable_filters,
                sync_registry_after_restore=sync_registry_after_restore,
            )
        except Exception:
            traceback.print_exc()
    tag_redraw_all()


def restore_unregistered_panels(panel_ids=None, space_type=None):
    if panel_ids is None:
        ids_to_restore = set(UNREGISTERED_PANEL_IDS)
        for cls in iter_panel_subclasses(bpy.types.Panel):
            panel_id = getattr(cls, "bl_idname", "") or getattr(cls, "__name__", "")
            if not panel_id or panel_id.startswith("BWFLOW_"):
                continue
            if is_builtin_default_panel_class(cls):
                continue
            if space_type and getattr(cls, "bl_space_type", None) != space_type:
                continue
            if getattr(cls, "bl_region_type", None) != "UI":
                continue
            if not hasattr(cls, "draw"):
                continue
            ids_to_restore.add(panel_id)
    else:
        ids_to_restore = set(panel_ids)
    unresolved_ids = set()
    for panel_id in list(ids_to_restore):
        if resolve_panel_class_anywhere(panel_id, space_type=space_type) is None:
            unresolved_ids.add(panel_id)
    ids_to_restore.difference_update(unresolved_ids)
    UNREGISTERED_PANEL_IDS.difference_update(unresolved_ids)

    pending_ids = ordered_panel_ids_for_register(ids_to_restore, space_type=space_type)
    for _attempt in range(max(1, len(pending_ids))):
        if not pending_ids:
            break
        still_pending = []
        made_progress = False
        for panel_id in pending_ids:
            cls = resolve_panel_class_anywhere(panel_id, space_type=space_type)
            if cls is None:
                UNREGISTERED_PANEL_IDS.discard(panel_id)
                made_progress = True
                continue
            try:
                if _safe_register_panel_class(cls):
                    UNREGISTERED_PANEL_IDS.discard(panel_id)
                    PANEL_ORDER_SIGNATURE_BY_SPACE.pop(getattr(cls, "bl_space_type", space_type or "VIEW_3D"), None)
                    made_progress = True
                else:
                    still_pending.append(panel_id)
            except ValueError:
                UNREGISTERED_PANEL_IDS.discard(panel_id)
                made_progress = True
            except RuntimeError:
                still_pending.append(panel_id)
            except Exception:
                still_pending.append(panel_id)
        if still_pending == pending_ids and not made_progress:
            break
        pending_ids = still_pending
    enforce_go_workflow_panel_order()


def unregister_panels(panel_ids, space_type=None):
    for panel_id in sorted_panel_ids_for_unregister(panel_ids, space_type=space_type):
        if panel_id in UNREGISTERED_PANEL_IDS:
            continue
        cls = resolve_panel_class(panel_id, space_type=space_type)
        if cls is None:
            continue
        try:
            if _safe_unregister_panel_class(cls):
                UNREGISTERED_PANEL_IDS.add(panel_id)
        except Exception:
            pass


def reorder_registered_panels(panel_ids, space_type=None):
    ids_to_reorder = [
        panel_id
        for panel_id in ordered_panel_ids_for_register(panel_ids, space_type=space_type)
        if panel_id not in UNREGISTERED_PANEL_IDS
    ]
    for panel_id in reversed(ids_to_reorder):
        cls = resolve_panel_class(panel_id, space_type=space_type)
        if cls is None:
            continue
        try:
            _safe_unregister_panel_class(cls)
        except Exception:
            pass

    for panel_id in ids_to_reorder:
        cls = resolve_panel_class(panel_id, space_type=space_type)
        if cls is None:
            continue
        try:
            _safe_register_panel_class(cls)
        except Exception:
            pass
    target_spaces = (space_type,) if space_type else iter_supported_space_types()
    for target_space in target_spaces:
        registry = dict(get_panel_registry(target_space))
        reordered_registry = {}
        for panel_id in ids_to_reorder:
            cls = registry.get(panel_id)
            if cls is not None:
                reordered_registry[panel_id] = cls
        for panel_id, cls in registry.items():
            if panel_id not in reordered_registry:
                reordered_registry[panel_id] = cls
        PANEL_CLASS_REGISTRY_BY_SPACE[target_space] = reordered_registry
    enforce_go_workflow_panel_order()


def apply_panel_visibility_overrides(scene=None, space_type=None, restore_first=True, install_poll=True):
    if install_poll:
        install_panel_poll_overrides()
    if restore_first:
        restore_unregistered_panels(space_type=space_type)

    target_spaces = (space_type,) if space_type else iter_supported_space_types()
    for target_space in target_spaces:
        state = get_state(scene=scene, space_type=target_space)
        if state is None:
            continue
        allowed_ids = compute_allowed_panel_ids(state)
        ACTIVE_ALLOWED_PANEL_IDS_BY_SPACE[target_space] = allowed_ids

        registry_ids = set(get_panel_registry(target_space).keys())
        if allowed_ids is None:
            restore_space_default_n_panel_state(scene=scene, space_type=target_space, disable_filters=True)
            continue

        allowed_ids = set(allowed_ids)
        hidden_ids = [
            panel_id
            for panel_id in registry_ids
            if panel_id not in allowed_ids and not panel_id.startswith("BWFLOW_")
        ]

        restore_unregistered_panels(allowed_ids, space_type=target_space)
        unregister_panels(hidden_ids, space_type=target_space)

        ordered_ids = workflow_ordered_panel_ids(state, get_active_workflow(state))
        apply_panel_order_overrides(ordered_ids, space_type=target_space)
        restore_panel_registry_order(ordered_ids, space_type=target_space)
        PANEL_FILTER_SIGNATURE_BY_SPACE[target_space] = panel_filter_signature(state)


def ensure_one_default_workflow(state):
    if state is None or not state.workflows:
        return

    default_indexes = [index for index, workflow in enumerate(state.workflows) if workflow.is_default]
    if not default_indexes:
        state.workflows[0].is_default = True
        default_indexes = [0]

    keep_index = default_indexes[0]
    for index, workflow in enumerate(state.workflows):
        workflow.is_default = index == keep_index
    normalize_workflow_texts(state)


def sync_registry(scene, space_type="VIEW_3D"):
    state = get_state(scene=scene, space_type=space_type)
    if state is None:
        return
    PANEL_GROUP_INDEX_CACHE_BY_SPACE.pop(space_type, None)
    PANEL_LIBRARY_GROUP_ENTRY_CACHE_BY_SPACE.pop(space_type, None)

    discovered = dict(get_panel_registry(space_type))
    existing = {item.panel_id: item for item in state.panel_registry}

    for item in state.panel_registry:
        item.discovered = False

    for panel_id, cls in discovered.items():
        record = existing.get(panel_id)
        if record is None:
            record = state.panel_registry.add()
            record.panel_id = panel_id
        record.title = clean_panel_title(getattr(cls, "bl_label", panel_id), panel_id)
        record.category = panel_display_category(panel_id, cls, space_type=space_type)
        record.source_module = getattr(cls, "__module__", "")
        record.discovered = True

def rebuild_panel_cache(scene=None, space_type=None, restore_first=True, install_poll=True):
    target_scene = scene or safe_context_scene()
    target_spaces = (space_type,) if space_type else iter_supported_space_types()
    for target_space in target_spaces:
        if restore_first and has_runtime_unregistered_panels(space_type=target_space):
            restore_unregistered_panels(space_type=target_space)
        PANEL_GROUP_INDEX_CACHE_BY_SPACE.pop(target_space, None)
        PANEL_LIBRARY_GROUPS_CACHE_BY_SPACE.pop(target_space, None)
        PANEL_LIBRARY_GROUP_ENTRY_CACHE_BY_SPACE.pop(target_space, None)
        SELECTED_PANEL_GROUPS_CACHE_BY_SPACE.pop(target_space, None)
        PANEL_ORDER_SIGNATURE_BY_SPACE.pop(target_space, None)
        PANEL_FILTER_SIGNATURE_BY_SPACE.pop(target_space, None)
        PANEL_CLASS_CACHE_BY_SPACE[target_space] = discover_sidebar_panels(target_space)
        if target_scene is not None:
            sync_registry(target_scene, space_type=target_space)
    if install_poll:
        install_panel_poll_overrides()


def rebuild_runtime_panels(scene=None, space_type=None, rebuild_cache=True):
    if has_runtime_unregistered_panels(space_type=space_type):
        restore_unregistered_panels(space_type=space_type)
    if rebuild_cache:
        rebuild_panel_cache(scene=scene, space_type=space_type, restore_first=False, install_poll=True)
    force_reload_script_panels(scene=scene, space_type=space_type, restore_first=False)
    apply_panel_visibility_overrides(
        scene=scene,
        space_type=space_type,
        restore_first=False,
        install_poll=not rebuild_cache,
    )
    tag_redraw_all()
    return True


def refresh_runtime_overrides(scene=None, space_type=None, restore_first=False, include_script_panels=True):
    rebuild_runtime_panels(scene=scene, space_type=space_type, rebuild_cache=False)


def refresh_runtime(scene=None):
    target_scene = scene or safe_context_scene()
    if target_scene is None:
        return

    for target_space in iter_supported_space_types():
        state = get_state(scene=target_scene, space_type=target_space)
        if state is None:
            continue

        if state.settings.auto_sync_registry:
            rebuild_panel_cache(scene=target_scene, space_type=target_space)
        else:
            PANEL_CLASS_CACHE_BY_SPACE[target_space] = discover_sidebar_panels(target_space)
    initialize_all_module_runtime_fields(scene=target_scene)
    rebuild_runtime_panels(scene=target_scene, rebuild_cache=False)


def schedule_deferred_runtime_refresh(scene=None, intervals=None, space_type=None):
    target_scene = scene or safe_context_scene()
    if target_scene is None:
        return

    scene_name = getattr(target_scene, "name", "")
    if not scene_name:
        return

    refresh_key = f"{scene_name}:{space_type or '*'}"
    DEFERRED_REFRESH_TOKENS[refresh_key] = time.time()
    if refresh_key in DEFERRED_REFRESH_PENDING_KEYS:
        return

    DEFERRED_REFRESH_PENDING_KEYS.add(refresh_key)
    delays = intervals or DEFERRED_REFRESH_INTERVALS
    delay = min(max(0.01, float(item)) for item in delays)

    def _refresh_once(scene_name=scene_name, target_space_type=space_type, pending_key=refresh_key):
        DEFERRED_REFRESH_PENDING_KEYS.discard(pending_key)
        scene_ref = bpy.data.scenes.get(scene_name)
        if scene_ref is None:
            DEFERRED_REFRESH_TOKENS.pop(pending_key, None)
            return None
        try:
            rebuild_runtime_panels(scene=scene_ref, space_type=target_space_type)
        except Exception:
            traceback.print_exc()
        return None

    try:
        _register_one_shot_timer(_refresh_once, first_interval=delay)
    except Exception:
        DEFERRED_REFRESH_PENDING_KEYS.discard(refresh_key)
        traceback.print_exc()


def ensure_minimum_setup(scene, restore_global=True, save_state=True):
    created = False
    library_changed = False
    builtin_module_changed = False
    restored_from_global = try_restore_global_workflow_state(scene) if restore_global else False
    for space_type in SUPPORTED_SPACE_TYPES:
        state = get_state(scene=scene, space_type=space_type)
        if state is None:
            continue
        state.space_type = space_type
        library_changed = ensure_builtin_script_library(state) or library_changed
        builtin_module_changed = bool(refresh_builtin_workflow_modules(state)) or builtin_module_changed
        if state.workflows:
            ensure_one_default_workflow(state)
            ensure_go_workflow_panel_entry(state)
            continue

        workflow = state.workflows.add()
        workflow.name = DEFAULT_WORKFLOW_NAME
        workflow.is_default = True
        workflow.description = DEFAULT_WORKFLOW_DESCRIPTION
        state.active_workflow_index = 0
        ensure_go_workflow_panel_entry(state)
        created = True
    if save_state and (created or library_changed or builtin_module_changed):
        save_global_workflow_state(scene)
    return created or restored_from_global or library_changed or builtin_module_changed


def clear_collection(collection):
    while len(collection):
        collection.remove(len(collection) - 1)


def clear_scene_go_workflow_runtime(scene):
    if scene is None:
        return 0
    removed = 0
    for key in list(scene.keys()):
        if not isinstance(key, str) or not key.startswith("_go_workflow_"):
            continue
        try:
            del scene[key]
            removed += 1
        except Exception:
            pass
    return removed


def reset_space_state(state):
    if state is None:
        return
    clear_collection(state.workflows)
    clear_collection(state.panel_registry)
    clear_collection(state.script_library)
    state.active_workflow_index = 0
    state.panel_registry_index = 0
    state.script_library_index = 0
    state.panel_library_last_click_index = -1
    state.panel_library_last_click_time = 0.0
    state.panel_library_last_click_target = ""
    state.panel_group_expanded_keys = ""
    state.selected_group_expanded_keys = ""
    state.module_editor_text = ""
    if state.settings is not None:
        state.settings.auto_sync_registry = True
        state.settings.show_missing_summary = True
        state.settings.runtime_preview_lines = 3
        state.settings.show_settings = False
        state.settings.ui_tab = "WORKFLOWS"


def unique_script_library_name(state, base_name, exclude_index=None):
    base = normalize_workflow_name(base_name, "脚本模板")
    existing = set()
    for index, item in enumerate(getattr(state, "script_library", [])):
        if exclude_index is not None and index == exclude_index:
            continue
        name = (getattr(item, "name", "") or "").strip()
        if name:
            existing.add(name)
    if base not in existing:
        return base
    suffix = 2
    while True:
        candidate = f"{base} ({suffix})"
        if candidate not in existing:
            return candidate
        suffix += 1


def script_library_item_snapshot(item):
    if item is None:
        return None
    return {
        "name": getattr(item, "name", ""),
        "description": getattr(item, "description", ""),
        "tags": getattr(item, "tags", ""),
        "use_custom_panel": bool(getattr(item, "use_custom_panel", False)),
        "panel_title": getattr(item, "panel_title", ""),
        "panel_description": getattr(item, "panel_description", ""),
        "script_path": getattr(item, "script_path", ""),
        "text_block_name": getattr(item, "text_block_name", ""),
        "script_source": getattr(item, "script_source", ""),
        "config_payload": getattr(item, "config_payload", ""),
        "ai_doc": getattr(item, "ai_doc", ""),
    }


def apply_script_library_item_to_module(module, item):
    if module is None or item is None:
        return
    module.name = normalize_workflow_name(getattr(item, "name", ""), "默认脚本模板")
    module.description = normalize_text_value(getattr(item, "description", ""), "")
    module.use_custom_panel = bool(getattr(item, "use_custom_panel", False))
    module.runtime_panel_expanded = True
    module.panel_title = normalize_text_value(getattr(item, "panel_title", ""), "")
    module.panel_description = normalize_text_value(getattr(item, "panel_description", ""), "")
    # 脚本库是模板仓库，载入到模块后必须绑定到当前工作流自己的 .py，
    # 不能继续指向脚本库原文件，否则后续编辑会反向污染脚本库。
    module.script_path = ""
    # 脚本库是模板仓库，不复用旧 Text 块名，避免跨工作流互相覆盖代码。
    module.text_block_name = ""
    module.script_source = getattr(item, "script_source", "")
    module.config_payload = getattr(item, "config_payload", "")
    module.ai_doc = getattr(item, "ai_doc", "")


def copy_module_to_script_library_item(item, module):
    if item is None or module is None:
        return
    item.description = normalize_text_value(getattr(module, "description", ""), "")
    item.use_custom_panel = bool(getattr(module, "use_custom_panel", False))
    item.panel_title = normalize_text_value(getattr(module, "panel_title", ""), "")
    item.panel_description = normalize_text_value(getattr(module, "panel_description", ""), "")
    # 脚本库存源码快照，不保留当前模块工作文件路径，避免跨工作流回写污染。
    item.script_path = ""
    item.text_block_name = ""
    item.script_source = getattr(module, "script_source", "")
    item.config_payload = getattr(module, "config_payload", "")
    item.ai_doc = getattr(module, "ai_doc", "")


def normalized_script_source_for_match(source):
    return re.sub(r"\s+", "", source or "")


def normalized_script_path_for_match(path):
    raw_path = (path or "").strip()
    if not raw_path:
        return ""
    return os.path.normcase(os.path.normpath(bpy.path.abspath(raw_path)))


def workflow_module_matches_script_item(module, item):
    if module is None or item is None:
        return False
    module_source = normalized_script_source_for_match(getattr(module, "script_source", ""))
    item_source = normalized_script_source_for_match(getattr(item, "script_source", ""))
    if module_source and item_source and module_source == item_source:
        return True
    module_path = normalized_script_path_for_match(getattr(module, "script_path", ""))
    item_path = normalized_script_path_for_match(getattr(item, "script_path", ""))
    if module_path and item_path and module_path == item_path:
        return True
    module_name = normalize_workflow_name(getattr(module, "name", ""), "")
    item_name = normalize_workflow_name(getattr(item, "name", ""), "")
    return bool(module_name and item_name and module_name == item_name)


def find_workflow_module_for_script_item(workflow, item):
    if workflow is None or item is None:
        return None, -1
    for index, module in enumerate(getattr(workflow, "modules", [])):
        if workflow_module_matches_script_item(module, item):
            return module, index
    return None, -1


def workflow_module_match_index(workflow, item):
    module, module_index = find_workflow_module_for_script_item(workflow, item)
    return {
        "module": module,
        "module_index": module_index,
        "installed": module is not None,
        "enabled": bool(getattr(module, "enabled", False)) if module is not None else False,
    }


def script_library_workflow_match_index(state, item):
    workflows = list(getattr(state, "workflows", [])) if state is not None else []
    return [workflow_module_match_index(workflow, item) for workflow in workflows]


def add_script_item_to_workflow_module(workflow, item):
    if workflow is None or item is None:
        return None
    module = workflow.modules.add()
    module.name = normalize_workflow_name(getattr(item, "name", ""), "脚本模板")
    module.enabled = True
    apply_script_library_item_to_module(module, item)
    module.script_path = unique_default_module_script_path(workflow, module)
    module.ai_doc = module.ai_doc or build_module_ai_doc(workflow, module)
    workflow.active_module_index = len(workflow.modules) - 1
    return module


def global_workflow_state_path():
    try:
        base_dir = bpy.utils.user_resource("CONFIG")
    except Exception:
        base_dir = ""
    if not base_dir:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, GLOBAL_WORKFLOW_FILENAME)


def serialize_panel_registry(space_state):
    return [
        {
            "panel_id": record.panel_id,
            "title": record.title,
            "category": record.category,
            "tags": record.tags,
            "source_module": record.source_module,
        }
        for record in getattr(space_state, "panel_registry", [])
    ]


def panel_registry_payload_for_panel_id(space_state, panel_id):
    if space_state is None or not panel_id:
        return None
    record = panel_drawer_record(space_state, panel_id) or find_registry_record(space_state, panel_id)
    if record is None:
        return None
    return {
        "panel_id": record.panel_id,
        "title": record.title,
        "category": record.category,
        "tags": record.tags,
        "source_module": record.source_module,
    }


def collect_workflow_panel_registry_records(space_state, workflows):
    if space_state is None:
        return []
    workflows = [workflow for workflow in (workflows or []) if workflow is not None]
    if not workflows:
        return []
    lookup = {item.panel_id: item for item in getattr(space_state, "panel_registry", []) if getattr(item, "panel_id", "")}
    referenced_ids = set()
    for workflow in workflows:
        for item in getattr(workflow, "panels", []):
            panel_id = getattr(item, "panel_id", "")
            if not panel_id or panel_id == "BWFLOW_PT_workflow":
                continue
            drawer_id = panel_drawer_root_id(panel_id, space_type=getattr(space_state, "space_type", "VIEW_3D")) or panel_id
            referenced_ids.add(panel_id)
            referenced_ids.add(drawer_id)

    records = []
    seen = set()
    for workflow in workflows:
        for item in getattr(workflow, "panels", []):
            panel_id = getattr(item, "panel_id", "")
            if not panel_id or panel_id == "BWFLOW_PT_workflow":
                continue
            drawer_id = panel_drawer_root_id(panel_id, space_type=getattr(space_state, "space_type", "VIEW_3D")) or panel_id
            for candidate_id in (drawer_id, panel_id):
                if not candidate_id or candidate_id in seen:
                    continue
                record = lookup.get(candidate_id)
                if record is None:
                    continue
                seen.add(candidate_id)
                records.append(
                    {
                        "panel_id": record.panel_id,
                        "title": record.title,
                        "category": record.category,
                        "tags": record.tags,
                        "source_module": record.source_module,
                    }
                )
    return records


def serialize_script_library(space_state):
    return [
        {
            "name": item.name,
            "description": item.description,
            "tags": item.tags,
            "use_custom_panel": item.use_custom_panel,
            "panel_title": item.panel_title,
            "panel_description": item.panel_description,
            "script_path": item.script_path,
            "text_block_name": item.text_block_name,
            "script_source": item.script_source,
            "config_payload": getattr(item, "config_payload", ""),
            "ai_doc": item.ai_doc,
        }
        for item in getattr(space_state, "script_library", [])
    ]


def serialize_script_library_items(items):
    serialized = []
    for item in items or []:
        if item is None:
            continue
        serialized.append(
            {
                "name": getattr(item, "name", ""),
                "description": getattr(item, "description", ""),
                "tags": getattr(item, "tags", ""),
                "use_custom_panel": bool(getattr(item, "use_custom_panel", False)),
                "panel_title": getattr(item, "panel_title", ""),
                "panel_description": getattr(item, "panel_description", ""),
                "script_path": getattr(item, "script_path", ""),
                "text_block_name": getattr(item, "text_block_name", ""),
                "script_source": getattr(item, "script_source", ""),
                "config_payload": getattr(item, "config_payload", ""),
                "ai_doc": getattr(item, "ai_doc", ""),
            }
        )
    return serialized


def sync_script_library_item_source(item):
    if item is None:
        return False
    text_name = (getattr(item, "text_block_name", "") or "").strip()
    if text_name:
        text_block = bpy.data.texts.get(text_name)
        if text_block is not None:
            source = text_block.as_string()
            if source != getattr(item, "script_source", ""):
                item.script_source = source
            return True
    raw_path = (getattr(item, "script_path", "") or "").strip()
    if not raw_path:
        return False
    filepath = bpy.path.abspath(raw_path)
    if not os.path.isfile(filepath):
        return False
    source = read_cached_text_file(filepath, encodings=("utf-8", "utf-8-sig"))
    if source != getattr(item, "script_source", ""):
        item.script_source = source
    return True


def refresh_script_library_sources(space_state):
    if space_state is None:
        return 0
    refreshed = 0
    for item in getattr(space_state, "script_library", []):
        try:
            updated = sync_script_library_item_source(item)
        except Exception:
            updated = False
        if updated:
            refreshed += 1
    return refreshed


def refresh_script_library_sources_for_workflows(space_state, workflows):
    if space_state is None:
        return 0
    workflow_names = {
        getattr(workflow, "name", "")
        for workflow in (workflows or [])
        if workflow is not None and getattr(workflow, "name", "")
    }
    if not workflow_names:
        return 0
    refreshed = 0
    for item in getattr(space_state, "script_library", []):
        name = getattr(item, "name", "")
        if name not in workflow_names:
            continue
        try:
            updated = sync_script_library_item_source(item)
        except Exception:
            updated = False
        if updated:
            refreshed += 1
    return refreshed


def workflow_related_script_library_items(state, workflows):
    if state is None:
        return []
    workflow_names = {
        getattr(workflow, "name", "")
        for workflow in (workflows or [])
        if workflow is not None and getattr(workflow, "name", "")
    }
    module_names = set()
    for workflow in workflows or []:
        for module in getattr(workflow, "modules", []):
            module_name = getattr(module, "name", "")
            if module_name:
                module_names.add(module_name)
    selected = []
    seen = set()
    for item in getattr(state, "script_library", []):
        name = getattr(item, "name", "")
        if name not in workflow_names and name not in module_names:
            continue
        if script_library_item_has_builtin_tag(item):
            continue
        signature = script_library_signature_from_item(item)
        if signature in seen:
            continue
        seen.add(signature)
        selected.append(item)
    return selected


def serialize_workflow(workflow):
    if workflow is None:
        return None
    return {
        "name": workflow.name,
        "is_default": workflow.is_default,
        "description": workflow.description,
        "tag_filter": workflow.tag_filter,
        "panels": [item.panel_id for item in workflow.panels],
        "modules": [
            {
                "name": module.name,
                "enabled": module.enabled,
                "use_custom_panel": module.use_custom_panel,
                "runtime_panel_expanded": getattr(module, "runtime_panel_expanded", True),
                "panel_title": module.panel_title,
                "panel_description": module.panel_description,
                "script_path": module.script_path,
                "description": module.description,
                "text_block_name": module.text_block_name,
                "script_source": module.script_source,
                "config_payload": getattr(module, "config_payload", ""),
                "ai_doc": module.ai_doc,
            }
            for module in workflow.modules
        ],
    }


def serialize_space_settings(space_state):
    return {
        "auto_sync_registry": space_state.settings.auto_sync_registry,
        "show_missing_summary": space_state.settings.show_missing_summary,
        "runtime_preview_lines": space_state.settings.runtime_preview_lines,
        "show_workflow_description": space_state.settings.show_workflow_description,
        "show_runtime_module_descriptions": space_state.settings.show_runtime_module_descriptions,
        "show_module_ai_doc_preview": space_state.settings.show_module_ai_doc_preview,
        "show_script_library_source_preview": space_state.settings.show_script_library_source_preview,
        "show_script_library_ai_doc_preview": space_state.settings.show_script_library_ai_doc_preview,
        "show_help_text_blocks": space_state.settings.show_help_text_blocks,
    }


def serialize_space_state(space_state):
    if space_state is None:
        return None
    return {
        "label": SPACE_LABELS.get(getattr(space_state, "space_type", ""), getattr(space_state, "space_type", "")),
        "active_workflow_index": space_state.active_workflow_index,
        "settings": serialize_space_settings(space_state),
        "panel_registry": serialize_panel_registry(space_state),
        "script_library": serialize_script_library(space_state),
        "workflows": [
            workflow_payload
            for workflow_payload in (serialize_workflow(workflow) for workflow in space_state.workflows)
            if workflow_payload is not None
        ],
    }


def selected_preset_export_workflows(state):
    if state is None:
        return []
    return [
        workflow
        for workflow in state.workflows
        if bool(getattr(workflow, "preset_export_selected", False))
    ]


def workflow_preset_filename(state, workflows):
    workflows = list(workflows or [])
    if len(workflows) == 1:
        workflow_name = getattr(workflows[0], "name", "") or "workflow"
        return f"{safe_filename_component(workflow_name, 'workflow')}{PRESET_FILE_EXTENSION}"
    space_type = getattr(state, "space_type", "") if state is not None else ""
    space_suffix = safe_filename_component(space_type.lower(), "workflow")
    count = max(1, len(workflows))
    return f"go_workflow_{space_suffix}_{count}_workflows{PRESET_FILE_EXTENSION}"


def default_preset_export_path(context, state):
    workflows = selected_preset_export_workflows(state)
    if not workflows:
        workflow = get_active_workflow(state)
        workflows = [workflow] if workflow is not None else []
    filename = workflow_preset_filename(state, workflows)
    try:
        base_dir = bpy.path.abspath("//")
    except Exception:
        base_dir = ""
    if not base_dir:
        base_dir = os.path.expanduser("~")
    return os.path.join(base_dir, filename)


def load_json_payload_file(filepath, max_bytes=None):
    return read_cached_json_file(filepath, max_bytes=max_bytes)


def build_current_workflow_preset_payload(scene, context=None):
    space_type = current_space_type(context=context)
    state = get_state(context=context, scene=scene, space_type=space_type)
    workflow = get_active_workflow(state)
    workflow_payload = serialize_workflow(workflow)
    if state is None or workflow_payload is None:
        return None
    related_scripts = workflow_related_script_library_items(state, [workflow] if workflow is not None else [])
    payload = {
        "schema_version": SCHEMA_VERSION,
        "preset_kind": CURRENT_WORKFLOW_PRESET_KIND,
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "space_type": space_type,
        "space_label": SPACE_LABELS.get(space_type, space_type),
        "workflow_name": workflow_payload.get("name", ""),
        "settings": serialize_space_settings(state),
        "panel_registry": collect_workflow_panel_registry_records(state, [workflow]),
        "script_library": serialize_script_library_items(related_scripts),
        "workflow": workflow_payload,
    }
    return sanitize_current_workflow_preset_payload(payload)


def build_selected_workflows_preset_payload(scene, context=None):
    space_type = current_space_type(context=context)
    state = get_state(context=context, scene=scene, space_type=space_type)
    workflows = selected_preset_export_workflows(state)
    if state is None or not workflows:
        return None

    workflow_payloads = [
        workflow_payload
        for workflow_payload in (serialize_workflow(workflow) for workflow in workflows)
        if workflow_payload is not None
    ]
    if not workflow_payloads:
        return None
    related_scripts = workflow_related_script_library_items(state, workflows)

    active_workflow = get_active_workflow(state)
    active_name = getattr(active_workflow, "name", "") if active_workflow is not None else ""
    active_workflow_index = 0
    for index, workflow_payload in enumerate(workflow_payloads):
        if workflow_payload.get("name", "") == active_name:
            active_workflow_index = index
            break

    space_payload = sanitize_space_payload(
        {
            "label": SPACE_LABELS.get(space_type, space_type),
            "active_workflow_index": active_workflow_index,
            "settings": serialize_space_settings(state),
            "panel_registry": collect_workflow_panel_registry_records(state, workflows),
            "script_library": serialize_script_library_items(related_scripts),
            "workflows": workflow_payloads,
        }
    )
    if space_payload is None:
        return None

    return sanitize_full_payload(
        {
            "schema_version": SCHEMA_VERSION,
            "preset_kind": "workflow_collection",
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "space_states": {
                space_type: space_payload,
            },
        }
    )


def build_full_state_payload(scene):
    payload = {
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.utcnow().isoformat() + "Z",
        "space_states": {},
    }
    for space_type in iter_supported_space_types():
        space_state = get_state(scene=scene, space_type=space_type)
        if space_state is None:
            continue
        payload["space_states"][space_type] = serialize_space_state(space_state)
    return sanitize_full_payload(payload)


def apply_space_state_payload(state, space_payload):
    if state is None or not isinstance(space_payload, dict):
        return
    cleaned_payload = sanitize_space_payload(space_payload)
    if cleaned_payload is None:
        return

    clear_collection(state.workflows)
    clear_collection(state.panel_registry)
    clear_collection(state.script_library)

    settings = cleaned_payload.get("settings", {})
    state.settings.auto_sync_registry = coerce_bool_value(settings.get("auto_sync_registry", True), True)
    state.settings.show_missing_summary = coerce_bool_value(settings.get("show_missing_summary", True), True)
    state.settings.runtime_preview_lines = coerce_int_value(settings.get("runtime_preview_lines", 3), 3)
    state.settings.show_workflow_description = coerce_bool_value(settings.get("show_workflow_description", False), False)
    state.settings.show_runtime_module_descriptions = coerce_bool_value(settings.get("show_runtime_module_descriptions", False), False)
    state.settings.show_module_ai_doc_preview = coerce_bool_value(settings.get("show_module_ai_doc_preview", False), False)
    state.settings.show_script_library_source_preview = coerce_bool_value(settings.get("show_script_library_source_preview", False), False)
    state.settings.show_script_library_ai_doc_preview = coerce_bool_value(settings.get("show_script_library_ai_doc_preview", False), False)
    state.settings.show_help_text_blocks = coerce_bool_value(settings.get("show_help_text_blocks", False), False)

    for record_data in cleaned_payload.get("panel_registry", []):
        record = state.panel_registry.add()
        record.panel_id = record_data.get("panel_id", "")
        record.title = record_data.get("title", "")
        record.category = record_data.get("category", "")
        record.tags = record_data.get("tags", "")
        record.source_module = record_data.get("source_module", "")
        record.discovered = False

    for script_data in cleaned_payload.get("script_library", []):
        item = state.script_library.add()
        item.name = unique_script_library_name(state, script_data.get("name", ""), exclude_index=None)
        item.description = normalize_text_value(script_data.get("description", ""), "")
        item.tags = script_data.get("tags", "")
        item.use_custom_panel = script_data.get("use_custom_panel", False)
        item.panel_title = normalize_text_value(script_data.get("panel_title", ""), "")
        item.panel_description = normalize_text_value(script_data.get("panel_description", ""), "")
        item.script_path = script_data.get("script_path", "")
        item.text_block_name = normalize_text_value(script_data.get("text_block_name", ""), "")
        item.script_source = script_data.get("script_source", "")
        item.config_payload = script_data.get("config_payload", "")
        item.ai_doc = script_data.get("ai_doc", "")

    for workflow_data in cleaned_payload.get("workflows", []):
        workflow = state.workflows.add()
        workflow.is_default = workflow_data.get("is_default", False)
        workflow.name = unique_workflow_name(
            state,
            workflow_data.get("name", ""),
            DEFAULT_WORKFLOW_NAME if workflow.is_default else "自定义工作流",
            exclude_index=len(state.workflows) - 1,
        )
        workflow.description = normalize_workflow_description(workflow_data.get("description", ""), "")
        workflow.tag_filter = workflow_data.get("tag_filter", "")
        for panel_id in workflow_data.get("panels", []):
            item = workflow.panels.add()
            item.panel_id = panel_id
        for module_data in workflow_data.get("modules", []):
            module = workflow.modules.add()
            module.name = normalize_workflow_name(module_data.get("name", ""), "默认脚本模板")
            module.enabled = module_data.get("enabled", True)
            module.use_custom_panel = module_data.get("use_custom_panel", False)
            module.runtime_panel_expanded = module_data.get("runtime_panel_expanded", True)
            module.panel_title = normalize_text_value(module_data.get("panel_title", ""), "")
            module.panel_description = normalize_text_value(module_data.get("panel_description", ""), "")
            module.script_path = module_data.get("script_path", "")
            module.description = normalize_text_value(module_data.get("description", ""), "")
            module.text_block_name = normalize_text_value(module_data.get("text_block_name", ""), "")
            module.script_source = module_data.get("script_source", "")
            module.config_payload = module_data.get("config_payload", "")
            module.ai_doc = module_data.get("ai_doc", "")

    ensure_one_default_workflow(state)
    ensure_go_workflow_panel_entry(state)
    ensure_builtin_script_library(state)
    state.active_workflow_index = clamp_index(cleaned_payload.get("active_workflow_index", 0), len(state.workflows))


def merge_panel_registry_payload(state, panel_records):
    if state is None:
        return 0
    existing = {item.panel_id: item for item in state.panel_registry if item.panel_id}
    added = 0
    for record_data in panel_records or []:
        if not isinstance(record_data, dict):
            continue
        panel_id = (record_data.get("panel_id", "") or "").strip()
        if not panel_id:
            continue
        record = existing.get(panel_id)
        if record is None:
            record = state.panel_registry.add()
            record.panel_id = panel_id
            record.discovered = False
            existing[panel_id] = record
            added += 1
        if not record.title:
            record.title = clean_panel_title(record_data.get("title", ""), panel_id)
        if not record.category:
            record.category = normalize_text_value(record_data.get("category", ""), "")
        if not record.tags:
            record.tags = record_data.get("tags", "")
        if not record.source_module:
            record.source_module = normalize_text_value(record_data.get("source_module", ""), "")
    return added


def script_library_signature_from_payload(payload):
    return (
        payload.get("description", ""),
        payload.get("tags", ""),
        bool(payload.get("use_custom_panel", False)),
        payload.get("panel_title", ""),
        payload.get("panel_description", ""),
        normalized_script_path_for_match(payload.get("script_path", "")),
        payload.get("text_block_name", ""),
        normalized_script_source_for_match(payload.get("script_source", "")),
        payload.get("config_payload", ""),
        payload.get("ai_doc", ""),
    )


def script_library_signature_from_item(item):
    return script_library_signature_from_payload(script_library_item_snapshot(item) or {})


def merge_script_library_payload(state, script_items):
    if state is None:
        return 0
    existing_signatures = {script_library_signature_from_item(item) for item in state.script_library}
    added = 0
    for script_data in script_items or []:
        payload = sanitize_script_library_payload(script_data)
        if payload is None:
            continue
        signature = script_library_signature_from_payload(payload)
        if signature in existing_signatures:
            continue
        item = state.script_library.add()
        item.name = unique_script_library_name(state, payload.get("name", ""), exclude_index=len(state.script_library) - 1)
        item.description = payload.get("description", "")
        item.tags = payload.get("tags", "")
        item.use_custom_panel = bool(payload.get("use_custom_panel", False))
        item.panel_title = payload.get("panel_title", "")
        item.panel_description = payload.get("panel_description", "")
        item.script_path = payload.get("script_path", "")
        item.text_block_name = payload.get("text_block_name", "")
        item.script_source = payload.get("script_source", "")
        item.config_payload = payload.get("config_payload", "")
        item.ai_doc = payload.get("ai_doc", "")
        existing_signatures.add(signature)
        added += 1
    return added


def unique_workflow_name_for_import(state, base_name, exclude_index=None):
    return unique_workflow_name(state, base_name, fallback="导入工作流", exclude_index=exclude_index)


def apply_module_payload_to_workflow(workflow, module_data):
    module = workflow.modules.add()
    module.name = normalize_workflow_name(module_data.get("name", ""), "导入模块")
    module.enabled = bool(module_data.get("enabled", True))
    module.use_custom_panel = bool(module_data.get("use_custom_panel", False))
    module.runtime_panel_expanded = bool(module_data.get("runtime_panel_expanded", True))
    module.panel_title = normalize_text_value(module_data.get("panel_title", ""), "")
    module.panel_description = normalize_text_value(module_data.get("panel_description", ""), "")
    module.script_path = module_data.get("script_path", "")
    module.description = normalize_text_value(module_data.get("description", ""), "")
    module.text_block_name = normalize_text_value(module_data.get("text_block_name", ""), "")
    module.script_source = module_data.get("script_source", "")
    module.config_payload = module_data.get("config_payload", "")
    module.ai_doc = module_data.get("ai_doc", "")
    if not module.script_path:
        module.script_path = unique_default_module_script_path(workflow, module)


def apply_current_workflow_preset_payload(state, preset_payload, merge_shared=True):
    sanitized = sanitize_current_workflow_preset_payload(preset_payload)
    if state is None or sanitized is None:
        return None

    workflow_data = dict(sanitized.get("workflow", {}))
    imported_name = normalize_workflow_name(workflow_data.get("name", ""), "导入工作流")
    if merge_shared:
        merge_panel_registry_payload(state, sanitized.get("panel_registry", []))
        merge_script_library_payload(state, sanitized.get("script_library", []))

    workflow = state.workflows.add()
    target_index = len(state.workflows) - 1
    workflow.name = unique_workflow_name_for_import(state, imported_name, exclude_index=target_index)
    workflow.is_default = False
    workflow.description = normalize_workflow_description(workflow_data.get("description", ""), "")
    workflow.tag_filter = workflow_data.get("tag_filter", "")
    replace_workflow_panels(workflow, workflow_data.get("panels", []))
    clear_collection(workflow.modules)
    for module_data in workflow_data.get("modules", []):
        apply_module_payload_to_workflow(workflow, module_data)

    state.active_workflow_index = target_index
    ensure_one_default_workflow(state)
    ensure_go_workflow_panel_entry(state)
    normalize_workflow_active_panel_index(workflow)
    return workflow


def merge_preset_entries_shared_payloads(state, entries):
    if state is None:
        return
    panel_records = []
    script_items = []
    for entry in entries or []:
        workflow_payload = current_workflow_preset_payload_from_entry(entry)
        if not isinstance(workflow_payload, dict):
            continue
        panel_records.extend(workflow_payload.get("panel_registry", []))
        script_items.extend(workflow_payload.get("script_library", []))
    merge_panel_registry_payload(state, panel_records)
    merge_script_library_payload(state, script_items)


def workflow_entry_is_default(entry):
    workflow = entry.get("workflow", {}) if isinstance(entry, dict) else {}
    return bool(workflow.get("is_default", False))


def direct_import_workflow_entries(entries, preferred_space_type=None):
    entries = [entry for entry in (entries or []) if isinstance(entry, dict)]
    if not entries:
        return []
    non_default = [entry for entry in entries if not workflow_entry_is_default(entry)]
    if preferred_space_type:
        preferred = [entry for entry in non_default if entry.get("space_type", "VIEW_3D") == preferred_space_type]
        if preferred:
            return preferred
    if non_default:
        return non_default
    if preferred_space_type:
        preferred = [entry for entry in entries if entry.get("space_type", "VIEW_3D") == preferred_space_type]
        if preferred:
            return preferred
    return entries


def preset_entry_key(space_type, workflow_index, workflow_name):
    return f"{space_type}:{workflow_index}:{workflow_name}"


def current_workflow_preset_entry(payload):
    sanitized = sanitize_current_workflow_preset_payload(payload)
    if sanitized is None:
        return None
    workflow = sanitized.get("workflow", {})
    space_type = sanitized.get("space_type", "VIEW_3D") or "VIEW_3D"
    return {
        "key": preset_entry_key(space_type, 0, workflow.get("name", "")),
        "space_type": space_type,
        "source_label": sanitized.get("space_label", "") or SPACE_LABELS.get(space_type, space_type),
        "workflow_index": 0,
        "workflow": workflow,
        "panel_registry": sanitized.get("panel_registry", []),
        "script_library": sanitized.get("script_library", []),
        "settings": sanitized.get("settings", {}),
    }


def workflow_preset_entries_from_payload(payload, preferred_space_type=None):
    if not isinstance(payload, dict):
        return []
    if is_current_workflow_preset_payload(payload):
        entry = current_workflow_preset_entry(payload)
        return [entry] if entry is not None else []

    entries = []
    space_payloads = payload.get("space_states")
    if isinstance(space_payloads, dict) and space_payloads:
        sanitized_payload = sanitize_full_payload(payload)
        cleaned_spaces = sanitized_payload.get("space_states", {}) if sanitized_payload else {}
        ordered_space_types = []
        if preferred_space_type and preferred_space_type in cleaned_spaces:
            ordered_space_types.append(preferred_space_type)
        ordered_space_types.extend(space_type for space_type in iter_supported_space_types() if space_type not in ordered_space_types)
        ordered_space_types.extend(space_type for space_type in cleaned_spaces.keys() if space_type not in ordered_space_types)
        for space_type in ordered_space_types:
            space_payload = cleaned_spaces.get(space_type)
            if not isinstance(space_payload, dict):
                continue
            workflows = space_payload.get("workflows", [])
            for workflow_index, workflow in enumerate(workflows):
                if not isinstance(workflow, dict):
                    continue
                workflow_name = workflow.get("name", "")
                entries.append(
                    {
                        "key": preset_entry_key(space_type, workflow_index, workflow_name),
                        "space_type": space_type,
                        "source_label": SPACE_LABELS.get(space_type, space_type),
                        "workflow_index": workflow_index,
                        "workflow": workflow,
                        "panel_registry": space_payload.get("panel_registry", []),
                        "script_library": space_payload.get("script_library", []),
                        "settings": space_payload.get("settings", {}),
                    }
                )
        return entries

    fallback_space = sanitize_space_payload(
        {
            "active_workflow_index": payload.get("active_workflow_index", 0),
            "settings": payload.get("settings", {}),
            "panel_registry": payload.get("panel_registry", []),
            "script_library": payload.get("script_library", []),
            "workflows": payload.get("workflows", []),
        }
    )
    if fallback_space is None:
        return []
    for workflow_index, workflow in enumerate(fallback_space.get("workflows", [])):
        workflow_name = workflow.get("name", "")
        entries.append(
            {
                "key": preset_entry_key("VIEW_3D", workflow_index, workflow_name),
                "space_type": "VIEW_3D",
                "source_label": SPACE_LABELS.get("VIEW_3D", "VIEW_3D"),
                "workflow_index": workflow_index,
                "workflow": workflow,
                "panel_registry": fallback_space.get("panel_registry", []),
                "script_library": fallback_space.get("script_library", []),
                "settings": fallback_space.get("settings", {}),
            }
        )
    return entries


def current_workflow_preset_payload_from_entry(entry):
    if not isinstance(entry, dict):
        return None
    return sanitize_current_workflow_preset_payload(
        {
            "schema_version": SCHEMA_VERSION,
            "preset_kind": CURRENT_WORKFLOW_PRESET_KIND,
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "space_type": entry.get("space_type", "VIEW_3D"),
            "space_label": entry.get("source_label", ""),
            "workflow_name": (entry.get("workflow") or {}).get("name", ""),
            "settings": entry.get("settings", {}),
            "panel_registry": entry.get("panel_registry", []),
            "script_library": entry.get("script_library", []),
            "workflow": entry.get("workflow", {}),
        }
    )


def populate_preset_workflow_list(state, entries):
    if state is None:
        return 0
    clear_collection(state.preset_workflows)
    for entry in entries:
        workflow = entry.get("workflow", {})
        item = state.preset_workflows.add()
        item.selected = not bool(workflow.get("is_default", False))
        item.name = workflow.get("name", "") or "Workflow"
        item.source_space_type = entry.get("space_type", "VIEW_3D")
        item.source_label = entry.get("source_label", "") or SPACE_LABELS.get(item.source_space_type, item.source_space_type)
        item.source_key = entry.get("key", "")
        item.panel_count = len(workflow.get("panels", []))
        item.module_count = len(workflow.get("modules", []))
        item.is_default = bool(workflow.get("is_default", False))
    state.preset_workflow_index = 0
    return len(state.preset_workflows)


def save_global_workflow_state_now(scene=None):
    target_scene = scene or safe_context_scene()
    if target_scene is None:
        return False
    filepath = global_workflow_state_path()
    folder = os.path.dirname(filepath)
    if folder:
        os.makedirs(folder, exist_ok=True)
    payload = build_full_state_payload(target_scene)
    tmp_filepath = filepath + ".tmp"
    backup_filepath = filepath + ".bak"
    with open(tmp_filepath, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    if os.path.isfile(filepath):
        try:
            shutil.copy2(filepath, backup_filepath)
        except Exception:
            traceback.print_exc()
    os.replace(tmp_filepath, filepath)
    return True


def schedule_global_workflow_state_save(scene=None, delay=DEFERRED_SAVE_INTERVAL):
    target_scene = scene or safe_context_scene()
    if target_scene is None:
        return False
    scene_name = getattr(target_scene, "name", "")
    if not scene_name:
        return False
    if scene_name in DEFERRED_SAVE_PENDING_SCENES:
        return True
    DEFERRED_SAVE_PENDING_SCENES.add(scene_name)

    def _save_once(name=scene_name):
        DEFERRED_SAVE_PENDING_SCENES.discard(name)
        scene_ref = bpy.data.scenes.get(name)
        if scene_ref is None:
            return None
        try:
            save_global_workflow_state_now(scene_ref)
        except Exception:
            traceback.print_exc()
        return None

    try:
        _register_one_shot_timer(_save_once, first_interval=max(0.05, float(delay)))
        return True
    except Exception:
        DEFERRED_SAVE_PENDING_SCENES.discard(scene_name)
        traceback.print_exc()
        return False


def save_global_workflow_state(scene=None):
    return schedule_global_workflow_state_save(scene)


def load_global_workflow_state():
    filepath = global_workflow_state_path()
    for candidate in (filepath, filepath + ".bak"):
        if not os.path.isfile(candidate):
            continue
        try:
            payload = load_json_payload_file(candidate, max_bytes=MAX_GLOBAL_STATE_FILE_BYTES)
            cleaned = sanitize_full_payload(payload) if isinstance(payload, dict) else None
            if cleaned is not None:
                return cleaned
        except Exception:
            traceback.print_exc()
    return None


def state_is_auto_default_only(state):
    if state is None or len(getattr(state, "workflows", [])) != 1:
        return False
    workflow = state.workflows[0]
    explicit_panels = [item.panel_id for item in getattr(workflow, "panels", []) if item.panel_id != "BWFLOW_PT_workflow"]
    return (
        bool(getattr(workflow, "is_default", False))
        and (getattr(workflow, "name", "") or "") == DEFAULT_WORKFLOW_NAME
        and not explicit_panels
        and not getattr(workflow, "modules", [])
        and not (getattr(workflow, "tag_filter", "") or "").strip()
    )


def try_restore_global_workflow_state(scene):
    payload = load_global_workflow_state()
    if not payload or scene is None:
        return False
    space_payloads = payload.get("space_states")
    if not isinstance(space_payloads, dict) or not space_payloads:
        return False

    restored = False
    for space_type in iter_supported_space_types():
        state = get_state(scene=scene, space_type=space_type)
        if state is None:
            continue
        if state.workflows and not state_is_auto_default_only(state):
            continue
        space_payload = space_payloads.get(space_type)
        if not isinstance(space_payload, dict):
            continue
        try:
            apply_space_state_payload(state, space_payload)
            restored = True
        except Exception:
            traceback.print_exc()
    return restored


def split_preview_lines(text, limit=6, width=64):
    if not text:
        return []

    max_lines = max(1, int(limit or 1))
    max_width = max(12, int(width or 64))
    lines = []
    for raw_line in str(text).splitlines():
        if len(lines) >= max_lines:
            break
        line = raw_line or " "
        while len(line) > max_width and len(lines) < max_lines:
            lines.append(line[:max_width])
            line = line[max_width:]
        if len(lines) < max_lines:
            lines.append(line)

    return lines


def draw_folded_text_block(layout, owner, prop_name, title, text, icon="INFO", expanded_limit=6, width=64):
    value = str(text or "").strip()
    if not value:
        return False
    expanded = bool(getattr(owner, prop_name, False)) if owner is not None and hasattr(owner, prop_name) else False
    header = layout.row(align=True)
    if owner is not None and hasattr(owner, prop_name):
        header.prop(
            owner,
            prop_name,
            text="",
            icon="TRIA_DOWN" if expanded else "TRIA_RIGHT",
            emboss=False,
            toggle=True,
        )
    else:
        header.label(text="", icon="TRIA_DOWN" if expanded else "TRIA_RIGHT")
    header.label(text=title, icon=icon)
    limit = expanded_limit if expanded else 1
    for line in split_preview_lines(value, limit=limit, width=width):
        layout.label(text=line)
    return expanded


def compact_inline_text(text, max_chars=UI_LABEL_MAX_CHARS, fallback=""):
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if not value:
        return fallback
    return value


def compact_multiline_text(text, max_chars, max_lines=None):
    value = str(text or "").strip()
    if not value:
        return ""
    return value


def finalize_ai_doc(lines):
    text = "\n".join(str(line) for line in lines).strip()
    if len(text) <= AI_DOC_MAX_CHARS:
        return text
    suffix = "\n\n（AI 文档已按长度上限截断；请保留关键需求，长说明放到外部文档。）"
    return text[: max(0, AI_DOC_MAX_CHARS - len(suffix))].rstrip() + suffix


def module_script_abspath(module):
    raw_path = (module.script_path or "").strip()
    if not raw_path:
        return ""
    return bpy.path.abspath(raw_path)


def legacy_default_module_script_path(workflow_name, module_name):
    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bworkflow_modules")
    filename = f"{safe_filename_component(workflow_name, 'workflow')}_{safe_filename_component(module_name, 'module')}.py"
    return os.path.join(base_dir, filename)


def slugify_filename(text, fallback="module"):
    value = re.sub(r"[^0-9A-Za-z_\-]+", "_", text or "")
    value = value.strip("_")
    return value or fallback


WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def safe_filename_component(text, fallback="module", max_length=72):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text or "")
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"_+", "_", value).strip("._ ")
    if not value:
        value = fallback
    if value.upper() in WINDOWS_RESERVED_FILENAMES:
        value = f"{value}_file"
    return value[:max_length].rstrip("._ ") or fallback


def default_module_scripts_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "go_workflow_modules")


def project_root_dir():
    return os.path.dirname(default_module_scripts_dir())


def builtin_special_presets_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "special_presets")


BUILTIN_SCRIPT_LIBRARY_TAG = "go_workflow_builtin"
BUILTIN_ARKIT_SYNTHESIS_SCRIPT_NAMES = (
    "ARKit 合成 VRM 基础形态键 - 常规",
    "ARKit 合成 VRM 基础形态键 - 激进",
    "ARKit 合成 MMD 可构成形态键 - 常规",
    "ARKit 合成 MMD 可构成形态键 - 激进",
)


def builtin_scripts_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "builtin_scripts")


def builtin_script_library_manifest_path():
    return os.path.join(builtin_scripts_dir(), "library_manifest.json")


def _file_cache_signature(filepath):
    try:
        stat = os.stat(filepath)
    except OSError:
        return None
    return (int(getattr(stat, "st_mtime_ns", 0)), int(getattr(stat, "st_size", 0)))


def read_cached_text_file(filepath, encodings=("utf-8", "utf-8-sig"), errors=None):
    normalized = os.path.normcase(os.path.abspath(filepath))
    signature = _file_cache_signature(normalized)
    if signature is None:
        raise FileNotFoundError(normalized)
    cached = FILE_TEXT_CACHE.get(normalized)
    if cached is not None and cached.get("signature") == signature:
        return cached.get("data", "")
    last_error = None
    for encoding in encodings:
        try:
            with open(normalized, "r", encoding=encoding, errors=errors) as handle:
                data = handle.read()
            if isinstance(data, str) and data.startswith("\ufeff"):
                data = data.lstrip("\ufeff")
            FILE_TEXT_CACHE[normalized] = {"signature": signature, "data": data}
            return data
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    with open(normalized, "r", encoding="utf-8", errors=errors) as handle:
        data = handle.read()
    if isinstance(data, str) and data.startswith("\ufeff"):
        data = data.lstrip("\ufeff")
    FILE_TEXT_CACHE[normalized] = {"signature": signature, "data": data}
    return data


def read_cached_json_file(filepath, max_bytes=None):
    normalized = os.path.normcase(os.path.abspath(filepath))
    signature = _file_cache_signature(normalized)
    if signature is None:
        raise FileNotFoundError(normalized)
    if max_bytes is not None and signature[1] > max_bytes:
        raise ValueError(f"文件过大，已拒绝读取: {signature[1] / (1024 * 1024):.1f} MB")
    cached = FILE_JSON_CACHE.get(normalized)
    if cached is not None and cached.get("signature") == signature:
        return cached.get("data")
    payload = json.loads(read_cached_text_file(normalized, encodings=("utf-8", "utf-8-sig")))
    FILE_JSON_CACHE[normalized] = {"signature": signature, "data": payload}
    return payload


def builtin_script_library_payloads():
    manifest_path = builtin_script_library_manifest_path()
    if not os.path.isfile(manifest_path):
        return []
    manifest_signature = _file_cache_signature(manifest_path)
    if manifest_signature is None:
        return []
    try:
        entries = read_cached_json_file(manifest_path)
    except Exception:
        traceback.print_exc()
        return []
    if not isinstance(entries, list):
        return []

    base_dir = builtin_scripts_dir()
    file_signatures = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        script_name = (entry.get("script_file", "") or "").strip()
        script_file = os.path.join(base_dir, script_name) if script_name else ""
        file_signatures.append((script_name, _file_cache_signature(script_file) if script_file else None))
    cache_signature = (manifest_signature, tuple(file_signatures))
    cached_payloads = BUILTIN_SCRIPT_LIBRARY_PAYLOAD_CACHE.get("payloads", [])
    if BUILTIN_SCRIPT_LIBRARY_PAYLOAD_CACHE.get("signature") == cache_signature:
        return [dict(payload) for payload in cached_payloads]

    payloads = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        script_name = (entry.get("script_file", "") or "").strip()
        script_file = os.path.join(base_dir, script_name) if script_name else ""
        source = ""
        if script_file and os.path.isfile(script_file):
            try:
                source = read_cached_text_file(script_file, encodings=("utf-8", "utf-8-sig"))
            except Exception:
                traceback.print_exc()
                continue

        replacements = entry.get("replace", {})
        if isinstance(replacements, dict):
            for old, new in replacements.items():
                source = source.replace(str(old), str(new))

        payload = sanitize_script_library_payload(
            {
                "name": entry.get("name", ""),
                "description": entry.get("description", ""),
                "tags": entry.get("tags", BUILTIN_SCRIPT_LIBRARY_TAG),
                "use_custom_panel": bool(entry.get("use_custom_panel", False)),
                "panel_title": entry.get("panel_title", ""),
                "panel_description": entry.get("panel_description", ""),
                "script_path": script_file if entry.get("script_path_mode") == "builtin" else "",
                "text_block_name": "",
                "script_source": source,
                "config_payload": entry.get("config_payload", ""),
                "ai_doc": entry.get("ai_doc", ""),
            }
        )
        if payload is not None:
            tags = payload.get("tags", "")
            if BUILTIN_SCRIPT_LIBRARY_TAG not in {tag.strip() for tag in tags.split(",")}:
                payload["tags"] = f"{tags},{BUILTIN_SCRIPT_LIBRARY_TAG}" if tags else BUILTIN_SCRIPT_LIBRARY_TAG
            payloads.append(payload)
    BUILTIN_SCRIPT_LIBRARY_PAYLOAD_CACHE["signature"] = cache_signature
    BUILTIN_SCRIPT_LIBRARY_PAYLOAD_CACHE["payloads"] = [dict(payload) for payload in payloads]
    return [dict(payload) for payload in payloads]


def builtin_script_library_payload_by_name(name):
    target = (name or "").strip()
    if not target:
        return None
    for payload in builtin_script_library_payloads():
        if payload.get("name", "") == target:
            return payload
    return None


def script_library_item_has_builtin_tag(item):
    tags = getattr(item, "tags", "") if item is not None else ""
    return BUILTIN_SCRIPT_LIBRARY_TAG in {tag.strip() for tag in (tags or "").split(",")}


def ensure_builtin_script_library(state):
    if state is None:
        return False
    changed = False
    legacy_arkit_names = {
        "ARKit 鍚堟垚 VRM 鍩虹褰㈡€侀敭 - 甯歌",
        "ARKit 鍚堟垚 VRM 鍩虹褰㈡€侀敭 - 婵€杩?",
        "ARKit 鍚堟垚 MMD 鍙瀯鎴愬舰鎬侀敭 - 甯歌",
        "ARKit 鍚堟垚 MMD 鍙瀯鎴愬舰鎬侀敭 - 婵€杩?",
    }
    current_arkit_names = {payload.get("name", "") for payload in builtin_script_library_payloads()}
    for index in range(len(getattr(state, "script_library", [])) - 1, -1, -1):
        item = state.script_library[index]
        if not script_library_item_has_builtin_tag(item):
            continue
        item_name = getattr(item, "name", "")
        if item_name in legacy_arkit_names and item_name not in current_arkit_names:
            state.script_library.remove(index)
            changed = True
    for payload in builtin_script_library_payloads():
        payload_name = payload.get("name", "")
        item = None
        for candidate in getattr(state, "script_library", []):
            if getattr(candidate, "name", "") == payload_name and script_library_item_has_builtin_tag(candidate):
                item = candidate
                break
        if item is None:
            item = state.script_library.add()
            item.name = unique_script_library_name(state, payload_name, exclude_index=len(state.script_library) - 1)
            changed = True

        for attr in (
            "description",
            "tags",
            "use_custom_panel",
            "panel_title",
            "panel_description",
            "script_path",
            "text_block_name",
            "script_source",
            "config_payload",
            "ai_doc",
        ):
            value = payload.get(attr, "")
            if attr == "use_custom_panel":
                value = bool(value)
            if getattr(item, attr) != value:
                setattr(item, attr, value)
                changed = True
    return changed


def special_preset_spec(preset_type):
    if preset_type == SPECIAL_PRESET_ARKIT_52:
        return {
            "preset_type": SPECIAL_PRESET_ARKIT_52,
            "preset_name": "arkit形态键工作流参考",
            "workflow_name": "arkit形态键工作流参考",
            "workflow_description": "用于逐步检查 ARKit / Perfect Sync 52 个表情形态键，支持自动置 1、进入编辑模式与打开参考图。",
            "module_name": "arkit形态键工作流参考",
            "module_description": "按步骤切换 ARKit 52 参考形态键，自动归零其它参考键，并显示当前步骤的注意重点与技巧。",
            "data_file": os.path.join(builtin_special_presets_dir(), "arkit形态键工作流参考.json"),
            "script_file": os.path.join(builtin_special_presets_dir(), "arkit形态键工作流参考.py"),
            "image_folder": os.path.join(builtin_special_presets_dir(), "arkit形态键工作流参考_images"),
            "viewer_script": os.path.join(builtin_special_presets_dir(), "arkit_reference_viewer.ps1"),
            "extra_script_library_names": ("ARKit 形态键合成", "口型生成（Go Workflow 面板版）", "形态键鉴定"),
        }
    return None


def apply_special_preset_to_module(workflow, module, spec):
    if workflow is None or module is None or not isinstance(spec, dict):
        return False
    module.name = spec.get("module_name", module.name or "特殊预设模块")
    module.enabled = True
    module.use_custom_panel = True
    module.runtime_panel_expanded = True
    module.panel_title = spec.get("preset_name", module.name)
    module.panel_description = spec.get("workflow_description", "")
    module.description = spec.get("module_description", "")
    script_file = spec.get("script_file", "")
    if script_file:
        module.script_path = script_file
        try:
            if os.path.isfile(script_file):
                module.script_source = read_cached_text_file(script_file, encodings=("utf-8", "utf-8-sig"))
        except Exception:
            traceback.print_exc()
    return True


def apply_module_payload_to_existing_module(module, module_data):
    if module is None or not isinstance(module_data, dict):
        return False
    changed = False
    updates = (
        ("name", normalize_workflow_name(module_data.get("name", ""), getattr(module, "name", "") or "模块")),
        ("enabled", bool(module_data.get("enabled", True))),
        ("use_custom_panel", bool(module_data.get("use_custom_panel", False))),
        ("runtime_panel_expanded", bool(module_data.get("runtime_panel_expanded", True))),
        ("panel_title", normalize_text_value(module_data.get("panel_title", ""), "")),
        ("panel_description", normalize_text_value(module_data.get("panel_description", ""), "")),
        ("script_path", module_data.get("script_path", "")),
        ("description", normalize_text_value(module_data.get("description", ""), "")),
        ("text_block_name", normalize_text_value(module_data.get("text_block_name", ""), "")),
        ("script_source", module_data.get("script_source", "")),
        ("config_payload", module_data.get("config_payload", "")),
        ("ai_doc", module_data.get("ai_doc", "")),
    )
    for attr, value in updates:
        if getattr(module, attr) != value:
            setattr(module, attr, value)
            changed = True
    return changed


def refresh_builtin_workflow_modules(state):
    if state is None:
        return 0
    changed = 0
    special_spec = special_preset_spec(SPECIAL_PRESET_ARKIT_52)
    builtin_payloads = {payload.get("name", ""): payload for payload in builtin_script_library_payloads()}
    builtin_panel_titles = {
        (payload.get("panel_title", "") or "").strip(): payload
        for payload in builtin_script_library_payloads()
        if (payload.get("panel_title", "") or "").strip()
    }
    preset_script_name = os.path.basename(special_spec.get("script_file", "")) if special_spec else ""
    for workflow in getattr(state, "workflows", []):
        workflow_has_special_preset = False
        existing_module_names = set()
        for module in getattr(workflow, "modules", []):
            module_name = getattr(module, "name", "") or ""
            if module_name:
                existing_module_names.add(module_name)
            panel_title = getattr(module, "panel_title", "") or ""
            script_name = os.path.basename((getattr(module, "script_path", "") or "").strip())
            if special_spec and (
                module_name == special_spec.get("module_name", "")
                or panel_title == special_spec.get("preset_name", "")
                or (preset_script_name and script_name == preset_script_name)
            ):
                workflow_has_special_preset = True
                before = (
                    getattr(module, "script_path", ""),
                    getattr(module, "script_source", ""),
                    getattr(module, "config_payload", ""),
                    getattr(module, "panel_title", ""),
                    getattr(module, "panel_description", ""),
                    getattr(module, "description", ""),
                    getattr(module, "use_custom_panel", False),
                    getattr(module, "runtime_panel_expanded", True),
                )
                apply_special_preset_to_module(workflow, module, special_spec)
                module.runtime_panel_expanded = True
                after = (
                    getattr(module, "script_path", ""),
                    getattr(module, "script_source", ""),
                    getattr(module, "config_payload", ""),
                    getattr(module, "panel_title", ""),
                    getattr(module, "panel_description", ""),
                    getattr(module, "description", ""),
                    getattr(module, "use_custom_panel", False),
                    getattr(module, "runtime_panel_expanded", True),
                )
                if before != after:
                    changed += 1
                continue
            payload = builtin_payloads.get(module_name)
            if payload is None:
                payload = builtin_panel_titles.get((panel_title or "").strip())
            if payload is None:
                continue
            payload_data = dict(payload)
            payload_data["enabled"] = getattr(module, "enabled", True)
            payload_data["runtime_panel_expanded"] = True
            if apply_module_payload_to_existing_module(module, payload_data):
                changed += 1
        if workflow_has_special_preset and special_spec:
            for script_name in special_spec.get("extra_script_library_names", []):
                if script_name in existing_module_names:
                    continue
                payload = builtin_payloads.get(script_name)
                if payload is None:
                    continue
                module_data = dict(payload)
                module_data["enabled"] = True
                module_data["runtime_panel_expanded"] = True
                apply_module_payload_to_workflow(workflow, module_data)
                existing_module_names.add(script_name)
                changed += 1
    return changed


def create_special_preset_workflow(scene, preset_type):
    spec = special_preset_spec(preset_type)
    if spec is None:
        return []
    created = create_synced_workflow_for_all_spaces(scene, spec.get("workflow_name", "特殊预设"))
    if not created:
        return []
    for _space_type, workflow in created:
        workflow.description = spec.get("workflow_description", "")
        module = workflow.modules.add()
        apply_special_preset_to_module(workflow, module, spec)
        for script_name in spec.get("extra_script_library_names", []):
            payload = builtin_script_library_payload_by_name(script_name)
            if payload is None:
                continue
            module_data = dict(payload)
            module_data["enabled"] = True
            module_data["runtime_panel_expanded"] = True
            apply_module_payload_to_workflow(workflow, module_data)
        workflow.active_module_index = 0
    return created


def default_module_script_path(workflow_name, module_name):
    filename = f"{safe_filename_component(workflow_name, 'workflow')}_{safe_filename_component(module_name, 'module')}.py"
    return os.path.join(default_module_scripts_dir(), filename)


def normalized_abs_path(path):
    if not path:
        return ""
    return os.path.normcase(os.path.normpath(bpy.path.abspath(path)))


def is_default_module_script_path(path):
    normalized = normalized_abs_path(path)
    if not normalized:
        return False
    default_dir = normalized_abs_path(default_module_scripts_dir())
    legacy_dir = normalized_abs_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bworkflow_modules"))
    return os.path.dirname(normalized) in {default_dir, legacy_dir}


def workflow_module_index(workflow, module):
    try:
        for index, item in enumerate(getattr(workflow, "modules", [])):
            if item == module:
                return index
    except Exception:
        pass
    return -1


def unique_default_module_script_path(workflow, module):
    workflow_name = getattr(workflow, "name", "") or "workflow"
    module_name = getattr(module, "name", "") or "module"
    base_name = f"{safe_filename_component(workflow_name, 'workflow')}_{safe_filename_component(module_name, 'module')}"
    base_dir = default_module_scripts_dir()
    used_paths = set()
    for item in getattr(workflow, "modules", []):
        if item == module:
            continue
        used_paths.add(normalized_abs_path(getattr(item, "script_path", "")))

    suffix = 1
    while True:
        suffix_text = "" if suffix == 1 else f"_{suffix}"
        candidate = os.path.join(base_dir, f"{base_name}{suffix_text}.py")
        if normalized_abs_path(candidate) not in used_paths:
            return candidate
        suffix += 1


def module_script_path_conflicts(workflow, module, filepath):
    target = normalized_abs_path(filepath)
    if not target:
        return False
    for item in getattr(workflow, "modules", []):
        if item == module:
            continue
        if normalized_abs_path(getattr(item, "script_path", "")) == target:
            return True
    return False


def ensure_module_script_path_matches_name(workflow, module, force=False):
    current_path = (getattr(module, "script_path", "") or "").strip()
    should_generate = force or not current_path or is_default_module_script_path(current_path)
    if current_path and module_script_path_conflicts(workflow, module, current_path):
        should_generate = True
    if should_generate:
        module.script_path = unique_default_module_script_path(workflow, module)
    return module.script_path


def normalize_module_script_path(workflow, module):
    raw_path = (module.script_path or "").strip()
    if not raw_path:
        module.script_path = unique_default_module_script_path(workflow, module)
        return

    absolute_path = bpy.path.abspath(raw_path)
    legacy_path = legacy_default_module_script_path(workflow.name, module.name)
    if normalized_abs_path(absolute_path) == normalized_abs_path(legacy_path):
        module.script_path = unique_default_module_script_path(workflow, module)


def build_module_ai_doc_legacy_unused(workflow, module):
    script_path = module.script_path or unique_default_module_script_path(workflow, module)
    panel_title = module.panel_title.strip() or module.name or "自定义面板"
    return "\n".join(
        [
            "# Go工作流通用脚本模块说明",
            f"- 模块名称: {module.name}",
            f"- 所属工作流: {workflow.name}",
            f"- 建议脚本路径: {script_path}",
            f"- 需要自定义面板: {'是' if module.use_custom_panel else '否'}",
            f"- 自定义面板显示名: {panel_title}",
            "",
            "目标: 生成一个可以直接放进 Go工作流 的 Blender Python 工具脚本。请根据模块名称和模块说明判断具体用途，不要假设固定任务类型；脚本应该可读、可维护、错误提示清楚，并优先使用 Blender 数据 API。",
            "",
            "必须遵守的接口:",
            "1. 必须定义 run(context, scene, workflow, module)。点击“运行模块”时会调用它。",
            "2. 可选定义 draw_panel(layout, context, scene, workflow, module, panel_api, module_state)。只绘制本模块的 UI，不要注册新的 Panel/Class，不要修改 Go工作流 外壳。",
            "3. 可选定义 on_panel_action(action, context, scene, workflow, module, panel_api, module_state)。按钮通过 panel_api.draw_button(...) 触发。",
            "4. 运行环境会把 bpy、context、scene、workflow、module、panel_api、module_state 放进脚本全局变量；run(...) 虽然只有 5 个参数，也可以直接读取 panel_api/module_state。",
            "5. 绘制 draw_panel 时不要写入场景数据；需要改数据时放到 run(...) 或 on_panel_action(...)。",
            "6. 成功返回 {'FINISHED'}；用户操作不满足条件时 raise Exception('清楚的人类可读错误') 或返回 {'CANCELLED'}。",
            "",
            "panel_api 可用能力:",
            "- 布局: box/layout.section/row/column/separator/label，用于组织清楚的参数区、操作区、提示区。",
            "- 可读写字段: draw_object_picker、draw_active_object_capture、draw_text_input、draw_toggle、draw_float_input、draw_int_input。",
            "- 读取字段: get_object、get_text、get_bool、get_float、get_int。",
            "- 写入字段: set_object、set_text、set_bool、set_float、set_int。字段会保存到当前场景，不要自己写全局变量。",
            "- 操作按钮: draw_button(layout, action, label, icon) 调用 on_panel_action；draw_run_button(...) 调用 run(...)。",
            "",
            "生成代码时的安全规则:",
            "- 根据任务先校验上下文、对象、模式、选择数量、数据类型和用户输入；错误信息要说明用户该怎么修正。",
            "- 批量处理时跳过非法项，或在确实无法继续时 raise Exception。",
            "- 创建修改器、约束、材质、集合、文本、属性或其他数据块时优先复用同名项，避免重复叠加。",
            "- 不要自动删除用户数据；如需覆盖、应用、删除或写文件，提供明确开关，例如 overwrite、apply_result、delete_source。",
            "- 不要在 draw_panel 里执行耗时操作、切换模式、创建物体、写文件或调用 bpy.ops。",
            "- 如果需要 bpy.ops，先检查 context/mode/active_object，并尽量用数据 API 替代。",
            "",
            "通用骨架:",
            "import bpy",
            "",
            "def _selected_objects(context):",
            "    return list(getattr(context, 'selected_objects', []) or [])",
            "",
            "def _validate(context, scene, workflow, module):",
            "    # 按模块需求改写这里：检查对象、模式、输入参数、文件路径或场景状态。",
            "    return _selected_objects(context)",
            "",
            "def run(context, scene, workflow, module):",
            "    items = _validate(context, scene, workflow, module)",
            "    dry_run = panel_api.get_bool('dry_run', False)",
            "    processed = 0",
            "    for item in items:",
            "        # TODO: 在这里写模块真正要做的事情。",
            "        # 示例: 读取/修改对象属性、创建数据块、批量重命名、检查场景、导入导出等。",
            "        if dry_run:",
            "            continue",
            "        processed += 1",
            "    module_state.set('last_result', f'已检查 {len(items)} 项，执行 {processed} 项')",
            "    return {'FINISHED'}",
            "",
            "def draw_panel(layout, context, scene, workflow, module, panel_api, module_state):",
            "    settings = panel_api.section(layout, '参数', icon='TOOL_SETTINGS')",
            "    panel_api.draw_text_input(settings, 'name_prefix', '名称/前缀', default=module.name or 'GoWorkflowTool')",
            "    panel_api.draw_toggle(settings, 'dry_run', '只检查不写入', default=True)",
            "    status = module_state.get('last_result', '')",
            "    if status:",
            "        panel_api.label(layout, status, icon='CHECKMARK')",
            "    actions = panel_api.row(layout)",
            "    panel_api.draw_button(actions, 'preview', '预览/检查', icon='VIEWZOOM')",
            "    panel_api.draw_run_button(actions, '执行', icon='PLAY')",
            "",
            "def on_panel_action(action, context, scene, workflow, module, panel_api, module_state):",
            "    if action == 'preview':",
            "        items = _selected_objects(context)",
            "        module_state.set('last_result', f'当前选择 {len(items)} 项；请根据模块说明补充更具体的检查。')",
            "        return {'FINISHED'}",
            "    return {'CANCELLED'}",
        ]
    )

def build_module_script_template(workflow, module):
    return ""


def build_module_ai_doc_v029_unused(workflow, module):
    script_path = module.script_path or unique_default_module_script_path(workflow, module)
    panel_title = module.panel_title.strip() or module.name or "自定义面板"
    return "\n".join(
        [
            "# Go工作流脚本模块开发说明",
            f"- 模块名称: {module.name}",
            f"- 所属工作流: {workflow.name}",
            f"- 目标 .py 路径: {script_path}",
            f"- 是否需要自定义面板: {'是' if module.use_custom_panel else '否'}",
            f"- 面板显示名: {panel_title}",
            "",
            "任务: 根据模块名称和模块说明，生成一个可直接保存为 .py 的 Blender Python 脚本。代码要短、清楚、可维护，优先使用 bpy 数据 API。",
            "",
            "必须接口:",
            "1. 必须定义 run(context, scene, workflow, module)，按钮运行时会调用它。",
            "2. 成功返回 {'FINISHED'}；条件不满足时 raise Exception('给用户看的中文错误原因')。",
            "3. 不要在导入脚本时执行真实操作，所有写入都放进 run 或按钮回调。",
            "",
            "可选自定义面板:",
            "def draw_panel(layout, context, scene, workflow, module, panel_api, module_state):",
            "    # 只画 UI，不改场景、不写文件、不调用 bpy.ops",
            "    pass",
            "",
            "def on_panel_action(action, context, scene, workflow, module, panel_api, module_state):",
            "    # 处理 draw_button 触发的轻量按钮",
            "    return {'FINISHED'}",
            "",
            "panel_api 常用方法:",
            "- 布局: section(layout, title), row(layout), column(layout), label(layout, text, icon='INFO')",
            "- 输入: draw_object_picker, draw_text_input, draw_toggle, draw_float_input, draw_int_input",
            "- 读取: get_object, get_text, get_bool, get_float, get_int",
            "- 写入: set_object, set_text, set_bool, set_float, set_int",
            "- 按钮: draw_button(layout, action, label, icon='PLAY'), draw_run_button(layout, label, icon='PLAY')",
            "",
            "生成要求:",
            "- 先检查对象、模式、选择、路径、输入参数；错误信息写给普通用户看。",
            "- 批处理时跳过不适用对象，必要时统计处理数量。",
            "- 不自动删除用户数据；覆盖、删除、写文件前要有明确开关。",
            "- draw_panel 只负责显示和读取参数，真正修改数据放到 run 或 on_panel_action。",
            "",
            "推荐代码骨架:",
            "import bpy",
            "",
            "def _selected(context):",
            "    return list(getattr(context, 'selected_objects', []) or [])",
            "",
            "def _validate(context):",
            "    items = _selected(context)",
            "    if not items:",
            "        raise Exception('请先选择要处理的对象')",
            "    return items",
            "",
            "def run(context, scene, workflow, module):",
            "    items = _validate(context)",
            "    processed = 0",
            "    for obj in items:",
            "        # TODO: 在这里写模块真正要做的事",
            "        processed += 1",
            "    module_state.set('last_result', f'已处理 {processed} 个对象')",
            "    return {'FINISHED'}",
            "",
            "def draw_panel(layout, context, scene, workflow, module, panel_api, module_state):",
            "    box = panel_api.section(layout, '参数')",
            "    panel_api.draw_toggle(box, 'dry_run', '只检查不写入', default=True)",
            "    status = module_state.get('last_result', '')",
            "    if status:",
            "        panel_api.label(layout, status, icon='CHECKMARK')",
            "    panel_api.draw_run_button(layout, '运行', icon='PLAY')",
        ]
    )


def build_module_ai_doc(workflow, module):
    script_path = module.script_path or unique_default_module_script_path(workflow, module)
    needs_panel = bool(getattr(module, "use_custom_panel", False))
    module_description = compact_multiline_text(
        getattr(module, "description", ""),
        AI_DOC_DESCRIPTION_MAX_CHARS,
        max_lines=12,
    ) or "未填写，请根据模块名称补全合理的功能目标。"
    lines = [
        "# Go工作流通用脚本模块开发说明",
        f"- 模块名称: {module.name}",
        f"- 所属工作流: {workflow.name}",
        f"- 目标 .py 路径: {script_path}",
        f"- 自定义面板: {'启用' if needs_panel else '关闭'}",
        "",
        "模块说明:",
        module_description,
        "",
        "必须接口:",
        "1. 必须定义 run(context, scene, workflow, module)。",
        "2. 成功返回 {'FINISHED'}；条件不满足时 raise Exception('给用户看的中文错误原因')。",
        "3. 导入脚本时不要执行真实操作，所有写入都放进 run 或 on_panel_action。",
        "",
        "运行环境:",
        "- 已注入: bpy, context, scene, workflow, module, panel_api, module_state。",
        "- `module_state` 适合保存短状态、日志、最近结果。",
        "- `module.config_payload` 可保存会随 `.goworkflow` 导出的附加文本配置，例如 csv/json。",
        "",
        "通用要求:",
        "- 先检查对象、模式、选择、路径、输入参数和数据块类型。",
        "- 批处理时跳过不适用对象，并统计处理数量。",
        "- 不自动删除用户数据；覆盖、删除、写文件前要有明确开关。",
        "- 如果没有特殊要求，不要使用 bpy.ops；需要时先检查 context/mode/active_object。",
        "- 记录短状态: module_state.set('last_result', 文本) 或 panel_api.set_status(文本)。",
        "",
        "基础脚本模板骨架（建议直接按这个结构生成）:",
        "```python",
        "import bpy",
        "",
        "def _selected(context):",
        "    return list(getattr(context, 'selected_objects', []) or [])",
        "",
        "def _validate(context, scene, workflow, module):",
        "    items = _selected(context)",
        "    if not items:",
        "        raise Exception('请先选择要处理的对象')",
        "    return items",
        "",
        "def run(context, scene, workflow, module):",
        "    items = _validate(context, scene, workflow, module)",
        "    processed = 0",
        "    for obj in items:",
        "        if obj is None:",
        "            continue",
        "        # TODO: 在这里写真正操作。批处理时跳过不适用对象。",
        "        processed += 1",
        "    module_state.set('last_result', f'已处理 {processed} 个对象')",
        "    return {'FINISHED'}",
        "```",
    ]
    if needs_panel:
        lines.extend(
            [
                "",
                "自定义面板已启用:",
                "- 可定义 draw_panel(layout, context, scene, workflow, module, panel_api, module_state)。",
                "- 可定义 on_panel_action(action, context, scene, workflow, module, panel_api, module_state)。",
                "- draw_panel 只画 UI；不要改场景、写文件、切模式、调用 bpy.ops。",
                "- on_panel_action 处理 draw_button / 字段回写后的动作；动作名可能是 FIELD_WRITE::字段名。",
                "- 若字段需要长期保存，建议在 FIELD_WRITE::字段名 或 run(...) 中同步写入 module.config_payload / 模块配置，不要只依赖运行时临时字段。",
                "- 参考图/预览类模块优先复用插件现有的 Blender 内部参考通道，不要再额外启动外部窗口或常驻子进程。",
                "",
                "panel_api 接口:",
                "- 布局: section(layout, title, icon='NONE'), row(layout), column(layout), separator(layout), label(layout, text, icon='INFO')。",
                "- 文本/提示: draw_note, draw_status, draw_log, set_status, get_status。",
                "- 基础输入: draw_text_input, draw_toggle, draw_float_input, draw_int_input, draw_enum。",
                "- 数据块输入: draw_object_picker, draw_active_object_capture, draw_material_picker, draw_collection_picker, draw_text_block_picker。",
                "- 按钮: draw_button(layout, action, label, icon='NONE'), draw_run_button(layout, label, icon='PLAY')。",
                "- 读取: get_text, get_bool, get_float, get_int, get_enum, get_object, get_material, get_collection, get_text_block。",
                "- 写入: set_text, set_bool, set_float, set_int, set_enum, set_object, set_data_block, clear_value。",
                "- 上下文: active_object(), selected_objects(type=None), visible_objects(type=None), context_summary()。",
                "",
                "自定义面板 UI 骨架（字段 key 用英文，label 可用中文）:",
                "```python",
                "def draw_panel(layout, context, scene, workflow, module, panel_api, module_state):",
                "    box = panel_api.section(layout, module.name or '参数', icon='TOOL_SETTINGS')",
                "    panel_api.draw_object_picker(box, 'target_object', '目标对象')",
                "    panel_api.draw_text_input(box, 'name_prefix', '名称前缀', default='')",
                "    panel_api.draw_toggle(box, 'dry_run', '只检查不写入', default=True)",
                "    panel_api.draw_float_input(box, 'strength', '强度', default=1.0, min=0.0, max=1.0)",
                "    status = module_state.get('last_result', '')",
                "    if status:",
                "        panel_api.label(layout, status, icon='INFO')",
                "    row = panel_api.row(layout, align=True)",
                "    panel_api.draw_button(row, 'preview', '预览', icon='VIEWZOOM')",
                "    panel_api.draw_run_button(row, '运行', icon='PLAY')",
                "",
                "def on_panel_action(action, context, scene, workflow, module, panel_api, module_state):",
                "    if action == 'preview':",
                "        module_state.set('last_result', '预览完成')",
                "        return {'FINISHED'}",
                "    if action.startswith('FIELD_WRITE::'):",
                "        return {'FINISHED'}",
                "    return {'FINISHED'}",
                "```",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "自定义面板已关闭:",
                "- 不要生成 draw_panel。",
                "- 不要生成 on_panel_action。",
                "- 不要使用 panel_api 绘制输入 UI。",
                "- 只输出 run 相关逻辑；需要参数时从模块说明或场景上下文读取。",
            ]
        )
    return finalize_ai_doc(lines)


def ensure_module_script_source(workflow, module):
    normalize_module_script_path(workflow, module)
    return module.script_source


def current_module_script_source(workflow, module, prefer_text_block=True, allow_initialize=True):
    if prefer_text_block:
        text_name = module.text_block_name.strip()
        if text_name:
            text_block = bpy.data.texts.get(text_name)
            if text_block is not None:
                return text_block.as_string().lstrip("\ufeff")
    if module.script_source.strip():
        return module.script_source.lstrip("\ufeff")
    if sync_module_source_from_file(module):
        return module.script_source.lstrip("\ufeff")
    if allow_initialize:
        return ensure_module_script_source(workflow, module).lstrip("\ufeff")
    return ""


def module_state_key(module):
    raw_name = (getattr(module, "name", "") or "").strip()
    return slugify_filename(raw_name, "module")


def workflow_state_key(workflow):
    raw_name = (getattr(workflow, "name", "") or "").strip()
    return slugify_filename(raw_name, "workflow")


def module_scene_prop_key(workflow, module, key, kind="value"):
    workflow_key = workflow_state_key(workflow)[:6]
    module_key = module_state_key(module)[:6]
    kind_key = slugify_filename(kind, "value")[:3]
    field_key = slugify_filename(key, "field")[:12]
    digest_source = "|".join(
        [
            workflow_state_key(workflow),
            module_state_key(module),
            str(kind or "value"),
            str(key or ""),
        ]
    )
    digest = hashlib.sha1(digest_source.encode("utf-8", "ignore")).hexdigest()[:8]
    return "_gwf_{workflow_key}_{module_key}_{kind_key}_{field_key}_{digest}".format(
        workflow_key=workflow_key or "workflow",
        module_key=module_key or "module",
        kind_key=kind_key or "val",
        field_key=field_key or "field",
        digest=digest,
    )


def module_scene_prop_key_legacy(workflow, module, key, kind="value"):
    return "_go_workflow_runtime_{workflow_key}_{module_key}_{kind}_{field_key}".format(
        workflow_key=workflow_state_key(workflow),
        module_key=module_state_key(module),
        kind=slugify_filename(kind, "value"),
        field_key=slugify_filename(key, "field"),
    )


def module_scene_prop_key_usable(prop_key):
    return bool(prop_key) and len(str(prop_key)) <= 63


def module_scene_prop_key_candidates(workflow, module, key, kind="value"):
    candidates = []
    for candidate in (
        module_scene_prop_key(workflow, module, key, kind=kind),
        module_scene_prop_key_legacy(workflow, module, key, kind=kind),
    ):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return candidates


def module_runtime_specs_key(workflow, module):
    return "_go_workflow_runtime_specs_{workflow_key}_{module_key}".format(
        workflow_key=workflow_state_key(workflow),
        module_key=module_state_key(module),
    )


def normalize_module_field_default(kind, default):
    kind = str(kind or "text")
    if kind == "bool":
        return bool(default)
    if kind == "float":
        try:
            return float(default)
        except Exception:
            return 0.0
    if kind == "int":
        try:
            return int(default)
        except Exception:
            return 0
    if kind == "object":
        return str(default or "")
    return str(default or "")


def ensure_module_runtime_store(scene, workflow, module, allow_writes=True):
    if scene is None:
        return {}
    root_key = "_go_workflow_module_state"
    root_store = scene.get(root_key)
    if not isinstance(root_store, dict):
        root_store = {}
    workflow_key = slugify_filename(getattr(workflow, "name", ""), "workflow")
    workflow_store = root_store.get(workflow_key)
    if not isinstance(workflow_store, dict):
        workflow_store = {}
    module_key = module_state_key(module)
    module_store = workflow_store.get(module_key)
    if not isinstance(module_store, dict):
        module_store = {}
    if allow_writes:
        workflow_store[module_key] = module_store
        root_store[workflow_key] = workflow_store
        scene[root_key] = root_store
    return module_store


def save_module_runtime_store(scene, workflow, module, module_store):
    if scene is None:
        return
    root_key = "_go_workflow_module_state"
    root_store = scene.get(root_key)
    if not isinstance(root_store, dict):
        root_store = {}
    workflow_key = slugify_filename(getattr(workflow, "name", ""), "workflow")
    workflow_store = root_store.get(workflow_key)
    if not isinstance(workflow_store, dict):
        workflow_store = {}
    workflow_store[module_state_key(module)] = dict(module_store or {})
    root_store[workflow_key] = workflow_store
    scene[root_key] = root_store


def save_module_runtime_specs(scene, workflow, module, field_specs):
    if scene is None:
        return
    specs_key = module_runtime_specs_key(workflow, module)
    saved = []
    seen = set()
    for spec in field_specs or []:
        key = str(spec.get("key", "")).strip()
        kind = str(spec.get("kind", "text")).strip() or "text"
        if not key:
            continue
        pair = (key, kind)
        if pair in seen:
            continue
        seen.add(pair)
        saved.append(
            {
                "key": key,
                "kind": kind,
                "default": normalize_module_field_default(kind, spec.get("default")),
                "prop_key": module_scene_prop_key(workflow, module, key, kind=kind),
                "legacy_prop_key": module_scene_prop_key_legacy(workflow, module, key, kind=kind),
            }
        )
    scene[specs_key] = saved


def load_module_runtime_specs(scene, workflow, module):
    if scene is None:
        return []
    specs = scene.get(module_runtime_specs_key(workflow, module), [])
    if not isinstance(specs, (list, tuple)):
        return []
    normalized = []
    seen = set()
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        key = str(spec.get("key", "")).strip()
        kind = str(spec.get("kind", "text")).strip() or "text"
        if not key:
            continue
        pair = (key, kind)
        if pair in seen:
            continue
        seen.add(pair)
        normalized.append(
            {
                "key": key,
                "kind": kind,
                "default": normalize_module_field_default(kind, spec.get("default")),
                "prop_key": module_scene_prop_key(workflow, module, key, kind=kind),
                "legacy_prop_key": module_scene_prop_key_legacy(workflow, module, key, kind=kind),
            }
        )
    return normalized


def migrate_module_runtime_scene_values(scene, workflow, module, field_specs):
    if scene is None:
        return False
    changed = False
    for spec in field_specs or []:
        key = spec.get("key")
        kind = spec.get("kind", "text")
        if not key:
            continue
        candidates = module_scene_prop_key_candidates(workflow, module, key, kind=kind)
        if not candidates:
            continue
        primary_key = candidates[0]
        if not module_scene_prop_key_usable(primary_key):
            continue
        if primary_key in scene:
            continue
        fallback_value = None
        found = False
        for candidate in candidates[1:]:
            if module_scene_prop_key_usable(candidate) and candidate in scene:
                fallback_value = scene.get(candidate)
                found = True
                break
        if not found:
            continue
        try:
            scene[primary_key] = fallback_value
            changed = True
        except Exception:
            continue
    return changed


def module_runtime_field_scene_value(scene, workflow, module, key, kind, default=None):
    if scene is None:
        return default, ""
    for prop_key in module_scene_prop_key_candidates(workflow, module, key, kind=kind):
        if not module_scene_prop_key_usable(prop_key):
            continue
        if prop_key in scene:
            return scene.get(prop_key, default), prop_key
    return default, ""


def merge_module_runtime_store(scene, workflow, module, module_store):
    existing = ensure_module_runtime_store(scene, workflow, module, allow_writes=False)
    merged = dict(existing or {})
    merged.update(dict(module_store or {}))
    save_module_runtime_store(scene, workflow, module, merged)
    return merged


def module_runtime_context(context, scene, workflow, module, allow_writes=True, field_specs=None):
    module_store = ensure_module_runtime_store(scene, workflow, module, allow_writes=allow_writes)

    class ModuleStateProxy:
        def __init__(self, initial):
            self._data = dict(initial or {})

        def get(self, key, default=None):
            return self._data.get(key, default)

        def set(self, key, value):
            self._data[key] = value
            return value

        def pop(self, key, default=None):
            return self._data.pop(key, default)

        def update(self, values=None, **kwargs):
            if isinstance(values, dict):
                self._data.update(values)
            elif values is not None:
                try:
                    self._data.update(dict(values))
                except Exception:
                    pass
            if kwargs:
                self._data.update(kwargs)
            return dict(self._data)

        def clear(self):
            self._data.clear()

        def keys(self):
            return self._data.keys()

        def items(self):
            return self._data.items()

        def append_log(self, message, key="debug_log", max_items=20):
            items = self._data.get(key)
            if not isinstance(items, list):
                items = []
            items.append(str(message))
            if max_items and len(items) > max_items:
                items = items[-max_items:]
            self._data[key] = items
            return items

        def to_dict(self):
            return dict(self._data)

    class ModulePanelAPI:
        def __init__(self, ctx, scn, store_proxy):
            self.context = ctx
            self.scene = scn
            self.state = store_proxy
            self.allow_writes = allow_writes
            self.field_specs = field_specs if field_specs is not None else []
            self.workflow_name = getattr(workflow, "name", "")
            self.module_name = getattr(module, "name", "")
            self.module_index = -1
            try:
                for item_index, item in enumerate(getattr(workflow, "modules", [])):
                    if item == module:
                        self.module_index = item_index
                        break
            except Exception:
                self.module_index = -1

        def _prop_key(self, key, kind="value"):
            return module_scene_prop_key(workflow, module, key, kind=kind)

        def _prop_path(self, key, kind="value"):
            return f'["{self._prop_key(key, kind=kind)}"]'

        def _data_kind(self, collection_name):
            return f"datablock_{slugify_filename(collection_name, 'data')}"

        def compact_text(self, text, max_chars=UI_LABEL_MAX_CHARS, fallback=""):
            return compact_inline_text(text, max_chars=max_chars, fallback=fallback)

        def plain_text(self, text, fallback=""):
            value = str(text if text is not None else fallback)
            return value if value else str(fallback or "")

        def _remember_field(self, key, default, kind="value"):
            self.field_specs.append(
                {
                    "key": key,
                    "kind": kind,
                    "default": default,
                    "prop_key": self._prop_key(key, kind=kind),
                }
            )

        def _ensure_scene_value(self, key, default, kind="value"):
            prop_key = self._prop_key(key, kind=kind)
            self._remember_field(key, default, kind=kind)
            if self.scene is None:
                return prop_key
            if module_scene_prop_key_usable(prop_key) and prop_key not in self.scene and self.allow_writes:
                try:
                    self.scene[prop_key] = default
                except Exception:
                    pass
            return prop_key

        def box(self, layout):
            return layout.box()

        def row(self, layout, align=True):
            return layout.row(align=align)

        def column(self, layout, align=True):
            return layout.column(align=align)

        def separator(self, layout):
            layout.separator()

        def label(self, layout, text, icon="NONE"):
            layout.label(text=self.plain_text(text), icon=icon)

        def section(self, layout, title, icon="NONE"):
            box = layout.box()
            box.label(text=self.plain_text(title), icon=icon)
            return box

        def draw_note(self, layout, text, icon="INFO", limit=4, width=48):
            for line in split_preview_lines(str(text or ""), limit=limit, width=width):
                layout.label(text=self.plain_text(line), icon=icon)
                icon = "NONE"

        def draw_image_preview(self, layout, image=None, label="", scale=8.0, fallback=""):
            if label:
                layout.label(text=self.plain_text(label), icon="IMAGE_REFERENCE")
            if image is None:
                if fallback:
                    layout.label(text=self.plain_text(fallback), icon="INFO")
                return False
            try:
                preview_ensure = getattr(image, "preview_ensure", None)
                if callable(preview_ensure):
                    preview_ensure()
                preview = getattr(image, "preview", None)
                icon_id = getattr(preview, "icon_id", 0) if preview is not None else 0
                if icon_id:
                    layout.template_icon(icon_value=icon_id, scale=max(1.0, float(scale)))
                    return True
            except Exception:
                pass
            fallback_name = getattr(image, "name", "") or fallback
            if fallback_name:
                layout.label(text=self.plain_text(fallback_name), icon="FILE_IMAGE")
            return False

        def draw_button(self, layout, action, label=None, icon="NONE", tooltip=""):
            op = layout.operator(
                "bworkflow.module_runtime_action",
                text=self.compact_text(label or action, max_chars=UI_BUTTON_MAX_CHARS),
                icon=icon,
            )
            op.workflow_name = self.workflow_name
            op.module_name = self.module_name
            op.action_name = str(action)
            op.tooltip_text = str(tooltip or "")
            return op

        def draw_run_button(self, layout, label="运行模块", icon="PLAY"):
            op = layout.operator(
                "bworkflow.module_run",
                text=self.compact_text(label, max_chars=UI_BUTTON_MAX_CHARS),
                icon=icon,
            )
            op.module_index = self.module_index
            return op

        def get_value(self, key, default=None, kind="text"):
            kind = str(kind or "text")
            if kind == "bool":
                return self.get_bool(key, bool(default))
            if kind == "float":
                return self.get_float(key, 0.0 if default is None else default)
            if kind == "int":
                return self.get_int(key, 0 if default is None else default)
            if kind == "object":
                return self.get_object(key, default)
            if kind == "enum":
                return self.get_enum(key, default)
            return self.get_text(key, "" if default is None else default)

        def set_value(self, key, value, kind="text"):
            kind = str(kind or "text")
            if kind == "bool":
                return self.set_bool(key, value)
            if kind == "float":
                return self.set_float(key, value)
            if kind == "int":
                return self.set_int(key, value)
            if kind == "object":
                return self.set_object(key, value)
            if kind == "enum":
                return self.set_enum(key, value)
            return self.set_text(key, value)

        def clear_value(self, key, kind=None):
            kinds = [kind] if kind else ["text", "bool", "float", "int", "object", "enum", "value"]
            for item_kind in kinds:
                prop_key = self._prop_key(key, kind=item_kind)
                if (
                    self.scene is not None
                    and self.allow_writes
                    and module_scene_prop_key_usable(prop_key)
                    and prop_key in self.scene
                ):
                    try:
                        del self.scene[prop_key]
                    except Exception:
                        pass
            if self.allow_writes:
                self.state.pop(key, None)
            return None

        def get_object(self, key, default=None):
            scene_name = ""
            if self.scene is not None:
                scene_name, _scene_prop_key = module_runtime_field_scene_value(
                    self.scene, workflow, module, key, "object", ""
                )
            name = scene_name or self.state.get(key, "")
            if not name:
                return default
            return bpy.data.objects.get(name, default)

        def set_object(self, key, obj):
            name = getattr(obj, "name", "") if obj is not None else ""
            self._remember_field(key, name, kind="object")
            if self.scene is not None and self.allow_writes:
                prop_key = self._prop_key(key, kind="object")
                if module_scene_prop_key_usable(prop_key):
                    try:
                        self.scene[prop_key] = name
                    except Exception:
                        pass
            if self.allow_writes:
                self.state.set(key, name)
            return obj

        def get_text(self, key, default=""):
            self._remember_field(key, str(default), kind="text")
            scene_value, scene_prop_key = module_runtime_field_scene_value(
                self.scene, workflow, module, key, "text", self.state.get(key, default)
            )
            if scene_prop_key:
                return str(scene_value)
            return str(self.state.get(key, default))

        def set_text(self, key, value):
            text_value = str(value)
            self._remember_field(key, text_value, kind="text")
            if self.scene is not None and self.allow_writes:
                prop_key = self._prop_key(key, kind="text")
                if module_scene_prop_key_usable(prop_key):
                    try:
                        self.scene[prop_key] = text_value
                    except Exception:
                        pass
            if self.allow_writes:
                self.state.set(key, text_value)
            return text_value

        def get_bool(self, key, default=False):
            self._remember_field(key, bool(default), kind="bool")
            scene_value, scene_prop_key = module_runtime_field_scene_value(
                self.scene, workflow, module, key, "bool", self.state.get(key, default)
            )
            if scene_prop_key:
                return bool(scene_value)
            return bool(self.state.get(key, default))

        def set_bool(self, key, value):
            bool_value = bool(value)
            self._remember_field(key, bool_value, kind="bool")
            if self.scene is not None and self.allow_writes:
                prop_key = self._prop_key(key, kind="bool")
                if module_scene_prop_key_usable(prop_key):
                    try:
                        self.scene[prop_key] = bool_value
                    except Exception:
                        pass
            if self.allow_writes:
                self.state.set(key, bool_value)
            return bool_value

        def get_float(self, key, default=0.0):
            try:
                self._remember_field(key, float(default), kind="float")
                scene_value, scene_prop_key = module_runtime_field_scene_value(
                    self.scene, workflow, module, key, "float", self.state.get(key, default)
                )
                if scene_prop_key:
                    return float(scene_value)
                return float(self.state.get(key, default))
            except Exception:
                return float(default)

        def set_float(self, key, value):
            float_value = float(value)
            self._remember_field(key, float_value, kind="float")
            if self.scene is not None and self.allow_writes:
                prop_key = self._prop_key(key, kind="float")
                if module_scene_prop_key_usable(prop_key):
                    try:
                        self.scene[prop_key] = float_value
                    except Exception:
                        pass
            if self.allow_writes:
                self.state.set(key, float_value)
            return float_value

        def get_int(self, key, default=0):
            try:
                self._remember_field(key, int(default), kind="int")
                scene_value, scene_prop_key = module_runtime_field_scene_value(
                    self.scene, workflow, module, key, "int", self.state.get(key, default)
                )
                if scene_prop_key:
                    return int(scene_value)
                return int(self.state.get(key, default))
            except Exception:
                return int(default)

        def set_int(self, key, value):
            int_value = int(value)
            self._remember_field(key, int_value, kind="int")
            if self.scene is not None and self.allow_writes:
                prop_key = self._prop_key(key, kind="int")
                if module_scene_prop_key_usable(prop_key):
                    try:
                        self.scene[prop_key] = int_value
                    except Exception:
                        pass
            if self.allow_writes:
                self.state.set(key, int_value)
            return int_value

        def get_enum(self, key, default=""):
            default_value = str(default or "")
            self._remember_field(key, default_value, kind="enum")
            scene_value, scene_prop_key = module_runtime_field_scene_value(
                self.scene, workflow, module, key, "enum", self.state.get(key, default_value)
            )
            if scene_prop_key:
                return str(scene_value)
            return str(self.state.get(key, default_value))

        def set_enum(self, key, value):
            text_value = str(value or "")
            self._remember_field(key, text_value, kind="enum")
            if self.scene is not None and self.allow_writes:
                prop_key = self._prop_key(key, kind="enum")
                if module_scene_prop_key_usable(prop_key):
                    try:
                        self.scene[prop_key] = text_value
                    except Exception:
                        pass
            if self.allow_writes:
                self.state.set(key, text_value)
            return text_value

        def data_collection(self, collection_name):
            return getattr(bpy.data, str(collection_name or ""), None)

        def get_data_block(self, key, collection_name="objects", default=None):
            kind = self._data_kind(collection_name)
            default_name = getattr(default, "name", "") if default is not None else ""
            self._remember_field(key, default_name, kind=kind)
            scene_value, scene_prop_key = module_runtime_field_scene_value(
                self.scene, workflow, module, key, kind, self.state.get(key, default_name)
            )
            if scene_prop_key:
                name = str(scene_value or "")
            else:
                name = str(self.state.get(key, default_name) or "")
            collection = self.data_collection(collection_name)
            if collection is None or not name:
                return default
            try:
                return collection.get(name) or default
            except Exception:
                return default

        def set_data_block(self, key, data_block, collection_name="objects"):
            kind = self._data_kind(collection_name)
            name = getattr(data_block, "name", "") if data_block is not None else ""
            self._remember_field(key, name, kind=kind)
            if self.scene is not None and self.allow_writes:
                self.scene[self._prop_key(key, kind=kind)] = name
            if self.allow_writes:
                self.state.set(key, name)
            return data_block

        def get_material(self, key, default=None):
            return self.get_data_block(key, "materials", default=default)

        def get_collection(self, key, default=None):
            return self.get_data_block(key, "collections", default=default)

        def get_text_block(self, key, default=None):
            return self.get_data_block(key, "texts", default=default)

        def set_status(self, text, level="INFO"):
            value = self.compact_text(text, max_chars=96)
            if self.allow_writes:
                self.state.set("last_status", value)
                self.state.set("last_status_level", str(level or "INFO"))
            return value

        def get_status(self, default=""):
            return str(self.state.get("last_status", default) or "")

        def draw_status(self, layout, default="", icon=None):
            status = self.get_status(default)
            if not status:
                return
            level = str(self.state.get("last_status_level", "INFO") or "INFO").upper()
            icon_name = icon or {"ERROR": "ERROR", "WARNING": "ERROR", "OK": "CHECKMARK"}.get(level, "INFO")
            self.draw_note(layout, status, icon=icon_name, limit=3)

        def log(self, message, key="debug_log", max_items=8, print_to_console=True):
            text = self.compact_text(message, max_chars=160)
            if print_to_console:
                print(f"[GoWorkflow:{self.module_name}] {text}")
            if self.allow_writes:
                return self.state.append_log(text, key=key, max_items=max_items)
            return []

        def draw_log(self, layout, key="debug_log", limit=6):
            entries = self.state.get(key, [])
            if not isinstance(entries, (list, tuple)) or not entries:
                return
            for entry in list(entries)[-limit:]:
                layout.label(text=self.compact_text(entry), icon="CONSOLE")

        def active_object(self):
            return getattr(self.context, "object", None)

        def selected_objects(self, type=None):
            items = list(getattr(self.context, "selected_objects", []) or [])
            if type:
                items = [obj for obj in items if getattr(obj, "type", None) == type]
            return items

        def visible_objects(self, type=None):
            items = list(getattr(self.context, "visible_objects", []) or [])
            if type:
                items = [obj for obj in items if getattr(obj, "type", None) == type]
            return items

        def context_summary(self):
            active = self.active_object()
            return {
                "mode": getattr(self.context, "mode", ""),
                "scene": getattr(getattr(self.context, "scene", None), "name", ""),
                "active_object": getattr(active, "name", ""),
                "active_type": getattr(active, "type", ""),
                "selected_count": len(self.selected_objects()),
                "visible_count": len(self.visible_objects()),
            }

        def draw_text_input(self, layout, key, label, default=""):
            default_value = str(default)
            self._ensure_scene_value(key, default_value, kind="text")
            value = str(module_runtime_field_value(self.scene, workflow, module, key, "text", default_value) or "")
            row = layout.row(align=True)
            row.label(text=self.compact_text(label))
            op = row.operator("bworkflow.module_runtime_field_write", text=self.compact_text(value, UI_BUTTON_MAX_CHARS, "编辑"))
            op.workflow_name = self.workflow_name
            op.module_name = self.module_name
            op.field_key = key
            op.field_kind = "text"
            op.text_value = value

        def draw_toggle(self, layout, key, label, default=False):
            default_value = bool(default)
            self._ensure_scene_value(key, default_value, kind="bool")
            value = bool(module_runtime_field_value(self.scene, workflow, module, key, "bool", default_value))
            row = layout.row(align=True)
            row.label(text=self.compact_text(label))
            op = row.operator("bworkflow.module_runtime_field_write", text="开" if value else "关", depress=value)
            op.workflow_name = self.workflow_name
            op.module_name = self.module_name
            op.field_key = key
            op.field_kind = "bool"
            op.bool_value = not value

        def draw_float_input(self, layout, key, label, default=0.0):
            default_value = float(default)
            prop_key = self._ensure_scene_value(key, default_value, kind="float")
            value = float(module_runtime_field_value(self.scene, workflow, module, key, "float", default_value))
            row = layout.row(align=True)
            row.label(text=self.compact_text(label))
            if self.scene is not None and module_scene_prop_key_usable(prop_key):
                try:
                    if prop_key not in self.scene:
                        self.scene[prop_key] = value
                    row.prop(self.scene, f'["{prop_key}"]', text="")
                    if self.allow_writes:
                        self.state.set(key, float(self.scene.get(prop_key, value)))
                    return
                except Exception:
                    pass
            op = row.operator("bworkflow.module_runtime_field_write", text=f"{value:.3f}")
            op.workflow_name = self.workflow_name
            op.module_name = self.module_name
            op.field_key = key
            op.field_kind = "float"
            op.float_value = value

        def draw_int_input(self, layout, key, label, default=0):
            default_value = int(default)
            self._ensure_scene_value(key, default_value, kind="int")
            value = int(module_runtime_field_value(self.scene, workflow, module, key, "int", default_value))
            prop_key = self._prop_key(key, kind="int")
            row = layout.row(align=True)
            row.label(text=self.compact_text(label))
            if self.scene is not None and module_scene_prop_key_usable(prop_key):
                try:
                    row.prop(self.scene, f'["{prop_key}"]', text="")
                    if self.allow_writes:
                        self.state.set(key, int(self.scene.get(prop_key, value)))
                    return
                except Exception:
                    pass
            op = row.operator("bworkflow.module_runtime_field_write", text=str(value))
            op.workflow_name = self.workflow_name
            op.module_name = self.module_name
            op.field_key = key
            op.field_kind = "int"
            op.int_value = value

        def draw_enum(self, layout, key, label, items, default=None, max_items=8, disabled_items=None, display_value=None):
            normalized = []
            for item in items or []:
                if isinstance(item, str):
                    normalized.append((item, item))
                elif isinstance(item, (list, tuple)) and item:
                    identifier = str(item[0])
                    item_label = str(item[1]) if len(item) > 1 else identifier
                    normalized.append((identifier, item_label))
            if not normalized:
                return

            default_value = str(default if default is not None else normalized[0][0])
            identifiers = {identifier for identifier, _label in normalized}
            disabled_identifiers = {str(identifier) for identifier in (disabled_items or [])}
            self._ensure_scene_value(key, default_value, kind="enum")
            value = str(module_runtime_field_value(self.scene, workflow, module, key, "enum", default_value) or default_value)
            if value not in identifiers:
                value = default_value
            active_value = str(display_value or value)
            if active_value not in identifiers:
                active_value = value

            layout.label(text=self.compact_text(label))
            row = layout.row(align=True)
            for identifier, item_label in normalized[:max_items]:
                item_row = row.row(align=True)
                item_row.enabled = identifier not in disabled_identifiers
                op = item_row.operator(
                    "bworkflow.module_runtime_field_write",
                    text=self.compact_text(item_label, max_chars=16),
                    depress=identifier == active_value,
                )
                op.workflow_name = self.workflow_name
                op.module_name = self.module_name
                op.field_key = key
                op.field_kind = "enum"
                op.text_value = identifier
            if len(normalized) > max_items:
                layout.label(text=f"还有 {len(normalized) - max_items} 项，建议用文本输入。", icon="INFO")

        def draw_object_picker(self, layout, key, label="物体", default=None):
            default_name = getattr(default, "name", "") if default is not None else ""
            self._ensure_scene_value(key, default_name, kind="object")
            value = str(module_runtime_field_value(self.scene, workflow, module, key, "object", default_name) or "")
            row = layout.row(align=True)
            row.label(text=self.compact_text(label))
            op = row.operator("bworkflow.module_runtime_field_write", text=self.compact_text(value, UI_BUTTON_MAX_CHARS, "选择物体"))
            op.workflow_name = self.workflow_name
            op.module_name = self.module_name
            op.field_key = key
            op.field_kind = "object"
            op.object_name = value

        def draw_active_object_capture(self, layout, key, label="吸取当前选中", icon="EYEDROPPER"):
            active_name = getattr(getattr(self.context, "object", None), "name", "")
            op = layout.operator(
                "bworkflow.module_runtime_field_write",
                text=self.compact_text(label, UI_BUTTON_MAX_CHARS),
                icon=icon,
            )
            op.workflow_name = self.workflow_name
            op.module_name = self.module_name
            op.field_key = key
            op.field_kind = "object"
            op.object_name = active_name
            return op

        def draw_data_block_picker(self, layout, key, label, collection_name="objects", default=None):
            kind = self._data_kind(collection_name)
            default_name = getattr(default, "name", "") if default is not None else ""
            self._ensure_scene_value(key, default_name, kind=kind)
            value = str(module_runtime_field_value(self.scene, workflow, module, key, kind, default_name) or "")
            row = layout.row(align=True)
            row.label(text=self.compact_text(label))
            op = row.operator(
                "bworkflow.module_runtime_field_write",
                text=self.compact_text(value, UI_BUTTON_MAX_CHARS, "选择"),
            )
            op.workflow_name = self.workflow_name
            op.module_name = self.module_name
            op.field_key = key
            op.field_kind = kind
            op.text_value = value
            op.data_collection = str(collection_name or "")

        def draw_material_picker(self, layout, key, label="材质", default=None):
            return self.draw_data_block_picker(layout, key, label, "materials", default=default)

        def draw_collection_picker(self, layout, key, label="集合", default=None):
            return self.draw_data_block_picker(layout, key, label, "collections", default=default)

        def draw_text_block_picker(self, layout, key, label="文本", default=None):
            return self.draw_data_block_picker(layout, key, label, "texts", default=default)

    proxy = ModuleStateProxy(module_store)
    panel_api = ModulePanelAPI(context, scene, proxy)
    return proxy, panel_api


def build_module_namespace(context, scene, workflow, module, name, allow_writes=True, field_specs=None):
    module_state, panel_api = module_runtime_context(
        context,
        scene,
        workflow,
        module,
        allow_writes=allow_writes,
        field_specs=field_specs,
    )
    script_path = module_script_abspath(module)
    module_file = script_path if script_path else name
    return {
        "__name__": name,
        "__file__": module_file,
        "bpy": bpy,
        "context": context,
        "scene": scene,
        "workflow": workflow,
        "module": module,
        "module_state": module_state,
        "panel_api": panel_api,
    }


def execute_module_source(source, context, scene, workflow, module, name, allow_writes=True, persist_state=True, field_specs=None):
    source = str(source or "").lstrip("\ufeff")
    module_state, panel_api = module_runtime_context(
        context,
        scene,
        workflow,
        module,
        allow_writes=allow_writes,
        field_specs=field_specs,
    )
    script_path = module_script_abspath(module)
    module_file = script_path if script_path else name
    namespace = {
        "__name__": name,
        "__file__": module_file,
        "bpy": bpy,
        "context": context,
        "scene": scene,
        "workflow": workflow,
        "module": module,
        "module_state": module_state,
        "panel_api": panel_api,
    }
    exec(compile(source, name, "exec"), namespace, namespace)
    if persist_state:
        merge_module_runtime_store(scene, workflow, module, namespace["module_state"].to_dict())
    cleanup_fn = namespace.get("cleanup_runtime") or namespace.get("cleanup")
    if callable(cleanup_fn):
        cache_module_runtime_cleanup(scene, workflow, module, cleanup_fn, namespace.get("module_state"))
    return namespace


def load_module_namespace(context, scene, workflow, module, name, allow_writes=False, persist_state=False):
    source = current_module_script_source(workflow, module, prefer_text_block=True, allow_initialize=False).strip()
    if source:
        return execute_module_source(
            source,
            context,
            scene,
            workflow,
            module,
            name,
            allow_writes=allow_writes,
            persist_state=persist_state,
        )

    filepath = module_script_abspath(module)
    if not filepath or not os.path.isfile(filepath):
        raise FileNotFoundError("自定义模块脚本文件不存在，且当前模板里没有可执行代码。")
    with open(filepath, "r", encoding="utf-8") as handle:
        return execute_module_source(
            handle.read(),
            context,
            scene,
            workflow,
            module,
            filepath,
            allow_writes=allow_writes,
            persist_state=persist_state,
        )


def drain_validation_timer_callbacks():
    registry_key = "go_workflow.validation_timer_callbacks"
    try:
        registry = bpy.app.driver_namespace.get(registry_key)
    except Exception:
        registry = None
    if not registry:
        return 0
    callbacks = list(registry)
    try:
        registry.clear()
    except Exception:
        pass
    removed = 0
    for callback in callbacks:
        try:
            bpy.app.timers.unregister(callback)
        except Exception:
            pass
        removed += 1
    try:
        bpy.app.driver_namespace.pop(registry_key, None)
    except Exception:
        pass
    return removed


def cleanup_module_runtimes(context=None):
    context = context or getattr(bpy, "context", None)
    try:
        drain_validation_timer_callbacks()
    except Exception:
        traceback.print_exc()
    scenes = list(iter_available_scenes())
    seen = set()
    for scene in scenes:
        if scene is None:
            continue
        for space_type in iter_supported_space_types():
            state = get_state(scene=scene, space_type=space_type)
            if state is None:
                continue
            for workflow in state.workflows:
                for module in workflow.modules:
                    key = (id(scene), workflow.name, module.name)
                    if key in seen:
                        continue
                    source = current_module_script_source(workflow, module, prefer_text_block=True, allow_initialize=False).strip()
                    if "cleanup_runtime" not in source and "_VALIDATION_TIMER_STATE" not in source:
                        continue
                    seen.add(key)
                    try:
                        cached_cleanup = pop_module_runtime_cleanup(scene, workflow, module)
                        if cached_cleanup is not None:
                            cleanup_fn = cached_cleanup.get("cleanup_runtime")
                            module_state = cached_cleanup.get("module_state")
                        else:
                            namespace = load_module_namespace(
                                context,
                                scene,
                                workflow,
                                module,
                                "__go_workflow_cleanup__",
                                allow_writes=True,
                                persist_state=False,
                            )
                            cleanup_fn = namespace.get("cleanup_runtime") or namespace.get("cleanup")
                            module_state = namespace.get("module_state")
                        if callable(cleanup_fn):
                            cleanup_fn(scene=scene, workflow=workflow, module=module, module_state=module_state)
                    except Exception:
                        traceback.print_exc()
    try:
        drain_validation_timer_callbacks()
    except Exception:
        traceback.print_exc()


def module_text_block_name(workflow, module):
    if module.text_block_name.strip():
        return module.text_block_name
    index = workflow_module_index(workflow, module)
    suffix = "" if index < 0 else f"_{index + 1:02d}"
    return (
        "Go工作流_"
        f"{safe_filename_component(workflow.name, 'workflow')}_"
        f"{safe_filename_component(module.name, 'module')}{suffix}.py"
    )


def module_description_text_block_name(workflow, module):
    index = workflow_module_index(workflow, module)
    suffix = "" if index < 0 else f"_{index + 1:02d}"
    return (
        "Go工作流说明_"
        f"{safe_filename_component(workflow.name, 'workflow')}_"
        f"{safe_filename_component(module.name, 'module')}{suffix}.txt"
    )


def find_text_block_for_filepath(filepath):
    target = normalized_abs_path(filepath)
    if not target:
        return None
    for text_block in bpy.data.texts:
        try:
            if normalized_abs_path(getattr(text_block, "filepath", "") or "") == target:
                return text_block
        except Exception:
            pass
    return None


def load_module_script_text_block_from_file(workflow, module, filepath, source):
    folder = os.path.dirname(filepath)
    if folder:
        os.makedirs(folder, exist_ok=True)
    if not os.path.isfile(filepath):
        with open(filepath, "w", encoding="utf-8") as handle:
            handle.write(source)

    text_block = find_text_block_for_filepath(filepath)
    if text_block is None:
        text_block = bpy.data.texts.load(filepath)
    try:
        text_block.name = module_text_block_name(workflow, module)
    except Exception:
        pass
    try:
        text_block.filepath = filepath
    except Exception:
        pass

    desired = source
    try:
        with open(filepath, "r", encoding="utf-8") as handle:
            disk_source = handle.read()
        if disk_source != desired:
            with open(filepath, "w", encoding="utf-8") as handle:
                handle.write(desired)
    except Exception:
        pass
    if text_block.as_string() != desired:
        text_block.clear()
        text_block.write(desired)
    return text_block


def ensure_module_text_block(workflow, module):
    text_name = module_text_block_name(workflow, module)
    source = ensure_module_script_source(workflow, module)
    filepath = module_script_abspath(module)
    if filepath and filepath.lower().endswith(".py"):
        try:
            text_block = load_module_script_text_block_from_file(workflow, module, filepath, source)
            module.text_block_name = text_block.name
            return text_block
        except Exception:
            traceback.print_exc()

    text_block = bpy.data.texts.get(text_name)
    if text_block is None:
        text_block = bpy.data.texts.new(text_name)
    existing = text_block.as_string()
    if existing != source:
        text_block.clear()
        text_block.write(source)
    module.text_block_name = text_block.name
    return text_block


def ensure_module_description_text_block(workflow, module):
    text_name = module_description_text_block_name(workflow, module)
    text_block = bpy.data.texts.get(text_name)
    if text_block is None:
        text_block = bpy.data.texts.new(text_name)
    existing = text_block.as_string()
    desired = module.description or ""
    if existing != desired:
        text_block.clear()
        text_block.write(desired)
    return text_block


def sync_module_description_from_text_block(workflow, module):
    text_block = bpy.data.texts.get(module_description_text_block_name(workflow, module))
    if text_block is None:
        return False
    module.description = text_block.as_string()
    return True


def sync_module_source_from_text_block(module):
    text_name = module.text_block_name.strip()
    if not text_name:
        return False
    text_block = bpy.data.texts.get(text_name)
    if text_block is None:
        return False
    module.script_source = text_block.as_string()
    return True


def sync_module_source_from_file(module):
    raw_path = (getattr(module, "script_path", "") or "").strip()
    if not raw_path:
        return False
    filepath = bpy.path.abspath(raw_path)
    if not filepath or not os.path.isfile(filepath):
        return False
    try:
        with open(filepath, "r", encoding="utf-8") as handle:
            module.script_source = handle.read()
        return True
    except Exception:
        return False


def sync_all_module_sources_from_text_blocks(scene=None):
    target_scene = scene or safe_context_scene()
    if target_scene is None:
        return False

    changed = False
    for space_type in iter_supported_space_types():
        state = get_state(scene=target_scene, space_type=space_type)
        if state is None:
            continue
        for workflow in state.workflows:
            for module in workflow.modules:
                if sync_module_source_from_text_block(module):
                    changed = True
    return changed


def sync_all_module_descriptions_from_text_blocks(scene=None):
    target_scene = scene or safe_context_scene()
    if target_scene is None:
        return False

    changed = False
    for space_type in iter_supported_space_types():
        state = get_state(scene=target_scene, space_type=space_type)
        if state is None:
            continue
        for workflow in state.workflows:
            for module in workflow.modules:
                if sync_module_description_from_text_block(workflow, module):
                    changed = True
    return changed


def module_has_custom_panel_source(module):
    source = (module.script_source or "").strip()
    if not source:
        return False
    return bool(re.search(r"^\s*def\s+draw_panel\s*\(", source, flags=re.MULTILINE))


def module_needs_runtime_panel(module):
    if module is None or not module.enabled:
        return False
    return bool(module.use_custom_panel)


def initialize_module_runtime_fields(scene, workflow, module, context=None):
    if scene is None or workflow is None or module is None:
        return False

    source = current_module_script_source(workflow, module, prefer_text_block=True, allow_initialize=True).strip()
    if not source:
        return False

    field_specs = []
    try:
        execute_module_source(
            source,
            context or bpy.context,
            scene,
            workflow,
            module,
            "__go_workflow_init__",
            allow_writes=False,
            persist_state=False,
            field_specs=field_specs,
        )
    except Exception:
        return False

    migrate_module_runtime_scene_values(scene, workflow, module, field_specs)
    save_module_runtime_specs(scene, workflow, module, field_specs)
    changed = False
    for spec in field_specs:
        prop_key = spec.get("prop_key")
        if not prop_key or not module_scene_prop_key_usable(prop_key) or prop_key in scene:
            continue
        try:
            scene[prop_key] = normalize_module_field_default(spec.get("kind", "text"), spec.get("default"))
            changed = True
        except Exception:
            continue
    return changed


def initialize_all_module_runtime_fields(scene=None, context=None):
    target_scene = scene or safe_context_scene()
    if target_scene is None:
        return False

    changed = False
    for space_type in iter_supported_space_types():
        state = get_state(scene=target_scene, space_type=space_type)
        workflow = get_active_workflow(state)
        if workflow is None:
            continue
        for module in workflow.modules:
            if not module_needs_runtime_panel(module):
                continue
            sync_module_source_from_text_block(module)
            if initialize_module_runtime_fields(target_scene, workflow, module, context=context):
                changed = True
    return changed


def module_runtime_field_value(scene, workflow, module, key, kind, default=None):
    scene_value, scene_prop_key = module_runtime_field_scene_value(scene, workflow, module, key, kind, default)
    if scene_prop_key:
        return scene_value

    module_store = ensure_module_runtime_store(scene, workflow, module, allow_writes=False)
    if kind == "object":
        return str(module_store.get(key, default or ""))
    return module_store.get(key, default)


def callable_accepts_panel_api(callback):
    try:
        signature = inspect.signature(callback)
    except Exception:
        return False
    positional_count = 0
    for parameter in signature.parameters.values():
        if parameter.kind == inspect.Parameter.VAR_POSITIONAL:
            return True
        if parameter.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            positional_count += 1
    return positional_count >= 7


class BWFLOW_OT_module_runtime_field_write(Operator):
    bl_idname = "bworkflow.module_runtime_field_write"
    bl_label = "写入自定义字段"
    bl_description = "为自定义脚本模块写入一个运行时字段值"

    workflow_name: StringProperty(default="")
    module_name: StringProperty(default="")
    field_key: StringProperty(default="")
    field_kind: StringProperty(default="text")
    text_value: StringProperty(default="")
    bool_value: BoolProperty(default=False)
    float_value: FloatProperty(default=0.0)
    int_value: IntProperty(default=0)
    object_name: StringProperty(default="")
    data_collection: StringProperty(default="")
    read_from_scene: BoolProperty(default=False)

    def invoke(self, context, event):
        field_kind = (self.field_kind or "text").strip()
        if field_kind in {"bool", "enum"}:
            return self.execute(context)
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        layout = self.layout
        field_kind = (self.field_kind or "text").strip()
        if field_kind == "float":
            layout.prop(self, "float_value", text=self.field_key or "数值")
        elif field_kind == "int":
            layout.prop(self, "int_value", text=self.field_key or "整数")
        elif field_kind == "object":
            layout.prop_search(self, "object_name", bpy.data, "objects", text=self.field_key or "物体")
        elif field_kind.startswith("datablock_"):
            collection_name = (self.data_collection or field_kind.removeprefix("datablock_") or "").strip()
            if collection_name and hasattr(bpy.data, collection_name):
                try:
                    layout.prop_search(self, "text_value", bpy.data, collection_name, text=self.field_key or "数据块")
                except Exception:
                    layout.prop(self, "text_value", text=self.field_key or "数据块")
            else:
                layout.prop(self, "text_value", text=self.field_key or "数据块")
        else:
            layout.prop(self, "text_value", text=self.field_key or "内容")

    def execute(self, context):
        scene = context.scene
        if scene is None:
            return {"CANCELLED"}

        target_workflow = None
        target_module = None
        for space_type in iter_supported_space_types():
            state = get_state(scene=scene, space_type=space_type)
            if state is None:
                continue
            for workflow in state.workflows:
                if workflow.name != self.workflow_name:
                    continue
                for module in workflow.modules:
                    if module.name == self.module_name:
                        target_workflow = workflow
                        target_module = module
                        break
                if target_module is not None:
                    break
            if target_module is not None:
                break

        if target_workflow is None or target_module is None:
            return {"CANCELLED"}

        field_kind = (self.field_kind or "text").strip()
        prop_keys = module_scene_prop_key_candidates(target_workflow, target_module, self.field_key, kind=field_kind)
        prop_key = prop_keys[0] if prop_keys else ""
        module_store = ensure_module_runtime_store(scene, target_workflow, target_module, allow_writes=True)

        if self.read_from_scene:
            raw_scene_value, found_prop_key = module_runtime_field_scene_value(
                scene, target_workflow, target_module, self.field_key, field_kind, None
            )
        else:
            raw_scene_value, found_prop_key = (None, "")
        if self.read_from_scene and found_prop_key:
            if field_kind == "float":
                value = float(raw_scene_value)
            elif field_kind == "int":
                value = int(raw_scene_value)
            elif field_kind == "bool":
                value = bool(raw_scene_value)
            elif field_kind == "object":
                value = str(raw_scene_value or "")
            else:
                value = str(raw_scene_value or "")
        elif field_kind == "bool":
            value = bool(self.bool_value)
        elif field_kind == "float":
            value = float(self.float_value)
        elif field_kind == "int":
            value = int(self.int_value)
        elif field_kind == "object":
            value = str(self.object_name or "")
        else:
            value = str(self.text_value or "")

        if module_scene_prop_key_usable(prop_key):
            try:
                scene[prop_key] = value
            except Exception:
                pass
        module_store[self.field_key] = value
        merge_module_runtime_store(scene, target_workflow, target_module, module_store)
        try:
            namespace = load_module_namespace(
                context,
                scene,
                target_workflow,
                target_module,
                "__go_workflow_runtime_field_write__",
                allow_writes=True,
                persist_state=False,
            )
            action_fn = namespace.get("on_panel_action")
            if callable(action_fn):
                result = action_fn(
                    f"FIELD_WRITE::{self.field_key}",
                    context,
                    scene,
                    target_workflow,
                    target_module,
                    namespace.get("panel_api"),
                    namespace.get("module_state"),
                )
                if namespace.get("module_state") is not None and hasattr(namespace["module_state"], "to_dict"):
                    merge_module_runtime_store(scene, target_workflow, target_module, namespace["module_state"].to_dict())
                if isinstance(result, set) and "CANCELLED" in result:
                    return result
        except BaseException:
            traceback.print_exc()
        tag_redraw_all()
        return {"FINISHED"}


class BWFLOW_OT_module_runtime_action(Operator):
    bl_idname = "bworkflow.module_runtime_action"
    bl_label = "执行自定义面板动作"
    bl_description = "执行自定义脚本面板里的按钮动作"

    workflow_name: StringProperty(default="")
    module_name: StringProperty(default="")
    action_name: StringProperty(default="")
    tooltip_text: StringProperty(default="")

    @classmethod
    def description(cls, _context, properties):
        text = getattr(properties, "tooltip_text", "") or ""
        return text or cls.bl_description

    def execute(self, context):
        scene = context.scene
        if scene is None:
            return {"CANCELLED"}

        target_workflow = None
        target_module = None
        for space_type in iter_supported_space_types():
            state = get_state(scene=scene, space_type=space_type)
            if state is None:
                continue
            for workflow in state.workflows:
                if workflow.name != self.workflow_name:
                    continue
                for module in workflow.modules:
                    if module.name == self.module_name:
                        target_workflow = workflow
                        target_module = module
                        break
                if target_module is not None:
                    break
            if target_module is not None:
                break

        if target_workflow is None or target_module is None:
            self.report({"ERROR"}, "找不到目标工作流或模块")
            return {"CANCELLED"}

        try:
            namespace = load_module_namespace(
                context,
                scene,
                target_workflow,
                target_module,
                "__go_workflow_action__",
                allow_writes=True,
                persist_state=False,
            )
            action_fn = namespace.get("on_panel_action")
            if not callable(action_fn):
                self.report({"WARNING"}, "脚本没有定义 on_panel_action(...)")
                return {"CANCELLED"}
            result = action_fn(
                self.action_name,
                context,
                scene,
                target_workflow,
                target_module,
                namespace.get("panel_api"),
                namespace.get("module_state"),
            )
            module_state = namespace.get("module_state")
            if module_state is not None and hasattr(module_state, "to_dict"):
                merge_module_runtime_store(scene, target_workflow, target_module, module_state.to_dict())
            tag_redraw_all()
            if isinstance(result, set):
                return result
            return {"FINISHED"}
        except BaseException as exc:
            traceback.print_exc()
            self.report({"ERROR"}, f"自定义面板动作失败: {exc}")
            return {"CANCELLED"}


class BWFLOW_OT_copy_runtime_error(Operator):
    bl_idname = "bworkflow.copy_runtime_error"
    bl_label = "复制错误"
    bl_description = "把当前自定义面板错误复制到系统剪贴板"

    error_text: StringProperty(default="")

    def execute(self, context):
        text = str(self.error_text or "").strip()
        if not text:
            self.report({"WARNING"}, "当前没有可复制的错误文本")
            return {"CANCELLED"}
        context.window_manager.clipboard = text
        self.report({"INFO"}, "错误文本已复制到剪贴板")
        return {"FINISHED"}


class BWFLOW_OT_open_runtime_error_report(Operator):
    bl_idname = "bworkflow.open_runtime_error_report"
    bl_label = "打开错误报告"
    bl_description = "把当前自定义面板错误写入临时文本块，方便在文本编辑器中框选复制"

    workflow_name: StringProperty(default="")
    module_name: StringProperty(default="")
    error_text: StringProperty(default="")

    def execute(self, context):
        error_text = str(self.error_text or "").strip()
        if not error_text:
            self.report({"WARNING"}, "当前没有可打开的错误文本")
            return {"CANCELLED"}
        title = f"Go工作流错误_{safe_filename_component(self.workflow_name or 'workflow', 'workflow')}_{safe_filename_component(self.module_name or 'module', 'module')}.txt"
        body = "\n".join(
            [
                f"工作流: {self.workflow_name}",
                f"模块: {self.module_name}",
                "",
                "错误报告:",
                error_text,
            ]
        )
        text_block = ensure_runtime_report_text_block(title, body)
        scripting_workspace = find_workspace_by_name("Scripting")
        activate_text_editor_for_text(context, text_block, workspace=scripting_workspace)
        self.report({"INFO"}, "已打开错误报告文本，可直接框选复制")
        return {"FINISHED"}


def draw_module_runtime_panel(card, context, workflow, module):
    try:
        result = load_module_namespace(context, context.scene, workflow, module, "__go_workflow_panel__")
        draw_fn = result.get("draw_panel")
        if callable(draw_fn):
            if callable_accepts_panel_api(draw_fn):
                draw_fn(
                    card,
                    context,
                    context.scene,
                    workflow,
                    module,
                    result.get("panel_api"),
                    result.get("module_state"),
                )
            else:
                draw_fn(card, context, context.scene, workflow, module)
            return
        card.label(text="脚本里没有 draw_panel(...)，当前模块不显示自定义面板。", icon="INFO")
    except BaseException as exc:
        traceback.print_exc()
        card.alert = True
        card.label(text=f"自定义面板绘制失败: {exc}", icon="ERROR")
        actions = card.row(align=True)
        copy_op = actions.operator("bworkflow.copy_runtime_error", text="复制错误", icon="COPYDOWN")
        copy_op.error_text = f"自定义面板绘制失败: {exc}"
        open_op = actions.operator("bworkflow.open_runtime_error_report", text="打开错误报告", icon="TEXT")
        open_op.workflow_name = getattr(workflow, "name", "")
        open_op.module_name = getattr(module, "name", "")
        open_op.error_text = f"自定义面板绘制失败: {exc}"


def find_workspace_by_name(name):
    try:
        return bpy.data.workspaces.get(name)
    except Exception:
        return None


def assign_text_to_screen(screen, text_block):
    if screen is None:
        return False

    for area in screen.areas:
        if area.type == "TEXT_EDITOR":
            try:
                area.spaces.active.text = text_block
                area.tag_redraw()
                return True
            except Exception:
                traceback.print_exc()
    return False


def iter_window_screens():
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return []
    result = []
    for window in wm.windows:
        screen = getattr(window, "screen", None)
        if screen is not None:
            result.append((window, screen))
    return result


def find_window_for_workspace(workspace):
    if workspace is None:
        return None
    for window, _screen in iter_window_screens():
        if getattr(window, "workspace", None) == workspace:
            return window
    return None


def activate_text_editor_for_text(context, text_block, workspace=None):
    if workspace is not None:
        workspace_window = find_window_for_workspace(workspace)
        if workspace_window is not None and assign_text_to_screen(getattr(workspace_window, "screen", None), text_block):
            return "workspace_window"

    window = getattr(context, "window", None)
    if workspace is not None:
        if window is None:
            return None
        previous_workspace = getattr(window, "workspace", None)
        workspace_switched = False
        if previous_workspace != workspace:
            try:
                window.workspace = workspace
                workspace_switched = True
            except Exception:
                workspace_switched = False
        try:
            with context.temp_override(window=window, screen=window.screen):
                if assign_text_to_screen(window.screen, text_block):
                    return "scripting_workspace"
        except Exception:
            traceback.print_exc()
        if assign_text_to_screen(window.screen, text_block):
            return "scripting_workspace"
        if workspace_switched and previous_workspace is not None:
            try:
                window.workspace = previous_workspace
            except Exception:
                pass
        return None

    if window is not None and assign_text_to_screen(window.screen, text_block):
        return "current_window"
    return None


def ensure_runtime_report_text_block(name, content):
    text_name = str(name or "Go工作流_错误报告.txt")
    text_block = bpy.data.texts.get(text_name)
    if text_block is None:
        text_block = bpy.data.texts.new(text_name)
    desired = str(content or "")
    if text_block.as_string() != desired:
        text_block.clear()
        text_block.write(desired)
    return text_block


def open_path_in_file_explorer(path):
    target_path = (path or "").strip()
    if not target_path:
        raise ValueError("当前路径为空")

    normalized = bpy.path.abspath(target_path)
    if os.path.isfile(normalized):
        folder = os.path.dirname(normalized)
        if os.name == "nt":
            subprocess.Popen(["explorer", "/select,", normalized])
            return normalized
        target_path = folder
    elif not os.path.isdir(normalized):
        folder = os.path.dirname(normalized)
        if folder and os.path.isdir(folder):
            normalized = folder
        else:
            raise FileNotFoundError(f"路径不存在: {normalized}")

    if os.name == "nt":
        os.startfile(normalized)
    else:
        subprocess.Popen([normalized])
    return normalized


def write_module_source_file(workflow, module, script_source=None):
    ensure_module_script_path_matches_name(workflow, module)
    if not module.script_path.strip():
        module.script_path = unique_default_module_script_path(workflow, module)

    filepath = module_script_abspath(module)
    if not filepath.lower().endswith(".py"):
        raise ValueError("脚本模板目前只支持写入 .py 文件")

    source = script_source if script_source is not None else ensure_module_script_source(workflow, module)
    folder = os.path.dirname(filepath)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as handle:
        handle.write(source)
    return filepath


def write_json_payload_file(filepath, payload):
    target_path = bpy.path.abspath(str(filepath or "").strip())
    if not target_path:
        raise ValueError("导出路径为空")
    folder = os.path.dirname(target_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(target_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return target_path


def open_module_script_directory(workflow, module):
    ensure_module_script_path_matches_name(workflow, module)
    filepath = module_script_abspath(module)
    folder = filepath if os.path.isdir(filepath) else os.path.dirname(filepath)
    if not folder:
        folder = default_module_scripts_dir()
    os.makedirs(folder, exist_ok=True)
    if os.name == "nt":
        os.startfile(folder)
    else:
        subprocess.Popen([folder])
    return folder


def force_reload_script_panels(scene=None, space_type=None, restore_first=True):
    initialize_all_module_runtime_fields(scene=scene)
    target_spaces = (space_type,) if space_type else iter_supported_space_types()
    for target_space in target_spaces:
        state = get_state(scene=scene, space_type=target_space)
        if state is None:
            continue
        workflow = get_active_workflow(state)
        if workflow is None or workflow.is_default:
            clear_panel_order_overrides(space_type=target_space)
            continue
        ordered_ids = workflow_ordered_panel_ids(state, workflow)

        if restore_first:
            restore_unregistered_panels(space_type=target_space)
        apply_panel_order_overrides(ordered_ids, space_type=target_space)


def on_workflow_changed(self, context):
    if IS_INITIALIZING_ADDON:
        return
    state = get_state(context=context)
    if state is None:
        return
    clamped = clamp_index(state.active_workflow_index, len(state.workflows))
    if clamped != state.active_workflow_index:
        state.active_workflow_index = clamped
        return
    ensure_one_default_workflow(state)
    workflow = get_active_workflow(state)
    if workflow is not None and workflow.is_default:
        restore_default_n_panel_state(scene=context.scene, disable_filters=True, sync_registry_after_restore=False)
    else:
        rebuild_runtime_panels(scene=context.scene)
    schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.5,))


def on_workflow_name_changed(self, context):
    if IS_INITIALIZING_ADDON:
        return
    tag_redraw_all()


def on_module_name_changed(self, context):
    if IS_INITIALIZING_ADDON:
        return
    scene = getattr(context, "scene", None) or safe_context_scene()
    if scene is None:
        tag_redraw_all()
        return
    for space_type in iter_supported_space_types():
        state = get_state(scene=scene, space_type=space_type)
        if state is None:
            continue
        for workflow in state.workflows:
            for module in workflow.modules:
                if module != self:
                    continue
                ensure_module_script_path_matches_name(workflow, module)
                save_global_workflow_state(scene)
                tag_redraw_all()
                return
    tag_redraw_all()


def on_module_runtime_panel_expanded_changed(self, context):
    if IS_INITIALIZING_ADDON:
        return
    try:
        save_global_workflow_state(getattr(context, "scene", None))
    except Exception:
        traceback.print_exc()
    tag_redraw_all()


def on_filter_config_changed(self, context):
    if IS_INITIALIZING_ADDON:
        return
    try:
        scene = context.scene
    except Exception:
        scene = safe_context_scene()
    if scene is not None:
        rebuild_runtime_panels(scene=scene)
        schedule_deferred_runtime_refresh(scene=scene, intervals=(0.25,))


def on_ui_text_fold_changed(self, context):
    if IS_INITIALIZING_ADDON:
        return
    try:
        save_global_workflow_state(getattr(context, "scene", None))
    except Exception:
        traceback.print_exc()
    tag_redraw_all()


class BWFLOW_PG_PanelRecord(PropertyGroup):
    panel_id: StringProperty()
    title: StringProperty()
    category: StringProperty()
    tags: StringProperty(
        name="面板标签",
        default="",
        description="用英文逗号分隔，例如 model,uv,render",
        update=on_filter_config_changed,
    )
    source_module: StringProperty()
    discovered: BoolProperty(default=False)


class BWFLOW_PG_ScriptLibraryItem(PropertyGroup):
    name: StringProperty(
        name="脚本名称",
        default="新脚本",
        description="脚本库里显示的名称",
    )
    description: StringProperty(
        name="脚本说明",
        default="",
        description="脚本库条目的用途说明",
    )
    tags: StringProperty(
        name="脚本标签",
        default="",
        description="用英文逗号分隔，方便按主题检索",
    )
    use_custom_panel: BoolProperty(
        name="需要自定义面板",
        default=False,
        description="从脚本库载入后是否在 Go工作流 主面板显示自定义 UI",
    )
    panel_title: StringProperty(
        name="面板标题",
        default="",
        description="脚本库条目对应的自定义面板标题",
    )
    panel_description: StringProperty(
        name="面板说明",
        default="",
        description="脚本库条目对应的自定义面板说明",
    )
    script_path: StringProperty(
        name="脚本路径",
        default="",
        description="脚本文件路径，便于回到原始文件",
        subtype="FILE_PATH",
    )
    text_block_name: StringProperty(
        name="文本块名称",
        default="",
        description="关联的 Blender 文本块名称",
    )
    script_source: StringProperty(
        name="脚本源码",
        default="",
        description="可复用的脚本源码内容",
    )
    config_payload: StringProperty(
        name="模块配置",
        default="",
        description="随脚本条目一起保存的附加配置文本，例如 csv 或 json",
    )
    ai_doc: StringProperty(
        name="AI 文档",
        default="",
        description="脚本相关的说明文档，便于下次继续开发",
    )


class BWFLOW_PG_WorkflowPanel(PropertyGroup):
    panel_id: StringProperty()


class BWFLOW_PG_WorkflowModule(PropertyGroup):
    name: StringProperty(name="模块名称", default="新模块", update=on_module_name_changed)
    enabled: BoolProperty(name="启用", default=True)
    use_custom_panel: BoolProperty(
        name="需要自定义面板",
        default=False,
        description="启用后，会在 Go工作流 主面板里为这个模块预留一个自定义操作区域",
    )
    runtime_panel_expanded: BoolProperty(
        name="展开自定义面板",
        default=True,
        description="控制 Go工作流 主面板中当前模块的自定义 UI 是否展开",
        update=on_module_runtime_panel_expanded_changed,
    )
    panel_title: StringProperty(
        name="面板标题",
        default="",
        description="仅在需要自定义面板时使用，留空则使用模块名称",
    )
    panel_description: StringProperty(
        name="面板说明",
        default="",
        description="仅在需要自定义面板时使用，用于描述这个模块面板放什么内容",
    )
    script_path: StringProperty(
        name="脚本路径",
        default="",
        description="用于挂载 Blender Python 脚本模板或实际执行脚本",
        subtype="FILE_PATH",
    )
    description: StringProperty(
        name="模块说明",
        default="",
        description="记录该模块用途、输入输出约定或批处理说明",
    )
    text_block_name: StringProperty(
        name="文本编辑器名称",
        default="",
        description="绑定到当前模块的 Blender 文本数据块名称",
    )
    script_source: StringProperty(
        name="脚本源码",
        default="",
        description="用于在插件内部直接编辑并写入 .py 文件的源代码文本",
    )
    config_payload: StringProperty(
        name="模块配置",
        default="",
        description="随工作流模块一起导出的附加配置文本，例如 csv 或 json",
    )
    ai_doc: StringProperty(
        name="AI 文档",
        default="",
        description="提供给你自己的 AI 用于继续开发这个模块",
    )


class BWFLOW_PG_PresetWorkflowItem(PropertyGroup):
    selected: BoolProperty(name="导入", default=True)
    name: StringProperty(default="")
    source_space_type: StringProperty(default="VIEW_3D")
    source_label: StringProperty(default="")
    source_key: StringProperty(default="")
    panel_count: IntProperty(default=0)
    module_count: IntProperty(default=0)
    is_default: BoolProperty(default=False)


class BWFLOW_PG_Workflow(PropertyGroup):
    name: StringProperty(name="名称", default="新工作流", update=on_workflow_name_changed)
    is_default: BoolProperty(default=False)
    preset_export_selected: BoolProperty(name="导出", default=True)
    description: StringProperty(
        name="工作流说明",
        default="",
        description="仅用于记录用途与说明，不影响工作流切换逻辑",
    )
    tag_filter: StringProperty(
        name="按标签自动加入",
        default="",
        description="用英文逗号分隔；带这些标签的面板会自动加入该工作流显示结果",
        update=on_filter_config_changed,
    )
    panels: CollectionProperty(type=BWFLOW_PG_WorkflowPanel)
    active_panel_index: IntProperty(default=0)
    modules: CollectionProperty(type=BWFLOW_PG_WorkflowModule)
    active_module_index: IntProperty(default=0)


class BWFLOW_PG_Settings(PropertyGroup):
    auto_sync_registry: BoolProperty(
        name="刷新时同步面板库",
        default=True,
    )
    show_missing_summary: BoolProperty(
        name="显示缺失面板摘要",
        default=True,
    )
    runtime_preview_lines: IntProperty(
        name="说明预览行数",
        default=3,
        min=1,
        max=12,
    )
    show_workflow_description: BoolProperty(
        name="展开工作流说明",
        default=False,
        update=on_ui_text_fold_changed,
    )
    show_runtime_module_descriptions: BoolProperty(
        name="展开主面板模块说明",
        default=False,
        update=on_ui_text_fold_changed,
    )
    show_module_ai_doc_preview: BoolProperty(
        name="展开 AI 文档预览",
        default=False,
        update=on_ui_text_fold_changed,
    )
    show_script_library_source_preview: BoolProperty(
        name="展开脚本库源码预览",
        default=False,
        update=on_ui_text_fold_changed,
    )
    show_script_library_ai_doc_preview: BoolProperty(
        name="展开脚本库 AI 文档预览",
        default=False,
        update=on_ui_text_fold_changed,
    )
    show_help_text_blocks: BoolProperty(
        name="展开帮助说明",
        default=False,
        update=on_ui_text_fold_changed,
    )
    show_settings: BoolProperty(
        name="显示设置",
        default=False,
    )
    ui_tab: EnumProperty(
        name="设置页签",
        items=SETTINGS_TABS,
        default="WORKFLOWS",
    )


class BWFLOW_PG_State(PropertyGroup):
    space_type: StringProperty(default="VIEW_3D")
    workflows: CollectionProperty(type=BWFLOW_PG_Workflow)
    active_workflow_index: IntProperty(default=0, update=on_workflow_changed)
    panel_registry: CollectionProperty(type=BWFLOW_PG_PanelRecord)
    panel_registry_index: IntProperty(default=0)
    script_library: CollectionProperty(type=BWFLOW_PG_ScriptLibraryItem)
    script_library_index: IntProperty(default=0)
    panel_library_last_click_index: IntProperty(default=-1)
    panel_library_last_click_time: FloatProperty(default=0.0)
    panel_library_last_click_target: StringProperty(default="")
    panel_group_expanded_keys: StringProperty(default="")
    selected_group_expanded_keys: StringProperty(default="")
    preset_filepath: StringProperty(name="预设文件", default="", subtype="FILE_PATH")
    preset_workflows: CollectionProperty(type=BWFLOW_PG_PresetWorkflowItem)
    preset_workflow_index: IntProperty(default=0)
    preset_status: StringProperty(default="")
    module_editor_text: StringProperty(
        name="模块编辑器文本",
        default="",
        description="用于当前模块脚本内容编辑窗口的临时文本缓存",
    )
    settings: PointerProperty(type=BWFLOW_PG_Settings)


class BWFLOW_UL_workflows(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.name, icon="FILE_FOLDER")
        if item.is_default:
            row.label(text="默认", icon="HOME")
        elif index == data.active_workflow_index:
            row.label(text="当前", icon="RADIOBUT_ON")


class BWFLOW_UL_workflow_panels(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        state = get_state(context=context)
        record = find_registry_record(state, item.panel_id)
        title = clean_panel_title(record.title if record else "", item.panel_id)
        row = layout.row(align=True)
        row.alert = bool(record) and not record.discovered
        row.label(text=f"{index + 1:02d}", icon="SORTSIZE")
        row.label(text=title, icon="PLUGIN" if record and record.discovered else "ERROR")
        if item.panel_id == "BWFLOW_PT_workflow":
            row.label(text="固定保留", icon="PINNED")


class BWFLOW_UL_panel_library(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        workflow = get_active_workflow(data)
        selected_ids = {panel.panel_id for panel in workflow.panels} if workflow is not None else set()
        is_selected = item.panel_id in selected_ids
        plugin_title = panel_plugin_title(data, item.panel_id)
        child_depth = panel_child_depth(item.panel_id, getattr(data, "space_type", "VIEW_3D"))
        row = layout.row(align=True)
        row.alert = not item.discovered
        if index == 0:
            row.label(text=plugin_title, icon="GROUP")
        else:
            items = getattr(data, "panel_registry")
            prev_item = items[index - 1] if index - 1 >= 0 else None
            prev_plugin = panel_plugin_title(data, prev_item.panel_id) if prev_item is not None else ""
            if prev_plugin != plugin_title:
                row.label(text=plugin_title, icon="GROUP")
            else:
                row.label(text="", icon="BLANK1")
        op = row.operator(
            "bworkflow.panel_library_click",
            text=("    " * min(child_depth, 3)) + clean_panel_title(item.title, item.panel_id),
            icon="ERROR" if not item.discovered else ("CHECKBOX_HLT" if is_selected else "CHECKBOX_DEHLT"),
            emboss=False,
        )
        op.index = index

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        helper = bpy.types.UI_UL_list
        flags = [self.bitflag_filter_item] * len(items)
        order = helper.sort_items_by_name(items, "title")
        settings = getattr(data, "settings", None)
        if settings is None:
            return flags, order

        return flags, order


class BWFLOW_UL_workflow_modules(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        row.label(text=item.name or f"模块 {index + 1}", icon="CONSOLE")


class BWFLOW_UL_script_library(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.label(text=item.name or f"脚本 {index + 1}", icon="FILE_SCRIPT")
        if item.tags.strip():
            row.label(text=item.tags, icon="ASSET_MANAGER")


class BWFLOW_UL_preset_workflows(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "selected", text="")
        row.label(text=item.name or f"Workflow {index + 1}", icon="HOME" if item.is_default else "FILE_FOLDER")
        row.label(text=item.source_label or item.source_space_type)
        row.label(text=f"{item.panel_count} 面板")
        row.label(text=f"{item.module_count} 模块")


class BWFLOW_OT_refresh_registry(Operator):
    bl_idname = "bworkflow.refresh_registry"
    bl_label = "刷新面板库"
    bl_description = "恢复并重新扫描当前编辑器的第三方 N 面板"

    def execute(self, context):
        restore_unregistered_panels()
        summary = refresh_all_panel_registries(context.scene)
        workflow = get_active_workflow(get_state(context=context))
        if workflow is not None and workflow.is_default:
            restore_default_n_panel_state(scene=context.scene, disable_filters=True, sync_registry_after_restore=False)
        else:
            rebuild_runtime_panels(scene=context.scene, rebuild_cache=False)
        save_global_workflow_state(context.scene)
        self.report(
            {"INFO"},
            f"面板库已刷新：新增 {summary['added_runtime']}，重复 {summary['removed_duplicates']}，失效 {summary['removed_stale']}，空记录 {summary['removed_empty']}",
        )
        return {"FINISHED"}


class BWFLOW_OT_initialize_defaults(Operator):
    bl_idname = "bworkflow.initialize_defaults"
    bl_label = "初始化默认工作流"
    bl_description = "创建默认工作流并刷新面板过滤"

    @classmethod
    def poll(cls, context):
        return get_state(context=context) is not None

    def execute(self, context):
        space_type = current_space_type(context)
        created = ensure_minimum_setup(context.scene, restore_global=False, save_state=False)
        try:
            rebuild_panel_cache(scene=context.scene, space_type=space_type)
        except Exception:
            traceback.print_exc()
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,), space_type=space_type)
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, "默认工作流已初始化" if created else "默认工作流已存在")
        return {"FINISHED"}


class BWFLOW_OT_reset_all_settings(Operator):
    bl_idname = "bworkflow.reset_all_settings"
    bl_label = "清除所有设置"
    bl_description = "清空所有 Go工作流 设置、工作流、脚本库和缓存，并恢复初始默认状态"

    @classmethod
    def poll(cls, context):
        return get_state(context=context) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        clear_panel_filter()
        uninstall_panel_poll_overrides()

        scenes = list(iter_available_scenes())
        for scene in scenes:
            clear_scene_go_workflow_runtime(scene)
            for space_type in iter_supported_space_types():
                state = get_state(scene=scene, space_type=space_type)
                if state is not None:
                    reset_space_state(state)

        filepath = global_workflow_state_path()
        if os.path.isfile(filepath):
            try:
                os.remove(filepath)
            except Exception:
                traceback.print_exc()

        for scene in scenes:
            ensure_minimum_setup(scene)
            rebuild_panel_cache(scene=scene)
            rebuild_runtime_panels(scene=scene, rebuild_cache=False)

        save_global_workflow_state(context.scene)
        tag_redraw_all()
        self.report({"INFO"}, "已清除所有设置并恢复默认状态")
        return {"FINISHED"}


class BWFLOW_OT_restore_default_n_panels(Operator):
    bl_idname = "bworkflow.restore_default_n_panels"
    bl_label = "恢复默认 N 面板"
    bl_description = "恢复所有被 Go工作流 临时隐藏或排序过的第三方 N 面板"

    disable_addon: BoolProperty(default=False)
    uninstall_addon: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def invoke(self, context, event):
        if self.disable_addon or self.uninstall_addon:
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        scenes = list(iter_available_scenes())
        for scene in scenes:
            restore_default_n_panel_state(scene=scene, disable_filters=True, sync_registry_after_restore=False)
        try:
            refresh_all_panel_registries(context.scene)
        except Exception:
            traceback.print_exc()
        save_global_workflow_state(context.scene)
        tag_redraw_all()

        if self.disable_addon or self.uninstall_addon:
            uninstall_addon = bool(self.uninstall_addon)

            def _disable_addon():
                for module_name in addon_module_candidates():
                    if uninstall_addon:
                        try:
                            bpy.ops.preferences.addon_remove(module=module_name)
                            return None
                        except Exception:
                            pass
                    try:
                        bpy.ops.preferences.addon_disable(module=module_name)
                        return None
                    except Exception:
                        pass
                print("[Go工作流] 无法通过 Blender Python API 禁用或卸载插件。")
                return None

            try:
                _register_one_shot_timer(_disable_addon, first_interval=0.1)
            except Exception:
                traceback.print_exc()
            if self.uninstall_addon:
                self.report({"INFO"}, "已恢复默认 N 面板，并尝试卸载 Go工作流。若文件未删除，请继续使用 Blender 插件列表卸载。")
            else:
                self.report({"INFO"}, "已恢复默认 N 面板，并准备禁用 Go工作流。文件卸载请继续使用 Blender 插件列表里的卸载按钮。")
        else:
            self.report({"INFO"}, "已恢复默认 N 面板")
        return {"FINISHED"}


class BWFLOW_OT_workflow_activate(Operator):
    bl_idname = "bworkflow.workflow_activate"
    bl_label = "切换工作流"
    bl_description = "切换当前工作流，并按工作流隐藏或显示第三方 N 面板"

    index: IntProperty()

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        return state is not None and bool(state.workflows)

    def execute(self, context):
        state = get_state(context=context)
        state.active_workflow_index = clamp_index(self.index, len(state.workflows))
        workflow = get_active_workflow(state)
        if workflow is not None and workflow.is_default:
            restore_default_n_panel_state(scene=context.scene, disable_filters=True, sync_registry_after_restore=False)
        else:
            rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        return {"FINISHED"}


class BWFLOW_OT_workflow_add(Operator):
    bl_idname = "bworkflow.workflow_add"
    bl_label = "新建工作流"
    bl_description = "创建一个新的工作流"

    name: StringProperty(name="工作流名称", default="新工作流")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "name", text="名称")

    def execute(self, context):
        created = create_synced_workflow_for_all_spaces(context.scene, self.name.strip() or "新工作流")
        if not created:
            self.report({"ERROR"}, "无法创建工作流")
            return {"CANCELLED"}
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        auto_count = sum(1 for space_type, _workflow in created if space_type != "VIEW_3D")
        self.report({"INFO"}, f"已同步新建工作流；{auto_count} 个非 3D 编辑器默认勾选全部面板")
        return {"FINISHED"}


class BWFLOW_OT_workflow_add_special_preset(Operator):
    bl_idname = "bworkflow.workflow_add_special_preset"
    bl_label = "创建特殊预设工作流"
    bl_description = "一键创建带专用模块与本地数据的特殊预设工作流"

    preset_type: StringProperty(default=SPECIAL_PRESET_ARKIT_52)

    def execute(self, context):
        created = create_special_preset_workflow(context.scene, self.preset_type)
        if not created:
            self.report({"ERROR"}, "特殊预设工作流创建失败")
            return {"CANCELLED"}
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        spec = special_preset_spec(self.preset_type) or {}
        self.report({"INFO"}, f"已创建特殊预设工作流: {spec.get('workflow_name', '特殊预设')}")
        return {"FINISHED"}


class BWFLOW_OT_workflow_duplicate(Operator):
    bl_idname = "bworkflow.workflow_duplicate"
    bl_label = "复制工作流"
    bl_description = "复制当前工作流的面板、模块和说明，生成一个新的工作流"

    @classmethod
    def poll(cls, context):
        return get_active_workflow(get_state(context=context)) is not None

    def execute(self, context):
        state = get_state(context=context)
        source = get_active_workflow(state)
        workflow = state.workflows.add()
        workflow.name = unique_workflow_name(
            state,
            f"{normalize_workflow_name(source.name, '工作流')} Copy",
            fallback="复制工作流",
            exclude_index=len(state.workflows) - 1,
        )
        workflow.is_default = False
        workflow.description = source.description
        workflow.tag_filter = source.tag_filter

        for panel_id in workflow_explicit_panel_ids(source):
            item = workflow.panels.add()
            item.panel_id = panel_id

        for source_module in source.modules:
            module = workflow.modules.add()
            module.name = source_module.name
            module.enabled = source_module.enabled
            module.use_custom_panel = source_module.use_custom_panel
            module.runtime_panel_expanded = getattr(source_module, "runtime_panel_expanded", True)
            module.panel_title = source_module.panel_title
            module.panel_description = source_module.panel_description
            module.description = source_module.description
            module.text_block_name = ""
            module.script_source = source_module.script_source
            module.ai_doc = source_module.ai_doc
            module.script_path = unique_default_module_script_path(workflow, module)

        workflow.active_panel_index = clamp_index(source.active_panel_index, len(workflow.panels))
        workflow.active_module_index = clamp_index(source.active_module_index, len(workflow.modules))
        state.active_workflow_index = len(state.workflows) - 1
        ensure_one_default_workflow(state)
        ensure_go_workflow_panel_entry(state)
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, "已复制当前工作流")
        return {"FINISHED"}


class BWFLOW_OT_workflow_remove(Operator):
    bl_idname = "bworkflow.workflow_remove"
    bl_label = "删除工作流"
    bl_description = "删除当前工作流，不会卸载任何第三方插件"

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        return state is not None and len(state.workflows) > 1

    def execute(self, context):
        state = get_state(context=context)
        old_index = clamp_index(state.active_workflow_index, len(state.workflows))
        state.workflows.remove(old_index)
        state.active_workflow_index = clamp_index(old_index - 1, len(state.workflows))
        ensure_one_default_workflow(state)
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        return {"FINISHED"}


class BWFLOW_OT_workflow_move(Operator):
    bl_idname = "bworkflow.workflow_move"
    bl_label = "移动工作流"
    bl_description = "调整工作流顺序"

    direction: StringProperty()

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        return state is not None and len(state.workflows) > 1

    def execute(self, context):
        state = get_state(context=context)
        old_index = clamp_index(state.active_workflow_index, len(state.workflows))
        new_index = old_index + (-1 if self.direction == "UP" else 1)
        new_index = clamp_index(new_index, len(state.workflows))
        if new_index == old_index:
            return {"CANCELLED"}
        state.workflows.move(old_index, new_index)
        state.active_workflow_index = new_index
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        return {"FINISHED"}


class BWFLOW_OT_workflow_set_default(Operator):
    bl_idname = "bworkflow.workflow_set_default"
    bl_label = "设为默认工作流"
    bl_description = "默认工作流始终显示全部第三方 N 面板"

    @classmethod
    def poll(cls, context):
        return get_active_workflow(get_state(context=context)) is not None

    def execute(self, context):
        state = get_state(context=context)
        active = get_active_workflow(state)
        for workflow in state.workflows:
            workflow.is_default = workflow == active
        restore_default_n_panel_state(scene=context.scene, disable_filters=True, sync_registry_after_restore=False)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, "已设为默认工作流")
        return {"FINISHED"}


class BWFLOW_OT_workflow_clear_description(Operator):
    bl_idname = "bworkflow.workflow_clear_description"
    bl_label = "清空工作流说明"
    bl_description = "清空当前工作流说明文本"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and bool(workflow.description.strip())

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        workflow.description = ""
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, "已清空当前工作流说明")
        return {"FINISHED"}


class BWFLOW_OT_module_add(Operator):
    bl_idname = "bworkflow.module_add"
    bl_label = "新增模块"
    bl_description = "为当前工作流新增一个脚本模块模板"

    name: StringProperty(name="模块名称", default="新模块")

    @classmethod
    def poll(cls, context):
        return get_active_workflow(get_state(context=context)) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "name", text="名称")

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        module = workflow.modules.add()
        module.name = self.name.strip() or "新模块"
        module.enabled = True
        module.use_custom_panel = False
        module.runtime_panel_expanded = True
        module.panel_title = ""
        module.panel_description = ""
        module.script_path = unique_default_module_script_path(workflow, module)
        module.description = "在这里写这个模块要执行的批处理目标，例如整理材质、批量命名、渲染前检查等。"
        module.script_source = ""
        module.ai_doc = build_module_ai_doc(workflow, module)
        ensure_module_text_block(workflow, module)
        workflow.active_module_index = len(workflow.modules) - 1
        initialize_module_runtime_fields(context.scene, workflow, module, context=context)
        tag_redraw_all()
        schedule_deferred_runtime_refresh(scene=context.scene)
        save_global_workflow_state(context.scene)
        return {"FINISHED"}


class BWFLOW_OT_module_assign_default_path(Operator):
    bl_idname = "bworkflow.module_assign_default_path"
    bl_label = "生成默认路径"
    bl_description = "为当前模块生成默认 .py 脚本路径"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and bool(workflow.modules)

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        module.script_path = ensure_module_script_path_matches_name(workflow, module, force=True)
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, f"已按模块名称生成脚本路径: {module.script_path}")
        return {"FINISHED"}


class BWFLOW_OT_module_remove(Operator):
    bl_idname = "bworkflow.module_remove"
    bl_label = "删除模块"
    bl_description = "删除当前工作流里选中的脚本模块"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and bool(workflow.modules)

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        index = clamp_index(workflow.active_module_index, len(workflow.modules))
        workflow.modules.remove(index)
        workflow.active_module_index = clamp_index(index - 1, len(workflow.modules))
        save_global_workflow_state(context.scene)
        return {"FINISHED"}


class BWFLOW_OT_module_move(Operator):
    bl_idname = "bworkflow.module_move"
    bl_label = "移动模块"
    bl_description = "调整模块顺序"

    direction: StringProperty()

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and len(workflow.modules) > 1

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        old_index = clamp_index(workflow.active_module_index, len(workflow.modules))
        new_index = old_index + (-1 if self.direction == "UP" else 1)
        new_index = clamp_index(new_index, len(workflow.modules))
        if new_index == old_index:
            return {"CANCELLED"}
        workflow.modules.move(old_index, new_index)
        workflow.active_module_index = new_index
        save_global_workflow_state(context.scene)
        return {"FINISHED"}


class BWFLOW_OT_module_fill_ai_doc(Operator):
    bl_idname = "bworkflow.module_fill_ai_doc"
    bl_label = "生成智能辅助说明"
    bl_description = "为当前模块生成可直接发给智能辅助工具的开发说明"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and bool(workflow.modules)

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        module.ai_doc = build_module_ai_doc(workflow, module)
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, "智能辅助说明已生成")
        return {"FINISHED"}


class BWFLOW_OT_module_edit_script_source(Operator):
    bl_idname = "bworkflow.module_edit_script_source"
    bl_label = "在文本编辑器中打开"
    bl_description = "把当前模块脚本同步到 Blender 文本编辑器，并在文本编辑器中继续编辑"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and bool(workflow.modules)

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        text_block = ensure_module_text_block(workflow, module)

        scripting_workspace = find_workspace_by_name("Scripting")
        if scripting_workspace is not None:
            result = activate_text_editor_for_text(context, text_block, workspace=scripting_workspace)
            if result in {"scripting_workspace", "workspace_window"}:
                self.report({"INFO"}, "已切换到 Scripting 工作区并打开当前模块")
                return {"FINISHED"}

        if activate_text_editor_for_text(context, text_block) == "current_window":
            self.report({"INFO"}, "已在当前工作区的文本编辑器中打开当前模块")
            return {"FINISHED"}

        self.report({"INFO"}, "已同步脚本到 Blender 文本数据块；未找到可用的 Scripting 文本窗口")
        return {"FINISHED"}


class BWFLOW_OT_module_open_script_path(Operator):
    bl_idname = "bworkflow.module_open_script_path"
    bl_label = "打开当前路径"
    bl_description = "用 Windows 文件资源管理器打开当前脚本路径或所在文件夹"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        if workflow is None or not workflow.modules:
            return False
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        return bool((module.script_path or "").strip())

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        try:
            opened = open_path_in_file_explorer(module.script_path)
        except Exception as exc:
            self.report({"ERROR"}, f"打开路径失败: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"已打开: {opened}")
        return {"FINISHED"}


class BWFLOW_OT_module_open_script_directory(Operator):
    bl_idname = "bworkflow.module_open_script_directory"
    bl_label = "打开脚本目录"
    bl_description = "用 Windows 文件资源管理器打开当前模块 .py 文件所在目录"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and bool(workflow.modules)

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        try:
            opened = open_module_script_directory(workflow, module)
        except Exception as exc:
            self.report({"ERROR"}, f"打开脚本目录失败: {exc}")
            return {"CANCELLED"}
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, f"已打开脚本目录: {opened}")
        return {"FINISHED"}


class BWFLOW_OT_module_export_to_default_scripts(Operator):
    bl_idname = "bworkflow.module_export_to_default_scripts"
    bl_label = "导出到公共脚本目录"
    bl_description = "把当前模块脚本写入插件公共脚本目录，便于别的工程直接复用，不用走预设导入导出"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and bool(workflow.modules)

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        sync_module_source_from_text_block(module)
        original_path = module.script_path
        try:
            module.script_path = unique_default_module_script_path(workflow, module)
            filepath = write_module_source_file(workflow, module)
        except Exception as exc:
            module.script_path = original_path
            self.report({"ERROR"}, f"导出公共脚本失败: {exc}")
            return {"CANCELLED"}
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, f"已导出到公共脚本目录: {filepath}")
        return {"FINISHED"}


class BWFLOW_OT_module_import_text_file(Operator, ImportHelper):
    bl_idname = "bworkflow.module_import_text_file"
    bl_label = "导入模块文本文件"
    bl_description = "通过 Blender 原生文件浏览器为当前模块导入 csv/txt 文本内容"

    filename_ext = ".csv"
    filter_glob: StringProperty(default="*.csv;*.txt", options={"HIDDEN"})
    workflow_name: StringProperty(default="")
    module_name: StringProperty(default="")
    target_text_key: StringProperty(default="")
    target_set: StringProperty(default="")
    mix_profile: StringProperty(default="")
    status_prefix: StringProperty(default="已导入")
    module_action: StringProperty(default="")

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and bool(workflow.modules)

    def execute(self, context):
        workflow, module = resolve_workflow_module(context.scene, self.workflow_name, self.module_name)
        if workflow is None or module is None:
            workflow = get_active_workflow(get_state(context=context))
            if workflow is None or not workflow.modules:
                self.report({"ERROR"}, "鎵句笉鍒板彂璧峰鍏ョ殑宸ヤ綔娴佹ā鍧?")
                return {"CANCELLED"}
            module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        filepath = bpy.path.abspath(self.filepath)
        if not filepath or not os.path.isfile(filepath):
            self.report({"ERROR"}, "导入文件不存在")
            return {"CANCELLED"}
        try:
            content = read_cached_text_file(
                filepath,
                encodings=("utf-8-sig", "utf-8", "gb18030", "gbk", "utf-16", "utf-16-le", "utf-16-be"),
                errors="replace",
            )
        except Exception as exc:
            self.report({"ERROR"}, f"读取导入文件失败: {exc}")
            return {"CANCELLED"}
        if self.target_text_key:
            save_module_runtime_store(context.scene, workflow, module, ensure_module_runtime_store(context.scene, workflow, module, allow_writes=True))
            namespace = load_module_namespace(
                context,
                context.scene,
                workflow,
                module,
                "__go_workflow_import_text__",
                allow_writes=True,
                persist_state=False,
            )
            panel_api = namespace.get("panel_api")
            if panel_api is not None:
                panel_api.set_text(self.target_text_key, filepath)
                if self.target_set:
                    panel_api.set_enum("target_set", self.target_set)
                if self.mix_profile:
                    panel_api.set_enum("mix_profile", self.mix_profile)
            if self.module_action:
                action_fn = namespace.get("on_panel_action")
                if callable(action_fn):
                    try:
                        result = action_fn(
                            self.module_action,
                            context,
                            context.scene,
                            workflow,
                            module,
                            panel_api,
                            namespace.get("module_state"),
                        )
                        if namespace.get("module_state") is not None and hasattr(namespace["module_state"], "to_dict"):
                            merge_module_runtime_store(context.scene, workflow, module, namespace["module_state"].to_dict())
                        if isinstance(result, set) and "CANCELLED" in result:
                            return result
                    except BaseException as exc:
                        traceback.print_exc()
                        self.report({"ERROR"}, f"导入处理失败: {exc}")
                        return {"CANCELLED"}
        self.report({"INFO"}, f"{self.status_prefix}: {filepath}")
        return {"FINISHED"}


class BWFLOW_OT_module_export_text_file(Operator, ExportHelper):
    bl_idname = "bworkflow.module_export_text_file"
    bl_label = "导出模块文本文件"
    bl_description = "通过 Blender 原生文件浏览器为当前模块选择导出位置，并执行自定义导出动作"

    filename_ext: StringProperty(default=".txt")
    filter_glob: StringProperty(default="*.csv;*.txt", options={"HIDDEN"})
    workflow_name: StringProperty(default="")
    module_name: StringProperty(default="")
    target_text_key: StringProperty(default="recipe_file_path")
    status_prefix: StringProperty(default="已选择导出位置")
    module_action: StringProperty(default="")
    default_filename: StringProperty(default="")
    text_payload: StringProperty(default="", options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and bool(workflow.modules)

    def invoke(self, context, event):
        filename = str(self.default_filename or "").strip()
        if filename:
            try:
                self.filepath = bpy.path.abspath("//" + filename)
            except Exception:
                self.filepath = filename
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        workflow, module = resolve_workflow_module(context.scene, self.workflow_name, self.module_name)
        if workflow is None or module is None:
            workflow = get_active_workflow(get_state(context=context))
            if workflow is None or not workflow.modules:
                self.report({"ERROR"}, "鎵句笉鍒板彂璧峰鍑虹殑宸ヤ綔娴佹ā鍧?")
                return {"CANCELLED"}
            module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        filepath = bpy.path.abspath(self.filepath)
        if not filepath:
            self.report({"ERROR"}, "导出路径为空")
            return {"CANCELLED"}
        folder = os.path.dirname(filepath)
        if folder:
            os.makedirs(folder, exist_ok=True)
        if self.text_payload:
            try:
                with open(filepath, "w", encoding="utf-8", newline="") as handle:
                    handle.write(self.text_payload)
            except Exception as exc:
                self.report({"ERROR"}, f"导出文件失败: {exc}")
                return {"CANCELLED"}
            if not self.module_action:
                self.report({"INFO"}, f"{self.status_prefix}: {filepath}")
                return {"FINISHED"}
        save_module_runtime_store(context.scene, workflow, module, ensure_module_runtime_store(context.scene, workflow, module, allow_writes=True))
        namespace = load_module_namespace(
            context,
            context.scene,
            workflow,
            module,
            "__go_workflow_export_text__",
            allow_writes=True,
            persist_state=False,
        )
        panel_api = namespace.get("panel_api")
        if panel_api is not None and self.target_text_key:
            panel_api.set_text(self.target_text_key, filepath)
        if self.module_action:
            action_fn = namespace.get("on_panel_action")
            if callable(action_fn):
                try:
                    result = action_fn(
                        self.module_action,
                        context,
                        context.scene,
                        workflow,
                        module,
                        panel_api,
                        namespace.get("module_state"),
                    )
                    if namespace.get("module_state") is not None and hasattr(namespace["module_state"], "to_dict"):
                        merge_module_runtime_store(context.scene, workflow, module, namespace["module_state"].to_dict())
                    if isinstance(result, set) and "CANCELLED" in result:
                        return result
                except BaseException as exc:
                    traceback.print_exc()
                    self.report({"ERROR"}, f"导出处理失败: {exc}")
                    return {"CANCELLED"}
        self.report({"INFO"}, f"{self.status_prefix}: {filepath}")
        return {"FINISHED"}


class BWFLOW_OT_module_edit_description(Operator):
    bl_idname = "bworkflow.module_edit_description"
    bl_label = "编辑说明"
    bl_description = "在当前面板弹窗中编辑当前模块说明"

    description_text: StringProperty(default="", options={"TEXTEDIT_UPDATE"})

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and bool(workflow.modules)

    def invoke(self, context, event):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        self.description_text = module.description or ""
        return context.window_manager.invoke_props_dialog(self, width=720)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.scale_y = 3.0
        col.prop(self, "description_text", text="")

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        module.description = self.description_text
        module.ai_doc = build_module_ai_doc(workflow, module)
        save_global_workflow_state(context.scene)
        tag_redraw_all()
        return {"FINISHED"}


class BWFLOW_OT_module_paste_script_source(Operator):
    bl_idname = "bworkflow.module_paste_script_source"
    bl_label = "从剪贴板载入代码"
    bl_description = "把系统剪贴板里的 Python 代码直接保存到当前模块编辑区"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        if workflow is None or not workflow.modules:
            return False
        return bool((context.window_manager.clipboard or "").strip())

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        clipboard_text = context.window_manager.clipboard or ""
        if not clipboard_text.strip():
            self.report({"WARNING"}, "剪贴板里没有可用代码")
            return {"CANCELLED"}
        module.script_source = clipboard_text
        state.module_editor_text = clipboard_text
        ensure_module_text_block(workflow, module)
        self.report({"INFO"}, "已从剪贴板载入代码到当前模块")
        return {"FINISHED"}


class BWFLOW_OT_module_copy_ai_doc(Operator):
    bl_idname = "bworkflow.module_copy_ai_doc"
    bl_label = "复制智能辅助说明"
    bl_description = "把当前模块的智能辅助说明复制到剪贴板"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and bool(workflow.modules)

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        module.ai_doc = build_module_ai_doc(workflow, module)
        context.window_manager.clipboard = module.ai_doc
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, "智能辅助说明已复制到剪贴板")
        return {"FINISHED"}


class BWFLOW_OT_module_copy_script_source(Operator):
    bl_idname = "bworkflow.module_copy_script_source"
    bl_label = "复制当前代码"
    bl_description = "把当前模块编辑区里的代码复制到系统剪贴板"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        if workflow is None or not workflow.modules:
            return False
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        return bool(module.script_source.strip())

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        context.window_manager.clipboard = module.script_source
        self.report({"INFO"}, "当前模块代码已复制到剪贴板")
        return {"FINISHED"}


class BWFLOW_OT_module_load_script_file(Operator):
    bl_idname = "bworkflow.module_load_script_file"
    bl_label = "从文件读取脚本"
    bl_description = "把当前脚本文件内容读取回插件内置编辑区"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        if workflow is None or not workflow.modules:
            return False
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        filepath = module_script_abspath(module)
        return bool(filepath) and os.path.isfile(filepath)

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        filepath = module_script_abspath(module)
        try:
            module.script_source = read_cached_text_file(filepath, encodings=("utf-8", "utf-8-sig"))
        except Exception as exc:
            self.report({"ERROR"}, f"读取脚本失败: {exc}")
            return {"CANCELLED"}
        ensure_module_text_block(workflow, module)
        self.report({"INFO"}, "脚本文件内容已加载到编辑区")
        return {"FINISHED"}


class BWFLOW_OT_module_write_template(Operator):
    bl_idname = "bworkflow.module_write_template"
    bl_label = "写入脚本文件"
    bl_description = "把当前代码内容写入当前模块绑定的 .py 文件"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and bool(workflow.modules)

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        sync_module_source_from_text_block(module)
        try:
            filepath = write_module_source_file(workflow, module)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"脚本文件已写入: {filepath}")
        return {"FINISHED"}


class BWFLOW_OT_module_run(Operator):
    bl_idname = "bworkflow.module_run"
    bl_label = "运行模块"
    bl_description = "运行当前工作流模块绑定的 Python 脚本"

    module_index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and bool(workflow.modules)

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        index = self.module_index
        if index < 0:
            index = clamp_index(workflow.active_module_index, len(workflow.modules))
        else:
            index = clamp_index(index, len(workflow.modules))

        module = workflow.modules[index]
        if not module.enabled:
            self.report({"WARNING"}, "该模块已禁用")
            return {"CANCELLED"}

        sync_module_source_from_text_block(module)
        source = (module.script_source or "").strip()
        filepath = module_script_abspath(module)
        if not source:
            if not filepath:
                self.report({"ERROR"}, "当前模块没有代码内容，也没有脚本路径")
                return {"CANCELLED"}
            if not os.path.isfile(filepath):
                self.report({"ERROR"}, f"脚本不存在: {filepath}")
                return {"CANCELLED"}
            if not filepath.lower().endswith(".py"):
                self.report({"ERROR"}, "当前只支持运行 .py 脚本")
                return {"CANCELLED"}
            try:
                source = read_cached_text_file(filepath, encodings=("utf-8", "utf-8-sig"))
            except Exception as exc:
                self.report({"ERROR"}, f"脚本读取失败: {exc}")
                return {"CANCELLED"}

        try:
            namespace = execute_module_source(source, context, context.scene, workflow, module, filepath or "__go_workflow_run__")
            entry = namespace.get("run")
            if callable(entry):
                result = entry(context, context.scene, workflow, module)
                if "module_state" in namespace:
                    merge_module_runtime_store(context.scene, workflow, module, namespace["module_state"].to_dict())
                if isinstance(result, set):
                    self.report({"INFO"}, f"模块执行完成: {module.name}")
                    return result
            if "module_state" in namespace:
                merge_module_runtime_store(context.scene, workflow, module, namespace["module_state"].to_dict())
            self.report({"INFO"}, f"脚本已执行: {module.name}")
            return {"FINISHED"}
        except BaseException as exc:
            traceback.print_exc()
            self.report({"ERROR"}, f"模块运行失败: {exc}")
            return {"CANCELLED"}


class BWFLOW_OT_script_library_save_current_module(Operator):
    bl_idname = "bworkflow.script_library_save_current_module"
    bl_label = "保存到脚本库"
    bl_description = "把当前模块的脚本内容保存到脚本库里，便于在其他工作流复用"

    overwrite_existing: BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        return state is not None and workflow is not None and bool(workflow.modules)

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        if not sync_module_source_from_text_block(module):
            sync_module_source_from_file(module)
        module.name = normalize_workflow_name(module.name, "脚本模板")
        module.ai_doc = build_module_ai_doc(workflow, module)
        index = -1
        if self.overwrite_existing and state.script_library:
            index = clamp_index(getattr(state, "script_library_index", 0), len(state.script_library))
            item = state.script_library[index]
        else:
            item = state.script_library.add()
            index = len(state.script_library) - 1
        item.name = unique_script_library_name(state, module.name or "脚本模板", exclude_index=index)
        copy_module_to_script_library_item(item, module)
        state.script_library_index = index
        save_global_workflow_state(context.scene)
        tag_redraw_all()
        action = "已覆盖脚本库条目" if self.overwrite_existing else "已新增脚本库条目"
        self.report({"INFO"}, f"{action}: {item.name}")
        return {"FINISHED"}


class BWFLOW_OT_script_library_apply_to_module(Operator):
    bl_idname = "bworkflow.script_library_apply_to_module"
    bl_label = "载入到当前模块"
    bl_description = "把选中的脚本库条目载入当前模块"

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        return state is not None and workflow is not None and bool(workflow.modules) and bool(state.script_library)

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
        index = clamp_index(state.script_library_index, len(state.script_library))
        item = state.script_library[index]
        apply_script_library_item_to_module(module, item)
        module.script_path = ensure_module_script_path_matches_name(workflow, module, force=True)
        ensure_module_text_block(workflow, module)
        module.ai_doc = module.ai_doc or build_module_ai_doc(workflow, module)
        initialize_module_runtime_fields(context.scene, workflow, module, context=context)
        rebuild_runtime_panels(scene=context.scene, rebuild_cache=False)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        tag_redraw_all()
        self.report({"INFO"}, f"已载入脚本库条目: {item.name}")
        return {"FINISHED"}


class BWFLOW_OT_script_library_toggle_workflow(Operator):
    bl_idname = "bworkflow.script_library_toggle_workflow"
    bl_label = "切换脚本库工作流启用"
    bl_description = "在指定工作流中启用或关闭当前脚本库条目"

    workflow_index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        return state is not None and bool(state.script_library) and bool(state.workflows)

    def execute(self, context):
        state = get_state(context=context)
        if state is None or not state.script_library or not state.workflows:
            return {"CANCELLED"}

        script_index = clamp_index(state.script_library_index, len(state.script_library))
        workflow_index = clamp_index(self.workflow_index, len(state.workflows))
        item = state.script_library[script_index]
        workflow = state.workflows[workflow_index]

        module, module_index = find_workflow_module_for_script_item(workflow, item)
        if module is None:
            module = add_script_item_to_workflow_module(workflow, item)
            if module is None:
                return {"CANCELLED"}
            module.enabled = True
            message = f"已在 {workflow.name} 启用脚本: {item.name}"
        else:
            module.enabled = not bool(module.enabled)
            workflow.active_module_index = clamp_index(module_index, len(workflow.modules))
            message = f"{workflow.name}: {'已启用' if module.enabled else '已关闭'} {item.name}"

        ensure_module_text_block(workflow, module)
        ensure_module_script_path_matches_name(workflow, module)
        initialize_module_runtime_fields(context.scene, workflow, module, context=context)
        rebuild_runtime_panels(scene=context.scene, rebuild_cache=False)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        tag_redraw_all()
        self.report({"INFO"}, message)
        return {"FINISHED"}


class BWFLOW_OT_script_library_remove(Operator):
    bl_idname = "bworkflow.script_library_remove"
    bl_label = "删除脚本库条目"
    bl_description = "删除当前选中的脚本库条目"

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        return state is not None and bool(state.script_library)

    def execute(self, context):
        state = get_state(context=context)
        index = clamp_index(state.script_library_index, len(state.script_library))
        removed_name = state.script_library[index].name if state.script_library else ""
        state.script_library.remove(index)
        state.script_library_index = clamp_index(index, len(state.script_library))
        save_global_workflow_state(context.scene)
        tag_redraw_all()
        self.report({"INFO"}, f"已删除脚本库条目: {removed_name or '未命名'}")
        return {"FINISHED"}


class BWFLOW_OT_script_library_refresh(Operator):
    bl_idname = "bworkflow.script_library_refresh"
    bl_label = "刷新脚本库"
    bl_description = "同步脚本库条目的文本块和 .py 文件内容，并刷新当前列表"

    @classmethod
    def poll(cls, context):
        return get_state(context=context) is not None

    def execute(self, context):
        state = get_state(context=context)
        if state is None:
            return {"CANCELLED"}

        refreshed = 0
        failed = 0
        for item in state.script_library:
            try:
                updated = sync_script_library_item_source(item)
            except Exception:
                updated = False
                failed += 1

            if updated:
                refreshed += 1

        state.script_library_index = clamp_index(getattr(state, "script_library_index", 0), len(state.script_library))
        save_global_workflow_state(context.scene)
        tag_redraw_all()
        if failed:
            self.report({"WARNING"}, f"脚本库已刷新 {refreshed} 项，{failed} 项读取失败")
        else:
            self.report({"INFO"}, f"脚本库已刷新 {refreshed} 项")
        return {"FINISHED"}


class BWFLOW_OT_script_library_open_storage_folder(Operator):
    bl_idname = "bworkflow.script_library_open_storage_folder"
    bl_label = "打开脚本文件夹"
    bl_description = "用 Windows 文件资源管理器打开当前脚本库条目的脚本存储文件夹"

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        return state is not None and bool(state.script_library)

    def execute(self, context):
        state = get_state(context=context)
        if state is None or not state.script_library:
            self.report({"ERROR"}, "当前没有脚本库条目")
            return {"CANCELLED"}

        item = state.script_library[clamp_index(state.script_library_index, len(state.script_library))]
        raw_path = (getattr(item, "script_path", "") or "").strip()
        try:
            if raw_path:
                target_path = bpy.path.abspath(raw_path)
                if os.path.isfile(target_path):
                    opened = open_path_in_file_explorer(target_path)
                else:
                    folder = target_path if os.path.isdir(target_path) else os.path.dirname(target_path)
                    if folder:
                        os.makedirs(folder, exist_ok=True)
                        opened = open_path_in_file_explorer(folder)
                    else:
                        opened = open_path_in_file_explorer(default_module_scripts_dir())
            else:
                os.makedirs(default_module_scripts_dir(), exist_ok=True)
                opened = open_path_in_file_explorer(default_module_scripts_dir())
        except Exception as exc:
            self.report({"ERROR"}, f"打开脚本文件夹失败: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"已打开脚本文件夹: {opened}")
        return {"FINISHED"}


class BWFLOW_OT_panel_toggle_for_workflow(Operator):
    bl_idname = "bworkflow.panel_toggle_for_workflow"
    bl_label = "切换当前面板组"
    bl_description = "把当前选中面板所属的组件组加入或移出当前工作流"

    panel_index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        return state is not None and workflow is not None and bool(state.panel_registry)

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        if workflow.is_default:
            self.report({"INFO"}, "默认工作流始终显示全部第三方 N 面板，无需手动勾选。")
            clear_panel_library_click_state(state)
            return {"CANCELLED"}

        index = self.panel_index if self.panel_index >= 0 else state.panel_registry_index
        index = clamp_index(index, len(state.panel_registry))
        state.panel_registry_index = index
        record = state.panel_registry[index]
        panel_id = record.panel_id
        if panel_id == "BWFLOW_PT_workflow":
            self.report({"INFO"}, "Go工作流主面板固定保留，不参与手动勾选。")
            clear_panel_library_click_state(state)
            return {"CANCELLED"}

        workflow_panel_ids = workflow_toggleable_panel_ids(state, panel_drawer_workflow_ids(state, panel_id))
        selected_ids = {item.panel_id for item in workflow.panels}
        has_any = any(candidate_id in selected_ids for candidate_id in workflow_panel_ids)

        if has_any:
            kept_ids = [item.panel_id for item in workflow.panels if item.panel_id not in workflow_panel_ids]
            replace_workflow_panels(workflow, kept_ids)
            rebuild_runtime_panels(scene=context.scene)
            schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
            save_global_workflow_state(context.scene)
            clear_panel_library_click_state(state)
            self.report({"INFO"}, f"已移出当前抽屉面板，共 {len(workflow_panel_ids)} 个。")
            return {"FINISHED"}

        added = append_panel_ids_to_workflow(workflow, workflow_panel_ids)
        rebuild_runtime_panels(scene=context.scene)
        save_global_workflow_state(context.scene)
        clear_panel_library_click_state(state)
        self.report({"INFO"}, f"已加入当前抽屉面板，共 {added} 个。")
        return {"FINISHED"}


class BWFLOW_OT_panel_toggle_group_for_workflow(Operator):
    bl_idname = "bworkflow.panel_toggle_group_for_workflow"
    bl_label = "切换当前面板大组"
    bl_description = "把当前面板大组下的抽屉面板整体加入或移出当前工作流"

    group_key: StringProperty(default="")
    panel_index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        return state is not None and workflow is not None and not workflow.is_default and bool(state.panel_registry)

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        groups = build_panel_library_groups(state, workflow)
        target_group = next((group for group in groups if group.get("key") == self.group_key), None)

        if target_group is None and self.panel_index >= 0:
            index = clamp_index(self.panel_index, len(state.panel_registry))
            state.panel_registry_index = index
            record = state.panel_registry[index]
            target_key = workflow_group_key_for_panel(state, record.panel_id)
            target_group = next((group for group in groups if group.get("key") == target_key), None)

        if target_group is None:
            return {"CANCELLED"}

        entries = panel_library_group_entries(state, workflow, target_group)
        drawer_ids = workflow_toggleable_panel_ids(
            state,
            (entry.get("panel_id", "") for entry in entries),
        )
        if not drawer_ids:
            return {"CANCELLED"}

        selected_ids = {item.panel_id for item in workflow.panels}
        if all(panel_id in selected_ids for panel_id in drawer_ids):
            kept_ids = [item.panel_id for item in workflow.panels if item.panel_id not in set(drawer_ids)]
            replace_workflow_panels(workflow, kept_ids)
            message = f"已移出面板大组: {target_group.get('title', '')}"
        else:
            append_panel_ids_to_workflow(workflow, drawer_ids)
            message = f"已加入面板大组: {target_group.get('title', '')}"

        set_active_workflow_panel_by_id(workflow, drawer_ids[0])
        rebuild_runtime_panels(scene=context.scene)
        save_global_workflow_state(context.scene)
        clear_panel_library_click_state(state)
        self.report({"INFO"}, message)
        return {"FINISHED"}


class BWFLOW_OT_panel_library_click(Operator):
    bl_idname = "bworkflow.panel_library_click"
    bl_label = "选择面板"
    bl_description = "选中面板；双击子面板只切换当前项"

    index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        return state is not None and bool(state.panel_registry)

    def execute(self, context):
        return self._handle(context, event=None)

    def invoke(self, context, event):
        return self._handle(context, event=event)

    def _handle(self, context, event=None):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        if state is None or not state.panel_registry:
            return {"CANCELLED"}

        index = clamp_index(self.index, len(state.panel_registry))
        click_target = f"panel:{index}"
        state.panel_registry_index = index
        state.panel_library_last_click_index = index
        record = state.panel_registry[index]
        if is_builtin_default_panel_record(record):
            clear_panel_library_click_state(state)
            return {"FINISHED"}

        if workflow is None or workflow.is_default:
            should_treat_as_double_click(state, click_target, event=event)
            return {"FINISHED"}

        if should_treat_as_double_click(state, click_target, event=event):
            return bpy.ops.bworkflow.panel_toggle_for_workflow("EXEC_DEFAULT", panel_index=index)
        return {"FINISHED"}


class BWFLOW_OT_panel_add_all_to_workflow(Operator):
    bl_idname = "bworkflow.panel_add_all_to_workflow"
    bl_label = "全部加入当前工作流"
    bl_description = "把当前已发现的第三方 N 面板全部加入当前工作流"

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        return state is not None and workflow is not None and not workflow.is_default

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        added = append_panel_ids_to_workflow(
            workflow,
            unique_panel_ids(
                panel_drawer_workflow_ids(state, record.panel_id)[0]
                for record in state.panel_registry
                if record.discovered and record.panel_id != "BWFLOW_PT_workflow" and panel_drawer_workflow_ids(state, record.panel_id)
            ),
        )
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, f"已加入 {added} 个面板组")
        return {"FINISHED"}


class BWFLOW_OT_panel_add_current_plugin_to_workflow(Operator):
    bl_idname = "bworkflow.panel_add_current_plugin_to_workflow"
    bl_label = "添加当前面板组"
    bl_description = "把当前选中面板所属的组件组加入当前工作流"

    panel_index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        return state is not None and workflow is not None and not workflow.is_default and bool(state.panel_registry)

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        index = self.panel_index if self.panel_index >= 0 else state.panel_registry_index
        index = clamp_index(index, len(state.panel_registry))
        state.panel_registry_index = index
        record = state.panel_registry[index]
        panel_ids = panel_drawer_workflow_ids(state, record.panel_id)
        added = append_panel_ids_to_workflow(workflow, panel_ids)
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, f"已添加当前抽屉面板，共 {added} 个。")
        return {"FINISHED"}


class BWFLOW_OT_panel_toggle_single_for_workflow(Operator):
    bl_idname = "bworkflow.panel_toggle_single_for_workflow"
    bl_label = "切换单个面板"
    bl_description = "只把当前选中面板加入或移出当前工作流"

    panel_index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        return state is not None and workflow is not None and not workflow.is_default and bool(state.panel_registry)

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        index = self.panel_index if self.panel_index >= 0 else state.panel_registry_index
        index = clamp_index(index, len(state.panel_registry))
        state.panel_registry_index = index
        record = state.panel_registry[index]
        panel_id = record.panel_id

        if panel_id == "BWFLOW_PT_workflow":
            self.report({"INFO"}, "Go工作流主面板固定保留，不参与手动勾选。")
            clear_panel_library_click_state(state)
            return {"CANCELLED"}

        selected_ids = {item.panel_id for item in workflow.panels}
        if panel_id in selected_ids:
            kept_ids = [item.panel_id for item in workflow.panels if item.panel_id != panel_id]
            replace_workflow_panels(workflow, kept_ids)
            rebuild_runtime_panels(scene=context.scene)
            schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
            save_global_workflow_state(context.scene)
            clear_panel_library_click_state(state)
            self.report({"INFO"}, "已移出当前面板。")
            return {"FINISHED"}

        added = append_panel_ids_to_workflow(workflow, [panel_id])
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        clear_panel_library_click_state(state)
        self.report({"INFO"}, f"已加入当前面板，共 {added} 个。")
        return {"FINISHED"}


class BWFLOW_OT_group_expand_toggle(Operator):
    bl_idname = "bworkflow.group_expand_toggle"
    bl_label = "展开或收起分组"
    bl_description = "展开或收起当前面板组"

    group_key: StringProperty(default="")
    target: StringProperty(default="LIBRARY")

    def execute(self, context):
        state = get_state(context=context)
        toggle_group_expanded(state, self.group_key, selected=self.target == "SELECTED")
        tag_redraw_all()
        return {"FINISHED"}


class BWFLOW_OT_select_workflow_panel(Operator):
    bl_idname = "bworkflow.select_workflow_panel"
    bl_label = "选择工作流面板"
    bl_description = "单击选中，双击切换当前工作流中的这个面板"

    panel_id: StringProperty(default="")

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and bool(workflow.panels)

    def execute(self, context):
        return self._handle(context, event=None)

    def invoke(self, context, event):
        return self._handle(context, event=event)

    def _handle(self, context, event=None):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        click_target = f"selected:{self.panel_id}"
        group_key = workflow_group_key_for_panel(state, self.panel_id)

        for index, item in enumerate(workflow.panels):
            if item.panel_id == self.panel_id:
                workflow.active_panel_index = index
                break

        if should_treat_as_double_click(state, click_target, event=event):
            drawer_ids = workflow_family_drawer_ids(state, workflow, group_key)
            registry_index = workflow_panel_registry_index(state, workflow, drawer_ids[0] if drawer_ids else self.panel_id)
            if registry_index >= 0 and group_key:
                clear_panel_library_click_state(state)
                return bpy.ops.bworkflow.panel_toggle_group_for_workflow(
                    "EXEC_DEFAULT",
                    panel_index=registry_index,
                    group_key=group_key,
                )
        return {"FINISHED"}


class BWFLOW_OT_panel_group_click(Operator):
    bl_idname = "bworkflow.panel_group_click"
    bl_label = "选择面板大组"
    bl_description = "单击展开或收起分组，双击把当前面板组加入或移出当前工作流"

    group_key: StringProperty(default="")
    panel_index: IntProperty(default=-1)
    target: StringProperty(default="LIBRARY")

    def execute(self, context):
        return self._handle(context, event=None)

    def invoke(self, context, event):
        return self._handle(context, event=event)

    def _handle(self, context, event=None):
        state = get_state(context=context)
        click_target = f"group:{self.group_key}"

        if self.panel_index >= 0:
            state.panel_registry_index = clamp_index(self.panel_index, len(state.panel_registry))
            state.panel_library_last_click_index = self.panel_index

        if self.target == "LIBRARY":
            if should_treat_as_double_click(state, click_target, event=event):
                return bpy.ops.bworkflow.panel_toggle_group_for_workflow(
                    "EXEC_DEFAULT",
                    group_key=self.group_key,
                    panel_index=self.panel_index,
                )
        else:
            if self.panel_index >= 0 and self.panel_index < len(state.panel_registry):
                record = state.panel_registry[self.panel_index]
                if is_builtin_default_panel_record(record):
                    return {"FINISHED"}
            register_click_target(state, click_target)
        tag_redraw_all()
        return {"FINISHED"}


class BWFLOW_OT_panel_add_tagged_to_workflow(Operator):
    bl_idname = "bworkflow.panel_add_tagged_to_workflow"
    bl_label = "加入标签命中面板"
    bl_description = "把当前工作流按标签自动加入命中的面板加入当前工作流显示列表"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and not workflow.is_default and bool(parse_tags(workflow.tag_filter))

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        tag_filter = set(parse_tags(workflow.tag_filter))
        added = append_panel_ids_to_workflow(
            workflow,
            unique_panel_ids(
                panel_drawer_workflow_ids(state, record.panel_id)[0]
                for record in state.panel_registry
                if (
                    record.discovered
                    and record.panel_id != "BWFLOW_PT_workflow"
                    and tag_filter.intersection(parse_tags(record.tags))
                    and panel_drawer_workflow_ids(state, record.panel_id)
                )
            ),
        )
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, f"已按标签加入 {added} 个面板组")
        return {"FINISHED"}


class BWFLOW_OT_panel_clear_workflow(Operator):
    bl_idname = "bworkflow.panel_clear_workflow"
    bl_label = "清空当前工作流面板"
    bl_description = "清空当前工作流的面板勾选；切换到该工作流时将隐藏全部第三方 N 面板"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and not workflow.is_default and bool(workflow.panels)

    def execute(self, context):
        workflow = get_active_workflow(get_state(context=context))
        go_panel_entry = [item.panel_id for item in workflow.panels if item.panel_id == "BWFLOW_PT_workflow"]
        replace_workflow_panels(workflow, go_panel_entry)
        workflow.active_panel_index = 0
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, "当前工作流面板勾选已清空")
        return {"FINISHED"}


class BWFLOW_OT_panel_remove_missing_from_workflow(Operator):
    bl_idname = "bworkflow.panel_remove_missing_from_workflow"
    bl_label = "移除缺失面板"
    bl_description = "移除当前工作流列表里在当前环境缺失的面板"

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        return workflow is not None and not workflow.is_default and bool(workflow_missing_panel_ids(state, workflow))

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        missing_ids = set(workflow_missing_panel_ids(state, workflow))
        kept_ids = [item.panel_id for item in workflow.panels if item.panel_id not in missing_ids]
        removed = len(workflow.panels) - len(kept_ids)
        replace_workflow_panels(workflow, kept_ids)
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, f"已移除 {removed} 个缺失面板")
        return {"FINISHED"}


class BWFLOW_OT_panel_reset_workflow_order(Operator):
    bl_idname = "bworkflow.panel_reset_workflow_order"
    bl_label = "重置面板顺序"
    bl_description = "按当前扫描到的大组与组件顺序，重建当前工作流的显式面板组顺序"

    @classmethod
    def poll(cls, context):
        workflow = get_active_workflow(get_state(context=context))
        return workflow is not None and len(workflow.panels) > 1

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        active_index = clamp_index(workflow.active_panel_index, len(workflow.panels))
        active_panel_id = workflow.panels[active_index].panel_id if workflow.panels else ""
        groups = workflow_family_order_groups(state, workflow)
        groups.sort(key=lambda group: (group["title"].casefold(), group["key"]))
        replace_workflow_groups(workflow, groups, active_panel_id=active_panel_id)
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, "当前工作流面板组顺序已重置")
        return {"FINISHED"}


class BWFLOW_OT_panel_jump_in_workflow(Operator):
    bl_idname = "bworkflow.panel_jump_in_workflow"
    bl_label = "快速移动工作流面板"
    bl_description = "把当前选中的面板组直接移到顶部或底部"

    target: StringProperty()
    group_key: StringProperty(default="")

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        return workflow is not None and len(workflow_family_order_groups(state, workflow)) > 1

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        groups = workflow_family_order_groups(state, workflow)
        if len(groups) <= 1:
            return {"CANCELLED"}
        active_key = self.group_key or active_workflow_family_group_key(state, workflow)
        old_pos = next((index for index, group in enumerate(groups) if group["key"] == active_key), -1)
        if old_pos < 0:
            return {"CANCELLED"}
        if self.target == "TOP":
            new_pos = 0
        else:
            new_pos = len(groups) - 1
        if new_pos == old_pos:
            return {"CANCELLED"}
        group = groups.pop(old_pos)
        groups.insert(new_pos, group)
        replace_workflow_groups(workflow, groups, active_panel_id=group["ids"][0] if group["ids"] else "")
        rebuild_runtime_panels(scene=context.scene)
        save_global_workflow_state(context.scene)
        return {"FINISHED"}


class BWFLOW_OT_panel_reverse_workflow_order(Operator):
    bl_idname = "bworkflow.panel_reverse_workflow_order"
    bl_label = "反转面板顺序"
    bl_description = "反转当前工作流面板组顺序"

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        return workflow is not None and len(workflow_family_order_groups(state, workflow)) > 1

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        active_index = clamp_index(workflow.active_panel_index, len(workflow.panels))
        active_panel_id = workflow.panels[active_index].panel_id if workflow.panels else ""
        groups = list(reversed(workflow_family_order_groups(state, workflow)))
        replace_workflow_groups(workflow, groups, active_panel_id=active_panel_id)
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, "当前工作流面板组顺序已反转")
        return {"FINISHED"}


class BWFLOW_OT_panel_move_in_workflow(Operator):
    bl_idname = "bworkflow.panel_move_in_workflow"
    bl_label = "移动工作流面板"
    bl_description = "调整当前工作流内已勾选面板组的顺序"

    direction: StringProperty()
    group_key: StringProperty(default="")

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        return workflow is not None and len(workflow_family_order_groups(state, workflow)) > 1

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        groups = workflow_family_order_groups(state, workflow)
        if len(groups) <= 1:
            return {"CANCELLED"}
        active_key = self.group_key or active_workflow_family_group_key(state, workflow)
        old_pos = next((index for index, group in enumerate(groups) if group["key"] == active_key), -1)
        if old_pos < 0:
            return {"CANCELLED"}
        new_pos = clamp_index(old_pos + (-1 if self.direction == "UP" else 1), len(groups))
        if new_pos == old_pos:
            return {"CANCELLED"}
        group = groups.pop(old_pos)
        groups.insert(new_pos, group)
        replace_workflow_groups(workflow, groups, active_panel_id=group["ids"][0] if group["ids"] else "")
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        return {"FINISHED"}


class BWFLOW_OT_panel_move_child_in_group(Operator):
    bl_idname = "bworkflow.panel_move_child_in_group"
    bl_label = "移动组内子面板"
    bl_description = "调整当前面板大组内部抽屉面板的顺序"

    direction: StringProperty()
    group_key: StringProperty(default="")
    panel_id: StringProperty(default="")

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        return workflow is not None and not workflow.is_default and bool(workflow.panels)

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        groups = workflow_family_order_groups(state, workflow)
        active_group_key = self.group_key or workflow_group_key_for_panel(state, self.panel_id)
        group = next((item for item in groups if item.get("key") == active_group_key), None)
        if (group is None or len(group.get("ids", [])) <= 1) and self.panel_id:
            fallback_key = workflow_group_key_for_panel(state, self.panel_id)
            if fallback_key and fallback_key != active_group_key:
                active_group_key = fallback_key
                group = next((item for item in groups if item.get("key") == active_group_key), None)
        if group is None or len(group.get("ids", [])) <= 1:
            return {"CANCELLED"}

        ids = list(group["ids"])
        space_type = getattr(state, "space_type", "VIEW_3D")
        target_id = self.panel_id
        drawer_id = panel_drawer_root_id(target_id, space_type=space_type) or target_id
        old_pos = ids.index(target_id) if target_id in ids else -1
        if old_pos < 0:
            for group_pos, panel_id in enumerate(ids):
                if panel_drawer_root_id(panel_id, space_type=space_type) == drawer_id:
                    old_pos = group_pos
                    target_id = panel_id
                    break
        if old_pos < 0:
            return {"CANCELLED"}

        new_pos = clamp_index(old_pos + (-1 if self.direction == "UP" else 1), len(ids))
        if new_pos == old_pos:
            return {"CANCELLED"}

        moved_id = ids.pop(old_pos)
        ids.insert(new_pos, moved_id)
        group["ids"] = ids
        replace_workflow_groups(workflow, groups, active_panel_id=moved_id)
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        return {"FINISHED"}


class BWFLOW_OT_panel_sort_children_by_default_order(Operator):
    bl_idname = "bworkflow.panel_sort_children_by_default_order"
    bl_label = "子抽屉默认排序"
    bl_description = "按插件默认扫描顺序重排当前勾选列表内每个大组的子抽屉"

    group_key: StringProperty(default="")

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        if workflow is None or workflow.is_default:
            return False
        return any(len(group.get("ids", [])) > 1 for group in workflow_family_order_groups(state, workflow))

    def execute(self, context):
        state = get_state(context=context)
        workflow = get_active_workflow(state)
        if state is None or workflow is None or workflow.is_default:
            return {"CANCELLED"}

        groups = workflow_family_order_groups(state, workflow)
        if not groups:
            return {"CANCELLED"}

        active_index = clamp_index(workflow.active_panel_index, len(workflow.panels))
        active_panel_id = workflow.panels[active_index].panel_id if workflow.panels else ""
        space_type = getattr(state, "space_type", "VIEW_3D")
        changed_count = 0

        for group in groups:
            if self.group_key and group.get("key") != self.group_key:
                continue
            ids = list(group.get("ids", []))
            if len(ids) <= 1:
                continue
            sorted_ids = [
                panel_id
                for _order_index, _old_index, panel_id in sorted(
                    (
                        (
                            panel_drawer_default_order_index(state, panel_id, space_type=space_type),
                            old_index,
                            panel_id,
                        )
                        for old_index, panel_id in enumerate(ids)
                    )
                )
            ]
            if sorted_ids != ids:
                group["ids"] = sorted_ids
                changed_count += 1

        if not changed_count:
            self.report({"INFO"}, "子抽屉已经是插件默认顺序")
            return {"FINISHED"}

        replace_workflow_groups(workflow, groups, active_panel_id=active_panel_id)
        rebuild_runtime_panels(scene=context.scene)
        schedule_deferred_runtime_refresh(scene=context.scene, intervals=(0.25,))
        save_global_workflow_state(context.scene)
        self.report({"INFO"}, f"已按插件默认顺序整理 {changed_count} 个面板组")
        return {"FINISHED"}


class BWFLOW_OT_debug_dump_panels(Operator):
    bl_idname = "bworkflow.debug_dump_panels"
    bl_label = "输出当前面板清单"
    bl_description = "把当前扫描到的第三方 N 面板清单输出到 Blender 系统控制台"

    def execute(self, context):
        space_type = current_space_type(context=context)
        rebuild_panel_cache(scene=context.scene, space_type=space_type)
        lines = []
        for panel_id, cls in get_panel_cache(space_type).items():
            category = panel_display_category(panel_id, cls, space_type=space_type) or "-"
            parent_id = getattr(cls, "bl_parent_id", "") or "-"
            source_module = getattr(cls, "__module__", "") or "-"
            lines.append(f"{panel_id} | category={category} | parent={parent_id} | module={source_module}")
        print("[Go工作流] 面板清单开始")
        for line in lines:
            print(line)
        print("[Go工作流] 面板清单结束")
        self.report({"INFO"}, f"已输出 {len(lines)} 个面板到系统控制台")
        return {"FINISHED"}


class BWFLOW_OT_preset_export(Operator, ExportHelper):
    bl_idname = "bworkflow.preset_export"
    bl_label = "导出预设"
    bl_description = "将勾选的工作流、面板顺序和脚本模块导出为 .goworkflow 文件"

    filename_ext = PRESET_FILE_EXTENSION
    filter_glob: StringProperty(default=PRESET_FILE_FILTER, options={"HIDDEN"})

    def invoke(self, context, event):
        state = get_state(context=context)
        if state is not None:
            self.filepath = default_preset_export_path(context, state)
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        state = get_state(context=context)
        if normalize_workflow_texts(state):
            save_global_workflow_state(context.scene)
            tag_redraw_all()
        workflows = selected_preset_export_workflows(state)
        if not workflows:
            workflow = get_active_workflow(state)
            workflows = [workflow] if workflow is not None else []
        refresh_script_library_sources_for_workflows(state, workflows)
        payload = build_selected_workflows_preset_payload(context.scene, context=context)
        if payload is None:
            self.report({"ERROR"}, "没有勾选可导出的工作流")
            return {"CANCELLED"}
        write_json_payload_file(self.filepath, payload)

        workflow_count = len(workflows)
        self.report({"INFO"}, f"已导出 {workflow_count} 个工作流")
        return {"FINISHED"}


class BWFLOW_OT_preset_import(Operator, ImportHelper):
    bl_idname = "bworkflow.preset_import"
    bl_label = "导入预设"
    bl_description = "导入 .goworkflow 文件并恢复工作流、面板和脚本模块"

    filename_ext = PRESET_FILE_EXTENSION
    filter_glob: StringProperty(default=f"{PRESET_FILE_FILTER};{LEGACY_PRESET_FILE_FILTER}", options={"HIDDEN"})

    def execute(self, context):
        try:
            payload = load_json_payload_file(self.filepath, max_bytes=MAX_PRESET_FILE_BYTES)
        except Exception as exc:
            self.report({"ERROR"}, f"预设读取失败: {exc}")
            return {"CANCELLED"}

        if not isinstance(payload, dict):
            self.report({"ERROR"}, "预设文件格式无效")
            return {"CANCELLED"}

        ensure_minimum_setup(context.scene)
        preferred_space_type = current_space_type(context=context)
        entries = workflow_preset_entries_from_payload(payload, preferred_space_type=preferred_space_type)
        if entries:
            entries = direct_import_workflow_entries(entries, preferred_space_type=preferred_space_type)
            state = get_state(context=context)
            imported = []
            missing_count = 0
            merge_preset_entries_shared_payloads(state, entries)
            for entry in entries:
                workflow_payload = current_workflow_preset_payload_from_entry(entry)
                workflow = apply_current_workflow_preset_payload(state, workflow_payload, merge_shared=False)
                if workflow is None:
                    continue
                imported.append(workflow.name)
                missing_count += len(workflow_missing_panel_ids(state, workflow))

            if not imported:
                self.report({"ERROR"}, "没有导入任何工作流")
                return {"CANCELLED"}

            space_type = current_space_type(context=context)
            rebuild_panel_cache(scene=context.scene, space_type=space_type)
            rebuild_runtime_panels(scene=context.scene, space_type=space_type, rebuild_cache=False)
            save_global_workflow_state(context.scene)
            tag_redraw_all()
            if missing_count:
                self.report({"WARNING"}, f"已导入 {len(imported)} 个工作流；缺失面板 {missing_count} 个")
            else:
                self.report({"INFO"}, f"已导入 {len(imported)} 个工作流")
            return {"FINISHED"}

        if is_current_workflow_preset_payload(payload):
            state = get_state(context=context)
            workflow = apply_current_workflow_preset_payload(state, payload)
            if workflow is None:
                self.report({"ERROR"}, "当前工作流预设无效")
                return {"CANCELLED"}
            space_type = current_space_type(context=context)
            rebuild_panel_cache(scene=context.scene, space_type=space_type)
            rebuild_runtime_panels(scene=context.scene, space_type=space_type, rebuild_cache=False)
            save_global_workflow_state(context.scene)
            missing_count = len(workflow_missing_panel_ids(state, workflow))
            if missing_count:
                self.report({"WARNING"}, f"已导入工作流: {workflow.name}；缺失面板 {missing_count} 个")
            else:
                self.report({"INFO"}, f"已导入工作流: {workflow.name}")
            return {"FINISHED"}

        space_payloads = payload.get("space_states")
        if isinstance(space_payloads, dict) and space_payloads:
            sanitized_payload = sanitize_full_payload(payload)
            target_space_payloads = sanitized_payload.get("space_states", {}) if sanitized_payload else {}
        else:
            fallback_space = sanitize_space_payload(
                {
                    "active_workflow_index": payload.get("active_workflow_index", 0),
                    "settings": payload.get("settings", {}),
                    "panel_registry": payload.get("panel_registry", []),
                    "script_library": payload.get("script_library", []),
                    "workflows": payload.get("workflows", []),
                }
            )
            target_space_payloads = {"VIEW_3D": fallback_space} if fallback_space is not None else {}

        imported_spaces = []
        failed_spaces = []
        for space_type in iter_supported_space_types():
            state = get_state(scene=context.scene, space_type=space_type)
            if state is None:
                continue

            space_payload = target_space_payloads.get(space_type)
            if not isinstance(space_payload, dict):
                continue

            try:
                apply_space_state_payload(state, space_payload)
                imported_spaces.append(space_type)
            except Exception:
                traceback.print_exc()
                failed_spaces.append(space_type)

        if not imported_spaces:
            if failed_spaces:
                self.report({"ERROR"}, "预设导入失败：工作流数据无法应用，请检查文件是否损坏")
            else:
                self.report({"ERROR"}, "预设中没有可导入到当前版本的工作流空间")
            return {"CANCELLED"}

        ensure_minimum_setup(context.scene)
        refresh_all_panel_registries(context.scene)
        refresh_runtime(scene=context.scene)
        save_global_workflow_state(context.scene)
        missing_count = 0
        missing_workflow_count = 0
        for space_type in imported_spaces:
            state = get_state(scene=context.scene, space_type=space_type)
            if state is None:
                continue
            for workflow in state.workflows:
                if workflow.is_default:
                    continue
                count = len(workflow_missing_panel_ids(state, workflow))
                if count:
                    missing_count += count
                    missing_workflow_count += 1

        if failed_spaces:
            self.report(
                {"WARNING"},
                f"预设已部分导入，成功 {len(imported_spaces)} 个空间，失败 {len(failed_spaces)} 个空间",
            )
        elif missing_count:
            self.report(
                {"WARNING"},
                f"预设已导入，但有 {missing_workflow_count} 个工作流共 {missing_count} 个面板暂时缺失",
            )
        else:
            self.report({"INFO"}, f"预设已导入，覆盖 {len(imported_spaces)} 个编辑器空间")
        return {"FINISHED"}


class BWFLOW_OT_preset_load(Operator, ImportHelper):
    bl_idname = "bworkflow.preset_load"
    bl_label = "载入预设"
    bl_description = "读取 .goworkflow 文件，并列出可多选导入的工作流"

    filename_ext = PRESET_FILE_EXTENSION
    filter_glob: StringProperty(default=f"{PRESET_FILE_FILTER};{LEGACY_PRESET_FILE_FILTER}", options={"HIDDEN"})

    def execute(self, context):
        state = get_state(context=context)
        if state is None:
            self.report({"ERROR"}, "没有可用的 Go工作流 状态")
            return {"CANCELLED"}
        try:
            payload = load_json_payload_file(self.filepath, max_bytes=MAX_PRESET_FILE_BYTES)
        except Exception as exc:
            self.report({"ERROR"}, f"预设读取失败: {exc}")
            return {"CANCELLED"}

        entries = workflow_preset_entries_from_payload(payload, preferred_space_type=current_space_type(context=context))
        count = populate_preset_workflow_list(state, entries)
        state.preset_filepath = self.filepath
        state.preset_status = f"已从预设读取 {count} 个工作流"
        if count <= 0:
            self.report({"WARNING"}, "预设中没有工作流")
            return {"CANCELLED"}
        self.report({"INFO"}, state.preset_status)
        return {"FINISHED"}


class BWFLOW_OT_preset_export_select_all(Operator):
    bl_idname = "bworkflow.preset_export_select_all"
    bl_label = "选择导出工作流"
    bl_description = "全选或清空预设导出的工作流"

    select: BoolProperty(default=True)

    def execute(self, context):
        state = get_state(context=context)
        if state is None:
            return {"CANCELLED"}
        for workflow in state.workflows:
            workflow.preset_export_selected = bool(self.select)
        return {"FINISHED"}


class BWFLOW_OT_preset_select_all(Operator):
    bl_idname = "bworkflow.preset_select_all"
    bl_label = "选择预设工作流"
    bl_description = "全选或清空已载入预设中的工作流"

    select: BoolProperty(default=True)

    def execute(self, context):
        state = get_state(context=context)
        if state is None:
            return {"CANCELLED"}
        for item in state.preset_workflows:
            item.selected = bool(self.select)
        return {"FINISHED"}


class BWFLOW_OT_preset_import_selected(Operator):
    bl_idname = "bworkflow.preset_import_selected"
    bl_label = "导入选中工作流"
    bl_description = "将已勾选的预设工作流导入到当前编辑器空间"

    @classmethod
    def poll(cls, context):
        state = get_state(context=context)
        return state is not None and bool(state.preset_filepath) and len(state.preset_workflows) > 0

    def execute(self, context):
        state = get_state(context=context)
        if state is None:
            return {"CANCELLED"}
        selected_keys = {item.source_key for item in state.preset_workflows if item.selected and item.source_key}
        if not selected_keys:
            self.report({"WARNING"}, "没有勾选要导入的工作流")
            return {"CANCELLED"}
        try:
            payload = load_json_payload_file(state.preset_filepath, max_bytes=MAX_PRESET_FILE_BYTES)
        except Exception as exc:
            self.report({"ERROR"}, f"预设读取失败: {exc}")
            return {"CANCELLED"}

        entries = workflow_preset_entries_from_payload(payload, preferred_space_type=current_space_type(context=context))
        selected_entries = [entry for entry in entries if entry.get("key", "") in selected_keys]
        if not selected_entries:
            self.report({"WARNING"}, "预设中没有找到已勾选的工作流")
            return {"CANCELLED"}

        ensure_minimum_setup(context.scene)
        imported = []
        missing_count = 0
        merge_preset_entries_shared_payloads(state, selected_entries)
        for entry in selected_entries:
            workflow_payload = current_workflow_preset_payload_from_entry(entry)
            workflow = apply_current_workflow_preset_payload(state, workflow_payload, merge_shared=False)
            if workflow is None:
                continue
            imported.append(workflow.name)
            missing_count += len(workflow_missing_panel_ids(state, workflow))

        if not imported:
            self.report({"ERROR"}, "没有导入任何工作流")
            return {"CANCELLED"}

        space_type = current_space_type(context=context)
        rebuild_panel_cache(scene=context.scene, space_type=space_type)
        rebuild_runtime_panels(scene=context.scene, space_type=space_type, rebuild_cache=False)
        save_global_workflow_state(context.scene)
        tag_redraw_all()

        state.preset_status = f"已导入 {len(imported)} 个工作流"
        if missing_count:
            self.report({"WARNING"}, f"已导入 {len(imported)} 个工作流；缺失面板 {missing_count} 个")
        else:
            self.report({"INFO"}, state.preset_status)
        return {"FINISHED"}


def refresh_all_panel_registries(scene):
    summary = {
        "removed_duplicates": 0,
        "removed_stale": 0,
        "removed_empty": 0,
        "added_runtime": 0,
    }

    for space_type in iter_supported_space_types():
        rebuild_panel_cache(scene=scene, space_type=space_type)
        state = get_state(scene=scene, space_type=space_type)
        if state is None:
            continue

        state_summary = validate_panel_registry_against_runtime(
            state,
            space_type=space_type,
            prune_missing_unreferenced=True,
        )
        summary["removed_duplicates"] += state_summary["removed_duplicates"]
        summary["removed_stale"] += state_summary["removed_stale"]
        summary["removed_empty"] += state_summary["removed_empty"]
        summary["added_runtime"] += state_summary["added_runtime"]

        dedupe_workflows(state)
        ensure_one_default_workflow(state)
        ensure_go_workflow_panel_entry(state)
        purge_builtin_default_panels(state)

    return summary


def draw_workflow_switcher(layout, state):
    for start in range(0, len(state.workflows), WORKFLOW_SWITCHER_COLUMNS):
        row = layout.row(align=True)
        chunk = state.workflows[start : start + WORKFLOW_SWITCHER_COLUMNS]
        for offset, workflow in enumerate(chunk):
            index = start + offset
            icon = "RADIOBUT_ON" if index == state.active_workflow_index else "RADIOBUT_OFF"
            op = row.operator(
                "bworkflow.workflow_activate",
                text=workflow.name,
                icon=icon,
                depress=index == state.active_workflow_index,
            )
            op.index = index
        for _unused in range(len(chunk), WORKFLOW_SWITCHER_COLUMNS):
            row.label(text="")


def draw_workflow_runtime(layout, state):
    workflow = get_active_workflow(state)
    if workflow is None:
        layout.label(text="当前没有可用 Go工作流", icon="INFO")
        return

    preview_lines = state.settings.runtime_preview_lines

    header = layout.row(align=True)
    header.label(text="Go工作流", icon="FILE_FOLDER")
    header.prop(
        state.settings,
        "show_settings",
        text="",
        icon="PREFERENCES",
        toggle=True,
    )

    switch_col = layout.column(align=True)
    draw_workflow_switcher(switch_col, state)

    info = layout.column(align=True)
    info.label(text=workflow.name, icon="OUTLINER_COLLECTION")
    summary = info.row(align=True)
    summary.label(text="默认模式" if workflow.is_default else "筛选模式", icon="HOME" if workflow.is_default else "BOOKMARKS")
    summary.label(text=f"模块 {len(workflow.modules)}", icon="CONSOLE")
    if workflow.description:
        draw_folded_text_block(
            info,
            state.settings,
            "show_workflow_description",
            "工作流说明",
            workflow.description,
            icon="INFO",
            expanded_limit=preview_lines,
            width=64,
        )
    if workflow.is_default:
        repair_box = layout.box()
        repair_box.label(text="默认工作流修复", icon="FILE_REFRESH")
        repair_box.label(text="如果 N 面板显示异常，可用这里一键恢复 Blender 初始 N 面板。", icon="INFO")
        repair_box.operator("bworkflow.restore_default_n_panels", text="重置 Blender 初始 N 面板", icon="FILE_REFRESH")

    if state.settings.show_missing_summary:
        missing_records = workflow_missing_records(state, workflow)
        if missing_records:
            warning = layout.box()
            warning.alert = True
            warning.label(text=f"当前 Go工作流有 {len(missing_records)} 个缺失面板", icon="ERROR")
            for record in missing_records[: min(len(missing_records), preview_lines)]:
                label = record.title if record and record.title else (record.panel_id if record else "未知面板")
                warning.label(text=label)

    modules_box = layout.box()
    modules_box.label(text="自定义脚本模块", icon="CONSOLE")
    if not workflow.modules:
        modules_box.label(text="当前 Go工作流还没有脚本模块，请到“独立设置 > 脚本模板”中添加。", icon="INFO")
        return

    for index, module in enumerate(workflow.modules):
        card = modules_box.box()
        card.enabled = module.enabled
        header = card.row(align=True)
        needs_panel = module_needs_runtime_panel(module)
        if needs_panel:
            header.prop(
                module,
                "runtime_panel_expanded",
                text="",
                icon="TRIA_DOWN" if module.runtime_panel_expanded else "TRIA_RIGHT",
                toggle=True,
            )
        header.label(text=module.name or f"模块 {index + 1}", icon="CONSOLE")
        run_op = header.operator("bworkflow.module_run", text="运行")
        run_op.module_index = index

        if needs_panel and not module.runtime_panel_expanded:
            continue

        if module.description:
            draw_folded_text_block(
                card,
                state.settings,
                "show_runtime_module_descriptions",
                "模块说明",
                module.description,
                icon="INFO",
                expanded_limit=2,
                width=64,
            )

        if not module.enabled:
            card.label(text="该模块已禁用，请到脚本模板页启用。", icon="PAUSE")
            continue

        if needs_panel:
            panel_box = card.box()
            draw_module_runtime_panel(panel_box, bpy.context, workflow, module)


def draw_settings_embedded(layout, state):
    settings_box = layout.column(align=True)
    header = settings_box.row(align=True)
    header.label(text="Go工作流设置", icon="PREFERENCES")
    header.prop(state.settings, "show_settings", text="", icon="X", toggle=True)

    tabs = settings_box.row(align=True)
    tabs.prop(state.settings, "ui_tab", expand=True)

    body = settings_box.column(align=True)
    tab = state.settings.ui_tab
    if tab == "WORKFLOWS":
        draw_workflow_settings(body, state)
    elif tab == "PANELS":
        draw_panel_library_editor(body, state)
    elif tab == "MODULES":
        draw_module_template_editor(body, state)
    elif tab == "SCRIPTS":
        draw_script_library_editor(body, state)
    elif tab == "PRESETS":
        draw_preset_editor(body, state)
    else:
        draw_global_settings(body, state)


def draw_workflow_settings(layout, state):
    list_box = layout.box()
    list_box.label(text="Go工作流列表", icon="FILE_FOLDER")
    row = list_box.row()
    row.template_list(
        "BWFLOW_UL_workflows",
        "",
        state,
        "workflows",
        state,
        "active_workflow_index",
        rows=7,
    )
    ops = row.column(align=True)
    ops.operator("bworkflow.workflow_add", text="", icon="ADD")
    ops.operator("bworkflow.workflow_duplicate", text="", icon="DUPLICATE")
    ops.operator("bworkflow.workflow_remove", text="", icon="REMOVE")
    ops.separator()
    move_up = ops.operator("bworkflow.workflow_move", text="", icon="TRIA_UP")
    move_up.direction = "UP"
    move_down = ops.operator("bworkflow.workflow_move", text="", icon="TRIA_DOWN")
    move_down.direction = "DOWN"

    workflow = get_active_workflow(state)
    if workflow is None:
        layout.label(text="请先创建 Go工作流", icon="INFO")
        return

    detail = layout.box()
    detail.label(text="当前工作流设置", icon="SETTINGS")
    detail.prop(workflow, "name", text="名称")
    detail.prop(workflow, "description", text="说明")
    detail.prop(workflow, "tag_filter", text="按标签自动加入")
    detail.label(text="写法: 用英文逗号分隔，例如 model, uv, render", icon="INFO")
    detail.label(text="含义: 面板库里带这些标签的面板，会自动出现在当前工作流里。", icon="BOOKMARKS")
    row = detail.row(align=True)
    row.operator("bworkflow.workflow_set_default", text="设为默认", icon="HOME")
    row.operator("bworkflow.workflow_clear_description", text="清空说明", icon="TRASH")
    if workflow.is_default:
        detail.label(text="当前 Go工作流就是默认 Go工作流。", icon="CHECKMARK")
        return
    elif workflow_missing_panel_ids(state, workflow):
        detail.label(text=f"当前 Go工作流存在 {len(workflow_missing_panel_ids(state, workflow))} 个缺失面板。", icon="ERROR")
        return


def draw_panel_library_editor(layout, state):
    workflow = get_active_workflow(state)
    if workflow is None:
        layout.label(text="请先创建 Go工作流", icon="INFO")
        return

    groups = build_panel_library_groups(state, workflow)

    header = layout.box()
    header.label(text=f"当前 Go工作流配置目标: {workflow.name}", icon="OUTLINER_COLLECTION")
    if workflow.is_default:
        header.label(text="默认 Go工作流不需要勾选，默认显示全部面板。", icon="HOME")
    else:
        header.label(text="切换到这个 Go工作流 后，只保留下面勾选的面板组，其余全部隐藏。", icon="CHECKMARK")

    split = layout.split(factor=0.52)
    left = split.column(align=True)
    right = split.column(align=True)

    selected_ids = {item.panel_id for item in workflow.panels}

    library_box = left.box()
    library_box.label(text="面板组", icon="PLUGIN")
    library_box.label(text="按 N 面板抽屉标题栏统计；也就是右侧带 8 点拖拽手柄的折叠面板。", icon="INFO")
    library_box.label(text=f"共有 {len(groups)} 个面板组", icon="OUTLINER_COLLECTION")
    tool_row = library_box.row(align=True)
    tool_row.operator("bworkflow.refresh_registry", text="刷新面板库")

    if groups:
        for group in groups:
            group_box = library_box.box()
            header_row = group_box.row(align=True)
            header_row.operator_context = "INVOKE_DEFAULT"
            selected_count = group.get("selected_count", 0)
            if selected_count:
                status_icon = "CHECKBOX_HLT"
            else:
                status_icon = "CHECKBOX_DEHLT"
            expand_icon = "TRIA_DOWN" if is_group_expanded(state, group["key"]) else "TRIA_RIGHT"
            expand_op = header_row.operator(
                "bworkflow.group_expand_toggle",
                text="",
                icon=expand_icon,
                emboss=False,
            )
            expand_op.group_key = group["key"]
            expand_op.target = "LIBRARY"
            group_op = header_row.operator(
                "bworkflow.panel_group_click",
                text=group["title"],
                icon="OUTLINER_COLLECTION",
                emboss=False,
            )
            group_op.group_key = group["key"]
            group_op.panel_index = group.get("first_panel_index", -1)
            group_op.target = "LIBRARY"
            header_row.label(text="", icon=status_icon)
            header_row.label(text=panel_count_label(group.get("panel_count", 0)), icon="MENU_PANEL")
            if is_group_expanded(state, group["key"]):
                for entry in panel_library_group_entries(state, workflow, group):
                    record = entry["record"]
                    row = group_box.row(align=True)
                    row.operator_context = "INVOKE_DEFAULT"
                    row.alert = not entry["discovered"]
                    title = entry.get("component_title") or entry.get("title") or clean_panel_title(record.title if record else "", entry.get("panel_id", ""))
                    op = row.operator(
                        "bworkflow.panel_library_click",
                        text=("    " * min(entry["depth"] + 1, 4)) + title,
                        icon="ERROR" if not entry["discovered"] else ("CHECKBOX_HLT" if entry["selected"] else "CHECKBOX_DEHLT"),
                        emboss=False,
                    )
                    op.index = entry["index"]

    if not state.panel_registry:
        left.label(text="面板库为空，请先刷新。", icon="INFO")
        return

    record = state.panel_registry[clamp_index(state.panel_registry_index, len(state.panel_registry))]
    record_discovered = panel_drawer_discovered(state, panel_drawer_root_id(record.panel_id, space_type=getattr(state, "space_type", "VIEW_3D")))
    component_ids = set(panel_drawer_workflow_ids(state, record.panel_id))
    is_checked = bool(component_ids.intersection(selected_ids)) or record.panel_id in selected_ids

    detail = left.box()
    detail.label(text="面板详情", icon="MENU_PANEL")
    detail.label(text=f"名称: {clean_panel_title(record.title, record.panel_id)}")
    status_row = detail.row(align=True)
    status_row.label(
        text="已加入" if is_checked else ("已发现" if record_discovered else "缺失"),
        icon="CHECKMARK" if is_checked else ("PLUGIN" if record_discovered else "ERROR"),
    )
    detail.label(text=f"所属大组: {panel_family_title(state, record)}")
    detail.label(text=f"所属抽屉面板: {panel_drawer_title(state, panel_drawer_root_id(record.panel_id, space_type=getattr(state, 'space_type', 'VIEW_3D')))}")
    detail.label(text=f"模块: {record.source_module or '-'}")
    detail.prop(record, "tags", text="面板标签")
    detail.label(text="标签用于自动匹配到工作流。")
    if not workflow.is_default:
        action_row = detail.row(align=True)
        toggle_plugin_op = action_row.operator("bworkflow.panel_toggle_for_workflow", text="抽屉面板加入/移出")
        toggle_plugin_op.panel_index = clamp_index(state.panel_registry_index, len(state.panel_registry))

    selected = right.box()
    selected.label(text=f"{workflow.name} 当前勾选")
    if workflow.is_default:
        selected.label(text="默认工作流不显示勾选限制。", icon="INFO")
    elif workflow.panels:
        selected.label(text="当前列表按组管理；选中组后，上移/下移会移动整组。")
        active_title = ""
        active_panel_id = ""
        if workflow.panels:
            active_index = clamp_index(workflow.active_panel_index, len(workflow.panels))
            active_panel_id = workflow.panels[active_index].panel_id
        if active_panel_id and active_panel_id != "BWFLOW_PT_workflow":
            active_record = find_registry_record(state, active_panel_id)
            active_title = clean_panel_title(active_record.title if active_record else "", active_panel_id)
        active_hint = selected.box()
        active_hint.label(
            text=f"当前高亮: {active_title}" if active_title else "当前没有高亮面板",
            icon="LAYER_ACTIVE" if active_title else "INFO",
        )
        selected_groups = build_selected_panel_groups(state, workflow)
        for group in selected_groups:
            group_box = selected.box()
            header_row = group_box.row(align=True)
            header_row.operator_context = "INVOKE_DEFAULT"
            expand_icon = "TRIA_DOWN" if is_group_expanded(state, group["key"], selected=True) else "TRIA_RIGHT"
            group_toggle = header_row.operator(
                "bworkflow.group_expand_toggle",
                text="",
                icon=expand_icon,
                emboss=False,
            )
            group_toggle.group_key = group["key"]
            group_toggle.target = "SELECTED"
            group_select = header_row.operator(
                "bworkflow.select_workflow_panel",
                text=group["title"],
                icon="LAYER_ACTIVE" if group.get("is_active") else "OUTLINER_COLLECTION",
                emboss=False,
            )
            group_select.panel_id = group.get("panel_id", "") or (group["entries"][0]["panel_id"] if group["entries"] else "")
            header_row.label(text=panel_count_label(group.get("panel_count", 0)), icon="DOT")
            group_up = header_row.operator(
                "bworkflow.panel_move_in_workflow",
                text="",
                icon="TRIA_UP",
                emboss=False,
            )
            group_up.direction = "UP"
            group_up.group_key = group["key"]
            group_down = header_row.operator(
                "bworkflow.panel_move_in_workflow",
                text="",
                icon="TRIA_DOWN",
                emboss=False,
            )
            group_down.direction = "DOWN"
            group_down.group_key = group["key"]
            if is_group_expanded(state, group["key"], selected=True):
                for entry in group["entries"]:
                    row_host = group_box.box() if entry.get("is_active", False) else group_box
                    row = row_host.row(align=True)
                    row.operator_context = "INVOKE_DEFAULT"
                    row.alert = not entry["discovered"]
                    select_op = row.operator(
                        "bworkflow.select_workflow_panel",
                        text=("    " * min(entry["depth"] + 1, 4)) + entry["title"],
                        icon="LAYER_ACTIVE" if entry.get("is_active", False) else ("PLUGIN" if entry["discovered"] else "ERROR"),
                        emboss=False,
                    )
                    select_op.panel_id = entry["panel_id"]
                    child_up = row.operator(
                        "bworkflow.panel_move_child_in_group",
                        text="",
                        icon="TRIA_UP",
                        emboss=False,
                    )
                    child_up.direction = "UP"
                    child_up.group_key = group["key"]
                    child_up.panel_id = entry["panel_id"]
                    child_down = row.operator(
                        "bworkflow.panel_move_child_in_group",
                        text="",
                        icon="TRIA_DOWN",
                        emboss=False,
                    )
                    child_down.direction = "DOWN"
                    child_down.group_key = group["key"]
                    child_down.panel_id = entry["panel_id"]
        order_box = selected.box()
        order_box.label(text="抽屉顺序操作", icon="SORTALPHA")
        move_row = order_box.row(align=True)
        up = move_row.operator("bworkflow.panel_move_in_workflow", text="上移当前组")
        up.direction = "UP"
        down = move_row.operator("bworkflow.panel_move_in_workflow", text="下移当前组")
        down.direction = "DOWN"
        row = order_box.row(align=True)
        row.operator("bworkflow.panel_sort_children_by_default_order", text="子抽屉默认排序", icon="SORTALPHA")
        row = order_box.row(align=True)
        row.operator("bworkflow.panel_clear_workflow", text="清空当前勾选")
    else:
        selected.label(text="当前 Go工作流还没有勾选任何面板。", icon="INFO")
        row = selected.row(align=True)
        row.operator("bworkflow.panel_add_all_to_workflow", text="加入全部抽屉面板")
        row.operator("bworkflow.panel_add_tagged_to_workflow", text="加入标签命中")

    note = right.box()
    draw_folded_text_block(
        note,
        state.settings,
        "show_help_text_blocks",
        "说明",
        "手动勾选和标签自动命中的面板，都会进入当前 Go工作流 的显示结果。\n"
        "显示顺序以当前 Go工作流 勾选列表为准，自动加入的面板排在后面。\n"
        "缺失面板会保留在预设里，方便跨机器恢复。",
        icon="INFO",
        expanded_limit=3,
        width=56,
    )


def draw_module_template_editor(layout, state):
    workflow = get_active_workflow(state)
    if workflow is None:
        layout.label(text="请先创建 Go工作流", icon="INFO")
        return

    top = layout.column(align=True)
    top.label(text=f"当前 Go工作流: {workflow.name}", icon="OUTLINER_COLLECTION")
    top.label(text="脚本模块只属于当前工作流；代码可直接保存在插件内，也可以写入 .py 文件。", icon="INFO")

    row = top.row()
    row.template_list(
        "BWFLOW_UL_workflow_modules",
        "",
        workflow,
        "modules",
        workflow,
        "active_module_index",
        rows=7,
    )
    ops = row.column(align=True)
    ops.operator("bworkflow.module_add", text="", icon="ADD")
    ops.operator("bworkflow.module_remove", text="", icon="REMOVE")
    ops.separator()
    move_up = ops.operator("bworkflow.module_move", text="", icon="TRIA_UP")
    move_up.direction = "UP"
    move_down = ops.operator("bworkflow.module_move", text="", icon="TRIA_DOWN")
    move_down.direction = "DOWN"

    if not workflow.modules:
        layout.label(text="当前 Go工作流还没有模块。", icon="INFO")
        return

    module = workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))]
    detail = layout.box()
    detail.label(text="当前模块设置", icon="CONSOLE")
    detail.prop(module, "name", text="模块名称")
    detail.operator("bworkflow.module_assign_default_path", text="按模块名称生成 .py 路径", icon="FILE_TEXT")
    detail.prop(module, "script_path", text="脚本路径")
    detail.prop(module, "enabled", text="启用")
    detail.prop(module, "use_custom_panel", text="需要自定义面板")
    detail.prop(module, "description", text="模块说明")

    actions = layout.box()
    actions.label(text="模块操作", icon="TOOL_SETTINGS")
    row = actions.row(align=True)
    row.operator("bworkflow.module_fill_ai_doc", text="生成 AI 文档", icon="TEXT")
    row.operator("bworkflow.module_copy_ai_doc", text="复制 AI 文档", icon="COPYDOWN")
    row = actions.row(align=True)
    row.operator("bworkflow.module_edit_script_source", text="打开代码编辑", icon="TEXT")
    row.operator("bworkflow.module_paste_script_source", text="从剪贴板载入", icon="PASTEDOWN")
    row = actions.row(align=True)
    row.operator("bworkflow.module_write_template", text="写入 .py 文件", icon="FILE_TICK")
    row = actions.row(align=True)
    row.operator("bworkflow.module_run", text="运行测试", icon="PLAY").module_index = workflow.active_module_index
    actions.operator("bworkflow.script_library_save_current_module", text="保存到脚本库", icon="ASSET_MANAGER")

    if module.script_source.strip():
        actions.label(text=f"当前已保存 {len(module.script_source)} 个字符的代码内容", icon="TEXT")
    else:
        actions.label(text="当前还没有代码内容；可以打开编辑器编写，或从剪贴板载入。", icon="INFO")

    if module_needs_runtime_panel(module):
        actions.label(text="当前模块会在 Go工作流 主面板里显示自定义面板区域。", icon="MENU_PANEL")
    else:
        actions.label(text="当前模块不会在 Go工作流 主面板里额外占用面板位置。", icon="INFO")

    protocol = layout.box()
    protocol.label(text="模块脚本固定入口", icon="INFO")
    protocol.label(text="def run(context, scene, workflow, module):")
    protocol.label(text="    return {'FINISHED'}")
    protocol.label(text="可选: def draw_panel(layout, context, scene, workflow, module, panel_api, module_state):")
    protocol.label(text="panel_api 提供 UI、字段、数据块选择、状态日志与上下文辅助。")
    protocol.label(text="module_state 用于保存当前模块自己的短状态和调试记录。")

    preview = layout.box()
    if module.ai_doc.strip():
        draw_folded_text_block(
            preview,
            state.settings,
            "show_module_ai_doc_preview",
            "AI 辅助说明预览",
            module.ai_doc,
            icon="TEXT",
            expanded_limit=8,
            width=64,
        )
    else:
        preview.label(text="当前还没有生成 AI 文档。点击“生成 AI 文档”后再预览。", icon="INFO")


def draw_preset_editor(layout, state):
    col = layout.column(align=True)

    special_box = col.box()
    special_box.label(text="特殊预设", icon="SHAPEKEY_DATA")
    special_box.operator(
        "bworkflow.workflow_add_special_preset",
        text="创建arkit形态键工作流参考",
        icon="SHAPEKEY_DATA",
    ).preset_type = SPECIAL_PRESET_ARKIT_52

    export_box = col.box()
    export_box.label(text="导出勾选的工作流", icon="EXPORT")
    if state.workflows:
        for index, workflow in enumerate(state.workflows):
            row = export_box.row(align=True)
            row.prop(workflow, "preset_export_selected", text="")
            row.label(text=workflow.name or f"Workflow {index + 1}", icon="HOME" if workflow.is_default else "FILE_FOLDER")
            row.label(text=f"{len(workflow.panels)} 面板")
            row.label(text=f"{len(workflow.modules)} 模块")
        row = export_box.row(align=True)
        row.operator("bworkflow.preset_export_select_all", text="全选", icon="CHECKBOX_HLT").select = True
        row.operator("bworkflow.preset_export_select_all", text="清空", icon="CHECKBOX_DEHLT").select = False
        export_box.operator("bworkflow.preset_export", text="导出勾选工作流", icon="EXPORT")
    else:
        export_box.label(text="当前没有可导出的工作流。", icon="INFO")

    import_box = col.box()
    import_box.label(text="从预设导入工作流", icon="IMPORT")
    import_box.operator("bworkflow.preset_load", text="选择 .goworkflow 文件", icon="FILE_FOLDER")
    if state.preset_filepath:
        import_box.label(text=os.path.basename(bpy.path.abspath(state.preset_filepath)), icon="FILE")
    if state.preset_status:
        import_box.label(text=state.preset_status, icon="INFO")

    if state.preset_workflows:
        row = import_box.row()
        row.template_list(
            "BWFLOW_UL_preset_workflows",
            "",
            state,
            "preset_workflows",
            state,
            "preset_workflow_index",
            rows=6,
        )
        ops = row.column(align=True)
        ops.operator("bworkflow.preset_select_all", text="", icon="CHECKBOX_HLT").select = True
        ops.operator("bworkflow.preset_select_all", text="", icon="CHECKBOX_DEHLT").select = False
        import_box.operator("bworkflow.preset_import_selected", text="导入勾选工作流", icon="IMPORT")
    else:
        import_box.label(text="请选择 .goworkflow 文件以预览其中的工作流。", icon="INFO")

    info = col.box()
    draw_folded_text_block(
        info,
        state.settings,
        "show_help_text_blocks",
        "预设说明",
        "导出会把当前编辑器空间中勾选的工作流写入一个 .goworkflow 文件。\n"
        "导入会保留现有工作流，并把勾选项目合并到当前编辑器空间。\n"
        "缺失面板会保留为警告，方便共享预设在不同环境中继续使用。",
        icon="INFO",
        expanded_limit=3,
        width=56,
    )


def draw_script_library_editor(layout, state):
    workflow = get_active_workflow(state)
    box = layout.column(align=True)
    header = box.row(align=True)
    header.label(text="脚本库", icon="FILE_SCRIPT")
    docs = header.operator("wm.url_open", text="腾讯文档", icon="URL")
    docs.url = SCRIPT_LIBRARY_DOC_URL
    box.label(text="把常用脚本先存起来，下次切到别的工作流也能直接复用。")
    if workflow is not None and workflow.modules:
        box.label(text=f"当前可保存来源: {workflow.name} / {workflow.modules[clamp_index(workflow.active_module_index, len(workflow.modules))].name}", icon="INFO")

    split = layout.split(factor=0.50)
    left = split.column(align=True)
    right = split.column(align=True)

    list_box = left.box()
    list_box.label(text="脚本目录", icon="PRESET")
    row = list_box.row()
    row.template_list(
        "BWFLOW_UL_script_library",
        "",
        state,
        "script_library",
        state,
        "script_library_index",
        rows=8,
    )
    ops = row.column(align=True)
    ops.operator("bworkflow.script_library_save_current_module", text="", icon="ADD")
    ops.operator("bworkflow.script_library_remove", text="", icon="REMOVE")
    ops.operator("bworkflow.script_library_refresh", text="", icon="FILE_REFRESH")

    if not state.script_library:
        left.label(text="当前没有脚本库条目，先到“脚本模板”页保存一个。", icon="INFO")
        return

    item = state.script_library[clamp_index(state.script_library_index, len(state.script_library))]
    detail = left.box()
    detail.label(text="脚本详情", icon="TEXT")
    detail.prop(item, "name", text="名称")
    detail.prop(item, "description", text="说明")
    detail.prop(item, "tags", text="标签")
    detail.prop(item, "use_custom_panel", text="载入后显示自定义面板")
    detail.prop(item, "script_path", text="路径")
    detail.prop(item, "text_block_name", text="文本块")
    draw_folded_text_block(
        detail,
        state.settings,
        "show_script_library_source_preview",
        "脚本内容预览",
        item.script_source or "",
        icon="FILE_TEXT",
        expanded_limit=6,
        width=48,
    )
    if item.ai_doc.strip():
        draw_folded_text_block(
            detail,
            state.settings,
            "show_script_library_ai_doc_preview",
            "AI 文档预览",
            item.ai_doc,
            icon="INFO",
            expanded_limit=6,
            width=48,
        )
    action = detail.row(align=True)
    action.operator("bworkflow.script_library_apply_to_module", text="载入到当前模块", icon="IMPORT")
    action.operator("bworkflow.script_library_save_current_module", text="覆盖当前条目", icon="FILE_TICK").overwrite_existing = True
    action.operator("bworkflow.script_library_open_storage_folder", text="打开脚本文件夹", icon="FILE_FOLDER")

    workflow_box = right.box()
    workflow_box.label(text="工作流启用状态", icon="OUTLINER_COLLECTION")
    workflow_box.label(text="按钮会把当前脚本载入对应工作流，或切换该工作流里的启用状态。", icon="INFO")
    if not state.workflows:
        workflow_box.label(text="当前没有工作流。", icon="INFO")
    else:
        workflow_matches = script_library_workflow_match_index(state, item)
        for workflow_index, target_workflow in enumerate(state.workflows):
            match_info = workflow_matches[workflow_index] if workflow_index < len(workflow_matches) else {}
            is_installed = bool(match_info.get("installed", False))
            is_enabled = bool(match_info.get("enabled", False))
            row = workflow_box.row(align=True)
            row.label(text=target_workflow.name, icon="HOME" if target_workflow.is_default else "FILE_FOLDER")
            button_text = "已启用" if is_enabled else "已关闭"
            icon = "CHECKMARK" if is_enabled else "ADD"
            op = row.operator(
                "bworkflow.script_library_toggle_workflow",
                text=button_text,
                icon=icon,
                depress=is_enabled,
            )
            op.workflow_index = workflow_index
            if is_installed and not is_enabled:
                row.label(text="已载入", icon="DOT")


def draw_global_settings(layout, state):
    box = layout.column(align=True)
    box.label(text="全局设置", icon="PREFERENCES")
    box.prop(state.settings, "auto_sync_registry")
    box.prop(state.settings, "show_missing_summary")
    box.prop(state.settings, "runtime_preview_lines")
    danger = box.box()
    danger.alert = True
    danger.label(text="危险操作", icon="ERROR")
    danger.operator("bworkflow.restore_default_n_panels", text="恢复默认 N 面板", icon="FILE_REFRESH")
    disable_op = danger.operator("bworkflow.restore_default_n_panels", text="恢复默认 N 面板并禁用插件", icon="CANCEL")
    disable_op.disable_addon = True
    uninstall_op = danger.operator("bworkflow.restore_default_n_panels", text="恢复默认并尝试卸载插件", icon="TRASH")
    uninstall_op.uninstall_addon = True
    danger.operator("bworkflow.reset_all_settings", text="清除所有设置并恢复默认", icon="TRASH")


class BWFLOW_AddonPreferences(AddonPreferences):
    bl_idname = addon_module_name()

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Go工作流 / Go Workflow 维护", icon="PREFERENCES")
        box.label(text="如果 N 面板没有恢复，先执行恢复按钮，再用 Blender 插件列表卸载。", icon="INFO")
        box.operator("bworkflow.restore_default_n_panels", text="恢复默认 N 面板", icon="FILE_REFRESH")
        danger = box.box()
        danger.alert = True
        op = danger.operator("bworkflow.restore_default_n_panels", text="恢复默认 N 面板并禁用插件", icon="CANCEL")
        op.disable_addon = True
        uninstall_op = danger.operator("bworkflow.restore_default_n_panels", text="恢复默认并尝试卸载插件", icon="TRASH")
        uninstall_op.uninstall_addon = True



class BWFLOW_PT_workflow(Panel):
    bl_idname = "BWFLOW_PT_workflow"
    bl_label = "Go工作流"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = WORKFLOW_CATEGORY
    bl_order = 1

    def draw(self, context):
        state = get_state(context=context)
        layout = self.layout

        if state is None:
            layout.label(text="当前无法读取插件状态。", icon="ERROR")
            return

        if not state.workflows:
            box = layout.box()
            box.label(text="当前没有 Go工作流", icon="INFO")
            box.operator("bworkflow.initialize_defaults", icon="ADD")
            return

        draw_workflow_runtime(layout, state)
        if state.settings.show_settings:
            draw_settings_embedded(layout, state)


class BWFLOW_PT_workflow_image_editor(Panel):
    bl_idname = "BWFLOW_PT_workflow_image_editor"
    bl_label = "Go工作流"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = WORKFLOW_CATEGORY
    bl_order = 1

    def draw(self, context):
        state = get_state(context=context, space_type="IMAGE_EDITOR")
        layout = self.layout

        if state is None:
            layout.label(text="当前无法读取插件状态。", icon="ERROR")
            return

        if not state.workflows:
            box = layout.box()
            box.label(text="当前没有 Go工作流", icon="INFO")
            box.operator("bworkflow.initialize_defaults", icon="ADD")
            return

        draw_workflow_runtime(layout, state)
        if state.settings.show_settings:
            draw_settings_embedded(layout, state)


class BWFLOW_PT_workflow_node_editor(Panel):
    bl_idname = "BWFLOW_PT_workflow_node_editor"
    bl_label = "Go工作流"
    bl_space_type = "NODE_EDITOR"
    bl_region_type = "UI"
    bl_category = WORKFLOW_CATEGORY
    bl_order = 1

    def draw(self, context):
        state = get_state(context=context, space_type="NODE_EDITOR")
        layout = self.layout

        if state is None:
            layout.label(text="当前无法读取插件状态。", icon="ERROR")
            return

        if not state.workflows:
            box = layout.box()
            box.label(text="当前没有 Go工作流", icon="INFO")
            box.operator("bworkflow.initialize_defaults", icon="ADD")
            return

        draw_workflow_runtime(layout, state)
        if state.settings.show_settings:
            draw_settings_embedded(layout, state)


@persistent
def bworkflow_load_post(_dummy):
    def _initialize_after_load():
        global IS_INITIALIZING_ADDON
        IS_INITIALIZING_ADDON = True
        scenes = iter_available_scenes()
        try:
            for scene in scenes:
                ensure_minimum_setup(scene, restore_global=True, save_state=False)
            scene = safe_context_scene() or (scenes[0] if scenes else None)
            rebuild_panel_cache(scene=scene)
            refresh_runtime_overrides(scene=scene, include_script_panels=True)
            schedule_deferred_runtime_refresh(scene=scene, intervals=(0.25,))
        except Exception:
            traceback.print_exc()
        finally:
            IS_INITIALIZING_ADDON = False
        return None

    try:
        _register_one_shot_timer(_initialize_after_load, first_interval=0.1)
    except Exception:
        traceback.print_exc()


CLASSES = [
    BWFLOW_PG_PanelRecord,
    BWFLOW_PG_ScriptLibraryItem,
    BWFLOW_PG_WorkflowPanel,
    BWFLOW_PG_WorkflowModule,
    BWFLOW_PG_PresetWorkflowItem,
    BWFLOW_PG_Workflow,
    BWFLOW_PG_Settings,
    BWFLOW_PG_State,
    BWFLOW_UL_workflows,
    BWFLOW_UL_workflow_panels,
    BWFLOW_UL_panel_library,
    BWFLOW_UL_workflow_modules,
    BWFLOW_UL_script_library,
    BWFLOW_UL_preset_workflows,
    BWFLOW_AddonPreferences,
    BWFLOW_OT_refresh_registry,
    BWFLOW_OT_initialize_defaults,
    BWFLOW_OT_reset_all_settings,
    BWFLOW_OT_restore_default_n_panels,
    BWFLOW_OT_workflow_activate,
    BWFLOW_OT_workflow_add,
    BWFLOW_OT_workflow_add_special_preset,
    BWFLOW_OT_workflow_duplicate,
    BWFLOW_OT_workflow_remove,
    BWFLOW_OT_workflow_move,
    BWFLOW_OT_workflow_set_default,
    BWFLOW_OT_workflow_clear_description,
    BWFLOW_OT_module_add,
    BWFLOW_OT_module_assign_default_path,
    BWFLOW_OT_module_remove,
    BWFLOW_OT_module_move,
    BWFLOW_OT_module_fill_ai_doc,
    BWFLOW_OT_module_edit_script_source,
    BWFLOW_OT_module_open_script_path,
    BWFLOW_OT_module_import_text_file,
    BWFLOW_OT_module_export_text_file,
    BWFLOW_OT_module_edit_description,
    BWFLOW_OT_module_paste_script_source,
    BWFLOW_OT_module_copy_ai_doc,
    BWFLOW_OT_module_copy_script_source,
    BWFLOW_OT_module_load_script_file,
    BWFLOW_OT_module_write_template,
    BWFLOW_OT_module_run,
    BWFLOW_OT_script_library_save_current_module,
    BWFLOW_OT_script_library_apply_to_module,
    BWFLOW_OT_script_library_toggle_workflow,
    BWFLOW_OT_script_library_remove,
    BWFLOW_OT_script_library_refresh,
    BWFLOW_OT_script_library_open_storage_folder,
    BWFLOW_OT_module_runtime_field_write,
    BWFLOW_OT_module_runtime_action,
    BWFLOW_OT_copy_runtime_error,
    BWFLOW_OT_open_runtime_error_report,
    BWFLOW_OT_native_reference_cleanup,
    BWFLOW_OT_native_reference_viewer,
    BWFLOW_OT_panel_toggle_for_workflow,
    BWFLOW_OT_panel_toggle_group_for_workflow,
    BWFLOW_OT_panel_library_click,
    BWFLOW_OT_panel_add_all_to_workflow,
    BWFLOW_OT_panel_add_current_plugin_to_workflow,
    BWFLOW_OT_panel_toggle_single_for_workflow,
    BWFLOW_OT_group_expand_toggle,
    BWFLOW_OT_select_workflow_panel,
    BWFLOW_OT_panel_group_click,
    BWFLOW_OT_panel_add_tagged_to_workflow,
    BWFLOW_OT_panel_clear_workflow,
    BWFLOW_OT_panel_remove_missing_from_workflow,
    BWFLOW_OT_panel_reset_workflow_order,
    BWFLOW_OT_panel_jump_in_workflow,
    BWFLOW_OT_panel_reverse_workflow_order,
    BWFLOW_OT_panel_move_in_workflow,
    BWFLOW_OT_panel_move_child_in_group,
    BWFLOW_OT_panel_sort_children_by_default_order,
    BWFLOW_OT_debug_dump_panels,
    BWFLOW_OT_preset_export,
    BWFLOW_OT_preset_import,
    BWFLOW_OT_preset_load,
    BWFLOW_OT_preset_export_select_all,
    BWFLOW_OT_preset_select_all,
    BWFLOW_OT_preset_import_selected,
    BWFLOW_PT_workflow,
    BWFLOW_PT_workflow_image_editor,
    BWFLOW_PT_workflow_node_editor,
]


def register():
    global LOAD_HANDLER_REGISTERED, IS_INITIALIZING_ADDON
    IS_INITIALIZING_ADDON = True

    try:
        for cls in CLASSES:
            bpy.utils.register_class(cls)

        for prop_name in SPACE_STATE_PROP_NAMES.values():
            setattr(bpy.types.Scene, prop_name, PointerProperty(type=BWFLOW_PG_State))

        scenes = iter_available_scenes()
        for scene in scenes:
            ensure_minimum_setup(scene, restore_global=True, save_state=False)

        if bworkflow_load_post not in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.append(bworkflow_load_post)
            LOAD_HANDLER_REGISTERED = True

        scene = safe_context_scene() or (scenes[0] if scenes else None)
        rebuild_panel_cache(scene=scene)
        refresh_runtime_overrides(scene=scene, include_script_panels=True)
        schedule_deferred_runtime_refresh(scene=scene, intervals=(0.25,))
    finally:
        IS_INITIALIZING_ADDON = False


def unregister():
    global LOAD_HANDLER_REGISTERED

    try:
        _cancel_tracked_one_shot_timers()
    except Exception:
        traceback.print_exc()
    try:
        drain_validation_timer_callbacks()
    except Exception:
        traceback.print_exc()
    try:
        cleanup_module_runtimes(context=getattr(bpy, "context", None))
    except Exception:
        traceback.print_exc()
    try:
        drain_validation_timer_callbacks()
    except Exception:
        traceback.print_exc()
    try:
        _cancel_tracked_one_shot_timers()
    except Exception:
        traceback.print_exc()

    scenes = list(iter_available_scenes())
    for scene in scenes:
        try:
            save_global_workflow_state_now(scene)
            break
        except Exception:
            traceback.print_exc()
    for scene in scenes:
        try:
            restore_default_n_panel_state(
                scene=scene,
                disable_filters=True,
                switch_workflow=False,
                sync_registry_after_restore=False,
            )
        except Exception:
            traceback.print_exc()
    if not scenes:
        try:
            restore_default_n_panel_state(
                disable_filters=True,
                switch_workflow=False,
                sync_registry_after_restore=False,
            )
        except Exception:
            traceback.print_exc()
    try:
        uninstall_panel_poll_overrides()
    except Exception:
        traceback.print_exc()
    try:
        clear_panel_order_overrides()
    except Exception:
        traceback.print_exc()
    FILE_TEXT_CACHE.clear()
    FILE_JSON_CACHE.clear()
    BUILTIN_SCRIPT_LIBRARY_PAYLOAD_CACHE["signature"] = None
    BUILTIN_SCRIPT_LIBRARY_PAYLOAD_CACHE["payloads"] = []
    MODULE_RUNTIME_CLEANUP_CACHE.clear()

    try:
        if LOAD_HANDLER_REGISTERED and bworkflow_load_post in bpy.app.handlers.load_post:
            bpy.app.handlers.load_post.remove(bworkflow_load_post)
    except Exception:
        traceback.print_exc()
    LOAD_HANDLER_REGISTERED = False

    for prop_name in SPACE_STATE_PROP_NAMES.values():
        try:
            if hasattr(bpy.types.Scene, prop_name):
                delattr(bpy.types.Scene, prop_name)
        except Exception:
            traceback.print_exc()

    for cls in reversed(CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
        except Exception:
            traceback.print_exc()


if __name__ == "__main__":
    register()
