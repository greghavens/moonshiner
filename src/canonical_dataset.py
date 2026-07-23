"""Canonical published row primitives shared by every model and source."""
from __future__ import annotations

import json
from functools import lru_cache

PUBLISH_KEY_ORDER = [
    "task", "source_trajectory_id", "source_trajectory_sha256", "lang",
    "category", "domain", "verifier", "split", "teacher_runtime", "teacher_model",
    "reasoning_effort", "provider", "observed_models", "model_attested",
    "trace_format", "tools_used", "derivation", "assistant_step",
    "assistant_steps", "target_message_index", "original_n_messages",
    "n_messages", "messages", "tools"]

MESSAGE_KEY_ORDER = [
    "role", "content", "reasoning_content", "tool_calls", "tool_call_id", "name"]


@lru_cache(maxsize=1)
def catalog_categories() -> dict[str, str]:
    """Return the catalog's one task-to-category mapping."""
    from common import ROOT, SEEDS_DIR
    candidates = [
        SEEDS_DIR.parents[1] / "SEED_CATALOG.json",
        ROOT / "SEED_CATALOG.json",
    ]
    for path in candidates:
        try:
            value = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        return {
            str(item["id"]): str(category)
            for category, items in (value.get("categories") or {}).items()
            for item in items if item.get("id")
        }
    return {}


def canonical_category(task: str | None, supplied: str | None) -> str | None:
    return catalog_categories().get(str(task), supplied)


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _reasoning(message: dict) -> str:
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value
    details = message.get("reasoning_details")
    if isinstance(details, list):
        return "".join(
            str(item.get("text") or "")
            for item in details if isinstance(item, dict))
    return ""


def _tool_call(call: dict) -> dict:
    function = call.get("function") if isinstance(call.get("function"), dict) else {}
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        arguments = json.dumps(
            arguments or {}, ensure_ascii=False, separators=(",", ":"))
    return {
        "id": _text(call.get("id")),
        "type": _text(call.get("type") or "function"),
        "function": {
            "name": _text(function.get("name")),
            "arguments": arguments,
        },
    }


def normalize_messages(messages: list[dict]) -> list[dict]:
    """Map every source message into one ordered training representation."""
    normalized = []
    for source in messages:
        calls = source.get("tool_calls")
        normalized.append({
            "role": _text(source.get("role")),
            "content": _text(source.get("content")),
            "reasoning_content": _reasoning(source),
            "tool_calls": [_tool_call(call) for call in (calls or [])
                           if isinstance(call, dict)],
            "tool_call_id": _text(source.get("tool_call_id")),
            "name": _text(source.get("name")),
        })
    return normalized


def normalize_public_row(row: dict) -> dict:
    """Normalize a historical/current row without selecting by model or source."""
    values = dict(row)
    values["category"] = canonical_category(
        values.get("task"), values.get("category"))
    values["messages"] = normalize_messages(values.get("messages") or [])
    values["n_messages"] = len(values["messages"])
    tools = values.get("tools")
    if not isinstance(tools, str):
        tools = json.dumps(tools or [], ensure_ascii=False, separators=(",", ":"))
    values["tools"] = tools
    return {key: values.get(key) for key in PUBLISH_KEY_ORDER}
