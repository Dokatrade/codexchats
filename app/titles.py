from __future__ import annotations

import re
from pathlib import PurePath
from typing import Any, Mapping


_ROLLOUT_RE = re.compile(
    r"^rollout-(\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})-([0-9a-fA-F-]+)$"
)

_NOISE_PREFIXES = (
    "# AGENTS.md instructions",
    "<environment_context>",
    "<collaboration_mode>",
    "<permissions instructions>",
)


def build_display_title(
    *,
    title: object = None,
    session_key: object = None,
    rel_path: object = None,
    title_candidate: object = None,
    fallback_session_id: object = None,
) -> str:
    raw_title = _as_text(title)
    raw_session_key = _as_text(session_key)
    raw_rel_path = _as_text(rel_path)

    if raw_title and not _looks_technical_title(raw_title):
        return _clip_text(_normalize_ws(raw_title))

    candidate = _clean_prompt_candidate(_as_text(title_candidate))
    if candidate:
        return candidate

    for value in (raw_title, raw_session_key, _stem_from_path(raw_rel_path)):
        pretty = _prettify_rollout_name(value)
        if pretty:
            return pretty

    for value in (raw_title, raw_session_key):
        if value:
            return _clip_text(_normalize_ws(value))

    if fallback_session_id not in (None, ""):
        return f"Session {fallback_session_id}"
    return "Session"


def enrich_session_row(row: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["display_title"] = build_display_title(
        title=data.get("title"),
        session_key=data.get("session_key"),
        rel_path=data.get("rel_path"),
        title_candidate=data.get("title_candidate"),
        fallback_session_id=data.get("id"),
    )
    return data


def enrich_search_result_row(row: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["display_title"] = build_display_title(
        title=data.get("title") or data.get("session_title"),
        session_key=data.get("session_key"),
        rel_path=data.get("rel_path"),
        title_candidate=data.get("title_candidate"),
        fallback_session_id=data.get("session_id"),
    )
    return data


def _as_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_ws(text: str) -> str:
    return " ".join(text.split())


def _clip_text(text: str, limit: int = 110) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _clean_prompt_candidate(value: str | None) -> str | None:
    if not value:
        return None
    text = _normalize_ws(value)
    if not text:
        return None
    if any(text.startswith(prefix) for prefix in _NOISE_PREFIXES):
        return None
    if text.startswith("```"):
        return None

    # Strip markdown heading marker from prompts like "# Title".
    text = re.sub(r"^#{1,6}\s+", "", text)
    if any(text.startswith(prefix) for prefix in _NOISE_PREFIXES):
        return None

    if _looks_technical_title(text):
        return None
    if len(text) < 4:
        return None

    return _clip_text(text)


def _stem_from_path(path_value: str | None) -> str | None:
    if not path_value:
        return None
    try:
        name = PurePath(path_value).name
    except Exception:  # noqa: BLE001
        name = path_value.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if name.lower().endswith(".jsonl"):
        return name[:-6]
    return name or None


def _looks_technical_title(value: str) -> bool:
    text = _normalize_ws(value)
    if not text:
        return True
    if text.lower().endswith(".jsonl"):
        text = text[:-6]
    stem = _stem_from_path(text) or text
    if _ROLLOUT_RE.match(stem):
        return True
    return False


def _prettify_rollout_name(value: str | None) -> str | None:
    if not value:
        return None
    stem = _stem_from_path(value) or value
    match = _ROLLOUT_RE.match(stem)
    if not match:
        return None
    year, month, day, hour, minute, _second, tail = match.groups()
    short_id = tail.split("-", 1)[0][:8]
    return f"{year}-{month}-{day} {hour}:{minute} | rollout | {short_id}"
