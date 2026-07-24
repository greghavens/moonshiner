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

def _redact(text: str, secret_values: tuple[str, ...], redact_email=True):
    text, count = str(text).replace("\x00", ""), 0
    for value in secret_values:
        n = text.count(value); text = text.replace(value, "[REDACTED_SECRET]"); count += n
    for pattern in SECRET_PATTERNS:
        text, n = pattern.subn("[REDACTED_SECRET]", text); count += n
    if redact_email:
        text, n = EMAIL_RE.subn("[REDACTED_EMAIL]", text); count += n
    return text, count

def redact(text: str, *, exact_secrets: Iterable[str] = (), redact_email=True):
    return _redact(text, live_secret_values(exact_secrets), redact_email)

def _findings(text: str, secret_values: tuple[str, ...], forbidden_paths=()):
    hits = []
    if any(v in text for v in secret_values): hits.append("live credential value")
    if any(p.search(text) for p in SECRET_PATTERNS): hits.append("credential pattern")
    if EMAIL_RE.search(text): hits.append("email address")
    if any(v and v in text for v in forbidden_paths): hits.append("host path")
    host = socket.gethostname()
    if len(host) >= 4 and host in text: hits.append("host name")
    return sorted(set(hits))

def findings(text: str, *, exact_secrets: Iterable[str] = (), forbidden_paths=()):
    return _findings(
        text, live_secret_values(exact_secrets), forbidden_paths)

def object_findings(value, *, exact_secrets: Iterable[str] = (),
                    forbidden_paths=()):
    secret_values = live_secret_values(exact_secrets)
    return _object_findings(value, secret_values, forbidden_paths)

def _object_findings(value, secret_values, forbidden_paths):
    hits = []
    if isinstance(value, str):
        hits.extend(_findings(value, secret_values, forbidden_paths))
    elif isinstance(value, list):
        for item in value:
            hits.extend(_object_findings(
                item, secret_values, forbidden_paths))
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str):
                hits.extend(_findings(key, secret_values, forbidden_paths))
            hits.extend(_object_findings(
                item, secret_values, forbidden_paths))
    return sorted(set(hits))

def sanitize_object(value, *, exact_secrets: Iterable[str] = ()):
    return _sanitize_object(value, live_secret_values(exact_secrets))

def _sanitize_object(value, secret_values):
    if isinstance(value, str): return _redact(value, secret_values)[0]
    if isinstance(value, list): return [_sanitize_object(v, secret_values) for v in value]
    if isinstance(value, dict):
        return {
            (_redact(k, secret_values)[0]
             if isinstance(k, str) else k):
            _sanitize_object(v, secret_values)
            for k, v in value.items()
        }
    return value
