def drawer_count_for_entries(entries, unique_panel_ids_fn):
    drawer_ids = []
    for entry in entries or []:
        panel_id = entry.get("panel_id", "") if isinstance(entry, dict) else getattr(entry, "panel_id", "")
        if panel_id:
            drawer_ids.append(panel_id)
    return len(unique_panel_ids_fn(drawer_ids))


def panel_count_label(count):
    count = max(0, int(count or 0))
    return f"{count} 个抽屉"
