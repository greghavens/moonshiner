"""Compose privacy-audited local/HF datasets and emit trainer configurations."""
from __future__ import annotations
import argparse, fnmatch, hashlib, json, random
from pathlib import Path
from common import DATA, _staged_secret_values
from privacy import findings, sanitize_object

def _local_rows(path: Path):
    if path.suffix==".jsonl":
        for line in path.read_text().splitlines():
            if line.strip(): yield json.loads(line)
    else:
        value=json.loads(path.read_text()); yield from (value if isinstance(value,list) else [value])

def _normalize(row: dict, source: str, index: int) -> dict:
    messages=row.get("messages") or row.get("conversations")
    if not messages and row.get("instruction") is not None:
        prompt=str(row["instruction"])+(f"\n{row['input']}" if row.get("input") else "")
        messages=[{"role":"user","content":prompt},{"role":"assistant","content":str(row.get("output",""))}]
    if not isinstance(messages,list) or not messages: raise ValueError(f"{source} row {index}: no conversation messages")
    normalized=[]
    aliases={"human":"user","gpt":"assistant","model":"assistant"}
    for message in messages:
        role=message.get("role",message.get("from")); content=message.get("content",message.get("value"))
        role=aliases.get(role,role)
        if role not in {"system","user","assistant","tool"} or content is None:
            raise ValueError(f"{source} row {index}: invalid message")
        normalized.append({"role":role,"content":content,**({"tool_calls":message["tool_calls"]} if "tool_calls" in message else {})})
    original_meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
    identity = row.get("task") or row.get("name") or row.get("id") or original_meta.get("task") or original_meta.get("name")
    category = row.get("category") or original_meta.get("category")
    tags = row.get("tags", original_meta.get("tags", original_meta.get("training_tags", [])))
    if isinstance(tags, str): tags = [tags]
    result=sanitize_object({"messages":normalized,"tools":row.get("tools") or [],
                            "meta":{"source":source,"source_row":index,
                                    "name":identity,"category":category,
                                    "tags":tags if isinstance(tags,list) else []}})
    serialized=json.dumps(result,ensure_ascii=False)
    hits=findings(serialized,exact_secrets=_staged_secret_values())
    if hits: raise ValueError(f"{source} row {index}: privacy findings {hits}")
    return result

def load_source(spec: str):
    if spec.startswith("hf:"):
        reference=spec[3:]
        if "@" not in reference:
            raise ValueError("Hugging Face sources must pin a revision: hf:owner/name@revision[#split]")
        dataset_id, pinned=reference.split("@",1)
        revision, _, split=pinned.partition("#")
        if not dataset_id or not revision:
            raise ValueError("invalid Hugging Face source; expected hf:owner/name@revision[#split]")
        try: from datasets import load_dataset
        except ImportError as error: raise RuntimeError("install moonshiner[huggingface]") from error
        dataset=load_dataset(dataset_id,split=split or "train",revision=revision)
        return [_normalize(dict(row),spec,i) for i,row in enumerate(dataset)]
    path=Path(spec.removeprefix("local:")).expanduser()
    return [_normalize(row,spec,i) for i,row in enumerate(_local_rows(path))]

def _matches(value, patterns) -> bool:
    return any(fnmatch.fnmatchcase(str(value or ""), pattern) for pattern in patterns)

def _selected(row, filters) -> bool:
    meta=row["meta"]; name=meta.get("name"); category=meta.get("category"); tags=meta.get("tags") or []
    include_name,exclude_name,include_category,exclude_category,include_tag,exclude_tag=filters
    if include_name and not _matches(name,include_name): return False
    if include_category and not _matches(category,include_category): return False
    if include_tag and not any(_matches(tag,include_tag) for tag in tags): return False
    if exclude_name and _matches(name,exclude_name): return False
    if exclude_category and _matches(category,exclude_category): return False
    if exclude_tag and any(_matches(tag,exclude_tag) for tag in tags): return False
    return True

def compose(sources, weights, output: Path, seed: int, filters=None) -> dict:
    filters=filters or ([],[],[],[],[],[])
    rng=random.Random(seed); seen=set(); pools=[]
    for spec in sources:
        rows=[row for row in load_source(spec) if _selected(row,filters)]; rng.shuffle(rows); pools.append(rows)
    if not weights: weights=[1.0]*len(pools)
    if len(weights)!=len(pools): raise ValueError("one --weight is required per source")
    if any(weight <= 0 for weight in weights): raise ValueError("all source weights must be positive")
    target=max(len(pool)/weight for pool,weight in zip(pools,weights) if weight>0)
    combined=[]
    for pool,weight in zip(pools,weights):
        for row in pool[:min(len(pool),round(target*weight))]:
            digest=hashlib.sha256(json.dumps(row["messages"],sort_keys=True).encode()).hexdigest()
            if digest not in seen: seen.add(digest); row["meta"]["content_sha256"]=digest; combined.append(row)
    rng.shuffle(combined); output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text("".join(json.dumps(row,ensure_ascii=False)+"\n" for row in combined))
    filter_names=("include_name","exclude_name","include_category","exclude_category","include_tag","exclude_tag")
    manifest={"sources":sources,"weights":weights,"seed":seed,"rows":len(combined),
              "filters":dict(zip(filter_names,filters)),
              "sha256":hashlib.sha256(output.read_bytes()).hexdigest()}
    output.with_suffix(output.suffix+".manifest.json").write_text(json.dumps(manifest,indent=2)+"\n")
    return manifest

def main(argv=None)->int:
    parser=argparse.ArgumentParser(prog="moonshiner dataset")
    sub=parser.add_subparsers(dest="action",required=True)
    comp=sub.add_parser("compose"); comp.add_argument("--source",action="append",required=True)
    comp.add_argument("--weight",action="append",type=float,default=[]); comp.add_argument("--seed",type=int,default=42)
    comp.add_argument("--out",type=Path,default=DATA/"composed"/"train.jsonl")
    for name in ("name","category","tag"):
        comp.add_argument(f"--include-{name}",action="append",default=[],metavar="GLOB")
        comp.add_argument(f"--exclude-{name}",action="append",default=[],metavar="GLOB")
    prep=sub.add_parser("prepare"); prep.add_argument("--trainer",choices=["axolotl"],required=True)
    prep.add_argument("--input",type=Path,required=True); prep.add_argument("--model",required=True); prep.add_argument("--out",type=Path,required=True)
    args=parser.parse_args(argv)
    if args.action=="compose":
        filters=(args.include_name,args.exclude_name,args.include_category,args.exclude_category,args.include_tag,args.exclude_tag)
        print(json.dumps(compose(args.source,args.weight,args.out,args.seed,filters),indent=2)); return 0
    # JSON is valid YAML and avoids executing a YAML serializer dependency.
    config={"base_model":args.model,"chat_template":"tokenizer_default",
            "datasets":[{"path":str(args.input.resolve()),"type":"chat_template","field_messages":"messages","roles_to_train":["assistant"]}],
            "dataset_prepared_path":str((args.out.parent/"prepared").resolve()),"sample_packing":True}
    args.out.parent.mkdir(parents=True,exist_ok=True); args.out.write_text(json.dumps(config,indent=2)+"\n")
    print(f"wrote Axolotl configuration: {args.out}"); return 0
