"""Publish only a validated immutable Hugging Face staging directory."""
from __future__ import annotations
import argparse, json, os, subprocess, urllib.request
from pathlib import Path
from common import CONFIG, DATA, ROOT, _staged_secret_values
from privacy import findings
from validate_hf_export import validate

def token():
    value=os.environ.get("HF_TOKEN","").strip()
    if value:return value
    for path in (Path(os.environ.get("HF_HOME",Path.home()/".cache/huggingface"))/"token",Path.home()/".huggingface/token"):
        if path.exists() and path.read_text().strip():return path.read_text().strip()
    raise RuntimeError("Hugging Face authentication is missing")

def main(argv=None)->int:
    parser=argparse.ArgumentParser(prog="moonshiner publish")
    parser.add_argument("--dataset",default=CONFIG.get("publish",{}).get("hf_dataset")); parser.add_argument("--dir",type=Path,default=DATA/"hf-publish")
    parser.add_argument("--yes",action="store_true"); args=parser.parse_args(argv)
    if not args.dataset:parser.error("--dataset is required")
    if not args.yes:parser.error("publishing requires --yes")
    traces=args.dir/"traces.jsonl"; validate(traces)
    for path in args.dir.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"upload directory contains prohibited symlink: {path}")
        if path.is_file():
            hits=findings(path.read_text(errors="replace"),exact_secrets=_staged_secret_values(),forbidden_paths=(str(ROOT),str(Path.home())))
            if hits:raise ValueError(f"{path}: privacy findings {hits}")
    private=bool(CONFIG.get("publish",{}).get("private",True))
    request=urllib.request.Request(f"https://huggingface.co/api/datasets/{args.dataset}/settings",
        data=json.dumps({"private":private}).encode(),headers={"Authorization":f"Bearer {token()}","Content-Type":"application/json"},method="PUT")
    with urllib.request.urlopen(request) as response:
        if response.status//100!=2:raise RuntimeError("dataset visibility update failed")
    subprocess.run(["hf","upload",args.dataset,str(args.dir),".","--repo-type","dataset"],check=True)
    print(f"published validated dataset -> {args.dataset}");return 0
