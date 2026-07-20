"""Versioned, immutable seed-corpus distribution management."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tarfile
import tempfile
import urllib.request
from collections import defaultdict
from pathlib import Path

from common import ROOT, SEEDS_DIR, STORAGE_ROOT

CORPORA = STORAGE_ROOT / "corpora"
RELEASES_API = "https://api.github.com/repos/greghavens/moonshiner/releases"

PROGRAM_DESCRIPTIONS = {
    "Building": "Implement complete libraries, services, CLIs, workflows, and systems from specifications.",
    "Debugging": "Diagnose failures, repair defects, resolve compiler/runtime issues, and preserve regressions.",
    "Tool calling": "Select tools, construct grounded arguments, run independent calls together, and stage dependent calls.",
    "Instruction following": "Honor constraints, formats, corrections, state, context, memory, relevance, and abstention.",
    "Project & integration": "Coordinate multi-file, repository-scale, migration, and integration work.",
    "Feature development": "Extend working systems while preserving existing behavior.",
    "Clarification": "Recognize missing information, ask only when required, and never invent parameters.",
    "Error recovery": "Recover from tool failures, partial results, retries, and idempotency hazards.",
    "Refactoring & performance": "Restructure safely and improve measured performance without behavior drift.",
    "Seed authoring": "Create and independently validate deterministic training tasks.",
    "Other verified work": "Verified work not yet assigned to one of the primary programs.",
}

PROGRAM_PRIORITY = [
    "Instruction following", "Tool calling", "Error recovery", "Clarification",
    "Building", "Debugging", "Project & integration", "Feature development",
    "Refactoring & performance", "Seed authoring", "Other verified work",
]


def program_for_category(category: str, *, tool_use: bool = False) -> str:
    """Map precise recipe taxonomy to one stable, human-facing program."""
    category = str(category or "uncategorized")
    if tool_use:
        if category in {"dependency-planning", "parallel-same", "parallel-mixed",
                        "tool-selection", "missing-function"}:
            return "Tool calling"
        if category == "missing-parameter":
            return "Clarification"
        if category == "error-recovery":
            return "Error recovery"
        return "Instruction following"
    prefix = category.replace("/", "-").split("-", 1)[0]
    if prefix == "build": return "Building"
    if prefix in {"debug", "warnfix", "compilefix", "syntax", "escape"}: return "Debugging"
    if prefix in {"project", "full"}: return "Project & integration"
    if prefix == "feature": return "Feature development"
    if prefix in {"refactor", "perf"}: return "Refactoring & performance"
    if prefix == "seed": return "Seed authoring"
    return "Other verified work"


def catalog(seed_dir: Path = SEEDS_DIR) -> tuple[str, dict]:
    """Build the human recipe book and its machine-readable twin."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for task_path in sorted(seed_dir.glob("*/task.json")):
        task = json.loads(task_path.read_text())
        prompt = " ".join(str(task.get("prompt") or "").split())
        summary = prompt[:197].rstrip() + ("…" if len(prompt) > 197 else "")
        item = {"id": task["id"], "kind": "coding_repair",
                "language": task.get("lang") or "unknown",
                "category": task.get("category") or "uncategorized",
                "training_tags": task.get("training_tags") or task.get("tags") or [],
                "summary": summary, "verify_command": task.get("verify_cmd")}
        item["program"] = program_for_category(item["category"])
        groups[item["category"]].append(item)
    behavior_dir = seed_dir.parent / "behavior-seeds"
    for task_path in sorted(behavior_dir.glob("behavior-*.json")):
        task = json.loads(task_path.read_text())
        prompt = " ".join(str(task.get("prompt") or "").split())
        summary = prompt[:197].rstrip() + ("…" if len(prompt) > 197 else "")
        item = {"id": task["id"], "kind": "tool_behavior",
                "language": "English", "world": task.get("world"),
                "category": task.get("category") or "uncategorized",
                "training_tags": task.get("training_tags") or [],
                "summary": summary, "verify_command": None}
        item["program"] = program_for_category(item["category"], tool_use=True)
        groups[item["category"]].append(item)
    programs: dict[str, dict] = {}
    for items in groups.values():
        for item in items:
            entry = programs.setdefault(item["program"], {
                "description": PROGRAM_DESCRIPTIONS[item["program"]],
                "seed_count": 0, "categories": set()})
            entry["seed_count"] += 1
            entry["categories"].add(item["category"])
    priority = {name: index for index, name in enumerate(PROGRAM_PRIORITY)}
    programs = {name: {**programs[name], "priority": priority.get(name, 1_000_000),
                       "categories": sorted(programs[name]["categories"])}
                for name in sorted(programs, key=lambda item: priority.get(item, 1_000_000))}
    data = {"name": "Moonshiner Seed Recipe Book",
            "seed_count": sum(map(len, groups.values())),
            "programs": programs,
            "categories": {name: groups[name] for name in sorted(groups)}}
    lines = ["# Moonshiner Seed Recipe Book", "",
             f"{data['seed_count']} seeds grouped into {len(groups)} categories. "
             "This file is generated; edit each seed's source, then regenerate it.", "",
             "## High-level overview", "",
             "| Training program | Seeds | What it trains |",
             "| --- | ---: | --- |"]
    for name, value in sorted(programs.items(), key=lambda pair: pair[1]["priority"]):
        lines.append(f"| **{name}** | {value['seed_count']:,} | {value['description']} |")
    lines += ["", "## Detailed recipe categories", ""]
    for category, items in data["categories"].items():
        lines += [f"## {category}", ""]
        for item in items:
            tags = " " + " ".join(f"`#{tag}`" for tag in item["training_tags"]) if item["training_tags"] else ""
            world = f", `{item['world']}`" if item.get("world") else ""
            language = f" (`{item['language']}`{world})" if item.get("language") else world
            lines.append(f"- **{item['id']}**{language}{tags} — {item['summary']}")
        lines.append("")
    return "\n".join(lines), data


def write_catalog(seed_dir: Path = SEEDS_DIR, directory: Path | None = None) -> tuple[Path, Path]:
    directory = directory or (ROOT if seed_dir == ROOT / "tasks" / "seeds"
                              else seed_dir.parent.parent)
    directory.mkdir(parents=True, exist_ok=True)
    markdown, data = catalog(seed_dir)
    markdown_path, json_path = directory / "SEED_CATALOG.md", directory / "SEED_CATALOG.json"
    markdown_path.write_text(markdown)
    json_path.write_text(json.dumps(data, indent=2) + "\n")
    return markdown_path, json_path


def manifest(seed_dir: Path = SEEDS_DIR, *, version: str | None = None) -> dict:
    version_path = ROOT / "corpus-version.json"
    header = json.loads(version_path.read_text()) if version_path.exists() else {
        "name": "moonshiner-seeds", "version": "development",
        "schema_version": 1, "minimum_moonshiner": "0.1.0"}
    if version:
        header["version"] = version
    entries = []
    for task in sorted(seed_dir.glob("*/task.json")):
        directory = task.parent
        spec = json.loads(task.read_text())
        files = {}
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError(f"corpus symlink prohibited: {path}")
            if path.is_file():
                files[path.relative_to(directory).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        digest = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        entries.append({"id": directory.name, "lang": spec.get("lang"),
                        "category": spec.get("category"), "fingerprint": digest,
                        "files": files})
    behavior_entries = []
    for path in sorted((seed_dir.parent / "behavior-seeds").glob("behavior-*.json")):
        spec = json.loads(path.read_text())
        behavior_entries.append({"id": spec["id"], "category": spec.get("category"),
                                 "world": spec.get("world"),
                                 "fingerprint": hashlib.sha256(path.read_bytes()).hexdigest(),
                                 "file": path.name})
    worlds = seed_dir.parent / "behavior-worlds.json"
    return {**header, "seed_count": len(entries) + len(behavior_entries),
            "coding_seed_count": len(entries),
            "behavior_seed_count": len(behavior_entries), "seeds": entries,
            "behavior_seeds": behavior_entries,
            "behavior_worlds_sha256": (hashlib.sha256(worlds.read_bytes()).hexdigest()
                                        if worlds.is_file() else None)}


def verify(directory: Path, expected: dict) -> None:
    actual = manifest(directory, version=expected.get("version"))
    wanted = [(x["id"], x["fingerprint"]) for x in expected["seeds"]]
    got = [(x["id"], x["fingerprint"]) for x in actual["seeds"]]
    if got != wanted:
        raise ValueError("seed corpus does not match its manifest")
    wanted_behavior = [(x["id"], x["fingerprint"])
                       for x in expected.get("behavior_seeds", [])]
    got_behavior = [(x["id"], x["fingerprint"])
                    for x in actual.get("behavior_seeds", [])]
    if got_behavior != wanted_behavior:
        raise ValueError("behavior seed corpus does not match its manifest")
    if actual.get("behavior_worlds_sha256") != expected.get("behavior_worlds_sha256"):
        raise ValueError("behavior world registry does not match its manifest")


def _safe_extract(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:*") as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if destination.resolve() not in target.parents and target != destination.resolve():
                raise ValueError(f"unsafe archive path: {member.name}")
            if member.issym() or member.islnk():
                raise ValueError("corpus archives may not contain links")
        tar.extractall(destination, filter="data")


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "moonshiner/0.1"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)


def _expected_digest(source: str, checksum: str | None, temp: Path) -> str:
    if checksum:
        return checksum.lower().removeprefix("sha256:")
    sums = temp / "SHA256SUMS"
    if source.startswith(("https://", "http://")):
        _download(source.rsplit("/", 1)[0] + "/SHA256SUMS", sums)
    else:
        candidate = Path(source).expanduser().parent / "SHA256SUMS"
        if not candidate.is_file():
            raise ValueError("local corpus install requires --sha256 or adjacent SHA256SUMS")
        shutil.copyfile(candidate, sums)
    archive_name = source.rsplit("/", 1)[-1]
    for line in sums.read_text().splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[-1].lstrip("*") == archive_name:
            return fields[0].lower()
    raise ValueError(f"no checksum for {archive_name} in SHA256SUMS")


def _versions() -> list[str]:
    request = urllib.request.Request(RELEASES_API, headers={"User-Agent": "moonshiner/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        releases = json.load(response)
    return [r["tag_name"].removeprefix("seeds-") for r in releases
            if r.get("tag_name", "").startswith("seeds-")]


def _install(version: str, source: str | None, checksum: str | None) -> None:
    source = source or ("https://github.com/greghavens/moonshiner/releases/download/"
                        f"seeds-{version}/moonshiner-seeds-{version}.tar.gz")
    with tempfile.TemporaryDirectory(prefix="moonshiner-seeds-") as temp_name:
        temp = Path(temp_name)
        archive = temp / source.rsplit("/", 1)[-1]
        _download(source, archive) if source.startswith(("https://", "http://")) else \
            shutil.copyfile(Path(source).expanduser(), archive)
        expected = _expected_digest(source, checksum, temp)
        actual = hashlib.sha256(archive.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"corpus checksum mismatch: expected {expected}, got {actual}")
        unpack = temp / "unpack"; unpack.mkdir(); _safe_extract(archive, unpack)
        manifest_path = next((p for p in unpack.rglob("MANIFEST.json")), None)
        if manifest_path is None:
            raise ValueError("corpus archive has no MANIFEST.json")
        corpus_root = manifest_path.parent
        expected_manifest = json.loads(manifest_path.read_text())
        if expected_manifest.get("version") != version:
            raise ValueError("corpus manifest version does not match requested version")
        verify(corpus_root / "tasks" / "seeds", expected_manifest)
        destination = CORPORA / "official" / version
        if destination.exists():
            raise ValueError(f"corpus version already installed: {version}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(corpus_root, destination)
        active = CORPORA / "active"
        if active.exists():
            shutil.rmtree(active)
        shutil.copytree(destination, active)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="moonshiner seeds")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("status"); sub.add_parser("verify"); sub.add_parser("list")
    cat = sub.add_parser("catalog"); cat.add_argument("--output", type=Path); cat.add_argument("--json", action="store_true")
    cat.add_argument("--category", action="append"); cat.add_argument("--tag", action="append")
    cat.add_argument("--name", help="Match seed ID, prompt summary, or tag")
    man = sub.add_parser("manifest"); man.add_argument("--output", type=Path); man.add_argument("--version")
    for action in ("install", "update"):
        command = sub.add_parser(action)
        command.add_argument("version", nargs="?" if action == "update" else None)
        command.add_argument("--source"); command.add_argument("--sha256")
    args = parser.parse_args(argv)
    if args.action == "status":
        data = manifest(); print(f"{data['name']} {data['version']} ({data['seed_count']} seeds) at {SEEDS_DIR}"); return 0
    if args.action == "manifest":
        data = manifest(version=args.version); output = json.dumps(data, indent=2) + "\n"
        if args.output: args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(output)
        else: print(output, end="")
        return 0
    if args.action == "catalog":
        markdown, data = catalog()
        categories = set(args.category or []); tags = set(args.tag or [])
        needle=(args.name or "").casefold(); filtered={}
        for category,items in data["categories"].items():
            kept=[item for item in items
                  if (not categories or category in categories)
                  and (not tags or tags <= set(item.get("training_tags") or []))
                  and (not needle or any(needle in str(value).casefold() for value in
                      (item["id"],item.get("summary","")," ".join(item.get("training_tags") or []))))]
            if kept: filtered[category]=kept
        data={**data,"seed_count":sum(map(len,filtered.values())),"categories":filtered}
        lines=["# Moonshiner Seed Recipe Book","",f"{data['seed_count']} matching seeds.",""]
        for category,items in filtered.items():
            lines += [f"## {category}",""]+[f"- **{x['id']}** — {x['summary']}" for x in items]+[""]
        markdown="\n".join(lines)
        output = json.dumps(data, indent=2) + "\n" if args.json else markdown
        if args.output: args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(output)
        else: print(output, end="")
        return 0
    if args.action == "verify":
        path = SEEDS_DIR.parent.parent / "MANIFEST.json"
        if not path.exists():
            print(f"development corpus: {manifest()['seed_count']} structurally readable seeds"); return 0
        verify(SEEDS_DIR, json.loads(path.read_text())); print("seed corpus verified"); return 0
    versions = _versions() if args.action in {"list", "update"} and not getattr(args, "version", None) else []
    if args.action == "list":
        print("\n".join(versions)); return 0
    version = args.version or (versions[0] if versions else None)
    if not version:
        raise ValueError("no seed corpus release is available")
    _install(version, args.source, args.sha256)
    print(f"installed moonshiner-seeds {version}")
    return 0
