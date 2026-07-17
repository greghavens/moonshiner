#!/usr/bin/env python3
"""Render training rows with the student model's own chat template into
``{"text"}`` JSONL that the local QLoRA trainer consumes directly
(``reasoning_content`` -> ``<think>``, ``tool_calls`` -> the student's native
format).

NO length filtering: every cumulative next-step row is rendered and kept. Token
length percentiles are REPORTED so the training run can pick its ``max_seq`` —
that decision is the user's, not this script's.

The base model and output directory default to ``config.student``. Run inside the
finetune conda env (needs transformers + HF access):
  python3 src/expand_next_steps.py
  conda run -n nemotron-ft python src/prepare_local.py
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from common import CONFIG, DATA


def main() -> None:
    student = CONFIG.get("student", {})
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", default=str(DATA / "next_step"))
    parser.add_argument("--out", default=os.path.expanduser(
        student.get("output_dir", "~/nemotron-super-finetune/data-moonshiner")))
    parser.add_argument("--model", default=student.get(
        "base_model", "unsloth/NVIDIA-Nemotron-3-Super-120B-A12B"))
    args = parser.parse_args()

    from transformers import AutoTokenizer  # deferred: heavy, env-specific
    tokenizer = AutoTokenizer.from_pretrained(args.model, trust_remote_code=True)
    os.makedirs(args.out, exist_ok=True)

    sample = None
    for split in ("train", "val"):
        source = Path(args.data) / f"{split}.jsonl"
        if not source.exists():
            continue
        kept, errors, lengths = [], 0, []
        for line in source.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            try:
                text = tokenizer.apply_chat_template(
                    row["messages"], tools=row.get("tools"), tokenize=False,
                    add_generation_prompt=False)
            except Exception as error:
                errors += 1
                print(f"  RENDER ERROR ({row.get('meta', {}).get('task')}): {error}")
                continue
            lengths.append(len(tokenizer(text, add_special_tokens=False)["input_ids"]))
            kept.append(text)
        with open(Path(args.out) / f"{split}.jsonl", "w") as handle:
            for text in kept:
                handle.write(json.dumps({"text": text}) + "\n")
        lengths.sort()

        def pct(quantile: float) -> int:
            return lengths[int(quantile * (len(lengths) - 1))] if lengths else 0

        print(f"{split}: {len(kept)} rows (errors {errors}) — exact tokens "
              f"p50={pct(.5)} p90={pct(.9)} p99={pct(.99)} max={pct(1.0)} "
              f"(nothing dropped; pick max_seq >= max at train time)")
        if kept and sample is None:
            sample = kept[0]

    if sample:
        print("\n===== RENDERED SAMPLE (first 1500 chars) =====")
        print(sample[:1500])
        print("===== (end sample) =====")
    print("wrote ->", args.out)


if __name__ == "__main__":
    main()
