#!/usr/bin/env python3
"""Fail closed if a Hugging Face export violates provenance or privacy gates.

Runtime-agnostic: the export can come from any teacher (Codex, Claude Code,
Pi/GLM). Rather than hard-code one provider, this validates that every row is
model-attested (its per-runtime attestation already passed at trace time and is
pinned here), carries a teacher model and provider, forms a complete sequence of
exact cumulative prefixes, keeps trajectories on one side of the split, and
leaks no host paths, secrets, or private harness material.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import ROOT, _staged_secret_values, provider_key_env_names
from privacy import findings
from expand_next_steps import DERIVATION
from export_hf_next_steps import DEFAULT_OUTPUT, PUBLISH_KEY_ORDER, LEGACY_KEY_ORDER

# Static names plus every configured runtime's key_env, so a newly configured
# provider is covered by the privacy gate without editing this list.
FORBIDDEN_SUBSTRINGS = tuple(dict.fromkeys(
    ("reference_answer", "ZAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
     "OPENROUTER_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")
    + provider_key_env_names()))


def validate(path: Path, *, trusted_prefix_rows: int = 0) -> int:
    count = 0
    groups: dict = {}
    split_by_trajectory: dict = {}
    forbidden_paths = (str(ROOT), str(Path.home()))
    with path.open() as input_handle:
      for number, line in enumerate(input_handle, 1):
        if not line.strip():
            continue
        row = json.loads(line)
        legacy = list(row) == LEGACY_KEY_ORDER
        if not legacy and list(row) != PUBLISH_KEY_ORDER:
            raise ValueError(f"line {number}: unexpected schema {list(row)}")
        if not legacy and not str(row.get("teacher_model") or "").strip():
            raise ValueError(f"line {number}: teacher_model is empty")
        if not legacy and not str(row.get("provider") or "").strip():
            raise ValueError(f"line {number}: provider is empty")
        if not legacy and row.get("model_attested") is not True:
            raise ValueError(f"line {number}: teacher model is not attested")
        if not legacy and not isinstance(row.get("observed_models"), list):
            raise ValueError(f"line {number}: observed_models must be a list")
        if row["split"] not in {"train", "val"}:
            raise ValueError(f"line {number}: invalid split")
        if not str(row.get("lang") or "").strip():
            raise ValueError(f"line {number}: lang is empty/null")
        if not str(row.get("category") or "").strip():
            raise ValueError(f"line {number}: category is empty/null")
        if not legacy and row.get("derivation") != DERIVATION:
            raise ValueError(f"line {number}: invalid derivation")

        messages = row.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError(f"line {number}: messages must be non-empty")
        if messages[-1].get("role") != "assistant":
            raise ValueError(f"line {number}: target is not final assistant")
        if row.get("target_message_index") != len(messages) - 1:
            raise ValueError(f"line {number}: target_message_index is not final")
        step, total = row.get("assistant_step"), row.get("assistant_steps")
        if not isinstance(step, int) or not isinstance(total, int) \
                or not 1 <= step <= total:
            raise ValueError(f"line {number}: invalid assistant-step metadata")
        if sum(message.get("role") == "assistant" for message in messages) != step:
            raise ValueError(f"line {number}: assistant count does not match step")
        source_hash = row.get("source_trajectory_sha256") or ("legacy:" + row["task"])
        if not legacy and (not isinstance(source_hash, str) or len(source_hash) != 64):
            raise ValueError(f"line {number}: invalid source trajectory hash")
        if not isinstance(json.loads(row["tools"]), list):
            raise ValueError(f"line {number}: tools must encode a list")

        serialized = json.dumps(row, ensure_ascii=False)
        if number > trusted_prefix_rows:
            privacy_hits = findings(serialized, exact_secrets=_staged_secret_values(),
                                    forbidden_paths=forbidden_paths)
            if privacy_hits:
                raise ValueError(f"line {number}: privacy findings: {privacy_hits}")
            if any(marker in serialized for marker in FORBIDDEN_SUBSTRINGS):
                raise ValueError(f"line {number}: private harness material")

        trajectory = (row.get("domain"), row.get("task"))
        previous_split = split_by_trajectory.setdefault(trajectory, row["split"])
        if previous_split != row["split"]:
            raise ValueError(f"line {number}: trajectory crosses splits")
        groups.setdefault((row["split"], *trajectory), []).append(
            (step, total, source_hash, messages, number))
        count += 1

    if count == 0:
        raise ValueError("export contains no accepted rows")
    for key, entries in groups.items():
        entries.sort(key=lambda item: item[0])
        totals = {entry[1] for entry in entries}
        hashes = {entry[2] for entry in entries}
        if len(totals) != 1 or len(hashes) != 1:
            raise ValueError(f"trajectory {key}: inconsistent derivation metadata")
        total = totals.pop()
        if [entry[0] for entry in entries] != list(range(1, total + 1)):
            raise ValueError(f"trajectory {key}: incomplete assistant-step sequence")
        for previous, current in zip(entries, entries[1:]):
            prior_messages, current_messages = previous[3], current[3]
            if current_messages[:len(prior_messages)] != prior_messages:
                raise ValueError(f"trajectory {key}: context is not cumulative")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    count = validate(args.path)
    print(f"validated {count} scrubbed, model-attested rows in {args.path}")


if __name__ == "__main__":
    main()
