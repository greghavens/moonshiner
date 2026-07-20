"""OpenRouter tool-call execution and deterministic grading for behavior seeds."""
from __future__ import annotations

import copy, hashlib, json, time, urllib.request
from pathlib import Path

from common import BEHAVIOR_WORLDS, CONFIG, TRACES, scrub_text
from runtimes.auth import load_provider_key
from review_contract import verdict_accepts

RAW, META, REVIEWS = TRACES / "raw", TRACES / "meta", TRACES / "reviews"
FORMAT = "moonshiner-behavior-openai-v1"
SYSTEM = ("Follow the user's instructions precisely. Use only the supplied fictional "
          "tools. Independent calls that can safely run together must be emitted in "
          "one assistant turn. Never invent tool results or perform unrequested changes.")


def _schemas(seed: dict) -> list[dict]:
    world = json.loads(BEHAVIOR_WORLDS.read_text())["worlds"][seed["world"]]
    wanted = set(seed["available_tools"])
    return [{"type": "function", "function": tool} for tool in world["tools"]
            if tool["name"] in wanted]


def schemas_for_seed(seed: dict, names: set[str] | None = None) -> list[dict]:
    world = json.loads(BEHAVIOR_WORLDS.read_text())["worlds"][seed["world"]]
    wanted = names or set(seed["available_tools"])
    return [{"type":"function","function":tool} for tool in world["tools"]
            if tool["name"] in wanted]


def _result(seed: dict, name: str, args: dict, occurrence: int, state: dict) -> dict:
    for failure in seed.get("failure_injections", []):
        if failure["tool"] == name and failure["occurrence"] == occurrence:
            return {"ok": False, "error": failure["error"],
                    "may_have_committed": failure.get("may_have_committed", False)}
    fixtures=seed.get("tool_results") or {}
    explicit=(fixtures.get(f"{name}#{occurrence}") or fixtures.get(str(args.get("url") or ""))
              or fixtures.get(str(args.get("id") or "")) or fixtures.get(name))
    if name == "web_search" and explicit is None:
        explicit=fixtures.get(f"search_{occurrence}") or fixtures.get("alternate_search")
    if explicit is not None:
        return copy.deepcopy(explicit)
    if name.startswith("memory_"):
        memory=state.setdefault("memory",{})
        if name == "memory_search":
            query=str(args.get("query","")).casefold()
            return {"matches": {k:v for k,v in memory.items()
                    if query in k.casefold() or query in str(v).casefold()}}
        if name in {"memory_add","memory_update"}:
            key=str(args.get("key") or args.get("name") or ""); value=args.get("value")
            memory[key]=value; return {"saved":True,"key":key,"value":value}
        if name == "memory_remove":
            return {"removed":memory.pop(str(args.get("key") or args.get("name") or ""),None)}
        if name == "memory_retrieve_summary": return {"summary":state.get("summary")}
        if name == "memory_update_summary": state["summary"]=args.get("summary"); return {"saved":True}
    records = state.setdefault("records", [])
    action = name.rsplit("_", 1)[-1]
    if action == "get":
        return {"record": next((r for r in records if r.get("id") == args.get("id")), None)}
    if action in {"search", "list"}:
        def matches(record):
            for key,value in args.items():
                if key == "query":
                    haystack=" ".join(str(v) for v in record.values())
                    if str(value).casefold() not in haystack.casefold(): return False
                elif str(record.get(key,"")).casefold() != str(value).casefold():
                    return False
            return True
        values = [r for r in records if matches(r)]
        return {"records": values}
    if action == "profile": return copy.deepcopy(state.get("profile", {}))
    if action == "availability": return {"available": True, **args}
    if action in {"create", "add"}:
        record = {"id": f"sim-{len(records)+1:04d}", **args}; records.append(record)
        return {"created": record}
    if action == "update":
        record = next((r for r in records if r.get("id") == args.get("id")), None)
        if record: record.update({k:v for k,v in args.items() if k != "id"})
        return {"updated": record}
    if action in {"cancel", "remove"}:
        record = next((r for r in records if r.get("id") == args.get("id")), None)
        if record: record["status"] = "cancelled"
        return {"cancelled": record}
    return {"ok": True, "arguments": args}


def _post(messages: list[dict], tools: list[dict], model: str, key: str) -> tuple[dict, dict]:
    body = json.dumps({"model": model, "messages": messages, "tools": tools,
                       "tool_choice": "auto", "parallel_tool_calls": True}).encode()
    request = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
        data=body, method="POST", headers={"Authorization": f"Bearer {key}",
        "Content-Type": "application/json", "HTTP-Referer": "https://github.com/greghavens/moonshiner",
        "X-Title": "Moonshiner"})
    with urllib.request.urlopen(request, timeout=900) as response:
        payload = json.load(response)
    return payload["choices"][0]["message"], payload


def grade(seed: dict, messages: list[dict]) -> dict:
    actual = []
    forbidden = set(seed["expected"].get("forbidden_tools", [])); used = []
    for message in messages:
        calls = message.get("tool_calls") or []
        if calls:
            stage=[]
            for call in calls:
                fn=call["function"]; args=fn.get("arguments", {})
                if isinstance(args,str):
                    try: args=json.loads(args)
                    except json.JSONDecodeError: args={"_invalid":args}
                stage.append({"tool":fn["name"],"arguments":args}); used.append(fn["name"])
            actual.append(stage)
    expected_spec = seed["expected"].get("stages", [])
    expected = [stage["calls"] for stage in expected_spec]
    def canonical(stage, parallel):
        values=[json.dumps(x,sort_keys=True) for x in stage]
        return sorted(values) if parallel else values
    stages_match=(len(actual)==len(expected) and all(
        canonical(got,spec.get("parallel",False)) == canonical(want,spec.get("parallel",False))
        for got,want,spec in zip(actual,expected,expected_spec)))
    decision = seed["expected"]["decision"]
    has_final = any(m.get("role")=="assistant" and not m.get("tool_calls")
                    and str(m.get("content") or "").strip() for m in messages)
    findings=[]
    if any(name in forbidden for name in used): findings.append("called a forbidden tool")
    if decision == "tool_calls" and not stages_match: findings.append("tool stages or arguments differ from recipe")
    if decision != "tool_calls" and actual: findings.append("called tools when the recipe requires no tool call")
    if not has_final: findings.append("no final assistant response")
    return {"accepted": not findings, "status":"accepted" if not findings else "rejected",
            "reason":"; ".join(findings), "expected_stages":expected, "actual_stages":actual}


def trace_task(seed: dict, teacher, *, feedback: str | None = None) -> dict:
    for directory in (RAW, META, REVIEWS): directory.mkdir(parents=True, exist_ok=True)
    runtime = teacher.runtime_config
    if runtime.get("provider") != "openrouter":
        raise RuntimeError("behavior tracing currently requires an OpenRouter provider profile")
    key=load_provider_key(runtime); model=teacher.role["model"]; tool_names=set(seed["available_tools"]); tools=schemas_for_seed(seed,tool_names)
    messages=[{"role":"system","content":SYSTEM},{"role":"user","content":seed["prompt"]}]
    if feedback: messages.append({"role":"user","content":"Correct the prior attempt: "+feedback})
    state=copy.deepcopy(seed["initial_state"]); counts={}; usage={}; observed=[]; started=time.monotonic()
    stage_count=0; delivered=set()
    for _ in range(max(4, len(seed["expected"].get("stages", []))+len(seed.get("follow_up_turns",[]))+3)):
        assistant,payload=_post(messages,tools,model,key); messages.append(assistant)
        observed.append(payload.get("model")); usage=payload.get("usage") or usage
        calls=assistant.get("tool_calls") or []
        if not calls: break
        stage_count += 1
        for call in calls:
            fn=call["function"]; args=fn.get("arguments", "{}")
            if isinstance(args,str): args=json.loads(args)
            name=fn["name"]; counts[name]=counts.get(name,0)+1
            result=_result(seed,name,args,counts[name],state)
            messages.append({"role":"tool","tool_call_id":call["id"],"name":name,
                             "content":json.dumps(result,ensure_ascii=False)})
        for index,turn in enumerate(seed.get("follow_up_turns", [])):
            if index not in delivered and turn["after_stage"] == stage_count:
                delivered.add(index); messages.append({"role":"user","content":turn["content"]})
                tool_names.update(turn.get("add_tools", [])); tools=schemas_for_seed(seed,tool_names)
    safe=json.loads(scrub_text(json.dumps(messages,ensure_ascii=False)))
    raw=RAW/f"{seed['id']}.jsonl"; raw.write_text("\n".join(json.dumps(m) for m in safe)+"\n")
    verdict=grade(seed,safe)
    raw_text=raw.read_text(); fingerprint=hashlib.sha256(seed["_path"].read_bytes()).hexdigest()
    record={"id":seed["id"],"kind":"tool_behavior","category":seed["category"],
      "passed":verdict["accepted"],"verify_passed":verdict["accepted"],"protected_intact":True,
      "verify_output":verdict["reason"],"trace_format":FORMAT,"prompt":seed["prompt"],
      "seed_fingerprint":fingerprint,"raw_sha256":hashlib.sha256(raw_text.encode()).hexdigest(),
      "diff_sha256":hashlib.sha256(b"").hexdigest(),"raw_path":str(raw.relative_to(TRACES.parent)),
      "duration_s":round(time.monotonic()-started,2),"teacher":{"runtime":teacher.name,"model":model,
      "reasoning":teacher.role.get("reasoning"),"observed_model":observed[-1] if observed else None,
      "observed_models":observed,"model_attested":all(x==model for x in observed),"usage":usage}}
    META.joinpath(f"{seed['id']}.json").write_text(json.dumps(record,indent=2)+"\n")
    return record


def judge_trace(seed: dict, judge) -> dict:
    """Require both exact recipe conformance and an independent model review."""
    raw=RAW/f"{seed['id']}.jsonl"; messages=[json.loads(x) for x in raw.read_text().splitlines()]
    deterministic=grade(seed,messages)
    schema={"type":"object","required":["accepted","reason"],"properties":{
        "accepted":{"type":"boolean"},"reason":{"type":"string"}},"additionalProperties":False}
    instruction=("Judge this fictional non-code tool-use trajectory harshly. It must follow every user "
      "instruction, choose correct tools and arguments, group independent calls in parallel, preserve "
      "dependencies across stages, use tool results honestly, and avoid forbidden side effects. Return "
      "only JSON matching the requested schema.\nSEED:\n"+json.dumps({k:v for k,v in seed.items() if not k.startswith('_')})+
      "\nTRACE:\n"+json.dumps(messages))
    result=judge.run_review(instruction, seed["_path"].parent, out_dir=REVIEWS,
                            schema=schema, read_only=True)
    verdict=result.verdict if isinstance(result.verdict,dict) else None
    healthy = (result.return_code in (0, None) and not result.timed_out
               and result.model_attested and not result.error)
    accepted = healthy and verdict_accepts(verdict)
    status = "accepted" if accepted else ("review_reject" if healthy else "judge_error")
    review={"id":seed["id"],"accepted":accepted,"status":status,
            "reason":(verdict or {}).get("reason") or result.error or "invalid judge verdict",
            "deterministic":deterministic,"verdict":verdict,"judge":{"runtime":judge.name,
            "model":judge.role["model"],"model_attested":result.model_attested},
            "raw_sha256":hashlib.sha256(raw.read_bytes()).hexdigest(),
            "seed_fingerprint":hashlib.sha256(seed["_path"].read_bytes()).hexdigest()}
    REVIEWS.joinpath(f"{seed['id']}.json").write_text(json.dumps(review,indent=2)+"\n")
    return review
