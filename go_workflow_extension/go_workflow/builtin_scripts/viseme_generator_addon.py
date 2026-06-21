bl_info = {
    "name": "AA/OH/CH Viseme Generator + MMD Reference (Blender 4.2)",
    "author": "3G||Gpt",
    "version": (0, 5, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Viseme",
    "description": "通过三个MMD表情形态键“AA/OH/CH”,自动生成整个MMD表情组。另外还有MMD表情中文英文日文对照组，方便查找与复制名称。",
    "category": "Animation",
}

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup
from array import array


# ---------------------------
# Viseme recipes (weights for AA/OH/CH)
# ---------------------------

VRC_RECIPES = {
    "sil": (0.00, 0.00, 0.00),
    "pp":  (0.00, 0.00, 0.18),
    "ff":  (0.00, 0.00, 0.35),
    "th":  (0.20, 0.00, 0.35),
    "dd":  (0.30, 0.00, 0.45),
    "kk":  (0.45, 0.00, 0.20),
    "ch":  (0.00, 0.00, 1.00),
    "ss":  (0.00, 0.00, 0.70),
    "nn":  (0.55, 0.00, 0.15),
    "rr":  (0.20, 0.45, 0.00),
    "aa":  (1.00, 0.00, 0.00),
    "e":   (0.60, 0.00, 0.25),
    "ih":  (0.35, 0.00, 0.50),
    "oh":  (0.00, 1.00, 0.00),
    "ou":  (0.10, 0.85, 0.10),
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


# ---------------------------
# MMD reference (common morph names)
# (category, jp_name, en_name, zh_desc)
# ---------------------------

MMD_REF_ITEMS = [
    # Eyes
    ("EYE", "まばたき",     "Blink",                 "眨眼"),
    ("EYE", "笑い",         "Smile Eyes",            "笑眼/眯眼"),
    ("EYE", "ウィンク",     "Wink (Left)",           "左眼眨眼"),
    ("EYE", "ウィンク右",   "Wink (Right)",          "右眼眨眼"),
    ("EYE", "ウィンク２",   "Wink 2 (Left)",         "左眨眼2（更夸张）"),
    ("EYE", "ウィンク２右", "Wink 2 (Right)",        "右眨眼2（更夸张）"),
    ("EYE", "なごみ",       "Calm/Soft Eyes",        "柔和/放松的眼神"),
    ("EYE", "はぅ",         "Embarrassed",           "害羞/委屈"),
    ("EYE", "じと目",       "Sullen/Unamused",       "死鱼眼/鄙视"),
    ("EYE", "ｷﾘｯ",          "Sharp/Serious",         "认真/锐利"),
    ("EYE", "びっくり",     "Surprised",             "惊讶（睁大眼）"),
    ("EYE", "瞳小",         "Small Pupils",          "瞳孔缩小"),

    # Added per request
    ("EYE", "星目",         "Star Eyes",             "星星眼"),
    ("EYE", "ハート目",     "Heart Eyes",            "爱心眼"),
    ("EYE", "涙",           "Tears",                 "眼泪/泪光"),

    # Brows
    ("BROW", "上",          "Brow Up",               "眉上扬"),
    ("BROW", "下",          "Brow Down",             "眉下压"),
    ("BROW", "困る",        "Troubled",              "困扰/八字眉"),
    ("BROW", "怒り",        "Angry Brows",           "生气眉"),
    ("BROW", "にこり",      "Smile Brows",           "微笑眉"),

    # Face / Expressions
    ("FACE", "にっこり",    "Smile",                 "微笑"),
    ("FACE", "にやり",      "Grin/Smirk",            "坏笑/得意笑"),
    ("FACE", "への字",      "Frown",                 "撇嘴/不开心"),
    ("FACE", "あせり",      "Sweat/Nervous",         "紧张/冒汗"),
    ("FACE", "泣き",        "Cry",                   "哭泣"),
    ("FACE", "てへぺろ",    "Tehepero",              "吐舌卖萌（经典表情名）"),
    ("FACE", "ぺろっ",      "Tongue Out",            "吐舌"),
    ("FACE", "口角上げ",    "Mouth Corners Up",      "嘴角上扬"),
    ("FACE", "口角下げ",    "Mouth Corners Down",    "嘴角下压"),
    ("FACE", "口横広げ",    "Mouth Wide",            "嘴横向拉伸/张大横向"),
]

MMD_REF_CATEGORY_ENUM = [
    ("ALL", "All / 全部", "Show all items"),
    ("EYE", "Eyes / 目", "Common eye morph names"),
    ("BROW", "Brows / 眉", "Common eyebrow morph names"),
    ("FACE", "Face / 表情", "Common face/mouth expression morph names"),
]


# ---------------------------
# Helpers
# ---------------------------

def _find_keyblock_case_insensitive(key_blocks, name: str):
    if not name:
        return None
    if name in key_blocks:
        return key_blocks[name]
    target = name.casefold()
    for kb in key_blocks:
        if kb.name.casefold() == target:
            return kb
    return None


def _ensure_object_mode(obj):
    if obj.mode == 'OBJECT':
        return True
    try:
        bpy.ops.object.mode_set(mode='OBJECT')
        return True
    except RuntimeError:
        return False


def _coords_of_keyblock(keyblock, vert_count: int):
    coords = array('f', [0.0]) * (vert_count * 3)
    keyblock.data.foreach_get("co", coords)
    return coords


def _create_mixed_key_from_sources(obj, target_name: str, source_weights, overwrite: bool):
    sk = getattr(obj.data, "shape_keys", None)
    key_blocks = getattr(sk, "key_blocks", None)
    if sk is None or key_blocks is None or not key_blocks:
        raise RuntimeError("Object has no shape keys.")

    existing = _find_keyblock_case_insensitive(key_blocks, target_name)
    if existing is not None and not overwrite:
        return "SKIPPED", []

    basis = sk.reference_key
    vert_count = len(basis.data)
    basis_coords = _coords_of_keyblock(basis, vert_count)
    source_coords = []
    missing = []
    for source_name, weight in source_weights:
        source_kb = _find_keyblock_case_insensitive(key_blocks, source_name)
        if source_kb is None:
            missing.append(source_name)
            continue
        source_coords.append((_coords_of_keyblock(source_kb, vert_count), float(weight)))
    if missing or not source_coords:
        return "MISSING", missing

    target_kb = existing if existing is not None else obj.shape_key_add(name=target_name, from_mix=False)
    total_floats = vert_count * 3
    new_coords = array('f', [0.0]) * total_floats
    for index in range(total_floats):
        base_value = basis_coords[index]
        mixed_delta = 0.0
        for coords, weight in source_coords:
            mixed_delta += (coords[index] - base_value) * weight
        new_coords[index] = base_value + mixed_delta
    target_kb.data.foreach_set("co", new_coords)
    return ("UPDATED" if existing is not None else "CREATED"), []


def _generate_base_keys_from_arkit(obj, settings):
    targets = (
        ("AA", settings.base_aa),
        ("OH", settings.base_oh),
        ("CH", settings.base_ch),
    )
    results = []
    for recipe_name, target_name in targets:
        status, missing = _create_mixed_key_from_sources(
            obj,
            target_name,
            ARKIT_BASE_RECIPES[recipe_name],
            overwrite=bool(settings.overwrite_existing),
        )
        results.append((recipe_name, target_name, status, missing))
    return results


def _eval_mesh_copy_and_coords(obj, depsgraph):
    """
    Evaluate object to a temporary mesh (modifiers included as currently enabled),
    return (mesh_copy_datablock, coords_array, vert_count).
    """
    obj_eval = obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        vert_count = len(mesh_eval.vertices)
        coords = array('f', [0.0]) * (vert_count * 3)
        mesh_eval.vertices.foreach_get("co", coords)
        mesh_copy = mesh_eval.copy()
        return mesh_copy, coords, vert_count
    finally:
        obj_eval.to_mesh_clear()


def _eval_mesh_coords(obj, depsgraph, expected_vert_count: int | None = None):
    """
    Evaluate object to a temporary mesh, return coords_array.
    Raises RuntimeError if vertex count mismatches expected_vert_count.
    """
    obj_eval = obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        vert_count = len(mesh_eval.vertices)
        if expected_vert_count is not None and vert_count != expected_vert_count:
            raise RuntimeError(f"Vertex count mismatch: expected {expected_vert_count}, got {vert_count}")
        coords = array('f', [0.0]) * (vert_count * 3)
        mesh_eval.vertices.foreach_get("co", coords)
        return coords
    finally:
        obj_eval.to_mesh_clear()


def _mmd_ref_filtered_indices(settings):
    cat = settings.mmd_ref_category
    q = (settings.mmd_ref_search or "").casefold().strip()

    for idx, (c, jp, en, zh) in enumerate(MMD_REF_ITEMS):
        if cat != "ALL" and c != cat:
            continue
        if q:
            hay = f"{jp}\n{en}\n{zh}".casefold()
            if q not in hay:
                continue
        yield idx


def _create_or_reset_empty_shape_key(obj, key_name: str, overwrite: bool):
    """
    Ensure shape key exists and is "empty" (same as Basis):
      - if missing -> create (empty by default)
      - if exists & overwrite -> reset coords to Basis
      - if exists & !overwrite -> skip
    Returns: 'CREATED' | 'UPDATED' | 'SKIPPED'
    """
    if not obj or obj.type != 'MESH':
        raise ValueError("Active object is not a mesh")

    if not _ensure_object_mode(obj):
        raise RuntimeError("Cannot switch to Object mode")

    sk = obj.data.shape_keys
    if sk is None or not sk.key_blocks:
        obj.shape_key_add(name=key_name, from_mix=False)
        return 'CREATED'

    key_blocks = sk.key_blocks
    existing = key_blocks.get(key_name)
    if existing is None:
        obj.shape_key_add(name=key_name, from_mix=False)
        return 'CREATED'

    if not overwrite:
        return 'SKIPPED'

    basis = sk.reference_key
    vert_count = len(basis.data)
    basis_coords = _coords_of_keyblock(basis, vert_count)
    existing.data.foreach_set("co", basis_coords)
    return 'UPDATED'


# ---------------------------
# Settings
# ---------------------------

class VISEMEGEN_Settings(PropertyGroup):
    base_aa: StringProperty(
        name="AA Shape Key",
        default="AA",
        description="Name of the base 'AA' shape key (open mouth)",
    )
    base_oh: StringProperty(
        name="OH Shape Key",
        default="OH",
        description="Name of the base 'OH' shape key (round mouth)",
    )
    base_ch: StringProperty(
        name="CH Shape Key",
        default="CH",
        description="Name of the base 'CH' shape key (wide mouth / teeth)",
    )

    preset: EnumProperty(
        name="Preset",
        items=[
            ("VRCHAT", "VRChat (15)", "Generate VRChat 15 visemes"),
            ("MMD", "MMD (A/I/U/E/O)", "Generate MMD vowel keys"),
            ("BOTH", "Both", "Generate both VRChat and MMD keys"),
        ],
        default="VRCHAT",
    )

    vrc_prefix: StringProperty(
        name="VRChat Prefix",
        default="vrc.v_",
        description="Prefix for VRChat viseme names (e.g. 'vrc.v_')",
    )

    mmd_use_japanese: BoolProperty(
        name="MMD Japanese Names (あいうえお)",
        default=True,
        description="Use Japanese vowel names for MMD keys",
    )

    overwrite_existing: BoolProperty(
        name="Overwrite Existing",
        default=False,
        description="If enabled, overwrite existing target shape keys",
    )

    generate_base_from_arkit: BoolProperty(
        name="Generate AA/OH/CH from ARKit First",
        default=False,
        description="Create or update AA/OH/CH from ARKit-style source shape keys before generating visemes",
    )

    strength: FloatProperty(
        name="Strength",
        default=1.0,
        min=0.0,
        soft_max=2.0,
        description="Global multiplier applied to the mixed delta",
    )

    # MMD reference tool
    mmd_ref_enabled: BoolProperty(
        name="Enable MMD Reference",
        default=False,
        description="Show/Hide MMD reference panel",
    )

    mmd_ref_category: EnumProperty(
        name="Category",
        items=MMD_REF_CATEGORY_ENUM,
        default="EYE",
        description="Filter MMD reference list",
    )

    mmd_ref_search: StringProperty(
        name="Search",
        default="",
        description="Search in JP/EN/ZH (case-insensitive)",
    )

    mmd_ref_copy_mode: EnumProperty(
        name="Copy Mode",
        items=[
            ("JP", "JP", "Copy Japanese name only"),
            ("JP_EN", "JP + EN", "Copy Japanese + English"),
            ("JP_EN_ZH", "JP + EN + 中文", "Copy Japanese + English + Chinese"),
        ],
        default="JP",
    )


# ---------------------------
# Operators - Viseme generation
# ---------------------------

class VISEMEGEN_OT_generate(Operator):
    bl_idname = "visemegen.generate"
    bl_label = "Generate Visemes"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        settings = obj.visemegen_settings

        if not _ensure_object_mode(obj):
            self.report({'ERROR'}, "Please switch to Object Mode.")
            return {'CANCELLED'}

        sk = obj.data.shape_keys
        if sk is None or not sk.key_blocks:
            self.report({'ERROR'}, "Object has no shape keys.")
            return {'CANCELLED'}

        base_summary = ""
        if settings.generate_base_from_arkit:
            base_results = _generate_base_keys_from_arkit(obj, settings)
            missing_messages = []
            result_chunks = []
            for recipe_name, target_name, status, missing in base_results:
                result_chunks.append(f"{recipe_name}->{target_name}:{status}")
                if status == "MISSING":
                    missing_messages.append(f"{recipe_name} missing {', '.join(missing)}")
            if missing_messages:
                self.report({'ERROR'}, "ARKit base generation failed: " + " | ".join(missing_messages))
                return {'CANCELLED'}
            base_summary = " Base: " + ", ".join(result_chunks)

        key_blocks = sk.key_blocks
        basis = sk.reference_key
        vert_count = len(basis.data)

        kb_aa = _find_keyblock_case_insensitive(key_blocks, settings.base_aa)
        kb_oh = _find_keyblock_case_insensitive(key_blocks, settings.base_oh)
        kb_ch = _find_keyblock_case_insensitive(key_blocks, settings.base_ch)

        missing = [n for n, kb in (("AA", kb_aa), ("OH", kb_oh), ("CH", kb_ch)) if kb is None]
        if missing:
            self.report({'ERROR'}, f"Missing base shape key(s): {', '.join(missing)}")
            return {'CANCELLED'}

        basis_coords = _coords_of_keyblock(basis, vert_count)
        aa_coords = _coords_of_keyblock(kb_aa, vert_count)
        oh_coords = _coords_of_keyblock(kb_oh, vert_count)
        ch_coords = _coords_of_keyblock(kb_ch, vert_count)

        tasks = []

        if settings.preset in {"VRCHAT", "BOTH"}:
            prefix = settings.vrc_prefix or ""
            for viseme_id, weights in VRC_RECIPES.items():
                tasks.append((f"{prefix}{viseme_id}", weights))

        if settings.preset in {"MMD", "BOTH"}:
            for vowel, weights in MMD_RECIPES.items():
                name = MMD_JP_NAMES[vowel] if settings.mmd_use_japanese else vowel
                tasks.append((name, weights))

        created = 0
        updated = 0
        skipped = 0

        total_floats = vert_count * 3
        strength = float(settings.strength)

        for target_name, (w_aa, w_oh, w_ch) in tasks:
            existing = key_blocks.get(target_name)

            if existing and not settings.overwrite_existing:
                skipped += 1
                continue

            if existing is None:
                target_kb = obj.shape_key_add(name=target_name, from_mix=False)
                created += 1
            else:
                target_kb = existing
                updated += 1

            new_coords = array('f', [0.0]) * total_floats
            for j in range(total_floats):
                b = basis_coords[j]
                mixed = (
                    w_aa * (aa_coords[j] - b) +
                    w_oh * (oh_coords[j] - b) +
                    w_ch * (ch_coords[j] - b)
                )
                new_coords[j] = b + (strength * mixed)

            target_kb.data.foreach_set("co", new_coords)

        obj.data.update()
        self.report(
            {'INFO'},
            f"Viseme generation done. Created: {created}, Updated: {updated}, Skipped: {skipped}{base_summary}"
        )
        return {'FINISHED'}


# ---------------------------
# Operators - Apply modifiers with shape keys
# ---------------------------

class VISEMEGEN_OT_apply_modifiers_with_shapekeys(Operator):
    bl_idname = "visemegen.apply_modifiers_with_shapekeys"
    bl_label = "应用修改器（保留形态键）"
    bl_options = {'REGISTER', 'UNDO'}

    apply_visible_only: BoolProperty(
        name="Only Viewport Enabled",
        default=True,
        description="Only apply modifiers that are enabled in viewport",
    )

    skip_armature: BoolProperty(
        name="Skip Armature",
        default=True,
        description="Skip Armature modifier by default (recommended for rigs)",
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH' and len(obj.modifiers) > 0

    def execute(self, context):
        obj = context.active_object

        if not _ensure_object_mode(obj):
            self.report({'ERROR'}, "Please switch to Object Mode.")
            return {'CANCELLED'}

        # Make mesh single-user to avoid affecting other objects
        if obj.data.users > 1:
            obj.data = obj.data.copy()

        # Decide modifiers to apply vs keep
        skip_types = set()
        if self.skip_armature:
            skip_types.add('ARMATURE')

        mods_to_apply = []
        mods_to_keep = []
        for m in obj.modifiers:
            if m.type in skip_types:
                mods_to_keep.append(m)
                continue
            if self.apply_visible_only and not m.show_viewport:
                mods_to_keep.append(m)
                continue
            mods_to_apply.append(m)

        if not mods_to_apply:
            self.report({'WARNING'}, "No modifiers to apply (check visibility/skip options).")
            return {'CANCELLED'}

        keep_viewport = {m.name: m.show_viewport for m in mods_to_keep}

        old_mesh = obj.data
        old_sk = old_mesh.shape_keys
        has_sk = old_sk is not None and old_sk.key_blocks is not None and len(old_sk.key_blocks) > 0

        if has_sk and not old_sk.use_relative:
            self.report({'ERROR'}, "Absolute shape keys are not supported by this operator (use_relative must be True).")
            return {'CANCELLED'}

        # Save and temporarily override shape key values/mutes for evaluation
        old_key_props = []
        old_key_values = {}
        old_key_mutes = {}

        # Temporarily disable kept modifiers so evaluation contains ONLY modifiers we apply
        for m in mods_to_keep:
            m.show_viewport = False

        mesh_applied = None
        try:
            if has_sk:
                for kb in old_sk.key_blocks:
                    old_key_values[kb.name] = kb.value
                    old_key_mutes[kb.name] = kb.mute
                    kb.mute = False

                    old_key_props.append({
                        "name": kb.name,
                        "mute": kb.mute,
                        "slider_min": kb.slider_min,
                        "slider_max": kb.slider_max,
                        "value": kb.value,
                        "vertex_group": kb.vertex_group,
                        "relative_key_name": kb.relative_key.name if kb.relative_key else None,
                        "interpolation": kb.interpolation,
                        "lock_shape": getattr(kb, "lock_shape", False),
                    })

                # Base: all keys 0
                for kb in old_sk.key_blocks:
                    kb.value = 0.0
                context.view_layer.update()

            depsgraph = context.evaluated_depsgraph_get()

            # Evaluate base mesh (this becomes new obj.data)
            mesh_applied, base_coords, vert_count = _eval_mesh_copy_and_coords(obj, depsgraph)
            mesh_applied.name = f"{old_mesh.name}_Applied"

            coords_by_name = {}
            if has_sk:
                basis_name = old_sk.reference_key.name
                coords_by_name[basis_name] = base_coords

                # Each key at value=1 (others=0)
                for kb in old_sk.key_blocks:
                    if kb == old_sk.reference_key:
                        continue
                    for kb2 in old_sk.key_blocks:
                        kb2.value = 0.0
                    kb.value = 1.0
                    context.view_layer.update()
                    coords_by_name[kb.name] = _eval_mesh_coords(obj, depsgraph, expected_vert_count=vert_count)

            # Restore old key values/mutes on old mesh before swapping data (best-effort)
            if has_sk:
                for kb in old_sk.key_blocks:
                    kb.value = old_key_values.get(kb.name, 0.0)
                    kb.mute = old_key_mutes.get(kb.name, False)

        except RuntimeError as e:
            if mesh_applied is not None and mesh_applied.users == 0:
                bpy.data.meshes.remove(mesh_applied)
            # Restore kept modifiers viewport state
            for name, val in keep_viewport.items():
                m = obj.modifiers.get(name)
                if m:
                    m.show_viewport = val
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        finally:
            # Ensure kept modifiers viewport state is restored if we exit early
            # (If success, we'll restore again after removing applied mods; harmless.)
            for name, val in keep_viewport.items():
                m = obj.modifiers.get(name)
                if m:
                    m.show_viewport = val

        # Swap mesh data to applied base mesh
        obj.data = mesh_applied

        # Remove applied modifiers (baked)
        apply_names = [m.name for m in mods_to_apply]
        for name in apply_names:
            m = obj.modifiers.get(name)
            if m:
                obj.modifiers.remove(m)

        # Re-restore kept modifiers viewport state (after removal)
        for name, val in keep_viewport.items():
            m = obj.modifiers.get(name)
            if m:
                m.show_viewport = val

        # Rebuild shape keys on the new mesh
        if has_sk:
            old_names_in_order = [p["name"] for p in old_key_props]
            basis_name = old_names_in_order[0] if old_names_in_order else "Basis"
            other_names = old_names_in_order[1:]

            # Create at least one non-basis key to initialize shape keys datablock
            created_temp = False
            if other_names:
                obj.shape_key_add(name=other_names[0], from_mix=False)
            else:
                obj.shape_key_add(name="__TEMP__", from_mix=False)
                created_temp = True

            new_sk = obj.data.shape_keys
            new_sk.use_relative = True
            new_sk.reference_key.name = basis_name

            # Ensure we have coords for basis name key
            if basis_name not in coords_by_name:
                # fallback to whatever the reference key was called
                coords_by_name[basis_name] = coords_by_name.get("Basis")

            expected = len(obj.data.vertices) * 3

            # Set coords for the key(s)
            for i, name in enumerate(other_names):
                kb = new_sk.key_blocks.get(name)
                if kb is None:
                    kb = obj.shape_key_add(name=name, from_mix=False)
                coords = coords_by_name.get(name)
                if coords is None or len(coords) != expected:
                    self.report({'ERROR'}, f"Missing or invalid coords for shape key: {name}")
                    return {'CANCELLED'}
                kb.data.foreach_set("co", coords)

            # Copy properties (pass 1: simple fields)
            for prop in old_key_props[1:]:
                kb = new_sk.key_blocks.get(prop["name"])
                if not kb:
                    continue
                kb.slider_min = prop["slider_min"]
                kb.slider_max = prop["slider_max"]
                kb.mute = prop["mute"]
                kb.vertex_group = prop["vertex_group"]
                kb.interpolation = prop["interpolation"]
                if hasattr(kb, "lock_shape"):
                    kb.lock_shape = prop["lock_shape"]
                kb.value = prop["value"]

            # Copy relative_key (pass 2: after all keys exist)
            for prop in old_key_props[1:]:
                kb = new_sk.key_blocks.get(prop["name"])
                if not kb:
                    continue
                rel_name = prop["relative_key_name"]
                if rel_name and rel_name in new_sk.key_blocks:
                    kb.relative_key = new_sk.key_blocks[rel_name]
                else:
                    kb.relative_key = new_sk.reference_key

            # Remove temp key if needed
            if created_temp and "__TEMP__" in new_sk.key_blocks:
                obj.shape_key_remove(new_sk.key_blocks["__TEMP__"])

        obj.data.update()

        if has_sk and old_sk and old_sk.animation_data:
            self.report({'WARNING'}, "Done. Note: shape key animation/drivers on the old Key datablock are not preserved.")
        else:
            self.report({'INFO'}, "Done. Modifiers applied and shape keys rebuilt.")

        return {'FINISHED'}


# ---------------------------
# Operators - MMD reference tool
# ---------------------------

class VISEMEGEN_OT_toggle_mmd_reference(Operator):
    bl_idname = "visemegen.toggle_mmd_reference"
    bl_label = "Toggle MMD Reference"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        settings = obj.visemegen_settings
        settings.mmd_ref_enabled = not settings.mmd_ref_enabled
        return {'FINISHED'}


class VISEMEGEN_OT_copy_mmd_ref(Operator):
    bl_idname = "visemegen.copy_mmd_ref"
    bl_label = "Copy MMD Morph Name"
    bl_options = {'INTERNAL'}

    index: IntProperty(default=-1)

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object first.")
            return {'CANCELLED'}

        settings = obj.visemegen_settings
        if self.index < 0 or self.index >= len(MMD_REF_ITEMS):
            self.report({'ERROR'}, "Invalid item index.")
            return {'CANCELLED'}

        _cat, jp, en, zh = MMD_REF_ITEMS[self.index]
        mode = settings.mmd_ref_copy_mode

        if mode == "JP":
            text = jp
        elif mode == "JP_EN":
            text = f"{jp}\t{en}"
        else:
            text = f"{jp}\t{en}\t{zh}"

        context.window_manager.clipboard = text
        self.report({'INFO'}, "Copied to clipboard.")
        return {'FINISHED'}


class VISEMEGEN_OT_create_empty_mmd_key(Operator):
    bl_idname = "visemegen.create_empty_mmd_key"
    bl_label = "Create Empty Shape Key"
    bl_options = {'REGISTER', 'UNDO'}

    index: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        settings = obj.visemegen_settings

        if self.index < 0 or self.index >= len(MMD_REF_ITEMS):
            self.report({'ERROR'}, "Invalid item index.")
            return {'CANCELLED'}

        _cat, jp, _en, _zh = MMD_REF_ITEMS[self.index]

        try:
            result = _create_or_reset_empty_shape_key(
                obj=obj,
                key_name=jp,
                overwrite=bool(settings.overwrite_existing),
            )
        except (ValueError, RuntimeError) as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        self.report({'INFO'}, f"{result}: {jp}")
        return {'FINISHED'}


class VISEMEGEN_OT_create_empty_mmd_keys_batch(Operator):
    bl_idname = "visemegen.create_empty_mmd_keys_batch"
    bl_label = "Create Empty Keys (Filtered)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        settings = obj.visemegen_settings

        created = 0
        updated = 0
        skipped = 0

        indices = list(_mmd_ref_filtered_indices(settings))
        if not indices:
            self.report({'WARNING'}, "No items match the current filter/search.")
            return {'CANCELLED'}

        for idx in indices:
            _cat, jp, _en, _zh = MMD_REF_ITEMS[idx]
            try:
                result = _create_or_reset_empty_shape_key(
                    obj=obj,
                    key_name=jp,
                    overwrite=bool(settings.overwrite_existing),
                )
            except (ValueError, RuntimeError) as e:
                self.report({'ERROR'}, str(e))
                return {'CANCELLED'}

            if result == 'CREATED':
                created += 1
            elif result == 'UPDATED':
                updated += 1
            else:
                skipped += 1

        self.report({'INFO'}, f"Batch done. Created: {created}, Updated: {updated}, Skipped: {skipped}")
        return {'FINISHED'}


# ---------------------------
# UI Panels
# ---------------------------

class VISEMEGEN_PT_panel(Panel):
    bl_label = "Viseme Generator (AA/OH/CH)"
    bl_idname = "VISEMEGEN_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Viseme"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object

        if obj is None or obj.type != 'MESH':
            layout.label(text="Select a mesh object.")
            return

        settings = obj.visemegen_settings

        col = layout.column(align=True)
        col.label(text="Base Shape Keys:")
        col.prop(settings, "base_aa")
        col.prop(settings, "base_oh")
        col.prop(settings, "base_ch")

        layout.separator()

        col = layout.column(align=True)
        col.prop(settings, "preset")

        if settings.preset in {"VRCHAT", "BOTH"}:
            col.prop(settings, "vrc_prefix")

        if settings.preset in {"MMD", "BOTH"}:
            col.prop(settings, "mmd_use_japanese")

        layout.separator()

        col = layout.column(align=True)
        col.prop(settings, "strength")
        col.prop(settings, "generate_base_from_arkit")
        col.prop(settings, "overwrite_existing")

        layout.separator()
        layout.operator(VISEMEGEN_OT_generate.bl_idname, icon='SHAPEKEY_DATA')

        layout.separator()
        # Single toggle button requested (MMD reference)
        if settings.mmd_ref_enabled:
            layout.operator(VISEMEGEN_OT_toggle_mmd_reference.bl_idname, text="隐藏 MMD 对照", icon='HIDE_ON')
        else:
            layout.operator(VISEMEGEN_OT_toggle_mmd_reference.bl_idname, text="显示 MMD 对照", icon='VIEWZOOM')

        layout.separator()
        # Single utility button requested (apply modifiers with shape keys)
        layout.operator(VISEMEGEN_OT_apply_modifiers_with_shapekeys.bl_idname, icon='MODIFIER_DATA')


class VISEMEGEN_PT_mmd_reference(Panel):
    bl_label = "MMD 对照（常用表情名）"
    bl_idname = "VISEMEGEN_PT_mmd_reference"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Viseme"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj is None or obj.type != 'MESH':
            return False
        if not hasattr(obj, "visemegen_settings"):
            return False
        return bool(obj.visemegen_settings.mmd_ref_enabled)

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        settings = obj.visemegen_settings

        row = layout.row(align=True)
        row.operator(VISEMEGEN_OT_toggle_mmd_reference.bl_idname, text="隐藏", icon='HIDE_ON')

        layout.separator()

        layout.prop(settings, "mmd_ref_category")
        layout.prop(settings, "mmd_ref_search")
        layout.prop(settings, "mmd_ref_copy_mode")
        layout.prop(settings, "overwrite_existing")

        layout.operator(VISEMEGEN_OT_create_empty_mmd_keys_batch.bl_idname, icon='ADD')

        layout.separator()

        shown = list(_mmd_ref_filtered_indices(settings))
        layout.label(text=f"Items: {len(shown)}")

        for idx in shown:
            _cat, jp, en, zh = MMD_REF_ITEMS[idx]

            box = layout.box()
            row = box.row(align=True)
            row.label(text=jp)

            op = row.operator(VISEMEGEN_OT_copy_mmd_ref.bl_idname, text="", icon='COPY')
            op.index = idx

            op2 = row.operator(VISEMEGEN_OT_create_empty_mmd_key.bl_idname, text="", icon='ADD')
            op2.index = idx

            box.label(text=f"EN: {en}")
            box.label(text=f"中文: {zh}")


# ---------------------------
# Register
# ---------------------------

classes = (
    VISEMEGEN_Settings,
    VISEMEGEN_OT_generate,
    VISEMEGEN_OT_apply_modifiers_with_shapekeys,
    VISEMEGEN_OT_toggle_mmd_reference,
    VISEMEGEN_OT_copy_mmd_ref,
    VISEMEGEN_OT_create_empty_mmd_key,
    VISEMEGEN_OT_create_empty_mmd_keys_batch,
    VISEMEGEN_PT_panel,
    VISEMEGEN_PT_mmd_reference,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.visemegen_settings = PointerProperty(type=VISEMEGEN_Settings)

def unregister():
    del bpy.types.Object.visemegen_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
