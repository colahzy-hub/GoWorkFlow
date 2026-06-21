import bpy


def _registered_panel_classes_list():
    return list(iter_registered_panel_classes())


def iter_panel_subclasses(base_cls):
    seen = set()
    stack = list(base_cls.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
        yield cls


def iter_registered_panel_classes():
    seen = set()
    panel_base = getattr(bpy.types, "Panel", None)
    if panel_base is None:
        return

    for attr_name in dir(bpy.types):
        if attr_name.startswith("__"):
            continue
        try:
            cls = getattr(bpy.types, attr_name)
        except Exception:
            continue
        if cls in seen or not isinstance(cls, type):
            continue
        try:
            is_panel = issubclass(cls, panel_base)
        except Exception:
            is_panel = False
        if not is_panel:
            continue
        seen.add(cls)
        yield cls


def is_registered_panel_class(cls):
    if cls is None:
        return False
    try:
        if getattr(cls, "is_registered", None) is True:
            return True
    except Exception:
        pass
    try:
        bl_rna = getattr(cls, "bl_rna", None)
    except Exception:
        return False
    if bl_rna is None:
        return False
    try:
        return bool(getattr(bl_rna, "identifier", ""))
    except Exception:
        return False


def panel_registration_order_map(space_type=None):
    return _panel_registration_order_map(_registered_panel_classes_list(), space_type=space_type)


def _panel_registration_order_map(registered_classes, space_type=None):
    order = {}
    for index, cls in enumerate(registered_classes):
        if not is_registered_panel_class(cls):
            continue
        if space_type and getattr(cls, "bl_space_type", None) != space_type:
            continue
        panel_id = getattr(cls, "bl_idname", "") or getattr(cls, "__name__", "")
        if panel_id and panel_id not in order:
            order[panel_id] = index
    return order


def discover_sidebar_panels(
    space_type="VIEW_3D",
    *,
    is_builtin_default_panel_class_fn,
    is_builtin_default_panel_name_fn,
    clean_panel_title_fn,
):
    registry = {}
    registered_classes = _registered_panel_classes_list()
    registration_order = _panel_registration_order_map(registered_classes, space_type=space_type)

    for cls in iter_panel_subclasses(bpy.types.Panel):
        if not is_registered_panel_class(cls):
            continue
        panel_id = getattr(cls, "bl_idname", "") or getattr(cls, "__name__", "")
        if not panel_id or panel_id.startswith("BWFLOW_"):
            continue
        if is_builtin_default_panel_class_fn(cls) and not is_builtin_default_panel_name_fn(panel_id):
            continue
        if getattr(cls, "bl_space_type", None) != space_type:
            continue
        if getattr(cls, "bl_region_type", None) != "UI":
            continue
        if not hasattr(cls, "draw"):
            continue
        registry[panel_id] = cls

    for cls in registered_classes:
        if not is_registered_panel_class(cls):
            continue
        panel_id = getattr(cls, "bl_idname", "") or getattr(cls, "__name__", "")
        if not panel_id or panel_id.startswith("BWFLOW_") or panel_id in registry:
            continue
        if getattr(cls, "bl_space_type", None) != space_type:
            continue
        if getattr(cls, "bl_region_type", None) != "UI":
            continue
        if not hasattr(cls, "draw"):
            continue
        if not is_builtin_default_panel_class_fn(cls) and not is_builtin_default_panel_name_fn(panel_id):
            continue
        registry[panel_id] = cls

    panels = dict(
        sorted(
            registry.items(),
            key=lambda item: (
                registration_order.get(item[0], 999999),
                getattr(item[1], "bl_category", ""),
                clean_panel_title_fn(getattr(item[1], "bl_label", item[0]), item[0]),
            ),
        )
    )
    return panels, registry


def panel_display_category(
    panel_id,
    cls,
    *,
    builtin_default_panel_category_fn,
    get_panel_cache_fn,
    space_type=None,
):
    category = getattr(cls, "bl_category", "")
    if category:
        return category

    builtin_category = builtin_default_panel_category_fn(panel_id)
    if builtin_category:
        return builtin_category

    parent_id = getattr(cls, "bl_parent_id", "")
    if parent_id and parent_id != panel_id:
        parent_cls = get_panel_cache_fn(space_type).get(parent_id)
        if parent_cls is not None:
            return panel_display_category(
                parent_id,
                parent_cls,
                builtin_default_panel_category_fn=builtin_default_panel_category_fn,
                get_panel_cache_fn=get_panel_cache_fn,
                space_type=space_type,
            )
    return ""
