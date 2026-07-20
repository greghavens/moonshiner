"""Publish only a validated immutable Hugging Face staging directory."""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, urllib.request
from pathlib import Path
from common import CONFIG, DATA, ROOT, _staged_secret_values
from privacy import findings
from validate_hf_export import validate
from hf_sync import ensure_local_dataset

def token():
    value=os.environ.get("HF_TOKEN","").strip()
    if value:return value
    from common import key_file_path,key_persist_path
    runtime={"provider":"huggingface","key_env":"HF_TOKEN",
             "key_file_name":"moonshiner-huggingface-key"}
    for path in (key_file_path(runtime),key_persist_path(runtime)):
        if path.exists() and path.read_text().strip():return path.read_text().strip()
    for path in (Path(os.environ.get("HF_HOME",Path.home()/".cache/huggingface"))/"token",Path.home()/".huggingface/token"):
        if path.exists() and path.read_text().strip():return path.read_text().strip()
    raise RuntimeError("Hugging Face authentication is missing")

def main(argv=None)->int:
    parser=argparse.ArgumentParser(prog="moonshiner publish")
    parser.add_argument("--dataset",default=CONFIG.get("publish",{}).get("hf_dataset")); parser.add_argument("--dir",type=Path,default=DATA/"hf-publish")
    parser.add_argument("--commit-message")
    parser.add_argument("--yes",action="store_true"); args=parser.parse_args(argv)
    if not args.dataset:parser.error("--dataset is required")
    if not args.yes:parser.error("publishing requires --yes")
    traces=args.dir/"traces.jsonl"
    sync = ensure_local_dataset(target=traces)
    if sync.get("dataset") and sync.get("dataset") != args.dataset:
        raise RuntimeError("publish target differs from the local append baseline")
    marker_name=hashlib.sha256(f"{args.dataset}:{traces.name}".encode()).hexdigest()[:16]
    marker=DATA/"hf-sync"/f"{marker_name}.json"
    state=json.loads(marker.read_text()) if marker.is_file() else {}
    trusted_rows=int(state.get("bootstrap_rows") or 0)
    trusted_size=int(state.get("bootstrap_size") or 0)
    if trusted_size:
        digest=hashlib.sha256()
        with traces.open("rb") as handle:
            remaining=trusted_size
            while remaining:
                block=handle.read(min(1024*1024,remaining))
                if not block:raise RuntimeError("local HF file is shorter than trusted baseline")
                digest.update(block);remaining-=len(block)
        if digest.hexdigest()!=state.get("bootstrap_sha256"):
            raise RuntimeError("local HF prefix differs from downloaded append baseline")
    validate(traces,trusted_prefix_rows=trusted_rows)
    # The card is derived from the exact cumulative file being uploaded. Build
    # it on every append batch so counts, percentages, and capability mix never
    # become stale.
    if args.dir == DATA/"hf-publish":
        from export_hf_card import main as render_card
        render_card()
    for path in args.dir.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"upload directory contains prohibited symlink: {path}")
        if path.is_file() and path != traces:
            hits=findings(path.read_text(errors="replace"),exact_secrets=_staged_secret_values(),forbidden_paths=(str(ROOT),str(Path.home())))
            if hits:raise ValueError(f"{path}: privacy findings {hits}")
    private=bool(CONFIG.get("publish",{}).get("private",True))
    request=urllib.request.Request(f"https://huggingface.co/api/datasets/{args.dataset}/settings",
        data=json.dumps({"private":private}).encode(),headers={"Authorization":f"Bearer {token()}","Content-Type":"application/json"},method="PUT")
    with urllib.request.urlopen(request) as response:
        if response.status//100!=2:raise RuntimeError("dataset visibility update failed")
    command=["hf","upload",args.dataset,str(args.dir),".","--repo-type","dataset"]
    if args.commit_message: command.extend(["--commit-message",args.commit_message])
    subprocess.run(command,check=True)
    print(f"published validated dataset -> {args.dataset}");return 0
