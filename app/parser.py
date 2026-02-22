from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PARSER_VERSION = "0.4"


@dataclass(slots=True)
class ParsedMessage:
    msg_index: int
    role: str
    message_kind: str
    content: str
    created_at: str | None
    raw_json: str | None


@dataclass(slots=True)
class ParsedSession:
    session_key: str
    title: str | None
    started_at: str | None
    updated_at: str | None
    raw_meta_json: str | None
    messages: list[ParsedMessage]


def parse_session_file(file_path: str | Path) -> ParsedSession:
    path = Path(file_path)
    raw = path.read_text(encoding="utf-8", errors="replace")
    raw = raw.strip()
    if not raw:
        return ParsedSession(
            session_key=path.stem,
            title=path.stem,
            started_at=None,
            updated_at=None,
            raw_meta_json=None,
            messages=[],
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _parse_jsonl_fallback(path, raw)

    if isinstance(data, dict):
        return _parse_dict_session(path, data)
    if isinstance(data, list):
        return _parse_list_session(path, data)
    return ParsedSession(
        session_key=path.stem,
        title=path.stem,
        started_at=None,
        updated_at=None,
        raw_meta_json=json.dumps({"scalar": data}, ensure_ascii=False),
        messages=[],
    )


def _parse_jsonl_fallback(path: Path, raw: str) -> ParsedSession:
    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)

    messages = _extract_messages_from_sequence(events)
    meta = {"format": "jsonl", "events_count": len(events)}
    return ParsedSession(
        session_key=_first_non_empty(*[str(v) for v in _pluck_many(events, ("session_id", "conversation_id", "id"))], path.stem),
        title=path.stem,
        started_at=_first_non_empty(*[v for v in _pluck_many(events, ("created_at", "timestamp", "time")) if isinstance(v, str)]),
        updated_at=_last_non_empty(*[v for v in _pluck_many(events, ("updated_at", "timestamp", "time")) if isinstance(v, str)]),
        raw_meta_json=json.dumps(meta, ensure_ascii=False),
        messages=messages,
    )


def _parse_dict_session(path: Path, data: dict[str, Any]) -> ParsedSession:
    seq = _pick_sequence(data, ("messages", "items", "events", "entries"))
    messages = _extract_messages_from_sequence(seq)
    if not messages and "conversation" in data and isinstance(data["conversation"], dict):
        nested = data["conversation"]
        seq = _pick_sequence(nested, ("messages", "items", "events"))
        messages = _extract_messages_from_sequence(seq)

    session_key = (
        _as_str(data.get("session_id"))
        or _as_str(data.get("conversation_id"))
        or _as_str(data.get("id"))
        or path.stem
    )
    title = _as_str(data.get("title")) or _as_str(data.get("name")) or path.stem
    started_at = _as_str(data.get("started_at")) or _as_str(data.get("created_at"))
    updated_at = _as_str(data.get("updated_at")) or _as_str(data.get("last_updated_at"))

    meta = {k: v for k, v in data.items() if k not in {"messages", "items", "events", "entries", "conversation"}}
    raw_meta = json.dumps(meta, ensure_ascii=False) if meta else None
    return ParsedSession(
        session_key=session_key,
        title=title,
        started_at=started_at,
        updated_at=updated_at,
        raw_meta_json=raw_meta,
        messages=messages,
    )


def _parse_list_session(path: Path, data: list[Any]) -> ParsedSession:
    seq = [item for item in data if isinstance(item, dict)]
    messages = _extract_messages_from_sequence(seq)
    return ParsedSession(
        session_key=path.stem,
        title=path.stem,
        started_at=None,
        updated_at=None,
        raw_meta_json=json.dumps({"format": "list", "items_count": len(data)}, ensure_ascii=False),
        messages=messages,
    )


def _pick_sequence(obj: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    for key in keys:
        value = obj.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _extract_messages_from_sequence(seq: list[dict[str, Any]]) -> list[ParsedMessage]:
    result: list[ParsedMessage] = []
    for idx, item in enumerate(seq):
        role = _detect_role(item)
        content = _detect_content(item)
        if not content:
            continue
        created_at = _detect_timestamp(item)
        message_kind = _detect_message_kind(item, role)
        result.append(
            ParsedMessage(
                msg_index=len(result),
                role=role or "unknown",
                message_kind=message_kind,
                content=content,
                created_at=created_at,
                raw_json=_safe_json(item),
            )
        )
    return result


def _detect_role(item: dict[str, Any]) -> str | None:
    candidates = [
        item.get("role"),
        item.get("author_role"),
        item.get("speaker"),
        _nested(item, "payload", "role"),
        _nested(item, "author", "role"),
        _nested(item, "message", "role"),
        _nested(item, "sender", "role"),
        item.get("type"),
        _nested(item, "payload", "type"),
    ]
    for value in candidates:
        text = _as_str(value)
        if text:
            lowered = text.lower()
            if lowered in {"user", "assistant", "system", "tool"}:
                return lowered
            if lowered == "developer":
                return "system"
            if lowered == "agent_reasoning":
                # Codex progress/reasoning events in session logs.
                return "assistant"
            if lowered.startswith("user"):
                return "user"
            if lowered.startswith("assistant"):
                return "assistant"
            if lowered.startswith("system"):
                return "system"
    return None


def _detect_content(item: dict[str, Any]) -> str:
    direct = [
        item.get("content"),
        item.get("text"),
        item.get("message"),
        _nested(item, "message", "content"),
        _nested(item, "delta", "content"),
        _nested(item, "payload", "content"),
        _nested(item, "payload", "text"),
    ]
    for candidate in direct:
        extracted = _flatten_content(candidate)
        if extracted:
            return extracted

    # Some event formats store content parts under "parts" / "segments".
    for key in ("parts", "segments", "content_parts"):
        if key in item:
            extracted = _flatten_content(item.get(key))
            if extracted:
                return extracted

    return ""


def _detect_timestamp(item: dict[str, Any]) -> str | None:
    for key in ("created_at", "timestamp", "time", "ts", "updated_at"):
        value = item.get(key)
        text = _as_str(value)
        if text:
            return text
    return None


def _detect_message_kind(item: dict[str, Any], role: str | None) -> str:
    top_type = (_as_str(item.get("type")) or "").lower()
    payload_type = (_as_str(_nested(item, "payload", "type")) or "").lower()
    normalized_role = (role or "").lower()

    if payload_type == "agent_reasoning":
        return "assistant_reasoning"
    if normalized_role == "assistant":
        if payload_type == "message" or top_type == "response_item":
            return "assistant_final"
        return "assistant_service"
    if normalized_role == "user":
        return "user"
    if normalized_role == "system":
        return "system"
    if normalized_role == "tool":
        return "tool"
    return "unknown"


def _flatten_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_flatten_content(v) for v in value]
        joined = "\n".join(p for p in parts if p)
        return joined.strip()
    if isinstance(value, dict):
        for key in ("text", "content", "value"):
            nested = _flatten_content(value.get(key))
            if nested:
                return nested
        if "parts" in value:
            return _flatten_content(value["parts"])
        return ""
    return ""


def _safe_json(value: Any) -> str | None:
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return None


def _nested(obj: dict[str, Any], *keys: str) -> Any:
    current: Any = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s or None
    if isinstance(value, (int, float)):
        return str(value)
    return None


def _pluck_many(seq: list[dict[str, Any]], keys: tuple[str, ...]) -> list[Any]:
    values: list[Any] = []
    for item in seq:
        for key in keys:
            if key in item:
                values.append(item.get(key))
    return values


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _last_non_empty(*values: str | None) -> str | None:
    for value in reversed(values):
        if value:
            return value
    return None
