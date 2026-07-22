"""One model profile shared by setup, runtimes, provenance, and publishing."""
from __future__ import annotations

import re


def display_name(model_id: str) -> str:
    tail = (model_id or "").rstrip("/").rsplit("/", 1)[-1]
    words = [word for word in re.split(r"[-_]+", tail) if word]
    return " ".join(word.upper() if len(word) <= 3 or word.isdigit()
                    else word.capitalize() for word in words) or model_id


def build(model_id: str, *, display: str | None = None,
          aliases: list[str] | None = None,
          banner_source: str | None = None) -> dict:
    if not model_id.strip():
        raise ValueError("model ID cannot be empty")
    return {
        "id": model_id.strip(),
        "display_name": (display or display_name(model_id)).strip(),
        "attestation_aliases": sorted({value.strip() for value in aliases or []
                                       if value.strip()}),
        "banner_source": banner_source or "assets/moonshiner-dataset-banner.png",
    }


def from_config(config: dict, role: str = "teacher") -> dict:
    model_id = str((config.get(role) or {}).get("model") or "")
    configured = config.get("model_profile") or {}
    if configured.get("id") == model_id:
        return build(model_id,
                     display=configured.get("display_name"),
                     aliases=configured.get("attestation_aliases") or [],
                     banner_source=configured.get("banner_source"))
    return build(model_id)


def matches(expected: str, observed: str | None,
            aliases: list[str] | None = None) -> bool:
    if not observed:
        return False
    allowed = {expected.casefold(), *(value.casefold() for value in aliases or [])}
    return observed.casefold() in allowed
