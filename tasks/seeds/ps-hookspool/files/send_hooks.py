# Scripted webhook sender for the hookspool acceptance harness.
# Usage: python3 send_hooks.py <base_url> <secret>
# Sends a fixed series of requests to the receiver under test and prints
# one line per request: "<n> <http status>".
import hashlib
import hmac
import json
import sys
import urllib.error
import urllib.request


def sig(secret, body):
    return "sha256=" + hmac.new(secret.encode("utf-8"), body,
                                hashlib.sha256).hexdigest()


def send(base, method, path, body=None, headers=None):
    req = urllib.request.Request(base + path, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
            return resp.status
    except urllib.error.HTTPError as e:
        e.read()
        return e.code


def body_of(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def main():
    base, secret = sys.argv[1], sys.argv[2]
    wrong = "not-the-" + secret

    b1 = body_of({"id": "evt-001", "event": "disk.usage", "host": "nas-2", "pct": 91})
    b2 = body_of({"id": "evt-002", "event": "ops.note", "msg": "café réunion"})
    b3 = body_of({"id": "evt-090", "event": "disk.usage", "host": "nas-9", "pct": 50})
    b4 = body_of({"id": "evt-091", "event": "ops.note", "msg": "no sig"})
    b5 = body_of({"id": "evt-092", "event": "disk.usage", "host": "nas-3", "pct": 77})
    b6 = body_of({"id": "evt-004", "event": "fan.speed", "host": "nas-2", "rpm": 4100})
    b9 = body_of({"id": "evt-003", "event": "disk.usage", "host": "nas-2", "pct": 88})

    steps = [
        # 1-2: correctly signed
        ("POST", "/hooks", b1, {"X-Hook-Signature": sig(secret, b1)}),
        ("POST", "/hooks", b2, {"X-Hook-Signature": sig(secret, b2)}),
        # 3: signed with the wrong secret
        ("POST", "/hooks", b3, {"X-Hook-Signature": sig(wrong, b3)}),
        # 4: no signature header at all
        ("POST", "/hooks", b4, {}),
        # 5: stale signature (computed over a different body)
        ("POST", "/hooks", b5, {"X-Hook-Signature": sig(secret, b1)}),
        # 6: correct signature, uppercase hex digits
        ("POST", "/hooks", b6,
         {"X-Hook-Signature": "sha256=" + sig(secret, b6)[len("sha256="):].upper()}),
        # 7: wrong method
        ("GET", "/hooks", None, {}),
        # 8: wrong path
        ("POST", "/metrics", b1, {"X-Hook-Signature": sig(secret, b1)}),
        # 9: correctly signed, after all the noise
        ("POST", "/hooks", b9, {"X-Hook-Signature": sig(secret, b9)}),
    ]

    for n, (method, path, body, headers) in enumerate(steps, 1):
        print(n, send(base, method, path, body, headers), flush=True)


if __name__ == "__main__":
    main()
