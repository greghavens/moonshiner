#!/usr/bin/env python3
"""Offline source-contract verification for durable saga compensation recovery."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "SagaCompensation" / "SagaCoordinator.cs"


def mask_non_code(source: str) -> str:
    """Mask C# comments and literals while preserving offsets and newlines."""
    masked = list(source)
    index = 0
    state = "code"

    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""

        if state == "code":
            if character == "/" and following == "/":
                masked[index] = masked[index + 1] = " "
                state = "line-comment"
                index += 2
                continue
            if character == "/" and following == "*":
                masked[index] = masked[index + 1] = " "
                state = "block-comment"
                index += 2
                continue
            if character == "@" and following == '"':
                masked[index] = masked[index + 1] = " "
                state = "verbatim-string"
                index += 2
                continue
            if character == '"':
                masked[index] = " "
                state = "string"
            elif character == "'":
                masked[index] = " "
                state = "character"
        elif state == "line-comment":
            if character == "\n":
                state = "code"
            else:
                masked[index] = " "
        elif state == "block-comment":
            if character == "*" and following == "/":
                masked[index] = masked[index + 1] = " "
                state = "code"
                index += 2
                continue
            if character != "\n":
                masked[index] = " "
        elif state == "verbatim-string":
            masked[index] = " "
            if character == '"' and following == '"':
                masked[index + 1] = " "
                index += 2
                continue
            if character == '"':
                state = "code"
        else:
            masked[index] = " "
            delimiter = '"' if state == "string" else "'"
            if character == "\\":
                if index + 1 < len(source):
                    if source[index + 1] != "\n":
                        masked[index + 1] = " "
                    index += 2
                    continue
            elif character == delimiter:
                state = "code"

        index += 1

    if state not in {"code", "line-comment"}:
        raise AssertionError("SagaCoordinator.cs contains an unterminated comment or literal")
    return "".join(masked)


def balanced_region(masked: str, opening: int, opener: str, closer: str) -> tuple[int, int]:
    if opening < 0 or masked[opening] != opener:
        raise AssertionError(f"expected {opener!r} while reading SagaCoordinator.cs")
    depth = 0
    for index in range(opening, len(masked)):
        character = masked[index]
        if character == opener:
            depth += 1
        elif character == closer:
            depth -= 1
            if depth == 0:
                return opening + 1, index
    raise AssertionError(f"unbalanced {opener}{closer} region in SagaCoordinator.cs")


def block_after(masked: str, start: int) -> tuple[int, int]:
    opening = masked.find("{", start)
    return balanced_region(masked, opening, "{", "}")


def method_body(masked: str, signature: str, description: str) -> tuple[int, int]:
    match = re.search(signature, masked)
    if match is None:
        raise AssertionError(f"{description} must keep its existing public signature")
    return block_after(masked, match.start())


def compact(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def require(pattern: str, value: str, message: str) -> re.Match[str]:
    match = re.search(pattern, value, re.DOTALL)
    if match is None:
        raise AssertionError(message)
    return match


def verify_public_surface(masked: str) -> None:
    signatures = (
        (r"public\s+SagaCoordinator\s*\(\s*ISagaStore\s+store\s*,\s*IStepCompensator\s+compensator\s*\)", "constructor"),
        (r"public\s+void\s+StartSaga\s*\(\s*string\s+sagaId\s*,\s*params\s+string\[\]\s+stepNames\s*\)", "StartSaga"),
        (r"public\s+void\s+MarkStepStarted\s*\(\s*string\s+sagaId\s*,\s*string\s+stepName\s*\)", "MarkStepStarted"),
        (r"public\s+void\s+RecordStepSucceeded\s*\(\s*string\s+sagaId\s*,\s*string\s+stepName\s*\)", "RecordStepSucceeded"),
        (r"public\s+void\s+RecordStepFailed\s*\(\s*string\s+sagaId\s*,\s*string\s+stepName\s*\)", "RecordStepFailed"),
        (r"public\s+void\s+BeginCompensation\s*\(\s*string\s+sagaId\s*\)", "BeginCompensation"),
        (r"public\s+CompensationRunResult\s+ResumeCompensation\s*\(\s*string\s+sagaId\s*\)", "ResumeCompensation"),
    )
    for pattern, name in signatures:
        if re.search(pattern, masked) is None:
            raise AssertionError(f"the public {name} API changed")


def find_reverse_scan(resume: str) -> tuple[int, int]:
    for match in re.finditer(r"\bfor\s*\(", resume):
        opening = resume.find("(", match.start())
        condition_start, condition_end = balanced_region(resume, opening, "(", ")")
        condition = compact(resume[condition_start:condition_end])
        if ".Steps.Count - 1" not in condition or "--" not in condition:
            continue
        body_start, body_end = block_after(resume, condition_end)
        return body_start, body_end
    raise AssertionError("compensation candidates must be scanned from the last step backwards")


def verify_resume_contract(masked: str) -> None:
    start, end = method_body(
        masked,
        r"public\s+CompensationRunResult\s+ResumeCompensation\s*\(\s*string\s+sagaId\s*\)\s*\{",
        "ResumeCompensation",
    )
    resume = masked[start:end]

    if resume.count("_compensator.Compensate") != 1:
        raise AssertionError("ResumeCompensation must have exactly one compensator call site")
    if "Interlocked" in masked or "_compensationAttempt" in masked:
        raise AssertionError("process-local attempt counters cannot identify durable compensation")

    outer = require(r"\bwhile\s*\(\s*true\s*\)", resume,
                    "recovery must retry store compare-and-swap conflicts")
    outer_start, outer_end = block_after(resume, outer.end())
    if "_compensator.Compensate" not in resume[outer_start:outer_end]:
        raise AssertionError("the complete recovery pass must run inside the retry loop")

    require(r"_store\s*\.\s*Load\s*\(\s*sagaId\s*\)", resume,
            "each recovery pass must reload durable saga state")
    require(r"Status\s*==\s*SagaStatus\.Running[\s\S]*?throw\s+new\s+InvalidOperationException",
            resume, "ResumeCompensation must preserve the Running-state error")
    require(r"Status\s*==\s*SagaStatus\.Compensated[\s\S]*?return\s+CompensationRunResult\.Completed",
            resume, "already completed compensation must remain restart-safe")

    scan_start, scan_end = find_reverse_scan(resume)
    scan = resume[scan_start:scan_end]
    require(r"SagaStepStatus\.Started[\s\S]*?return\s+CompensationRunResult\.WaitingForForwardOutcome",
            scan, "a Started forward call must stop recovery at that reverse-scan barrier")
    require(r"SagaStepStatus\.Succeeded\s+or\s+SagaStepStatus\.Compensating",
            scan, "recovery must select both new and interrupted compensation work")
    require(r"\bbreak\s*;", scan,
            "only the latest eligible step may be handled before durable state is reloaded")
    if resume.count("SagaStepStatus.Started") != scan.count("SagaStepStatus.Started"):
        raise AssertionError("Started must be a positional reverse-scan barrier, not a global stop")
    if resume.count("CompensationRunResult.WaitingForForwardOutcome") != 1:
        raise AssertionError("waiting must be returned only when the reverse scan reaches a Started step")

    no_work = require(r"if\s*\(\s*index\s*<\s*0\s*\)", resume,
                      "the saga may complete only when the reverse scan finds no work")
    no_work_start, no_work_end = block_after(resume, no_work.end())
    completion = resume[no_work_start:no_work_end]
    require(r"Status\s*=\s*SagaStatus\.Compensated", completion,
            "the no-work branch must durably mark the saga Compensated")
    require(r"Version\s*=\s*saga\.Version\s*\+\s*1", completion,
            "the saga completion write must advance its durable version")
    require(r"_store\s*\.\s*TryReplace\s*\(\s*saga\s*,", completion,
            "saga completion must use the store's compare-and-swap operation")
    require(r"return\s+CompensationRunResult\.Completed", completion,
            "Completed may be returned only after the completion write succeeds")
    require(r"\bcontinue\s*;", completion,
            "a conflicting saga completion write must retry from durable state")

    claim = require(
        r"if\s*\(\s*step\.Status\s*==\s*SagaStepStatus\.Succeeded\s*\)",
        resume,
        "a successful step must be durably claimed before compensation",
    )
    claim_start, claim_end = block_after(resume, claim.end())
    claim_body = resume[claim_start:claim_end]
    id_assignment = require(r"(?:string|var)\s+(?P<id>[A-Za-z_]\w*)\s*=\s*[^;]+;", claim_body,
                            "claiming a step must create one compensation id")
    id_name = id_assignment.group("id")
    require(r"Status\s*=\s*SagaStepStatus\.Compensating", claim_body,
            "the claim must transition the step to Compensating")
    require(rf"CompensationId\s*=\s*{re.escape(id_name)}\b", claim_body,
            "the claim must persist the newly created compensation id")
    require(r"Version\s*=\s*saga\.Version\s*\+\s*1", claim_body,
            "the compensation claim must advance the durable saga version")
    replace = require(r"_store\s*\.\s*TryReplace\s*\(\s*saga\s*,", claim_body,
                      "the Compensating transition must use compare-and-swap")
    require(r"!\s*_store\s*\.\s*TryReplace[\s\S]*?\bcontinue\s*;", claim_body,
            "a conflicting compensation claim must retry without invoking the compensator")
    persisted_step = require(r"step\s*=\s*[^;]+\.Steps\s*\[\s*index\s*\]", claim_body,
                             "the compensator must use the successfully persisted claim")
    if replace.start() > persisted_step.start():
        raise AssertionError("the persisted claim must succeed before it is used")

    call = require(
        r"_compensator\s*\.\s*Compensate\s*\(\s*saga\.Id\s*,\s*step\.Name\s*,\s*step\.CompensationId\s*\)",
        resume,
        "every invocation must reuse the compensation id loaded from durable step state",
    )
    mark = require(r"MarkStepCompensated\s*\(\s*saga\.Id\s*,\s*step\.Name\s*\)", resume,
                   "the step must be marked Compensated after the compensator returns")
    if not (scan_end < no_work.start() < claim.start() < call.start() < mark.start()):
        raise AssertionError("reverse scan, durable claim, compensator call, and completion mark are out of order")

    guard = require(r"string\.IsNullOrWhiteSpace\s*\(\s*step\.CompensationId\s*\)", resume,
                    "interrupted compensation without a durable id must fail closed")
    if guard.start() > call.start():
        raise AssertionError("the durable compensation id must be validated before invocation")

    helper = compact(masked[end:])
    mark_helper = require(
        r"private void MarkStepCompensated\s*\(\s*string sagaId\s*,\s*string stepName\s*\)\s*=>\s*UpdateStep\s*\((?P<args>.*?)\)\s*;",
        helper,
        "MarkStepCompensated must retain its internal state-transition guard",
    )
    helper_args = mark_helper.group("args")
    if "SagaStepStatus.Compensating" not in helper_args or "SagaStepStatus.Compensated" not in helper_args:
        raise AssertionError("only a durably Compensating step may become Compensated")
    if "SagaStepStatus.Succeeded" in helper_args:
        raise AssertionError("a Succeeded step cannot skip the durable Compensating transition")


def main() -> int:
    try:
        source = SOURCE.read_text(encoding="utf-8")
        masked = mask_non_code(source)
        balanced_region(masked, masked.find("{"), "{", "}")
        verify_public_surface(masked)
        verify_resume_contract(masked)
    except (AssertionError, OSError) as error:
        print(f"FAIL saga compensation contract: {error}", file=sys.stderr)
        return 1

    print("PASS saga compensation contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
