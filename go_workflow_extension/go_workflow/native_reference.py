import json
import os
import re
import shutil
import time
import traceback

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader
from bpy.props import BoolProperty
from bpy.types import Operator


_NATIVE_REFERENCE_GIF_CACHE = {}
TEMP_REFERENCE_IMAGE_TAG = "go_workflow_temp_reference"
MAX_GIF_CACHE_ENTRIES = 48
_DRAW_HANDLER_STATE = {
    "handle": None,
    "timer": None,
    "image": None,
    "frame_images": [],
    "image_paths": [],
    "media_files": [],
    "media_index": 0,
    "is_gif": False,
    "frame_index": 0,
    "frame_total": 1,
    "frame_interval": 0.085,
    "last_tick": 0.0,
    "window_ptr": None,
    "area_ptr": None,
    "title": "",
}


def native_reference_frame_prefix(path):
    stem = os.path.splitext(os.path.basename(str(path or "").strip()))[0].strip() or "reference"
    stem = re.sub(r"[^0-9A-Za-z_-]+", "_", stem).strip("_") or "reference"
    return stem[:48] + "__frame_"


def native_reference_preview_temp_root():
    import tempfile

    return os.path.join(tempfile.gettempdir(), "go_workflow_arkit_previews")


def normalized_abs_path(path):
    value = str(path or "").strip()
    if not value:
        return ""
    try:
        value = bpy.path.abspath(value)
    except Exception:
        pass
    return os.path.normcase(os.path.abspath(os.path.normpath(value)))


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


def ps_single_quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def reference_image_load(path):
    image = bpy.data.images.load(path, check_existing=True)
    try:
        image.colorspace_settings.name = "sRGB"
    except Exception:
        pass
    try:
        image.use_fake_user = False
    except Exception:
        pass
    try:
        image[TEMP_REFERENCE_IMAGE_TAG] = True
    except Exception:
        pass
    return image


def clear_image_from_all_editors(image):
    if image is None:
        return
    for window, screen in iter_window_screens():
        for area in getattr(screen, "areas", []):
            if getattr(area, "type", "") != "IMAGE_EDITOR":
                continue
            try:
                space = area.spaces.active
                if getattr(space, "image", None) == image:
                    space.image = None
            except Exception:
                pass


def remove_temp_images_by_paths(paths):
    normalized_paths = {normalized_abs_path(path) for path in (paths or []) if normalized_abs_path(path)}
    if not normalized_paths:
        return
    for image in list(bpy.data.images):
        filepath = normalized_abs_path(getattr(image, "filepath", "") or "")
        if filepath not in normalized_paths:
            continue
        clear_image_from_all_editors(image)
        try:
            image.user_clear()
        except Exception:
            pass
        try:
            bpy.data.images.remove(image, do_unlink=True)
        except Exception:
            pass


def remove_tagged_temp_images(extra_paths=None):
    normalized_extra = {normalized_abs_path(path) for path in (extra_paths or []) if normalized_abs_path(path)}
    for image in list(bpy.data.images):
        filepath = normalized_abs_path(getattr(image, "filepath", "") or "")
        tagged = False
        try:
            tagged = bool(image.get(TEMP_REFERENCE_IMAGE_TAG))
        except Exception:
            tagged = False
        if not tagged and filepath not in normalized_extra:
            continue
        clear_image_from_all_editors(image)
        try:
            image.user_clear()
        except Exception:
            pass
        try:
            bpy.data.images.remove(image, do_unlink=True)
        except Exception:
            pass


def builtin_reference_media_dirs():
    special_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "special_presets")
    if not os.path.isdir(special_dir):
        return []
    result = []
    for entry in os.listdir(special_dir):
        full = os.path.join(special_dir, entry)
        if not os.path.isdir(full):
            continue
        if entry.endswith("_images") or entry.endswith("_detail_images"):
            result.append(normalized_abs_path(full))
    return result


def is_builtin_reference_media_path(path):
    norm = normalized_abs_path(path)
    if not norm:
        return False
    media_dirs = builtin_reference_media_dirs()
    return any(norm == folder or norm.startswith(folder + os.sep) for folder in media_dirs)


def cleanup_builtin_reference_images():
    media_dirs = builtin_reference_media_dirs()
    if not media_dirs:
        return 0
    removed = 0
    for image in list(bpy.data.images):
        filepath = normalized_abs_path(getattr(image, "filepath", "") or "")
        if not filepath or not any(filepath == folder or filepath.startswith(folder + os.sep) for folder in media_dirs):
            continue
        clear_image_from_all_editors(image)
        try:
            image.user_clear()
        except Exception:
            pass
        try:
            bpy.data.images.remove(image, do_unlink=True)
            removed += 1
        except Exception:
            pass
    return removed


def cleanup_preview_temp_files():
    temp_root = normalized_abs_path(native_reference_preview_temp_root())
    if not temp_root or not os.path.isdir(temp_root):
        return 0
    removed = 0
    for path in os.listdir(temp_root):
        full = os.path.join(temp_root, path)
        try:
            os.remove(full)
            removed += 1
        except Exception:
            pass
    try:
        shutil.rmtree(temp_root, ignore_errors=True)
    except Exception:
        pass
    return removed


def trim_native_reference_gif_cache():
    stale_keys = [key for key, frames in list(_NATIVE_REFERENCE_GIF_CACHE.items()) if not frames or not all(os.path.isfile(frame) for frame in frames)]
    for key in stale_keys:
        _NATIVE_REFERENCE_GIF_CACHE.pop(key, None)
    if len(_NATIVE_REFERENCE_GIF_CACHE) <= MAX_GIF_CACHE_ENTRIES:
        return
    for key in list(_NATIVE_REFERENCE_GIF_CACHE.keys())[:-MAX_GIF_CACHE_ENTRIES]:
        _NATIVE_REFERENCE_GIF_CACHE.pop(key, None)


def is_native_reference_temp_frame(path):
    try:
        import tempfile

        folder = os.path.join(tempfile.gettempdir(), "go_workflow_reference_gif_frames")
        return normalized_abs_path(path).startswith(normalized_abs_path(folder))
    except Exception:
        return False


def extract_gif_frames_for_native_viewer(path):
    import hashlib
    import subprocess
    import tempfile

    source_path = bpy.path.abspath(path)
    if not source_path.lower().endswith(".gif") or not os.path.isfile(source_path):
        return [source_path]
    try:
        stat = os.stat(source_path)
        cache_key = f"{source_path}|{stat.st_mtime_ns}|{stat.st_size}"
    except Exception:
        cache_key = source_path
    frame_prefix = native_reference_frame_prefix(source_path)
    cached = _NATIVE_REFERENCE_GIF_CACHE.get(cache_key)
    if cached and all(os.path.isfile(frame) for frame in cached):
        return list(cached)
    trim_native_reference_gif_cache()

    digest = hashlib.sha1(cache_key.encode("utf-8", "ignore")).hexdigest()[:16]
    out_dir = os.path.join(tempfile.gettempdir(), "go_workflow_reference_gif_frames", f"{frame_prefix[:-8]}__{digest}")
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        return [source_path]

    existing = sorted(
        os.path.join(out_dir, name)
        for name in os.listdir(out_dir)
        if name.lower().endswith(".png") and name.startswith(frame_prefix)
    )
    if len(existing) > 1:
        _NATIVE_REFERENCE_GIF_CACHE[cache_key] = existing
        return list(existing)

    script = f"""
    $gifPath = {ps_single_quote(source_path)}
    $outDir = {ps_single_quote(out_dir)}
    $prefix = {ps_single_quote(frame_prefix)}
Add-Type -AssemblyName System.Drawing
$img = [System.Drawing.Image]::FromFile($gifPath)
$dim = New-Object System.Drawing.Imaging.FrameDimension($img.FrameDimensionsList[0])
$count = $img.GetFrameCount($dim)
for ($i = 0; $i -lt $count; $i++) {{
    $img.SelectActiveFrame($dim, $i) | Out-Null
    $bmp = New-Object System.Drawing.Bitmap($img.Width, $img.Height)
    $gfx = [System.Drawing.Graphics]::FromImage($bmp)
    $gfx.Clear([System.Drawing.Color]::Transparent)
    $gfx.DrawImage($img, 0, 0, $img.Width, $img.Height)
    $gfx.Dispose()
    $target = Join-Path $outDir ($prefix + $i.ToString("D4") + ".png")
    $bmp.Save($target, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
}}
$img.Dispose()
"""
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=12,
        )
    except Exception:
        traceback.print_exc()
    frames = sorted(
        os.path.join(out_dir, name)
        for name in os.listdir(out_dir)
        if name.lower().endswith(".png") and name.startswith(frame_prefix)
    )
    if len(frames) > 1:
        _NATIVE_REFERENCE_GIF_CACHE[cache_key] = frames
        trim_native_reference_gif_cache()
        return list(frames)
    return [source_path]


def prepare_native_reference_image_window(window, image):
    if window is None or getattr(window, "screen", None) is None:
        return None
    target_area = max(list(window.screen.areas), key=lambda area: area.width * area.height, default=None)
    if target_area is None:
        return None
    try:
        target_area.type = "IMAGE_EDITOR"
        space = target_area.spaces.active
        space.image = image
        try:
            space.show_region_toolbar = False
        except Exception:
            pass
        try:
            space.show_region_ui = False
        except Exception:
            pass
        try:
            space.display_channels = "COLOR"
        except Exception:
            pass
        try:
            image_user = getattr(space, "image_user", None)
            if image_user is not None:
                image_user.frame_start = 1
                image_user.frame_offset = 0
                image_user.frame_current = 1
                image_user.use_auto_refresh = False
                frame_duration = int(getattr(image, "frame_duration", 0) or 0)
                if frame_duration > 0:
                    image_user.frame_duration = frame_duration
        except Exception:
            pass
        return target_area
    except Exception:
        traceback.print_exc()
        return None


def native_reference_window_size_for_image(image):
    return 420, 420


def prepare_native_reference_view3d_window(window):
    if window is None or getattr(window, "screen", None) is None:
        return None
    target_area = max(list(window.screen.areas), key=lambda area: area.width * area.height, default=None)
    if target_area is None:
        return None
    try:
        target_area.type = "VIEW_3D"
        space = target_area.spaces.active
        try:
            space.show_region_toolbar = False
        except Exception:
            pass
        try:
            space.show_region_ui = False
        except Exception:
            pass
        try:
            shading = getattr(space, "shading", None)
            if shading is not None:
                shading.type = "SOLID"
        except Exception:
            pass
        return target_area
    except Exception:
        traceback.print_exc()
        return None


def current_process_top_level_hwnds():
    if os.name != "nt":
        return set()
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnds = set()
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == os.getpid():
                hwnds.add(int(hwnd))
            return True

        user32.EnumWindows(enum_proc(callback), 0)
        return hwnds
    except Exception:
        return set()


def try_configure_reference_window_hwnd(image, hwnd=None):
    if os.name != "nt":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        if not hwnd:
            hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        width, height = native_reference_window_size_for_image(image)
        screen_width = int(user32.GetSystemMetrics(0) or 1920)
        x = max(0, screen_width - width - 40)
        y = 40
        hwnd_topmost = -1
        swp_showwindow = 0x0040
        return bool(user32.SetWindowPos(hwnd, hwnd_topmost, x, y, width, height, swp_showwindow))
    except Exception:
        return False


def cleanup_native_reference_cache(paths):
    temp_root = ""
    try:
        import tempfile

        temp_root = normalized_abs_path(os.path.join(tempfile.gettempdir(), "go_workflow_reference_gif_frames"))
    except Exception:
        temp_root = ""
    normalized_paths = set()
    parent_dirs = set()
    for path in paths or []:
        norm = normalized_abs_path(path)
        if not norm or temp_root and not norm.startswith(temp_root):
            continue
        normalized_paths.add(norm)
        parent_dirs.add(os.path.dirname(norm))
    if not normalized_paths:
        return
    remove_temp_images_by_paths(normalized_paths)
    for image in list(bpy.data.images):
        filepath = normalized_abs_path(getattr(image, "filepath", "") or "")
        if not filepath:
            continue
        if filepath in normalized_paths or any(filepath.startswith(directory + os.sep) or filepath == directory for directory in parent_dirs):
            try:
                image.user_clear()
            except Exception:
                pass
            try:
                clear_image_from_all_editors(image)
            except Exception:
                pass
            try:
                bpy.data.images.remove(image, do_unlink=True)
            except Exception:
                pass
    for key, cached in list(_NATIVE_REFERENCE_GIF_CACHE.items()):
        cached_norm = {normalized_abs_path(path) for path in cached}
        if cached_norm & normalized_paths:
            _NATIVE_REFERENCE_GIF_CACHE.pop(key, None)
    for path in sorted(normalized_paths, key=len, reverse=True):
        try:
            os.remove(path)
        except Exception:
            pass
    for directory in sorted(parent_dirs, key=len, reverse=True):
        try:
            shutil.rmtree(directory, ignore_errors=True)
        except Exception:
            pass


def cleanup_panel_preview_cache():
    temp_root = normalized_abs_path(native_reference_preview_temp_root())
    if not temp_root or not os.path.isdir(temp_root):
        return
    preview_files = set()
    for path in os.listdir(temp_root):
        full = normalized_abs_path(os.path.join(temp_root, path))
        if full.lower().endswith(".png"):
            preview_files.add(full)
    if preview_files:
        remove_temp_images_by_paths(preview_files)
    try:
        shutil.rmtree(temp_root, ignore_errors=True)
    except Exception:
        pass


def cleanup_all_reference_runtime_images():
    removed_before = len(bpy.data.images)
    stop_draw_handler_reference_viewer()
    remove_tagged_temp_images()
    cleanup_panel_preview_cache()
    cleanup_builtin_reference_images()
    cleanup_preview_temp_files()
    cleanup_native_reference_cache(set())
    return max(0, removed_before - len(bpy.data.images))


def _draw_handler_reference_rect(area):
    width = int(getattr(area, "width", 0) or 0)
    height = int(getattr(area, "height", 0) or 0)
    padding = 14
    reserved_bottom = 58
    reserved_top = 28
    usable_width = max(80, width - padding * 2)
    usable_height = max(80, height - reserved_bottom - reserved_top)
    target = min(usable_width, usable_height)
    x0 = int((width - target) * 0.5)
    y0 = reserved_bottom + max(0, int((usable_height - target) * 0.5))
    return x0, y0, target, target


def _draw_handler_target_area():
    wm = getattr(bpy.context, "window_manager", None)
    target_window_ptr = _DRAW_HANDLER_STATE.get("window_ptr")
    target_area_ptr = _DRAW_HANDLER_STATE.get("area_ptr")
    if wm is None or not target_window_ptr or not target_area_ptr:
        return None, None
    for window in wm.windows:
        if window.as_pointer() != target_window_ptr:
            continue
        screen = getattr(window, "screen", None)
        if screen is None:
            return window, None
        for area in screen.areas:
            if area.as_pointer() == target_area_ptr:
                return window, area
        return window, None
    return None, None


def _draw_handler_button_rects(area):
    width = int(getattr(area, "width", 0) or 0)
    button_w = 112
    button_h = 30
    gap = 18
    total = button_w * 2 + gap
    start_x = max(16, int((width - total) / 2))
    y = 14
    prev_rect = (start_x, y, button_w, button_h)
    next_rect = (start_x + button_w + gap, y, button_w, button_h)
    return prev_rect, next_rect


def _draw_handler_cover_coords(area, image):
    x0, y0, rect_width, rect_height = _draw_handler_reference_rect(area)
    image_width = float(max(1, getattr(image, "size", [1, 1])[0] or 1))
    image_height = float(max(1, getattr(image, "size", [1, 1])[1] or 1))
    area_ratio = float(rect_width) / float(rect_height)
    image_ratio = image_width / image_height
    if image_ratio >= area_ratio:
        draw_width = float(rect_width)
        draw_height = draw_width / image_ratio
        draw_x = float(x0)
        draw_y = float(y0) + (float(rect_height) - draw_height) * 0.5
    else:
        draw_height = float(rect_height)
        draw_width = draw_height * image_ratio
        draw_x = float(x0) + (float(rect_width) - draw_width) * 0.5
        draw_y = float(y0)
    return (
        (draw_x, draw_y),
        (draw_x + draw_width, draw_y),
        (draw_x + draw_width, draw_y + draw_height),
        (draw_x, draw_y + draw_height),
    )


def _draw_handler_button(rect, label):
    x, y, width, height = rect
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    coords = ((x, y), (x + width, y), (x + width, y + height), (x, y + height))
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": coords})
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_float("color", (0.94, 0.94, 0.94, 0.86))
    batch.draw(shader)
    gpu.state.blend_set("NONE")
    font_id = 0
    blf.size(font_id, 15.0)
    text_width, text_height = blf.dimensions(font_id, label)
    blf.position(font_id, x + (width - text_width) * 0.5, y + (height - text_height) * 0.5 + 2.0, 0)
    blf.color(font_id, 0.15, 0.15, 0.15, 1.0)
    blf.draw(font_id, label)


def _draw_handler_reference_callback():
    _target_window, area = _draw_handler_target_area()
    image = _DRAW_HANDLER_STATE.get("image")
    if image is None:
        return
    current_area = getattr(bpy.context, "area", None)
    if area is None or current_area is None or getattr(current_area, "type", "") != "VIEW_3D":
        return
    if current_area.as_pointer() != area.as_pointer():
        return
    x0, y0, width, height = _draw_handler_reference_rect(area)
    shader = gpu.shader.from_builtin("IMAGE")
    coords = _draw_handler_cover_coords(area, image)
    uv = ((0, 0), (1, 0), (1, 1), (0, 1))
    batch = batch_for_shader(shader, "TRI_FAN", {"pos": coords, "texCoord": uv})
    texture = gpu.texture.from_image(image)
    gpu.state.blend_set("ALPHA")
    shader.bind()
    shader.uniform_sampler("image", texture)
    batch.draw(shader)
    gpu.state.blend_set("NONE")
    prev_rect, next_rect = _draw_handler_button_rects(area)
    _draw_handler_button(prev_rect, "上一张")
    _draw_handler_button(next_rect, "下一张")
    title = str(_DRAW_HANDLER_STATE.get("title", "") or "").strip()
    if title:
        font_id = 0
        blf.size(font_id, 15.0)
        blf.position(font_id, 18.0, float(max(44, height - 28)), 0)
        blf.color(font_id, 0.98, 0.98, 0.98, 1.0)
        blf.draw(font_id, title)


def stop_draw_handler_reference_viewer():
    wm = getattr(bpy.context, "window_manager", None)
    timer = _DRAW_HANDLER_STATE.get("timer")
    if wm is not None and timer is not None:
        try:
            wm.event_timer_remove(timer)
        except Exception:
            pass
    _DRAW_HANDLER_STATE["timer"] = None
    handle = _DRAW_HANDLER_STATE.get("handle")
    if handle is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
        except Exception:
            pass
    _DRAW_HANDLER_STATE["handle"] = None
    image = _DRAW_HANDLER_STATE.get("image")
    frame_images = list(_DRAW_HANDLER_STATE.get("frame_images") or [])
    loaded_paths = list(_DRAW_HANDLER_STATE.get("image_paths") or [])
    if image is not None:
        clear_image_from_all_editors(image)
        try:
            image.user_clear()
        except Exception:
            pass
        try:
            bpy.data.images.remove(image, do_unlink=True)
        except Exception:
            pass
    for frame_image in frame_images:
        if frame_image is None or frame_image == image:
            continue
        clear_image_from_all_editors(frame_image)
        try:
            frame_image.user_clear()
        except Exception:
            pass
        try:
            bpy.data.images.remove(frame_image, do_unlink=True)
        except Exception:
            pass
    _DRAW_HANDLER_STATE["image"] = None
    _DRAW_HANDLER_STATE["frame_images"] = []
    _DRAW_HANDLER_STATE["image_paths"] = []
    _DRAW_HANDLER_STATE["media_files"] = []
    _DRAW_HANDLER_STATE["media_index"] = 0
    _DRAW_HANDLER_STATE["is_gif"] = False
    _DRAW_HANDLER_STATE["frame_index"] = 0
    _DRAW_HANDLER_STATE["frame_total"] = 1
    _DRAW_HANDLER_STATE["last_tick"] = 0.0
    _DRAW_HANDLER_STATE["window_ptr"] = None
    _DRAW_HANDLER_STATE["area_ptr"] = None
    _DRAW_HANDLER_STATE["title"] = ""
    if loaded_paths:
        remove_tagged_temp_images(loaded_paths)


def cleanup_runtime(scene=None, workflow=None, module=None, module_state=None):
    try:
        stop_draw_handler_reference_viewer()
    except Exception:
        traceback.print_exc()
    try:
        cleanup_all_reference_runtime_images()
    except Exception:
        traceback.print_exc()
    return True


def _load_draw_handler_media(media_files, media_index):
    path = media_files[media_index]
    if str(path or "").lower().endswith(".gif"):
        frame_paths = extract_gif_frames_for_native_viewer(path)
    else:
        frame_paths = [path]
    frame_images = []
    loaded_paths = []
    for frame_path in frame_paths:
        image = reference_image_load(frame_path)
        frame_images.append(image)
        loaded_paths.append(normalized_abs_path(getattr(image, "filepath", "") or frame_path))
    _DRAW_HANDLER_STATE["frame_images"] = frame_images
    _DRAW_HANDLER_STATE["image"] = frame_images[0] if frame_images else None
    _DRAW_HANDLER_STATE["image_paths"] = loaded_paths
    _DRAW_HANDLER_STATE["media_files"] = list(media_files)
    _DRAW_HANDLER_STATE["media_index"] = media_index
    _DRAW_HANDLER_STATE["is_gif"] = len(frame_images) > 1
    _DRAW_HANDLER_STATE["frame_total"] = max(1, len(frame_images))
    _DRAW_HANDLER_STATE["frame_index"] = 0
    _DRAW_HANDLER_STATE["last_tick"] = time.perf_counter()
    return _DRAW_HANDLER_STATE["image"]


def step_draw_handler_media(delta):
    media_files = list(_DRAW_HANDLER_STATE.get("media_files") or [])
    if not media_files:
        return False
    current_index = int(_DRAW_HANDLER_STATE.get("media_index", 0) or 0)
    target_index = (current_index + int(delta)) % len(media_files)
    old_paths = list(_DRAW_HANDLER_STATE.get("image_paths") or [])
    old_images = list(_DRAW_HANDLER_STATE.get("frame_images") or [])
    _load_draw_handler_media(media_files, target_index)
    for frame_image in old_images:
        if frame_image is None:
            continue
        clear_image_from_all_editors(frame_image)
        try:
            frame_image.user_clear()
        except Exception:
            pass
        try:
            bpy.data.images.remove(frame_image, do_unlink=True)
        except Exception:
            pass
    if old_paths:
        remove_tagged_temp_images(old_paths)
    return True


def start_draw_handler_reference_viewer(payload, window=None, area=None):
    stop_draw_handler_reference_viewer()
    media_files = list(payload.get("media_files") or [])
    if not media_files:
        raise Exception("当前没有可用参考图")
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        raise Exception("当前没有可用窗口管理器")
    if window is None:
        window = getattr(bpy.context, "window", None)
    if area is None and window is not None:
        area = prepare_native_reference_view3d_window(window)
    if area is None:
        area = getattr(bpy.context, "area", None)
    if area is None or getattr(area, "type", "") != "VIEW_3D":
        raise Exception("无法创建 Blender 3D 参考窗")
    media_index = max(0, min(int(payload.get("media_index", 0) or 0), len(media_files) - 1))
    image = _load_draw_handler_media(media_files, media_index)
    _DRAW_HANDLER_STATE["window_ptr"] = window.as_pointer() if window is not None else None
    _DRAW_HANDLER_STATE["area_ptr"] = area.as_pointer()
    _DRAW_HANDLER_STATE["title"] = str(payload.get("name_bilingual") or payload.get("shape_key") or payload.get("title") or "").strip()
    _DRAW_HANDLER_STATE["handle"] = bpy.types.SpaceView3D.draw_handler_add(
        _draw_handler_reference_callback, (), "WINDOW", "POST_PIXEL"
    )
    _DRAW_HANDLER_STATE["timer"] = wm.event_timer_add(0.03, window=window)
    area.tag_redraw()
    return image


class BWFLOW_OT_native_reference_viewer(Operator):
    bl_idname = "bworkflow.native_reference_viewer"
    bl_label = "参考预览"
    bl_description = "在 Blender 原生图片窗口中查看当前参考图"

    show_details: BoolProperty(default=True)
    as_popup: BoolProperty(default=False)
    detached_window: BoolProperty(default=False)

    def _payload(self, context):
        scene = getattr(context, "scene", None)
        payload_text = str(scene.get("_go_workflow_native_reference_viewer", "") or "") if scene is not None else ""
        if not payload_text:
            return {}
        try:
            return json.loads(payload_text)
        except Exception:
            return {}

    def invoke(self, context, event):
        payload = self._payload(context)
        if not bool(self.as_popup):
            target_window = getattr(context, "window", None)
            target_area = getattr(context, "area", None)
            if bool(self.detached_window):
                before = {window.as_pointer() for window, _screen in iter_window_screens()}
                before_hwnds = current_process_top_level_hwnds()
                try:
                    bpy.ops.wm.window_new()
                except Exception:
                    traceback.print_exc()
                    return {"CANCELLED"}
                after_hwnds = current_process_top_level_hwnds()
                target_window = None
                for window, _screen in iter_window_screens():
                    if window.as_pointer() not in before:
                        target_window = window
                        break
                if target_window is None:
                    target_window = getattr(context, "window", None)
                target_area = prepare_native_reference_view3d_window(target_window)
                hwnd_candidates = list(after_hwnds - before_hwnds)
                reference_hwnd = hwnd_candidates[-1] if hwnd_candidates else None
                try_configure_reference_window_hwnd(None, reference_hwnd)
            try:
                start_draw_handler_reference_viewer(payload, window=target_window, area=target_area)
            except Exception:
                traceback.print_exc()
                return {"CANCELLED"}
            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        media_files = list(payload.get("media_files") or [])
        if not media_files:
            self.report({"WARNING"}, "当前没有可用参考图")
            return {"CANCELLED"}
        media_index = int(payload.get("media_index", 0) or 0)
        media_index = max(0, min(media_index, len(media_files) - 1))
        path = media_files[media_index]
        is_gif = str(path or "").lower().endswith(".gif")

        before = {window.as_pointer() for window, _screen in iter_window_screens()}
        before_hwnds = current_process_top_level_hwnds()
        try:
            bpy.ops.wm.window_new()
        except Exception:
            traceback.print_exc()
            return {"CANCELLED"}
        after_hwnds = current_process_top_level_hwnds()

        new_window = None
        for window, _screen in iter_window_screens():
            if window.as_pointer() not in before:
                new_window = window
                break
        if new_window is None:
            new_window = getattr(context, "window", None)
        if new_window is None:
            return {"CANCELLED"}

        try:
            image = reference_image_load(path)
        except Exception:
            traceback.print_exc()
            self.report({"ERROR"}, "参考图加载失败")
            return {"CANCELLED"}
        area = prepare_native_reference_image_window(new_window, image)
        if area is None:
            self.report({"ERROR"}, "无法创建 Blender 图片参考窗口")
            return {"CANCELLED"}
        hwnd_candidates = list(after_hwnds - before_hwnds)
        reference_hwnd = hwnd_candidates[-1] if hwnd_candidates else None
        try_configure_reference_window_hwnd(image, reference_hwnd)

        self._window_ptr = new_window.as_pointer()
        self._area_ptr = area.as_pointer()
        self._images = [image]
        self._loaded_paths = [normalized_abs_path(getattr(image, "filepath", "") or path)]
        self._temp_frame_paths = []
        self._is_animated_gif = bool(is_gif)
        self._frame_duration = max(1, int(getattr(image, "frame_duration", 0) or 1))
        self._frame_index = 1
        self._timer = context.window_manager.event_timer_add(0.085 if self._is_animated_gif and self._frame_duration > 1 else 0.35, window=new_window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _runtime_area(self, context):
        wm = getattr(context, "window_manager", None)
        if wm is None:
            return None, None
        for window in wm.windows:
            if window.as_pointer() != getattr(self, "_window_ptr", None):
                continue
            screen = getattr(window, "screen", None)
            if screen is None:
                return window, None
            for area in screen.areas:
                if area.as_pointer() == getattr(self, "_area_ptr", None):
                    return window, area
            return window, None
        return None, None

    def modal(self, context, event):
        if _DRAW_HANDLER_STATE.get("handle") is not None and not bool(self.as_popup):
            if event.type == "ESC":
                stop_draw_handler_reference_viewer()
                return {"FINISHED"}
            _window, area = _draw_handler_target_area()
            if event.type == "LEFTMOUSE" and event.value == "PRESS" and area is not None and getattr(context, "area", None) is not None:
                if context.area.as_pointer() == area.as_pointer():
                    prev_rect, next_rect = _draw_handler_button_rects(area)
                    region_x = float(getattr(event, "mouse_region_x", -1))
                    region_y = float(getattr(event, "mouse_region_y", -1))
                    if region_x < 0 or region_y < 0:
                        region = getattr(context, "region", None)
                        offset_x = float(getattr(region, "x", 0) or 0)
                        offset_y = float(getattr(region, "y", 0) or 0)
                        region_x = float(getattr(event, "mouse_x", -1)) - offset_x
                        region_y = float(getattr(event, "mouse_y", -1)) - offset_y
                    def _hit(rect):
                        rx, ry, rw, rh = rect
                        return rx <= region_x <= (rx + rw) and ry <= region_y <= (ry + rh)
                    if _hit(prev_rect):
                        step_draw_handler_media(-1)
                        area.tag_redraw()
                        return {"RUNNING_MODAL"}
                    if _hit(next_rect):
                        step_draw_handler_media(1)
                        area.tag_redraw()
                        return {"RUNNING_MODAL"}
            if event.type == "TIMER":
                target_window, target_area = _draw_handler_target_area()
                if target_window is None or target_area is None:
                    stop_draw_handler_reference_viewer()
                    return {"FINISHED"}
                frame_images = list(_DRAW_HANDLER_STATE.get("frame_images") or [])
                if len(frame_images) > 1:
                    now = time.perf_counter()
                    interval = max(0.02, float(_DRAW_HANDLER_STATE.get("frame_interval", 0.085) or 0.085))
                    if now - float(_DRAW_HANDLER_STATE.get("last_tick", 0.0) or 0.0) >= interval:
                        frame_index = (int(_DRAW_HANDLER_STATE.get("frame_index", 0) or 0) + 1) % len(frame_images)
                        _DRAW_HANDLER_STATE["frame_index"] = frame_index
                        _DRAW_HANDLER_STATE["image"] = frame_images[frame_index]
                        _DRAW_HANDLER_STATE["last_tick"] = now
                target_area.tag_redraw()
                return {"RUNNING_MODAL"}
            return {"PASS_THROUGH"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        window, area = self._runtime_area(context)
        if window is None or area is None:
            self._finish_timer(context)
            return {"FINISHED"}
        if not getattr(self, "_is_animated_gif", False) or int(getattr(self, "_frame_duration", 1) or 1) <= 1:
            return {"RUNNING_MODAL"}
        try:
            space = area.spaces.active
            image_user = getattr(space, "image_user", None)
            if image_user is None:
                return {"RUNNING_MODAL"}
            next_frame = int(getattr(self, "_frame_index", 1) or 1) + 1
            if next_frame > int(getattr(self, "_frame_duration", 1) or 1):
                next_frame = 1
            image_user.frame_current = next_frame
            self._frame_index = next_frame
            area.tag_redraw()
        except Exception:
            self._finish_timer(context)
            return {"FINISHED"}
        return {"RUNNING_MODAL"}

    def _finish_timer(self, context):
        timer = getattr(self, "_timer", None)
        if timer is not None:
            try:
                context.window_manager.event_timer_remove(timer)
            except Exception:
                pass
        self._timer = None
        window, area = self._runtime_area(context)
        for image in list(getattr(self, "_images", []) or []):
            try:
                if area is not None and getattr(area.spaces.active, "image", None) == image:
                    area.spaces.active.image = None
            except Exception:
                pass
            clear_image_from_all_editors(image)
            try:
                image.user_clear()
            except Exception:
                pass
            try:
                bpy.data.images.remove(image, do_unlink=True)
            except Exception:
                pass
        self._images = []
        remove_tagged_temp_images(getattr(self, "_loaded_paths", []) or [])
        cleanup_panel_preview_cache()

    def draw(self, context):
        self.layout.label(text="参考图已在独立图片窗口中打开", icon="IMAGE_REFERENCE")

    def execute(self, context):
        return {"FINISHED"}


class BWFLOW_OT_native_reference_cleanup(Operator):
    bl_idname = "bworkflow.native_reference_cleanup"
    bl_label = "清理参考图缓存"
    bl_description = "清理 Go Workflow 置顶参考图与面板预览残留的 Blender 图片数据"

    def execute(self, context):
        removed = cleanup_all_reference_runtime_images()
        self.report({"INFO"}, f"已清理参考图残留图片: {removed}")
        return {"FINISHED"}
