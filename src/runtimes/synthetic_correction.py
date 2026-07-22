"""Normalizer for explicitly labeled synthetic-correction trace artifacts."""
from __future__ import annotations

import json
from pathlib import Path


class SyntheticCorrectionAdapter:
    trace_formats = ("moonshiner-synthetic-correction-v1",)

    @staticmethod
    def parse_stream(path: Path, workspace=None):
        value = json.loads(Path(path).read_text())
        messages, tools = value.get("messages"), value.get("tools")
        if not isinstance(messages, list) or not isinstance(tools, list):
            raise ValueError("invalid synthetic correction artifact")
        return messages, {"tools": tools}

    @staticmethod
    def tool_schemas(messages: list[dict]) -> list[dict]:
        # parse_trace returns the artifact's exact offered schemas separately;
        # callers that only provide messages cannot reconstruct unused tools.
        names = sorted({(call.get("function") or {}).get("name")
                        for message in messages
                        for call in (message.get("tool_calls") or [])
                        if (call.get("function") or {}).get("name")})
        return [{"type": "function", "function": {"name": name,
                 "parameters": {"type": "object"}}} for name in names]
