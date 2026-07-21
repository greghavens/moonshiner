#!/usr/bin/env python3
"""Offline source-contract checks for the token-refresh single flight.

The protected C# runner is the behavioral acceptance contract.  This verifier
checks the same concurrency and telemetry invariants without restoring packages
or requiring a machine-wide .NET installation.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "TokenRefreshStorm" / "TokenProvider.cs"
PROJECT = ROOT / "src" / "TokenRefreshStorm" / "TokenRefreshStorm.csproj"


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
            if character == '@' and following == '"':
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
            if character == "\\" and index + 1 < len(source):
                if source[index + 1] != "\n":
                    masked[index + 1] = " "
                index += 2
                continue
            if character == delimiter:
                state = "code"

        index += 1

    return "".join(masked)


def balanced_region(masked: str, opening: int) -> tuple[int, int]:
    return balanced_delimited(masked, opening, "{", "}")


def balanced_delimited(
    masked: str, opening: int, opener: str, closer: str
) -> tuple[int, int]:
    depth = 0
    for index in range(opening, len(masked)):
        if masked[index] == opener:
            depth += 1
        elif masked[index] == closer:
            depth -= 1
            if depth == 0:
                return opening + 1, index
    raise AssertionError(f"TokenProvider.cs contains unbalanced {opener}{closer}")


def method_body(source: str, masked: str, name: str) -> tuple[str, str]:
    signature = re.search(rf"\b{re.escape(name)}\s*\([^;{{}}]*\)\s*\{{", masked)
    if signature is None:
        raise AssertionError(f"missing method {name}")
    opening = masked.find("{", signature.start())
    start, end = balanced_region(masked, opening)
    return source[start:end], masked[start:end]


def require(pattern: str, value: str, message: str, flags: int = 0) -> None:
    if re.search(pattern, value, flags) is None:
        raise AssertionError(message)


def split_arguments(source: str, masked: str, opening: int) -> list[str]:
    start, end = balanced_delimited(masked, opening, "(", ")")
    arguments: list[str] = []
    argument_start = start
    parens = brackets = braces = 0

    for index in range(start, end):
        character = masked[index]
        if character == "(":
            parens += 1
        elif character == ")":
            parens -= 1
        elif character == "[":
            brackets += 1
        elif character == "]":
            brackets -= 1
        elif character == "{":
            braces += 1
        elif character == "}":
            braces -= 1
        elif character == "," and parens == brackets == braces == 0:
            arguments.append(source[argument_start:index].strip())
            argument_start = index + 1

    arguments.append(source[argument_start:end].strip())
    return arguments


def argument_value(argument: str) -> str:
    return re.sub(r"^\s*[A-Za-z_]\w*\s*:\s*", "", argument).strip()


def normalized(argument: str) -> str:
    return re.sub(r"\s+", "", argument_value(argument))


def refresh_events(source: str, masked: str) -> dict[str, list[str]]:
    events: dict[str, list[str]] = {}
    for match in re.finditer(r"new\s+RefreshEvent\s*\(", masked):
        opening = masked.find("(", match.start())
        arguments = split_arguments(source, masked, opening)
        if len(arguments) < 4:
            raise AssertionError("every RefreshEvent must include id, participants, and duration")
        kind_match = re.search(r"RefreshEventKind\s*\.\s*(\w+)", arguments[0])
        if kind_match is None:
            raise AssertionError("RefreshEvent kind must use the existing enum")
        kind = kind_match.group(1)
        if kind in events:
            raise AssertionError(f"{kind} telemetry must have one emission path")
        events[kind] = arguments
    return events


def method_regions(source: str, masked: str) -> list[tuple[str, str, str]]:
    signature = re.compile(
        r"\b(?:public|private|protected|internal)\s+"
        r"(?:(?:static|async|sealed|virtual|override)\s+)*"
        r"[A-Za-z_]\w*(?:\s*<[^;{}()]+>)?\??\s+"
        r"(?P<name>[A-Za-z_]\w*)\s*\([^;{}]*\)\s*\{"
    )
    methods: list[tuple[str, str, str]] = []
    for match in signature.finditer(masked):
        opening = masked.find("{", match.start())
        start, end = balanced_region(masked, opening)
        methods.append((match.group("name"), source[start:end], masked[start:end]))
    return methods


def verify_fast_path_and_join(get_body: str, get_masked: str) -> None:
    lock_match = re.search(r"\block\s*\(", get_masked)
    if lock_match is None:
        raise AssertionError("stale-token coordination must synchronize creation of the shared flight")

    prefix = get_masked[:lock_match.start()]
    require(
        r"Volatile\s*\.\s*Read\s*\(\s*ref\s+_cachedToken\s*\)",
        prefix,
        "the valid-token fast path must read the cache before synchronization",
    )
    require(
        r"\bIsUsable\s*\([^)]*\)\s*\)\s*\{[^{}]*\breturn\b",
        prefix,
        "the valid-token fast path must return before synchronization",
        re.DOTALL,
    )

    if len(re.findall(r"Volatile\s*\.\s*Read\s*\(\s*ref\s+_cachedToken\s*\)", get_masked)) < 2:
        raise AssertionError("the cache must be re-read after entering refresh coordination")
    if len(re.findall(r"\bIsUsable\s*\(", get_masked)) < 2:
        raise AssertionError("the coordinated cache recheck must avoid a redundant follow-up refresh")

    require(
        r"\b\w*(?:inFlight|refreshFlight|refreshTask)\w*\b\s+(?:is|==)\s+null",
        get_masked,
        "create a flight only when no shared refresh is in progress",
        re.IGNORECASE,
    )
    require(
        r"(?:\+\+\s*[\w.]*Participants?\w*|[\w.]*Participants?\w*\s*\+\+|Interlocked\s*\.\s*Increment\s*\([^)]*Participants?\w*)",
        get_masked,
        "count every starter and follower in the shared flight",
    )

    for kind in ("Started", "Joined", "WaiterCanceled"):
        require(
            rf"RefreshEventKind\s*\.\s*{kind}\b",
            get_masked,
            f"GetAccessTokenAsync must emit {kind} telemetry",
        )

    wait_is_cancelable = any(
        re.search(pattern, get_masked) is not None
        for pattern in (
            r"\.\s*WaitAsync\s*\(\s*cancellationToken\s*\)",
            r"Task\s*\.\s*WhenAny\s*\(",
            r"cancellationToken\s*\.\s*Register\s*\(",
        )
    )
    if not wait_is_cancelable:
        raise AssertionError("each participant must apply its cancellation token only to its own wait")
    require(
        r"catch\s*\(\s*OperationCanceledException(?:\s+\w+)?\s*\)",
        get_masked,
        "an individually canceled wait must emit telemetry before it propagates",
    )
    require(
        r"cancellationToken\s*\.\s*IsCancellationRequested",
        get_masked,
        "waiter cancellation must be distinguished from endpoint cancellation",
    )


def verify_shared_operation(source: str, masked: str) -> None:
    require(
        r"private\s+readonly\s+object\s+\w+\s*=\s*new\s*\(\s*\)\s*;",
        masked,
        "TokenProvider needs a private refresh-coordination gate",
    )
    require(
        r"private\s+\w+(?:<[^;{}]+>)?\??\s+\w*(?:inFlight|refreshFlight|refreshTask)\w*\s*;",
        masked,
        "TokenProvider needs shared in-flight refresh state",
        re.IGNORECASE,
    )

    endpoint_calls = list(re.finditer(r"_endpoint\s*\.\s*RefreshAsync\s*\((?P<argument>[^)]*)\)", masked))
    if len(endpoint_calls) != 1:
        raise AssertionError("there must be one source location that starts an upstream refresh")
    argument = endpoint_calls[0].group("argument")
    if re.search(r"\bcancellationToken\b", argument) is not None:
        raise AssertionError("the shared upstream refresh cannot be owned by a request cancellation token")

    has_completion_source = re.search(r"TaskCompletionSource\s*<\s*AccessToken\s*>", masked) is not None
    has_shared_task = re.search(
        r"private\s+(?:readonly\s+)?Task\s*<\s*AccessToken\s*>\??\s+\w*(?:inFlight|refreshTask)\w*",
        masked,
        re.IGNORECASE,
    ) is not None
    if not has_completion_source and not has_shared_task:
        raise AssertionError("the in-flight operation must expose one shared Task<AccessToken>")

    if has_completion_source:
        require(
            r"(?:Try)?SetResult\s*\(\s*\w+\s*\)",
            masked,
            "successful refreshes must complete every participant with the same result",
        )
        require(
            r"(?:Try)?SetException\s*\(\s*(\w+)\s*\)",
            masked,
            "the original endpoint failure must complete every participant",
        )
        require(
            r"(?:Try)?SetCanceled\s*\([^)]*\)",
            masked,
            "endpoint cancellation must retain its cancellation token",
        )

    require(
        r"\w*(?:inFlight|refreshFlight|refreshTask)\w*\s*=\s*null\s*;",
        masked,
        "completed, failed, and canceled flights must be cleared so a request can retry",
        re.IGNORECASE,
    )

    methods = method_regions(source, masked)
    runners = [item for item in methods if re.search(r"_endpoint\s*\.\s*RefreshAsync", item[2])]
    if len(runners) != 1:
        raise AssertionError("the upstream endpoint call must belong to one shared refresh runner")
    _, _, runner_masked = runners[0]
    clear_pattern = re.compile(
        r"\w*(?:inFlight|refreshFlight|refreshTask)\w*\s*=\s*null\s*;",
        re.IGNORECASE,
    )

    if clear_pattern.search(runner_masked) is not None:
        inline_clears = len(clear_pattern.findall(runner_masked))
        if inline_clears < 3 and re.search(r"\bfinally\s*\{", runner_masked) is None:
            raise AssertionError("success, failure, and endpoint cancellation must all clear the flight")
    else:
        cleanup_methods = [item for item in methods if clear_pattern.search(item[2]) is not None]
        if len(cleanup_methods) != 1:
            raise AssertionError("flight cleanup must have one unambiguous path")
        cleanup_name = cleanup_methods[0][0]
        cleanup_calls = len(re.findall(rf"\b{re.escape(cleanup_name)}\s*\(", runner_masked))
        if cleanup_calls < 3 and not (
            cleanup_calls >= 1 and re.search(r"\bfinally\s*\{", runner_masked)
        ):
            raise AssertionError("success, failure, and endpoint cancellation must all clear the flight")


def verify_terminal_telemetry(source: str, masked: str) -> None:
    for kind in ("Succeeded", "Failed", "Canceled"):
        require(
            rf"RefreshEventKind\s*\.\s*{kind}\b",
            masked,
            f"shared refreshes need terminal {kind} telemetry",
        )

    if len(re.findall(r"Stopwatch\s*\.\s*GetElapsedTime\s*\(", masked)) < 4:
        raise AssertionError("joined, terminal, and waiter-canceled events need elapsed durations")
    require(
        r"\.\s*GetType\s*\(\s*\)\s*\.\s*FullName",
        masked,
        "failed refresh telemetry must include the endpoint exception type",
    )
    require(
        r"Volatile\s*\.\s*Write\s*\(\s*ref\s+_cachedToken\s*,",
        masked,
        "only a successful shared refresh should publish a new cached token",
    )

    events = refresh_events(source, masked)
    expected = {"Started", "Joined", "Succeeded", "Failed", "Canceled", "WaiterCanceled"}
    if set(events) != expected:
        missing = sorted(expected - set(events))
        extra = sorted(set(events) - expected)
        raise AssertionError(f"telemetry emission paths differ; missing={missing}, extra={extra}")

    waiter_ids = {normalized(events[kind][1]) for kind in ("Started", "Joined", "WaiterCanceled")}
    terminal_ids = {normalized(events[kind][1]) for kind in ("Succeeded", "Failed", "Canceled")}
    if len(waiter_ids) != 1 or len(terminal_ids) != 1:
        raise AssertionError("all events for a flight must use its shared refresh id")
    if not all("RefreshId" in expression for expression in waiter_ids | terminal_ids):
        raise AssertionError("telemetry correlation must come from the shared flight id")

    if normalized(events["Started"][3]) != "TimeSpan.Zero":
        raise AssertionError("Started telemetry must begin with zero duration")
    for kind in expected - {"Started"}:
        duration = normalized(events[kind][3])
        if "GetElapsedTime" not in duration and "duration" not in duration.casefold():
            raise AssertionError(f"{kind} telemetry must report elapsed flight duration")

    joined_participants = normalized(events["Joined"][2])
    waiter_participants = normalized(events["WaiterCanceled"][2])
    if joined_participants in {"0", "1"} or waiter_participants in {"0", "1"}:
        raise AssertionError("follower telemetry must report the shared participant count")
    for kind in ("Succeeded", "Failed", "Canceled"):
        participants = normalized(events[kind][2])
        if participants in {"0", "1"} or "participant" not in participants.casefold():
            raise AssertionError(f"{kind} telemetry must report the final participant count")

    if len(events["Failed"]) < 5:
        raise AssertionError("Failed telemetry must include the endpoint exception type")
    failure_type = normalized(events["Failed"][4])
    if "FullName" not in failure_type and "failure" not in failure_type.casefold():
        raise AssertionError("Failed telemetry must report the endpoint exception type")


def verify_contract() -> None:
    source = SOURCE.read_text(encoding="utf-8")
    masked = mask_non_code(source)
    project = PROJECT.read_text(encoding="utf-8")

    if re.search(r"<\s*PackageReference\b", project, re.IGNORECASE):
        raise AssertionError("package dependencies are not allowed")

    require(
        r"public\s+(?:async\s+)?Task\s*<\s*string\s*>\s+GetAccessTokenAsync\s*\(\s*CancellationToken\s+cancellationToken\s*=\s*default\s*\)",
        masked,
        "GetAccessTokenAsync must keep its public signature",
    )
    get_body, get_masked = method_body(source, masked, "GetAccessTokenAsync")

    verify_fast_path_and_join(get_body, get_masked)
    verify_shared_operation(source, masked)
    verify_terminal_telemetry(source, masked)


def main() -> int:
    try:
        verify_contract()
    except (AssertionError, OSError) as error:
        print(f"FAIL token refresh single-flight contract: {error}", file=sys.stderr)
        return 1

    print("PASS token refresh single-flight contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
