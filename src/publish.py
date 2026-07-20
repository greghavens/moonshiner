"""Publish only a validated immutable Hugging Face staging directory."""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess, urllib.parse, urllib.request
from pathlib import Path
from common import CONFIG, DATA, ROOT, _staged_secret_values
from privacy import findings
from validate_hf_export import validate
from hf_sync import ensure_local_dataset


def publication_files(directory: Path) -> list[Path]:
    """Return only the dataset artifacts intentionally published to the Hub."""
    return [path for path in (directory / "traces.jsonl", directory / "README.md",
                              directory / "moonshiner-dataset-banner.png")
            if path.is_file()]


def _verify_remote_card(dataset: str, card: Path, auth_token: str) -> None:
    """Require the Hub's current README bytes to match the rendered card."""
    expected = card.read_bytes()
    digest = hashlib.sha256(expected).hexdigest()
    repo = urllib.parse.quote(dataset, safe="/")
    url = (f"https://huggingface.co/datasets/{repo}/resolve/main/README.md"
           f"?download=true&moonshiner_sha256={digest}")
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {auth_token}",
                 "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(request) as response:
        actual = response.read()
    if actual != expected:
        raise RuntimeError("published Hugging Face dataset card failed remote verification")

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
    for path in publication_files(args.dir):
        if path.is_symlink():
            raise ValueError(f"upload artifact is a prohibited symlink: {path}")
        if path.suffix != ".png" and path != traces:
            hits=findings(path.read_text(errors="replace"),exact_secrets=_staged_secret_values(),forbidden_paths=(str(ROOT),str(Path.home())))
            if hits:raise ValueError(f"{path}: privacy findings {hits}")
    private=bool(CONFIG.get("publish",{}).get("private",True))
    auth_token = token()
    request=urllib.request.Request(f"https://huggingface.co/api/datasets/{args.dataset}/settings",
        data=json.dumps({"private":private}).encode(),headers={"Authorization":f"Bearer {auth_token}","Content-Type":"application/json"},method="PUT")
    with urllib.request.urlopen(request) as response:
        if response.status//100!=2:raise RuntimeError("dataset visibility update failed")
    for path in publication_files(args.dir):
        if path.name == "README.md":
            continue
        command=["hf","upload",args.dataset,str(path),path.name,"--repo-type","dataset"]
        if args.commit_message: command.extend(["--commit-message",args.commit_message])
        subprocess.run(command,check=True)
    # Folder uploads use a local resumable-transfer cache. A regenerated card
    # must never be skipped because that cache remembers an older upload, so
    # commit README.md explicitly and then compare the live Hub bytes.
    card = args.dir / "README.md"
    if card.is_file():
        card_command = ["hf", "upload", args.dataset, str(card), "README.md",
                        "--repo-type", "dataset"]
        if args.commit_message:
            card_command.extend(["--commit-message", args.commit_message])
        subprocess.run(card_command, check=True)
        _verify_remote_card(args.dataset, card, auth_token)
    print(f"published validated dataset -> {args.dataset}");return 0
