#!/usr/bin/env python3
"""Offline source-contract checks for the focused clock-domain defect."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DECIDER = ROOT / "src" / "ClockSkewIncident" / "LeaseExpiryDecider.cs"
MODELS = ROOT / "src" / "ClockSkewIncident" / "LeaseModels.cs"
PROJECT = ROOT / "src" / "ClockSkewIncident" / "ClockSkewIncident.csproj"
TIME_CONTRACTS = ROOT / "src" / "ClockSkewIncident" / "TimeContracts.cs"


def mask_non_code(source: str) -> str:
    """Mask comments and literals while preserving offsets and newlines."""
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


def balanced_region(masked: str, opening: int, opener: str, closer: str) -> tuple[int, int]:
    depth = 0
    for index in range(opening, len(masked)):
        character = masked[index]
        if character == opener:
            depth += 1
        elif character == closer:
            depth -= 1
            if depth == 0:
                return opening + 1, index
    raise AssertionError(f"unbalanced {opener}{closer} region")


def evaluate_method(source: str, masked: str) -> tuple[str, str]:
    signature = re.search(
        r"public\s+ExpiryDecision\s+Evaluate\s*\(\s*TrackedLease\s+tracked\s*\)\s*\{",
        masked,
    )
    if signature is None:
        raise AssertionError("Evaluate must keep its public API")

    opening = masked.find("{", signature.start())
    start, end = balanced_region(masked, opening, "{", "}")
    return source[start:end], masked[start:end]


def split_arguments(source: str, masked: str, opening: int) -> tuple[list[str], int]:
    start, end = balanced_region(masked, opening, "(", ")")
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
    return arguments, end


def normalize(expression: str) -> str:
    return re.sub(r"\s+", "", expression)


def unwrap_parentheses(expression: str) -> str:
    expression = expression.strip()
    while expression.startswith("(") and expression.endswith(")"):
        masked = mask_non_code(expression)
        _, end = balanced_region(masked, 0, "(", ")")
        if end != len(expression) - 1:
            break
        expression = expression[1:-1].strip()
    return expression


def assigned_expression(name: str, source: str, masked: str) -> str | None:
    assignment = re.compile(
        rf"(?:\b(?:var|DateTimeOffset)\s+)?\b{re.escape(name)}\s*=\s*(?P<value>[^;]+);"
    )
    matches = list(assignment.finditer(masked))
    if not matches:
        return None
    latest = matches[-1]
    return source[latest.start("value"):latest.end("value")]


def is_translated_local_now(expression: str, prefix: str) -> bool:
    expression = unwrap_parentheses(expression)
    direct = re.fullmatch(
        r"_remoteClockSkew\s*\.\s*ToRemoteTime\s*\(\s*localNow\s*\)",
        expression,
    )
    if direct is not None:
        return True
    if not re.fullmatch(r"[A-Za-z_]\w*", expression):
        return False
    masked_prefix = mask_non_code(prefix)
    assigned = assigned_expression(expression, prefix, masked_prefix)
    return assigned is not None and re.fullmatch(
        r"_remoteClockSkew\s*\.\s*ToRemoteTime\s*\(\s*localNow\s*\)",
        unwrap_parentheses(assigned),
    ) is not None


def remote_deadline_variable(body: str, body_masked: str) -> str:
    assignment_pattern = re.compile(
        r"\b(?:var|bool)\s+(?P<name>[A-Za-z_]\w*)\s*=\s*(?P<value>[^;]+);",
    )
    assignment = assignment_pattern.search(body_masked)
    while assignment is not None:
        value = body[assignment.start("value"):assignment.end("value")]
        comparison = re.fullmatch(
            r"(?P<left>[\s\S]+?)\s*>=\s*(?P<right>[\s\S]+)", value
        )
        reversed_comparison = re.fullmatch(
            r"(?P<left>[\s\S]+?)\s*<=\s*(?P<right>[\s\S]+)", value
        )
        prefix = body[:assignment.start()]
        if comparison is not None:
            left = comparison.group("left")
            right = comparison.group("right")
            if (
                normalize(right) == "tracked.Lease.ExpiresAtUtc"
                and is_translated_local_now(left, prefix)
            ):
                return assignment.group("name")
        if reversed_comparison is not None:
            left = reversed_comparison.group("left")
            right = reversed_comparison.group("right")
            if (
                normalize(left) == "tracked.Lease.ExpiresAtUtc"
                and is_translated_local_now(right, prefix)
            ):
                return assignment.group("name")
        assignment = assignment_pattern.search(body_masked, assignment.end())
    raise AssertionError(
        "remote expiry must inclusively compare a skew-translated localNow with the authority deadline"
    )


def constructor_arguments(
    source: str,
    masked: str,
    type_name: str,
) -> list[list[str]]:
    pattern = re.compile(rf"\bnew\s+{re.escape(type_name)}\s*\(")
    found: list[list[str]] = []
    for match in pattern.finditer(masked):
        opening = masked.find("(", match.start())
        arguments, _ = split_arguments(source, masked, opening)
        found.append(arguments)
    return found


def require_fragments(path: Path, fragments: list[str], contract: str) -> None:
    compact = normalize(mask_non_code(path.read_text(encoding="utf-8")))
    missing = [fragment for fragment in fragments if normalize(fragment) not in compact]
    if missing:
        raise AssertionError(f"{contract} must keep its supplied public contract and behavior")


def verify_supplied_contracts() -> None:
    require_fragments(
        TIME_CONTRACTS,
        [
            """public interface ISystemClock
            {
                DateTimeOffset UtcNow { get; }
                long GetTimestamp();
                TimeSpan GetElapsedTime(long startingTimestamp);
            }""",
            """public readonly record struct DistributedLogTimestamps(
                DateTimeOffset ProducerTimestampUtc,
                DateTimeOffset ConsumerReceivedAtUtc);""",
            """public interface IRemoteClockSkew
            {
                DateTimeOffset ToRemoteTime(DateTimeOffset localTimeUtc);
            }""",
            """public DistributedLogClockSkew(DistributedLogTimestamps timestamps)
            {
                _remoteMinusLocal =
                    timestamps.ProducerTimestampUtc - timestamps.ConsumerReceivedAtUtc;
            }""",
            """public DateTimeOffset ToRemoteTime(DateTimeOffset localTimeUtc) =>
                localTimeUtc + _remoteMinusLocal;""",
        ],
        "distributed clock-skew support",
    )
    require_fragments(
        MODELS,
        [
            """public sealed record RemoteLease(
                string LeaseId,
                DateTimeOffset IssuedAtUtc,
                DateTimeOffset ExpiresAtUtc,
                TimeSpan MaximumLocalAge);""",
            "public sealed record TrackedLease(RemoteLease Lease, long ReceivedTimestamp);",
            """public enum LeaseExpiryStatus
            {
                Active,
                RemoteDeadlineElapsed,
                LocalAgeElapsed
            }""",
            """public sealed record ExpiryAudit(
                string LeaseId,
                DateTimeOffset RemoteIssuedAtUtc,
                DateTimeOffset RemoteExpiresAtUtc,
                DateTimeOffset EvaluatedAtLocalUtc,
                TimeSpan LocalElapsed);""",
            "public sealed record ExpiryDecision(LeaseExpiryStatus Status, ExpiryAudit Audit);",
        ],
        "lease and audit models",
    )

    project = PROJECT.read_text(encoding="utf-8")
    if re.search(r"<\s*PackageReference\b", project, flags=re.IGNORECASE):
        raise AssertionError("the repair cannot add package dependencies")

    prohibited = re.compile(
        r"\b(?:DateTime|DateTimeOffset)\s*\.\s*(?:Now|UtcNow)\b"
        r"|\bStopwatch\s*\.\s*GetTimestamp\s*\("
        r"|\b(?:Thread\s*\.\s*Sleep|Task\s*\.\s*Delay)\s*\("
        r"|\b(?:HttpClient|WebRequest|TcpClient|UdpClient|Socket)\b"
    )
    for source_path in sorted((ROOT / "src").rglob("*.cs")):
        if prohibited.search(mask_non_code(source_path.read_text(encoding="utf-8"))):
            raise AssertionError(
                "the repair cannot use machine clocks, sleeps, or network APIs"
            )


def verify_decision_contract() -> None:
    source = DECIDER.read_text(encoding="utf-8")
    masked = mask_non_code(source)
    body, body_masked = evaluate_method(source, masked)

    if len(re.findall(r"\b_clock\s*\.\s*UtcNow\b", body_masked)) != 1:
        raise AssertionError("Evaluate must read the injected local wall clock exactly once")
    if re.search(
        r"\b(?:DateTime|DateTimeOffset)\s*\.\s*(?:Now|UtcNow)\b",
        body_masked,
    ):
        raise AssertionError("Evaluate cannot read the machine wall clock directly")

    local_now = re.search(
        r"\b(?:var|DateTimeOffset)\s+localNow\s*=\s*_clock\s*\.\s*UtcNow\s*;",
        body_masked,
    )
    if local_now is None:
        raise AssertionError("the injected local wall instant must be retained for decision and audit")

    local_elapsed = re.search(
        r"\b(?:var|TimeSpan)\s+localElapsed\s*=\s*_clock\s*\.\s*GetElapsedTime\s*"
        r"\(\s*tracked\s*\.\s*ReceivedTimestamp\s*\)\s*;",
        body_masked,
    )
    if local_elapsed is None or len(
        re.findall(r"\b_clock\s*\.\s*GetElapsedTime\s*\(", body_masked)
    ) != 1:
        raise AssertionError("local age must use the injected monotonic elapsed-time API")

    if len(
        re.findall(r"\b_remoteClockSkew\s*\.\s*ToRemoteTime\s*\(", body_masked)
    ) != 1:
        raise AssertionError("Evaluate must translate its single local wall instant once")

    remote_expired = remote_deadline_variable(body, body_masked)
    compact = normalize(body_masked)
    expected_status = (
        rf"varstatus={re.escape(remote_expired)}\?"
        r"LeaseExpiryStatus\.RemoteDeadlineElapsed:"
        r"localElapsed>=tracked\.Lease\.MaximumLocalAge\?"
        r"LeaseExpiryStatus\.LocalAgeElapsed:LeaseExpiryStatus\.Active;"
    )
    if re.search(expected_status, compact) is None:
        raise AssertionError(
            "remote deadline must retain precedence over the monotonic local-age limit"
        )

    audits = constructor_arguments(body, body_masked, "ExpiryAudit")
    if len(audits) != 1 or [normalize(item) for item in audits[0]] != [
        "tracked.Lease.LeaseId",
        "tracked.Lease.IssuedAtUtc",
        "tracked.Lease.ExpiresAtUtc",
        "localNow",
        "localElapsed",
    ]:
        raise AssertionError("audit timestamps and monotonic duration must remain unmodified")

    decisions = constructor_arguments(body, body_masked, "ExpiryDecision")
    if len(decisions) != 1 or len(decisions[0]) != 2:
        raise AssertionError("Evaluate must return one ExpiryDecision")
    if normalize(decisions[0][0]) != "status" or not normalize(decisions[0][1]).startswith(
        "newExpiryAudit("
    ):
        raise AssertionError("the computed status and preserved audit must be returned together")


def main() -> int:
    try:
        verify_supplied_contracts()
        verify_decision_contract()
    except (AssertionError, OSError) as error:
        print(f"FAIL clock-domain expiry contract: {error}", file=sys.stderr)
        return 1

    print("PASS clock-domain expiry contract")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
