"""Human-facing authentication and environment diagnostics."""
from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sys
from pathlib import Path

from common import (CONFIG, SEEDS_DIR, STORAGE_ROOT, key_env_name, key_file_path,
                    key_persist_path)
from runtimes import (get_judge, get_seed_author, get_seed_judge,
                      get_teacher, trace_harness_alternatives)


def _provider_label(value: str) -> str:
    return {"openrouter": "OpenRouter", "openai": "OpenAI",
            "anthropic": "Anthropic", "zai": "Z.ai",
            "zenmux": "ZenMux",
            "huggingface": "Hugging Face"}.get(
                value.lower(), value.replace("-", " ").title())


def _credential_target(name: str) -> tuple[str, dict] | None:
    """Resolve a provider name; runtime names remain compatibility aliases."""
    runtimes = CONFIG.get("runtimes") or {}
    needle = name.lower()
    if needle in {"huggingface", "hugging-face", "hf"}:
        return "huggingface", {"provider": "huggingface", "key_env": "HF_TOKEN",
                               "key_file_name": "moonshiner-huggingface-key"}
    if needle == "zenmux":
        return "zenmux", {
            "provider": "zenmux",
            "display_provider": "ZenMux",
            "base_url": "https://zenmux.ai/api/v1",
            "api": "openai-completions",
            "key_env": "ZENMUX_API_KEY",
        }
    if needle in {"claude-code", "claude_code", "claude"}:
        return "claude-code", dict(runtimes.get("claude-code") or {"cli": "claude"})
    for runtime in runtimes.values():
        provider = str(runtime.get("provider") or "").lower()
        display = str(runtime.get("display_provider") or "").lower()
        if needle in {provider, display} and provider:
            return provider, runtime
    runtime = runtimes.get(name)
    if runtime is not None:
        return str(runtime.get("provider") or name), runtime
    return None


def _account_auth(action: str, source: str | None) -> int:
    """``auth`` for a harness that logs in through its own CLI, not a key.

    There is no key to prompt for, but there is very much a repair to perform:
    a login that still works can be invisible to both the CLI and the sandbox
    simply because its config directory was renamed. Doing that repair here
    keeps it inside the tool, where ``doctor`` can point at it.
    """
    from runtimes.claude_code import (CREDENTIAL_NAME, account_credential,
                                      adopt_credential, credential_home,
                                      displaced_credentials)
    active, displaced = account_credential(), displaced_credentials()
    if action == "remove":
        print("Claude Code authenticates through its own CLI; "
              "sign out with: claude /logout", file=sys.stderr)
        return 2
    if action == "status":
        if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            print("Claude Code: configured via environment "
                  "$CLAUDE_CODE_OAUTH_TOKEN")
            return 0
        if active is not None:
            print(f"Claude Code: configured via {active}")
            return 0
        print(f"Claude Code: missing ({credential_home() / CREDENTIAL_NAME})")
        for path in displaced:
            print(f"  a login is sitting at {path}")
        print("  repair with: moonshiner auth set claude-code" if displaced
              else "  log in with: claude /login")
        return 1
    if source:
        chosen = Path(source).expanduser()
        if not chosen.is_file():
            print(f"no login file at {chosen}", file=sys.stderr)
            return 2
    elif active is not None:
        print(f"Claude Code: already configured via {active}")
        return 0
    elif not displaced:
        print("no Claude Code login found to adopt; log in with: claude /login",
              file=sys.stderr)
        return 2
    elif len(displaced) > 1:
        print("several logins found; choose one with --from:", file=sys.stderr)
        for path in displaced:
            print(f"  {path}", file=sys.stderr)
        return 2
    else:
        chosen = displaced[0]
    destination = adopt_credential(chosen)
    print(f"adopted Claude Code login from {chosen} into {destination} "
          "(mode 0600)")
    return 0


def auth_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="moonshiner auth")
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("set", "status", "remove"):
        child = sub.add_parser(action)
        child.add_argument("provider", help="Credential provider, e.g. openrouter")
        if action == "set":
            child.add_argument(
                "--from", dest="source", metavar="PATH",
                help="adopt an existing login file, for harnesses that "
                     "authenticate through their own CLI (claude-code)")
    args = parser.parse_args(argv)
    target = _credential_target(args.provider)
    if target is None:
        providers = sorted({str(value.get("provider")) for value in
                            (CONFIG.get("runtimes") or {}).values()
                            if value.get("provider")})
        if "zenmux" not in providers:
            providers.append("zenmux")
            providers.sort()
        print(f"unknown credential provider: {args.provider}; choose: "
              f"{', '.join(providers) or 'none configured'}", file=sys.stderr); return 2
    provider, runtime = target
    if provider == "claude-code":
        return _account_auth(args.action, getattr(args, "source", None))
    label = _provider_label(provider)
    try:
        env_name = key_env_name(runtime)
        staged, persistent = key_file_path(runtime), key_persist_path(runtime)
    except RuntimeError as error:
        print(f"{label} uses CLI/account authentication: {error}")
        return 1
    if args.action == "status":
        source = (f"environment ${env_name}" if os.environ.get(env_name) else
                  str(staged) if staged.exists() else
                  str(persistent) if persistent.exists() else None)
        print(f"{label}: {'configured via ' + source if source else 'missing'}")
        return 0 if source else 1
    if args.action == "remove":
        removed = []
        for path in (staged, persistent):
            if path.exists(): path.unlink(); removed.append(str(path))
        print("removed: " + (", ".join(removed) if removed else "nothing stored"))
        return 0
    value = getpass.getpass(f"{env_name}: ").strip()
    if not value:
        print("empty credential; nothing stored", file=sys.stderr); return 2
    persistent.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    persistent.write_text(value); persistent.chmod(0o600)
    if staged.parent.exists():
        staged.write_text(value); staged.chmod(0o600)
    print(f"stored {label} credential in {persistent} (mode 0600)")
    return 0


def doctor_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="moonshiner doctor")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    checks = []
    # Seed authoring runs on its own two runtimes, and a project can be
    # configured so they differ from the tracing pair. Preflighting only the
    # tracing pair reports a healthy system while every seed fails.
    roles = [("author", get_teacher), ("judge", get_judge)]
    if bool(((CONFIG.get("pipeline") or {}).get("queues") or {})
            .get("seed_authoring")):
        roles += [("seed author", get_seed_author), ("seed judge", get_seed_judge)]
    for role, resolve in roles:
        try:
            runtime = resolve()
        except BaseException as error:
            checks.append({"check": role, "ok": False, "detail": str(error)})
            continue
        try:
            runtime.preflight(require_auth=True)
            checks.append({"check": role, "ok": True,
                           "detail": f"{runtime.name}/{runtime.role['model']} ready"})
        except BaseException as error:  # adapters commonly raise SystemExit
            checks.append({"check": role, "ok": False, "detail": str(error)})
    # A seed declaring capabilities can be routed to a harness that is neither
    # the author nor the judge. Those are authenticated only once selected,
    # mid-run, where the failure is terminal and stops the queue — so check
    # them here too, while nothing is at stake.
    for runtime in trace_harness_alternatives():
        check = f"trace harness {runtime.name}"
        try:
            runtime.preflight(require_auth=True)
            checks.append({"check": check, "ok": True,
                           "detail": f"{runtime.name}/{runtime.role['model']} ready"})
        except BaseException as error:
            checks.append({"check": check, "ok": False, "detail": str(error)})
    checks.extend([
        {"check": "git", "ok": shutil.which("git") is not None,
         "detail": shutil.which("git") or "not found"},
        {"check": "storage", "ok": STORAGE_ROOT.exists(), "detail": str(STORAGE_ROOT)},
        {"check": "seeds", "ok": bool(list(SEEDS_DIR.glob('*/task.json'))),
         "detail": f"{len(list(SEEDS_DIR.glob('*/task.json')))} available at {SEEDS_DIR}"},
    ])
    if args.json:
        import json; print(json.dumps(checks, indent=2))
    else:
        for check in checks:
            print(f"[{'ok' if check['ok'] else 'FAIL':4}] {check['check']}: {check['detail']}")
    return 0 if all(c["ok"] for c in checks) else 1
