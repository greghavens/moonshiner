"""Normalizer for explicitly labeled synthetic-correction trace artifacts."""
from __future__ import annotations

import json
from pathlib import Path


class SyntheticCorrectionAdapter:
    trace_formats = ("moonshiner-synthetic-correction-v1",)

    @staticmethod
    def parse_stream(path: Path, workspace=None):
        value = json.loads(Path(path).read_text())
        messages = value.get("messages")
        if not isinstance(messages, list):
            raise ValueError("invalid synthetic correction artifact")
        return messages, {}
