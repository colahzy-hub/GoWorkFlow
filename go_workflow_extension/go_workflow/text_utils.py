import re


def is_placeholder_ui_text(text, placeholder_texts):
    value = (text or "").strip()
    if not value:
        return False
    compact = re.sub(r"\s+", "", value).casefold()
    return compact in placeholder_texts


def is_suspicious_ui_text(text, placeholder_texts, suspicious_tokens):
    value = (text or "").strip()
    if not value:
        return False
    if is_placeholder_ui_text(value, placeholder_texts):
        return True
    if set(value) == {"?"}:
        return True
    return any(token in value for token in suspicious_tokens)


def normalize_text_value(text, fallback="", placeholder_texts=(), suspicious_tokens=()):
    value = (text or "").strip()
    if not value or is_suspicious_ui_text(value, placeholder_texts, suspicious_tokens):
        return fallback
    return value


def normalize_workflow_name(name, fallback="", placeholder_texts=(), suspicious_tokens=()):
    return normalize_text_value(
        name,
        fallback,
        placeholder_texts=placeholder_texts,
        suspicious_tokens=suspicious_tokens,
    )


def normalize_workflow_description(description, fallback="", placeholder_texts=(), suspicious_tokens=()):
    return normalize_text_value(
        description,
        fallback,
        placeholder_texts=placeholder_texts,
        suspicious_tokens=suspicious_tokens,
    )


def unique_name_from_existing(base_name, existing_names, fallback="", normalize_name_fn=None):
    normalize_name = normalize_name_fn or normalize_workflow_name
    base = normalize_name(base_name, fallback)
    existing = {
        (name or "").strip()
        for name in existing_names
        if (name or "").strip()
    }
    if base not in existing:
        return base

    suffix = 2
    while True:
        candidate = f"{base} ({suffix})"
        if candidate not in existing:
            return candidate
        suffix += 1
