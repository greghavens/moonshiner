"""Canonical privacy policy used before dataset materialization or upload."""
from __future__ import annotations
import os, re, socket
from typing import Iterable

SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S),
    re.compile(r"\b(?:hf|glpat|npm|pypi|github_pat)_[A-Za-z0-9_.-]{16,}\b"),
    re.compile(r"\b(?:sk-(?:proj-)?|sk-ant-)[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"), re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b(?:authorization|proxy-authorization)\s*:\s*(?:bearer|basic)\s+[^\s\"']+"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\s*[=:]\s*[^\s,;\"']{6,}"),
)
SENSITIVE_ENV_RE = re.compile(r"(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|ACCESS_KEY|PRIVATE_KEY|CREDENTIAL|AUTH)", re.I)
EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I)

def live_secret_values(extra: Iterable[str] = ()) -> tuple[str, ...]:
    values = [v.strip() for k, v in os.environ.items()
              if SENSITIVE_ENV_RE.search(k) and len(v.strip()) >= 6]
    values.extend(v for v in extra if v and len(v) >= 6)
    return tuple(sorted(set(values), key=len, reverse=True))

def redact(text: str, *, exact_secrets: Iterable[str] = (), redact_email=True):
    text, count = str(text).replace("\x00", ""), 0
    for value in live_secret_values(exact_secrets):
        n = text.count(value); text = text.replace(value, "[REDACTED_SECRET]"); count += n
    for pattern in SECRET_PATTERNS:
        text, n = pattern.subn("[REDACTED_SECRET]", text); count += n
    if redact_email:
        text, n = EMAIL_RE.subn("[REDACTED_EMAIL]", text); count += n
    return text, count

def findings(text: str, *, exact_secrets: Iterable[str] = (), forbidden_paths=()):
    hits = []
    if any(v in text for v in live_secret_values(exact_secrets)): hits.append("live credential value")
    if any(p.search(text) for p in SECRET_PATTERNS): hits.append("credential pattern")
    if EMAIL_RE.search(text): hits.append("email address")
    if any(v and v in text for v in forbidden_paths): hits.append("host path")
    host = socket.gethostname()
    if len(host) >= 4 and host in text: hits.append("host name")
    return sorted(set(hits))

def object_findings(value, *, exact_secrets: Iterable[str] = (),
                    forbidden_paths=()):
    hits = []
    if isinstance(value, str):
        hits.extend(findings(
            value, exact_secrets=exact_secrets,
            forbidden_paths=forbidden_paths))
    elif isinstance(value, list):
        for item in value:
            hits.extend(object_findings(
                item, exact_secrets=exact_secrets,
                forbidden_paths=forbidden_paths))
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                hits.extend(findings(
                    key, exact_secrets=exact_secrets,
                    forbidden_paths=forbidden_paths))
            hits.extend(object_findings(
                item, exact_secrets=exact_secrets,
                forbidden_paths=forbidden_paths))
    return sorted(set(hits))

def sanitize_object(value, *, exact_secrets: Iterable[str] = ()):
    if isinstance(value, str): return redact(value, exact_secrets=exact_secrets)[0]
    if isinstance(value, list): return [sanitize_object(v, exact_secrets=exact_secrets) for v in value]
    if isinstance(value, dict):
        return {
            (redact(k, exact_secrets=exact_secrets)[0]
             if isinstance(k, str) else k):
            sanitize_object(v, exact_secrets=exact_secrets)
            for k, v in value.items()
        }
    return value
