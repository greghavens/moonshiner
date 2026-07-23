"""Analyze, compose, audit, and prepare privacy-scrubbed training datasets."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import random
import re
import unicodedata
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from common import CONFIG, DATA, _staged_secret_values
from privacy import findings, sanitize_object

ANALYSIS_VERSION = 1
DEFAULT_CONTEXTS = (8192, 16384, 32768, 65536)


def _runtime_versions() -> dict:
    packages = {}
    for package in ("moonshiner", "axolotl", "transformers", "datasets"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    return packages


class TokenCounter:
    """Exact tokenizer accounting when requested, documented estimate otherwise."""

    def __init__(self, tokenizer: str | None = None):
        self.name = tokenizer or "moonshiner-estimate-v1"
        self.exact = bool(tokenizer)
        self._tokenizer = None
        if tokenizer:
            try:
                from transformers import AutoTokenizer
            except ImportError as error:
                raise RuntimeError("exact token accounting requires moonshiner[trainers]") from error
            self._tokenizer = AutoTokenizer.from_pretrained(tokenizer)

    def text(self, value) -> int:
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if self._tokenizer is not None:
            return len(self._tokenizer(value, add_special_tokens=False)["input_ids"])
        return max(1, math.ceil(len(value) / 3.3))

    def message(self, message: dict) -> int:
        # Account for role/framing and every train-visible message property.
        return 4 + self.text(message.get("role", "")) + self.text(message.get("content", "")) + sum(
            self.text(message.get(key)) for key in ("reasoning_content", "tool_calls", "name", "tool_call_id")
            if message.get(key) not in (None, "", [])
        )


def _local_rows(path: Path):
    if path.suffix == ".jsonl":
        with path.open() as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)
    else:
        value = json.loads(path.read_text())
        yield from (value if isinstance(value, list) else [value])


def _remote_rows(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "moonshiner/0.4"})
    with urllib.request.urlopen(request, timeout=120) as response:
        if urllib.parse.urlparse(url).path.endswith(".jsonl"):
            for raw_line in response:
                if raw_line.strip():
                    yield json.loads(raw_line.decode("utf-8"))
        else:
            value = json.load(response)
            yield from (value if isinstance(value, list) else [value])


def _dataset_server_rows(dataset_id: str, split: str):
    offset, page_size = 0, 100
    while True:
        query = urllib.parse.urlencode({"dataset": dataset_id, "config": "default",
                                        "split": split, "offset": offset,
                                        "length": page_size})
        request = urllib.request.Request(
            f"https://datasets-server.huggingface.co/rows?{query}",
            headers={"User-Agent": "moonshiner/0.4"})
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.load(response)
        rows = payload.get("rows") or []
        for item in rows:
            yield item.get("row", item)
        offset += len(rows)
        if not rows or offset >= int(payload.get("num_rows_total", offset)):
            break


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize(row: dict, source: str, index: int, *, privacy_mode: str = "block") -> dict:
    messages = row.get("messages") or row.get("conversations")
    if not messages and row.get("instruction") is not None:
        prompt = str(row["instruction"]) + (f"\n{row['input']}" if row.get("input") else "")
        messages = [{"role": "user", "content": prompt},
                    {"role": "assistant", "content": str(row.get("output", ""))}]
    if not isinstance(messages, list) or not messages:
        raise ValueError(f"{source} row {index}: no conversation messages")
    normalized = []
    aliases = {"human": "user", "gpt": "assistant", "model": "assistant"}
    for message in messages:
        role = aliases.get(message.get("role", message.get("from")),
                           message.get("role", message.get("from")))
        content = message.get("content", message.get("value"))
        if role == "assistant" and content is None and message.get("tool_calls"):
            content = ""
        if role not in {"system", "user", "assistant", "tool"} or content is None:
            raise ValueError(f"{source} row {index}: invalid message")
        kept = {"role": role, "content": content}
        for key in ("tool_calls", "reasoning_content", "name", "tool_call_id"):
            if key in message:
                kept[key] = message[key]
        normalized.append(kept)
    original_meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    meta = dict(original_meta)
    meta.update({
        "source": source,
        "source_row": index,
        "name": row.get("task") or row.get("name") or row.get("id")
                or original_meta.get("task") or original_meta.get("name"),
        "category": row.get("category") or original_meta.get("category"),
    })
    tags = row.get("tags", original_meta.get("tags", original_meta.get("training_tags", [])))
    if isinstance(tags, str):
        tags = [tags]
    meta["tags"] = tags if isinstance(tags, list) else []
    for key in ("source_trajectory_id", "assistant_step", "assistant_steps", "lang", "domain"):
        if row.get(key) is not None:
            meta[key] = row[key]
    result = sanitize_object({"messages": normalized, "meta": meta})
    serialized = json.dumps(result, ensure_ascii=False)
    hits = findings(serialized, exact_secrets=_staged_secret_values())
    if hits and privacy_mode == "block":
        raise ValueError(f"{source} row {index}: privacy findings {hits}")
    if hits:
        result["meta"]["privacy_findings"] = hits
    return result


def _source_record(spec: str) -> dict:
    if spec.startswith(("https://huggingface.co/datasets/", "hf-file:")):
        return {"spec": spec, "kind": "huggingface-file", "url": _hf_file_url(spec)}
    if spec.startswith("hf:"):
        reference = spec[3:]
        dataset_id, pinned = reference.split("@", 1)
        revision, _, split = pinned.partition("#")
        return {"spec": spec, "kind": "huggingface", "dataset": dataset_id,
                "revision": revision, "split": split or "train"}
    path = Path(spec.removeprefix("local:")).expanduser().resolve()
    digest = _file_sha256(path)
    return {"spec": spec, "kind": "local", "path": str(path), "sha256": digest}


def _hf_file_url(spec: str) -> str:
    if spec.startswith("https://huggingface.co/datasets/"):
        return spec.replace("/blob/", "/resolve/", 1)
    reference = spec.removeprefix("hf-file:")
    if "@" not in reference or "/" not in reference.split("@", 1)[1]:
        raise ValueError("HF files must use hf-file:owner/dataset@revision/path.jsonl")
    dataset_id, pinned = reference.split("@", 1)
    revision, filename = pinned.split("/", 1)
    owner_repo = dataset_id.split("/", 1)
    if len(owner_repo) != 2 or not all((revision, filename)):
        raise ValueError("invalid Hugging Face file reference")
    return ("https://huggingface.co/datasets/"
            + urllib.parse.quote(dataset_id, safe="/") + "/resolve/"
            + urllib.parse.quote(revision, safe="") + "/"
            + urllib.parse.quote(filename, safe="/"))


def load_source(spec: str, *, privacy_mode: str = "block"):
    if spec.startswith(("https://huggingface.co/datasets/", "hf-file:")):
        return [_normalize(row, spec, i, privacy_mode=privacy_mode)
                for i, row in enumerate(_remote_rows(_hf_file_url(spec)))]
    if spec.startswith("hf:"):
        reference = spec[3:]
        if "@" not in reference:
            raise ValueError("Hugging Face sources must pin a revision: hf:owner/name@revision[#split]")
        dataset_id, pinned = reference.split("@", 1)
        revision, _, split = pinned.partition("#")
        if not dataset_id or not revision:
            raise ValueError("invalid Hugging Face source; expected hf:owner/name@revision[#split]")
        try:
            from datasets import load_dataset
        except ImportError:
            if revision not in {"main", "refs/heads/main"}:
                raise RuntimeError(
                    "revision-pinned dataset splits require moonshiner[huggingface]; "
                    "a direct hf-file: reference works with the standard install")
            rows = _dataset_server_rows(dataset_id, split or "train")
            return [_normalize(dict(row), spec, i, privacy_mode=privacy_mode)
                    for i, row in enumerate(rows)]
        dataset = load_dataset(dataset_id, split=split or "train", revision=revision)
        return [_normalize(dict(row), spec, i, privacy_mode=privacy_mode)
                for i, row in enumerate(dataset)]
    path = Path(spec.removeprefix("local:")).expanduser()
    return [_normalize(row, spec, i, privacy_mode=privacy_mode)
            for i, row in enumerate(_local_rows(path))]


def _matches(value, patterns) -> bool:
    return any(fnmatch.fnmatchcase(str(value or ""), pattern) for pattern in patterns)


def _selected(row, filters) -> bool:
    meta = row["meta"]
    name, category, tags = meta.get("name"), meta.get("category"), meta.get("tags") or []
    include_name, exclude_name, include_category, exclude_category, include_tag, exclude_tag = filters
    if include_name and not _matches(name, include_name): return False
    if include_category and not _matches(category, include_category): return False
    if include_tag and not any(_matches(tag, include_tag) for tag in tags): return False
    if exclude_name and _matches(name, exclude_name): return False
    if exclude_category and _matches(category, exclude_category): return False
    if exclude_tag and any(_matches(tag, exclude_tag) for tag in tags): return False
    return True


def _trajectory_id(row: dict) -> str:
    meta = row["meta"]
    identity = meta.get("source_trajectory_id") or meta.get("task") or meta.get("name")
    if identity:
        return f"{meta.get('source')}:{identity}"
    return f"{meta.get('source')}:{meta.get('source_row')}"


def _tool_calls(row: dict) -> list[dict]:
    return [call for message in row["messages"] if message["role"] == "assistant"
            for call in message.get("tool_calls") or []]


def row_metrics(row: dict, counter: TokenCounter) -> dict:
    messages = row["messages"]
    assistants = [m for m in messages if m["role"] == "assistant"]
    target = assistants[-1] if assistants else None
    message_tokens = [counter.message(message) for message in messages]
    tools_tokens = counter.text(row.get("tools") or [])
    calls = _tool_calls(row)
    reasoning_tokens = sum(counter.text(m.get("reasoning_content", ""))
                           for m in assistants if m.get("reasoning_content"))
    return {
        "total_tokens": sum(message_tokens) + tools_tokens,
        "target_tokens": counter.message(target) if target else 0,
        "prompt_tokens": sum(message_tokens) - (counter.message(target) if target else 0) + tools_tokens,
        "reasoning_tokens": reasoning_tokens,
        "assistant_turns": len(assistants),
        "tool_calls": len(calls),
        "parallel": any(len(m.get("tool_calls") or []) > 1 for m in assistants),
        "has_target": bool(target and (target.get("content") or target.get("tool_calls"))),
    }


def _percentiles(values: list[int]) -> dict:
    if not values:
        return {key: 0 for key in ("min", "p50", "p90", "p95", "p99", "max")}
    values = sorted(values)
    def pick(frac):
        return values[min(len(values) - 1, max(0, math.ceil(frac * len(values)) - 1))]
    return {"min": values[0], "p50": pick(.50), "p90": pick(.90),
            "p95": pick(.95), "p99": pick(.99), "max": values[-1]}


def _mix(rows: list[dict], metrics: list[dict], key) -> dict:
    totals = {"rows": len(rows),
              "target_tokens": sum(m["target_tokens"] for m in metrics),
              "total_tokens": sum(m["total_tokens"] for m in metrics)}
    groups = defaultdict(lambda: {"rows": 0, "target_tokens": 0,
                                  "total_tokens": 0, "trajectories": set()})
    all_trajectories = {_trajectory_id(row) for row in rows}
    for row, metric in zip(rows, metrics):
        values = key(row)
        if not isinstance(values, (list, tuple, set)):
            values = [values]
        for value in values or ["unlabeled"]:
            name = str(value or "unlabeled")
            group = groups[name]
            group["rows"] += 1
            group["target_tokens"] += metric["target_tokens"]
            group["total_tokens"] += metric["total_tokens"]
            group["trajectories"].add(_trajectory_id(row))
    result = {}
    for name, group in groups.items():
        trajectory_count = len(group.pop("trajectories"))
        result[name] = {"trajectories": trajectory_count, **group,
                        "shares": {
                            "trajectories": trajectory_count / len(all_trajectories) if all_trajectories else 0,
                            "rows": group["rows"] / totals["rows"] if totals["rows"] else 0,
                            "target_tokens": group["target_tokens"] / totals["target_tokens"] if totals["target_tokens"] else 0,
                            "total_tokens": group["total_tokens"] / totals["total_tokens"] if totals["total_tokens"] else 0}}
    return dict(sorted(result.items(), key=lambda item: (-item[1]["target_tokens"], item[0])))


def analyze_rows(rows: list[dict], counter: TokenCounter) -> dict:
    metrics = [row_metrics(row, counter) for row in rows]
    trajectory_rows = Counter(_trajectory_id(row) for row in rows)
    behavior = Counter()
    privacy = Counter(finding for row in rows
                      for finding in row["meta"].get("privacy_findings", []))
    for metric in metrics:
        behavior["multi-turn" if metric["assistant_turns"] > 1 else "single-turn"] += 1
        behavior["parallel-tool-calls" if metric["parallel"] else
                 "sequential-tool-calls" if metric["tool_calls"] else "direct-response"] += 1
    return {
        "analysis_version": ANALYSIS_VERSION,
        "tokenizer": {"name": counter.name, "exact": counter.exact},
        "summary": {
            "trajectories": len(trajectory_rows), "rows": len(rows),
            "target_tokens": sum(m["target_tokens"] for m in metrics),
            "total_tokens": sum(m["total_tokens"] for m in metrics),
            "reasoning_tokens": sum(m["reasoning_tokens"] for m in metrics),
            "assistant_turns": sum(m["assistant_turns"] for m in metrics),
            "tool_calls": sum(m["tool_calls"] for m in metrics),
            "privacy_findings": sum(privacy.values()),
        },
        "lengths": {
            "total_tokens": _percentiles([m["total_tokens"] for m in metrics]),
            "target_tokens": _percentiles([m["target_tokens"] for m in metrics]),
            "rows_per_trajectory": _percentiles(list(trajectory_rows.values())),
        },
        "behavior_rows": dict(sorted(behavior.items())),
        "privacy_findings": dict(sorted(privacy.items())),
        "mix": {
            "category": _mix(rows, metrics, lambda row: row["meta"].get("category")),
            "tag": _mix(rows, metrics, lambda row: row["meta"].get("tags") or []),
            "source": _mix(rows, metrics, lambda row: row["meta"].get("source")),
        },
    }


def analyze_sources(sources: list[str], tokenizer: str | None = None) -> dict:
    rows = [row for source in sources for row in load_source(source, privacy_mode="report")]
    result = analyze_rows(rows, TokenCounter(tokenizer))
    result["sources"] = [_source_record(source) for source in sources]
    return result


def _parse_rules(values: list[str], label: str) -> list[tuple[str, float]]:
    rules = []
    for value in values:
        pattern, separator, raw_weight = value.rpartition("=")
        if not separator or not pattern:
            raise ValueError(f"{label} weights must use PATTERN=WEIGHT")
        weight = float(raw_weight)
        if weight <= 0:
            raise ValueError(f"{label} weights must be positive")
        rules.append((pattern, weight))
    return rules


def _rule_weight(value, rules, *, multi=False) -> float:
    if not rules:
        return 1.0
    values = value if multi and isinstance(value, list) else [value]
    matched = [weight for pattern, weight in rules
               if any(fnmatch.fnmatchcase(str(item or ""), pattern) for item in values)]
    return sum(matched) if matched else 1.0


def _deduplicate(rows: list[dict]) -> tuple[list[dict], int]:
    seen, unique = set(), []
    for row in rows:
        digest = hashlib.sha256(json.dumps(row["messages"], sort_keys=True,
                                           ensure_ascii=False).encode()).hexdigest()
        if digest in seen:
            continue
        seen.add(digest)
        row["meta"]["content_sha256"] = digest
        unique.append(row)
    return unique, len(rows) - len(unique)


def compose(sources, weights, output: Path, seed: int, filters=None, *,
            target_tokens: int | None = None, tokenizer: str | None = None,
            category_weights: list[str] | None = None,
            tag_weights: list[str] | None = None,
            source_weights: list[str] | None = None,
            weight_unit: str = "target_tokens", dry_run: bool = False,
            context_length: int | None = None, sample_packing: bool = False) -> dict:
    filters = filters or ([], [], [], [], [], [])
    if target_tokens is not None and target_tokens <= 0:
        raise ValueError("--target-tokens must be positive")
    if weights and len(weights) != len(sources):
        raise ValueError("one --weight is required per source")
    if any(weight <= 0 for weight in weights):
        raise ValueError("all source weights must be positive")
    category_rules = _parse_rules(category_weights or [], "category")
    tag_rules = _parse_rules(tag_weights or [], "tag")
    source_rules = _parse_rules(source_weights or [], "source")
    positional_source_weights = dict(zip(sources, weights or [1.0] * len(sources)))
    rng = random.Random(seed)
    pools = []
    for spec in sources:
        pools.append([row for row in load_source(spec) if _selected(row, filters)])
    loaded = [row for pool in pools for row in pool]
    if target_tokens is None and (category_rules or tag_rules or source_rules):
        raise ValueError("category, tag, and source-pattern weights require --target-tokens")
    if target_tokens is None:
        effective = weights or [1.0] * len(pools)
        quota = max((len(pool) / weight for pool, weight in zip(pools, effective)), default=0)
        candidates = []
        for pool, weight in zip(pools, effective):
            rng.shuffle(pool)
            candidates.extend(pool[:min(len(pool), round(quota * weight))])
    else:
        candidates = loaded
    rows, duplicates_removed = _deduplicate(candidates)
    counter = TokenCounter(tokenizer)
    metrics_by_id = {id(row): row_metrics(row, counter) for row in rows}
    weighted = []
    for row in rows:
        meta, metric = row["meta"], metrics_by_id[id(row)]
        priority = positional_source_weights.get(meta.get("source"), 1.0)
        priority *= _rule_weight(meta.get("source"), source_rules)
        priority *= _rule_weight(meta.get("category"), category_rules)
        priority *= _rule_weight(meta.get("tags") or [], tag_rules, multi=True)
        units = metric.get(weight_unit, 1) if weight_unit in metric else 1
        item_weight = max(priority / max(1, units), 1e-12)
        weighted.append((rng.random() ** (1.0 / item_weight), row))
    weighted.sort(key=lambda item: item[0], reverse=True)
    selected, used_target_tokens = [], 0
    for _, row in weighted:
        tokens = metrics_by_id[id(row)]["target_tokens"]
        if target_tokens is not None and selected and used_target_tokens + tokens > target_tokens:
            continue
        selected.append(row)
        used_target_tokens += tokens
        if target_tokens is not None and used_target_tokens >= target_tokens:
            break
    rng.shuffle(selected)
    analysis = analyze_rows(selected, counter)
    filter_names = ("include_name", "exclude_name", "include_category",
                    "exclude_category", "include_tag", "exclude_tag")
    manifest = {
        "manifest_version": 2,
        "sources": [_source_record(source) for source in sources],
        "seed": seed,
        "requested": {
            "source_weights": positional_source_weights,
            "source_weight_rules": source_rules,
            "category_weights": category_rules,
            "tag_weights": tag_rules,
            "weight_unit": weight_unit,
            "target_tokens": target_tokens,
            "context_length": context_length,
            "sample_packing": sample_packing,
        },
        "filters": dict(zip(filter_names, filters)),
        "input_rows": len(loaded), "duplicates_removed": duplicates_removed,
        "rows": len(selected), "analysis": analysis,
        "dry_run": dry_run,
    }
    if not dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in selected))
        manifest["output"] = str(output.resolve())
        manifest["sha256"] = _file_sha256(output)
        output.with_suffix(output.suffix + ".manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n")
    return manifest


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.casefold())


def _prompt(row: dict) -> str:
    return "\n".join(str(message.get("content") or "") for message in row["messages"]
                     if message["role"] in {"system", "user"})


def _simhash(words: list[str]) -> int:
    vector = [0] * 64
    shingles = words if len(words) < 3 else [" ".join(words[i:i + 3]) for i in range(len(words) - 2)]
    for shingle in shingles:
        digest = int.from_bytes(hashlib.sha256(shingle.encode()).digest()[:8], "big")
        for bit in range(64):
            vector[bit] += 1 if digest & (1 << bit) else -1
    return sum((1 << bit) for bit, value in enumerate(vector) if value >= 0)


def _near_duplicate_pairs(rows: list[dict]) -> int:
    buckets, candidates = defaultdict(list), set()
    signatures = []
    for index, row in enumerate(rows):
        words = _words(_prompt(row))
        signature = _simhash(words)
        signatures.append((signature, set(words)))
        for chunk in range(4):
            key = (chunk, (signature >> (chunk * 16)) & 0xffff)
            for prior in buckets[key]:
                candidates.add((prior, index))
            buckets[key].append(index)
    count = 0
    for left, right in candidates:
        left_hash, left_words = signatures[left]
        right_hash, right_words = signatures[right]
        union = left_words | right_words
        if (left_hash ^ right_hash).bit_count() <= 6 and union and len(left_words & right_words) / len(union) >= .85:
            count += 1
    return count


def _mixed_scripts(text: str) -> bool:
    counts = Counter()
    for character in text:
        if not character.isalpha():
            continue
        name = unicodedata.name(character, "")
        for script in ("LATIN", "CJK", "HIRAGANA", "KATAKANA", "CYRILLIC", "ARABIC", "HEBREW"):
            if script in name:
                counts[script] += 1
                break
    return sum(1 for count in counts.values() if count >= 10) > 1


def _repetitive_reasoning(row: dict) -> bool:
    text = " ".join(str(message.get("reasoning_content") or "")
                    for message in row["messages"] if message["role"] == "assistant")
    words = _words(text)
    if len(words) < 80:
        return False
    grams = [tuple(words[i:i + 4]) for i in range(len(words) - 3)]
    return 1 - len(set(grams)) / len(grams) >= .25


def _malformed_tools(row: dict) -> bool:
    known, saw_call = set(), False
    for message in row["messages"]:
        if message["role"] == "assistant":
            for call in message.get("tool_calls") or []:
                saw_call = True
                if not isinstance(call, dict) or not (call.get("function") or {}).get("name"):
                    return True
                if call.get("id"):
                    known.add(call["id"])
        elif message["role"] == "tool":
            if not saw_call:
                return True
            if message.get("tool_call_id") and message["tool_call_id"] not in known:
                return True
    return False


def readiness_rows(rows: list[dict], counter: TokenCounter, *,
                   context_lengths=DEFAULT_CONTEXTS, packing=False,
                   low_share=.01) -> dict:
    analysis = analyze_rows(rows, counter)
    metrics = [row_metrics(row, counter) for row in rows]
    prompts = Counter(prompt for row in rows if (prompt := " ".join(_words(_prompt(row)))))
    truncation = {}
    for context in sorted(set(context_lengths)):
        over = [metric for metric in metrics if metric["total_tokens"] > context]
        truncation[str(context)] = {
            "rows": len(over), "share": len(over) / len(rows) if rows else 0,
            "target_truncated": len(over),
            "target_fully_removed": sum(1 for metric in over if metric["prompt_tokens"] >= context),
        }
    low_categories = [name for name, value in analysis["mix"]["category"].items()
                      if value["shares"]["target_tokens"] < low_share]
    issues = {
        "missing_or_empty_final_answer": sum(not metric["has_target"] for metric in metrics),
        "exact_duplicate_prompts": sum(count - 1 for count in prompts.values() if count > 1),
        "near_duplicate_prompt_pairs": _near_duplicate_pairs(rows),
        "language_mixing_heuristic": sum(_mixed_scripts(" ".join(
            str(message.get("content") or "") for message in row["messages"])) for row in rows),
        "repetitive_reasoning_heuristic": sum(_repetitive_reasoning(row) for row in rows),
        "malformed_tool_sequences": sum(_malformed_tools(row) for row in rows),
        "privacy_findings": analysis["privacy_findings"],
        "categories_below_target_token_share": low_categories,
        "cumulative_prefix_trajectories": sum(1 for value in Counter(
            _trajectory_id(row) for row in rows).values() if value > 1),
    }
    advisories = []
    if packing:
        advisories.append("sample packing is enabled; validate it experimentally because long distilled traces can regress when packed")
    if issues["cumulative_prefix_trajectories"]:
        advisories.append("cumulative prefixes repeat earlier context; supervise only the final assistant target in each row")
    if not counter.exact:
        advisories.append("token counts are estimates; pass --tokenizer for exact training-token accounting")
    return {"readiness_version": 1, "advisory_only": True,
            "analysis": analysis, "truncation": truncation,
            "issues": issues, "advisories": advisories}


def _sources_argument(parser):
    parser.add_argument("--source", "--input", dest="sources", action="append",
                        help="local path, HF dataset split, or direct HF file")
    parser.add_argument("--tokenizer")


def _resolved_sources(sources: list[str] | None) -> list[str]:
    if sources:
        return sources
    filename = str((CONFIG.get("publish") or {}).get("filename") or "traces.jsonl")
    configured = DATA / "hf-publish" / filename
    if configured.is_file():
        return [str(configured)]
    raise FileNotFoundError(
        f"configured local dataset not found at {configured}; pass --source PATH, "
        "hf:owner/dataset@revision#split, or hf-file:owner/dataset@revision/path.jsonl")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="moonshiner dataset")
    sub = parser.add_subparsers(dest="action", required=True)

    analyze = sub.add_parser("analyze")
    _sources_argument(analyze)
    analyze.add_argument("--out", type=Path)

    comp = sub.add_parser("compose")
    _sources_argument(comp)
    comp.add_argument("--weight", action="append", type=float, default=[])
    comp.add_argument("--weight-source", action="append", default=[], metavar="GLOB=WEIGHT")
    comp.add_argument("--weight-category", action="append", default=[], metavar="GLOB=WEIGHT")
    comp.add_argument("--weight-tag", action="append", default=[], metavar="GLOB=WEIGHT")
    comp.add_argument("--weight-unit", choices=["rows", "target_tokens", "total_tokens"],
                      default="target_tokens")
    comp.add_argument("--target-tokens", type=int)
    comp.add_argument("--seed", type=int, default=42)
    comp.add_argument("--out", type=Path, default=DATA / "composed" / "train.jsonl")
    comp.add_argument("--dry-run", action="store_true")
    comp.add_argument("--context-length", type=int)
    comp.add_argument("--sample-packing", action="store_true")
    for name in ("name", "category", "tag"):
        comp.add_argument(f"--include-{name}", action="append", default=[], metavar="GLOB")
        comp.add_argument(f"--exclude-{name}", action="append", default=[], metavar="GLOB")

    ready = sub.add_parser("readiness")
    _sources_argument(ready)
    ready.add_argument("--context-length", action="append", type=int, default=[])
    ready.add_argument("--sample-packing", action="store_true")
    ready.add_argument("--low-share", type=float, default=.01)
    ready.add_argument("--out", type=Path)

    prep = sub.add_parser("prepare")
    prep.add_argument("--trainer", choices=["axolotl"], required=True)
    prep.add_argument("--input", type=Path, required=True)
    prep.add_argument("--model", required=True)
    prep.add_argument("--tokenizer")
    prep.add_argument("--sequence-len", type=int, default=32768)
    prep.add_argument("--sample-packing", action="store_true")
    prep.add_argument("--out", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.action == "analyze":
        result = analyze_sources(_resolved_sources(args.sources), args.tokenizer)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2)); return 0
    if args.action == "compose":
        filters = (args.include_name, args.exclude_name, args.include_category,
                   args.exclude_category, args.include_tag, args.exclude_tag)
        result = compose(_resolved_sources(args.sources), args.weight, args.out, args.seed, filters,
                         target_tokens=args.target_tokens, tokenizer=args.tokenizer,
                         category_weights=args.weight_category, tag_weights=args.weight_tag,
                         source_weights=args.weight_source, weight_unit=args.weight_unit,
                         dry_run=args.dry_run, context_length=args.context_length,
                         sample_packing=args.sample_packing)
        print(json.dumps(result, indent=2)); return 0
    if args.action == "readiness":
        rows = [row for source in _resolved_sources(args.sources)
                for row in load_source(source, privacy_mode="report")]
        result = readiness_rows(rows, TokenCounter(args.tokenizer),
                                context_lengths=args.context_length or DEFAULT_CONTEXTS,
                                packing=args.sample_packing, low_share=args.low_share)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2)); return 0

    source_spec = str(args.input)
    rows = load_source(source_spec)
    counter = TokenCounter(args.tokenizer)
    readiness = readiness_rows(rows, counter, context_lengths=[args.sequence_len],
                               packing=args.sample_packing)
    config = {
        "base_model": args.model,
        "chat_template": "tokenizer_default",
        "sequence_len": args.sequence_len,
        "datasets": [{"path": str(args.input.resolve()), "type": "chat_template",
                      "field_messages": "messages", "roles_to_train": ["assistant"]}],
        "dataset_prepared_path": str((args.out.parent / "prepared").resolve()),
        "sample_packing": args.sample_packing,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(config, indent=2) + "\n")
    config_sha = hashlib.sha256(args.out.read_bytes()).hexdigest()
    recipe = {
        "manifest_version": 2, "trainer": "axolotl", "model": args.model,
        "tokenizer": {"name": counter.name, "exact": counter.exact},
        "input": {"path": str(args.input.resolve()), "sha256": _file_sha256(args.input)},
        "configuration": config, "configuration_sha256": config_sha,
        "trainer_command": ["axolotl", "train", str(args.out.resolve())],
        "runtime_versions": _runtime_versions(), "readiness": readiness,
    }
    args.out.with_suffix(args.out.suffix + ".manifest.json").write_text(
        json.dumps(recipe, indent=2) + "\n")
    print(f"wrote Axolotl configuration: {args.out}")
    print(f"wrote recipe manifest: {args.out.with_suffix(args.out.suffix + '.manifest.json')}")
    return 0
