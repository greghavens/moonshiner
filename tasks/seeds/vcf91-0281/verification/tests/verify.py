#!/usr/bin/env python3
"""Protected verifier for the VCF Operations credential rotation seed.

Compiles src/VcfOpsCredentialRotator.java together with the protected TestMain
harness, runs successful-drain and exhausted-drain scenarios against isolated
loopback mocks pinned to docs/contract.json, and asserts the exact wire shape
of every request the client made.

No VMware endpoint is contacted. The only socket used is 127.0.0.1.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROTECTED = os.path.join(ROOT, ".protected")
SRC_DIR = os.path.join(ROOT, "src")
CLIENT = os.path.join(SRC_DIR, "VcfOpsCredentialRotator.java")
CONTRACT = os.path.join(ROOT, "docs", "contract.json")
SOURCES = os.path.join(ROOT, "docs", "official_sources.json")

BASE_PATH = "/suite-api"
AUTH_PREFIX = "OpsToken "
OLD_CREDENTIAL_ID = "7b3f9c14-2e5a-4d68-9a01-3c6d5e8f1a20"
NEW_CREDENTIAL_ID = "2d9e4a71-6c08-4f3b-8b52-90a7d1e4c6f5"
EXPECTED_ADAPTER_IDS = {
    "a1c5e930-4b71-4f2e-9d83-1e6f0b27c845",
    "b2d6fa41-5c82-4a3f-8e94-2f70c138d956",
    "c3e70b52-6d93-4b40-9fa5-3081d249ea67",
}
NEW_PASSWORD = "NewRotationPassw0rd!"
OLD_PASSWORD = "OldRotationPassw0rd!"
EXPECTED_ADAPTER_KIND = "VMWARE"
EXPECTED_CREDENTIAL_KIND = "PRINCIPALCREDENTIAL"

FAILURES = []
CHECKS = []


def check(name, condition, detail=""):
    CHECKS.append((name, bool(condition)))
    if not condition:
        FAILURES.append("%s%s" % (name, (": " + detail) if detail else ""))
    return bool(condition)


def fatal(message):
    print("FAIL: %s" % message)
    print("\nRESULT: FAIL")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def compile_client(workdir):
    if not os.path.isfile(CLIENT):
        fatal("src/VcfOpsCredentialRotator.java is missing")
    java_files = sorted(f for f in os.listdir(SRC_DIR) if f.endswith(".java"))
    if java_files != ["VcfOpsCredentialRotator.java"]:
        fatal("the client must stay a single file; src/ contains %s"
              % ", ".join(java_files))

    classes = os.path.join(workdir, "classes")
    os.makedirs(classes, exist_ok=True)
    proc = subprocess.run(
        ["javac", "--release", "17", "-nowarn", "-d", classes,
         CLIENT, os.path.join(PROTECTED, "TestMain.java")],
        capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        print("FAIL: the client did not compile")
        print(proc.stdout)
        print(proc.stderr)
        print("\nRESULT: FAIL")
        sys.exit(1)
    return classes


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def start_mock(workdir, label):
    log_path = os.path.join(workdir, "%s-requests.jsonl" % label)
    port_path = os.path.join(workdir, "%s-port.txt" % label)
    stderr_path = os.path.join(workdir, "%s-mock.stderr" % label)
    stderr = open(stderr_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-B", os.path.join(PROTECTED, "mock_vcfops.py"),
         "--contract", CONTRACT, "--log", log_path, "--port-file", port_path],
        stdout=subprocess.DEVNULL, stderr=stderr)
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            stderr.close()
            with open(stderr_path, "r", encoding="utf-8") as handle:
                fatal("the mock exited before it began listening:\n%s"
                      % handle.read())
        if os.path.isfile(port_path) and os.path.getsize(port_path) > 0:
            with open(port_path, "r", encoding="utf-8") as handle:
                return proc, stderr, int(handle.read().strip()), log_path
        time.sleep(0.05)
    proc.kill()
    stderr.close()
    fatal("the mock did not start within 20s")


def run_client(classes, port, *extra_args):
    proc = subprocess.run(
        ["java", "-cp", classes, "TestMain", "http://127.0.0.1:%d" % port]
        + list(extra_args),
        capture_output=True, text=True, timeout=120)
    return proc


def exercise(classes, workdir, label, *extra_args):
    """Run one isolated scenario and return its process and complete wire log."""
    mock = stderr_handle = None
    try:
        mock, stderr_handle, port, log_path = start_mock(workdir, label)
        proc = run_client(classes, port, *extra_args)
    finally:
        if mock is not None:
            mock.terminate()
            try:
                mock.wait(timeout=10)
            except subprocess.TimeoutExpired:
                mock.kill()
                mock.wait(timeout=10)
        if stderr_handle is not None:
            stderr_handle.close()
    return proc, read_log(log_path)


def read_log(path):
    entries = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    entries.sort(key=lambda e: e["seq"])
    return entries


# ---------------------------------------------------------------------------
# Wire-shape assertions
# ---------------------------------------------------------------------------

def walk(value, path="body"):
    """Yield (json_pointer_ish_path, value) for every scalar in a body."""
    if isinstance(value, dict):
        for key, sub in value.items():
            yield from walk(sub, "%s.%s" % (path, key))
    elif isinstance(value, list):
        for index, sub in enumerate(value):
            yield from walk(sub, "%s[%d]" % (path, index))
    else:
        yield path, value


def assert_no_empty_values(entries):
    """Unset optional properties must be absent, not present-and-empty."""
    offenders = []
    for entry in entries:
        body = entry.get("body_json")
        if body is None:
            continue
        for path, value in walk(body):
            if value is None:
                offenders.append("seq %d %s is null" % (entry["seq"], path))
            elif isinstance(value, str) and value == "":
                offenders.append("seq %d %s is an empty string"
                                 % (entry["seq"], path))
    check("unset optional properties are omitted, never sent null or empty",
          not offenders, "; ".join(offenders[:6]))


def by_operation(entries, operation_id):
    return [e for e in entries if e.get("operationId") == operation_id]


def verify(entries, result, stdout, stderr, exit_code):
    # --- the client ran cleanly -------------------------------------------
    if not check("TestMain exited successfully", exit_code == 0,
                 "exit code %s; stderr tail: %s"
                 % (exit_code, stderr.strip()[-800:])):
        return
    if not check("the client printed a RESULT line", result is not None,
                 "stdout was: %s" % stdout.strip()[-800:]):
        return

    # --- only contracted operations, all of them successful ---------------
    unmatched = [e for e in entries if e.get("operationId") is None]
    check("every request hit an operation named by docs/contract.json",
          not unmatched,
          "; ".join("%s %s -> %s" % (e["method"], e["path"],
                                     e.get("reject_reason", "unmatched"))
                    for e in unmatched[:4]))

    bad_status = [e for e in entries if not (200 <= e["status"] < 300)]
    check("no request was rejected by the server", not bad_status,
          "; ".join("seq %d %s %s -> %d %s"
                    % (e["seq"], e["method"], e["path"], e["status"],
                       (e.get("response_json") or {}).get("message", ""))
                    for e in bad_status[:4]))

    acquires = by_operation(entries, "acquireToken")
    reads = by_operation(entries, "getCredential")
    creates = by_operation(entries, "createCredential")
    listings = by_operation(entries, "getAdapterInstancesUsingCredential")
    patches = by_operation(entries, "patchAdapterInstance")
    deletes = by_operation(entries, "deleteCredential")

    check("acquireToken was called exactly once", len(acquires) == 1,
          "called %d times" % len(acquires))
    check("createCredential was called exactly once", len(creates) == 1,
          "called %d times" % len(creates))
    check("deleteCredential was called exactly once", len(deletes) == 1,
          "called %d times" % len(deletes))
    check("getCredential read the outgoing credential", len(reads) >= 1,
          "never called")
    check("patchAdapterInstance was called once per adapter instance",
          len(patches) == len(EXPECTED_ADAPTER_IDS),
          "called %d times, expected %d" % (len(patches),
                                            len(EXPECTED_ADAPTER_IDS)))
    if not (acquires and creates and deletes and reads and patches and listings):
        return

    # --- base path, headers, transport ------------------------------------
    wrong_base = [e for e in entries if not e["path"].startswith(BASE_PATH + "/")]
    check("every request used the %s base path from the specification"
          % BASE_PATH, not wrong_base,
          "; ".join(e["path"] for e in wrong_base[:4]))

    no_accept = [e for e in entries
                 if (e["headers"].get("accept") or "") .split(";")[0].strip()
                 != "application/json"]
    check("every request asked for application/json", not no_accept,
          "; ".join("seq %d sent Accept: %r"
                    % (e["seq"], e["headers"].get("accept"))
                    for e in no_accept[:4]))

    bodied = [e for e in entries if e.get("body_raw")]
    wrong_ct = [e for e in bodied
                if (e["headers"].get("content-type") or "").split(";")[0].strip()
                != "application/json"]
    check("every bodied request declared application/json", not wrong_ct,
          "; ".join("seq %d sent Content-Type: %r"
                    % (e["seq"], e["headers"].get("content-type"))
                    for e in wrong_ct[:4]))

    unbodied_ops = {"getCredential", "getAdapterInstancesUsingCredential",
                    "deleteCredential"}
    stray_body = [e for e in entries
                  if e["operationId"] in unbodied_ops and e.get("body_raw")]
    check("operations the contract gives no request body sent none",
          not stray_body,
          "; ".join("seq %d %s" % (e["seq"], e["operationId"])
                    for e in stray_body[:4]))

    # --- acquireToken wire shape ------------------------------------------
    acquire = acquires[0]
    check("acquireToken was the first request", acquire["seq"] == entries[0]["seq"],
          "first request was %s" % entries[0].get("operationId"))
    check("acquireToken carried no Authorization header",
          acquire["headers"].get("authorization") is None,
          "sent %r" % acquire["headers"].get("authorization"))
    acquire_body = acquire.get("body_json") or {}
    check("acquireToken sent exactly username and password",
          set(acquire_body) == {"username", "password"},
          "sent %s" % ", ".join(sorted(acquire_body)) or "nothing")
    check("acquireToken omitted the unset optional authSource property",
          "authSource" not in acquire_body,
          "authSource was sent as %r" % acquire_body.get("authSource"))

    token = (acquire.get("response_json") or {}).get("token")
    expected_auth = AUTH_PREFIX + str(token)
    authed = [e for e in entries if e["seq"] != acquire["seq"]]
    wrong_auth = [e for e in authed
                  if e["headers"].get("authorization") != expected_auth]
    check("every authorized request carried the acquired token in the "
          "Authorization header", not wrong_auth,
          "; ".join("seq %d sent %r" % (e["seq"], e["headers"].get("authorization"))
                    for e in wrong_auth[:4]))

    # --- the outgoing credential was read before the replacement was made --
    read = reads[0]
    check("getCredential targeted the outgoing credential instance",
          read["path"] == "%s/api/credentials/%s" % (BASE_PATH, OLD_CREDENTIAL_ID),
          "read %s" % read["path"])
    create = creates[0]
    check("the outgoing credential was read before the replacement was created",
          read["seq"] < create["seq"],
          "read at seq %d, created at seq %d" % (read["seq"], create["seq"]))

    # --- createCredential wire shape --------------------------------------
    create_body = create.get("body_json") or {}
    check("createCredential sent only credential properties the contract permits",
          set(create_body) <= {"name", "adapterKindKey", "credentialKindKey",
                               "fields"},
          "also sent %s" % ", ".join(sorted(
              set(create_body) - {"name", "adapterKindKey", "credentialKindKey",
                                  "fields"})))
    check("createCredential omitted id, which the specification requires to be "
          "null on creation", "id" not in create_body,
          "sent id=%r" % create_body.get("id"))
    check("createCredential omitted the server-maintained editable property",
          "editable" not in create_body,
          "sent editable=%r" % create_body.get("editable"))
    check("createCredential carried every required credential property",
          all(k in create_body
              for k in ("name", "adapterKindKey", "credentialKindKey")),
          "sent %s" % ", ".join(sorted(create_body)))
    check("the replacement credential kept the adapter kind of the outgoing one",
          create_body.get("adapterKindKey") == EXPECTED_ADAPTER_KIND,
          "sent %r" % create_body.get("adapterKindKey"))
    check("the replacement credential kept the credential kind of the outgoing "
          "one",
          create_body.get("credentialKindKey") == EXPECTED_CREDENTIAL_KIND,
          "sent %r" % create_body.get("credentialKindKey"))

    fields = create_body.get("fields")
    check("createCredential sent the credential fields as an array",
          isinstance(fields, list) and len(fields) == 2,
          "sent %r" % (fields,))
    if isinstance(fields, list):
        shapes = [set(f) for f in fields if isinstance(f, dict)]
        check("every credential field was a bare name-value pair",
              len(shapes) == len(fields) and all(s == {"name", "value"}
                                                 for s in shapes),
              "field shapes were %s" % shapes)
        by_name = {f.get("name"): f.get("value") for f in fields
                   if isinstance(f, dict)}
        check("the new secret was sent in the PASSWORD field",
              by_name.get("PASSWORD") == NEW_PASSWORD,
              "PASSWORD field carried %r" % by_name.get("PASSWORD"))
        check("the USER field was carried over", "USER" in by_name,
              "fields were %s" % ", ".join(sorted(by_name)))

    created_id = (create.get("response_json") or {}).get("id")
    check("the mock issued the expected replacement credential id",
          created_id == NEW_CREDENTIAL_ID, "got %r" % created_id)

    # --- adapters were enumerated against the outgoing credential ----------
    off_target = [e for e in listings
                  if e["path"] != "%s/api/credentials/%s/adapters"
                  % (BASE_PATH, OLD_CREDENTIAL_ID)]
    check("adapter enumeration always targeted the outgoing credential",
          not off_target, "; ".join(e["path"] for e in off_target[:4]))

    enumerations = [e for e in listings if e["seq"] < patches[0]["seq"]]
    check("the adapters using the outgoing credential were enumerated before "
          "any adapter was repointed", len(enumerations) >= 1,
          "no enumeration preceded the first patch")

    known_resource_keys = {}
    for entry in enumerations:
        for adapter in (entry.get("response_json") or {}).get(
                "adapterInstancesInfoDto", []):
            known_resource_keys[adapter["id"]] = adapter["resourceKey"]

    # --- patchAdapterInstance wire shape ----------------------------------
    patched_ids = []
    for entry in patches:
        body = entry.get("body_json") or {}
        seq = entry["seq"]
        check("seq %d: the patch sent exactly id, resourceKey and "
              "credentialInstanceId" % seq,
              set(body) == {"id", "resourceKey", "credentialInstanceId"},
              "sent %s" % (", ".join(sorted(body)) or "nothing"))
        adapter_id = body.get("id")
        patched_ids.append(adapter_id)
        check("seq %d: the patch pointed the adapter at the replacement "
              "credential" % seq,
              body.get("credentialInstanceId") == NEW_CREDENTIAL_ID,
              "sent credentialInstanceId=%r" % body.get("credentialInstanceId"))
        expected_key = known_resource_keys.get(adapter_id)
        check("seq %d: the required resourceKey was echoed back exactly as it "
              "was read" % seq,
              expected_key is not None and body.get("resourceKey") == expected_key,
              "sent %s" % json.dumps(body.get("resourceKey"), sort_keys=True)[:300])

    check("every adapter instance using the outgoing credential was repointed",
          set(patched_ids) == EXPECTED_ADAPTER_IDS,
          "repointed %s" % ", ".join(sorted(str(i) for i in patched_ids)))
    check("no adapter instance was patched twice",
          len(patched_ids) == len(set(patched_ids)),
          "patched %s" % ", ".join(sorted(str(i) for i in patched_ids)))

    # --- ordering: create, repoint, drain, then delete ---------------------
    first_patch, last_patch = patches[0]["seq"], patches[-1]["seq"]
    delete = deletes[0]
    check("the replacement credential existed before any adapter was repointed",
          create["seq"] < first_patch,
          "created at seq %d, first patch at seq %d" % (create["seq"], first_patch))
    check("the outgoing credential was deleted only after every adapter had "
          "been repointed", delete["seq"] > last_patch,
          "deleted at seq %d, last patch at seq %d" % (delete["seq"], last_patch))
    check("the outgoing credential was the last thing the rotation touched",
          delete["seq"] == entries[-1]["seq"],
          "seq %d followed the delete" % entries[-1]["seq"])
    check("deleteCredential targeted the outgoing credential instance",
          delete["path"] == "%s/api/credentials/%s"
          % (BASE_PATH, OLD_CREDENTIAL_ID), "deleted %s" % delete["path"])

    drain_polls = [e for e in listings if e["seq"] > last_patch]
    check("no adapter was repointed once draining had begun",
          not [e for e in patches if drain_polls
               and e["seq"] > drain_polls[0]["seq"]],
          "a patch was issued after the first drain poll")
    check("the rotation polled the outgoing credential after repointing",
          len(drain_polls) >= 1, "no drain poll was made")
    if not drain_polls:
        return

    def bound_count(entry):
        return len((entry.get("response_json") or {}).get(
            "adapterInstancesInfoDto", []))

    check("the rotation kept polling while adapters were still on the old "
          "secret",
          any(bound_count(e) > 0 for e in drain_polls),
          "the first drain poll already came back empty, so nothing was drained")
    last_poll = drain_polls[-1]
    check("the final drain poll showed nothing was using the outgoing "
          "credential", bound_count(last_poll) == 0,
          "%d adapter(s) were still listed" % bound_count(last_poll))
    check("the outgoing credential was deleted only after the drain poll came "
          "back empty", last_poll["seq"] < delete["seq"],
          "the last poll ran at seq %d, the delete at seq %d"
          % (last_poll["seq"], delete["seq"]))

    # --- secrets stayed in request bodies ---------------------------------
    leaks = []
    for entry in entries:
        haystacks = [entry["path"], entry.get("query") or ""]
        haystacks += [v or "" for v in entry["headers"].values()]
        for secret, label in ((NEW_PASSWORD, "new"), (OLD_PASSWORD, "old")):
            if any(secret in h for h in haystacks):
                leaks.append("seq %d put the %s secret in the request line or "
                             "a header" % (entry["seq"], label))
    for entry in entries:
        if entry["operationId"] == "acquireToken":
            continue
        if OLD_PASSWORD in (entry.get("body_raw") or ""):
            leaks.append("seq %d resent the old secret in a request body"
                         % entry["seq"])
    check("secrets never left the request body they belong in", not leaks,
          "; ".join(leaks[:4]))

    assert_no_empty_values(entries)

    # --- the reported outcome matches what actually happened --------------
    check("the reported replacement credential id matches the created one",
          result.get("newCredentialId") == NEW_CREDENTIAL_ID,
          "reported %r" % result.get("newCredentialId"))
    check("the reported repointed adapters preserve the actual patch order",
          result.get("repointedAdapterIds") == patched_ids,
          "reported %s" % result.get("repointedAdapterIds"))
    check("the reported drain poll count matches the polls actually made",
          result.get("drainPolls") == len(drain_polls),
          "reported %r, log shows %d" % (result.get("drainPolls"),
                                         len(drain_polls)))
    check("the rotation reported that the outgoing credential was retired",
          result.get("oldCredentialDeleted") is True,
          "reported %r" % result.get("oldCredentialDeleted"))


def verify_timeout(entries, stdout, stderr, exit_code):
    """Prove maxDrainPolls is a safety bound, not permission to delete."""
    if not check("timeout scenario exited after observing the expected failure",
                 exit_code == 0 and any(
                     line.startswith("EXPECTED_TIMEOUT ")
                     for line in stdout.splitlines()),
                 "exit code %s; stdout tail: %s; stderr tail: %s"
                 % (exit_code, stdout.strip()[-500:], stderr.strip()[-500:])):
        return

    unmatched = [e for e in entries if e.get("operationId") is None]
    check("timeout scenario used only contracted operations", not unmatched,
          "; ".join("%s %s" % (e["method"], e["path"])
                    for e in unmatched[:4]))
    bad_status = [e for e in entries if not (200 <= e["status"] < 300)]
    check("timeout scenario stopped without provoking a server rejection",
          not bad_status,
          "; ".join("seq %d returned %d" % (e["seq"], e["status"])
                    for e in bad_status[:4]))

    acquires = by_operation(entries, "acquireToken")
    reads = by_operation(entries, "getCredential")
    creates = by_operation(entries, "createCredential")
    listings = by_operation(entries, "getAdapterInstancesUsingCredential")
    patches = by_operation(entries, "patchAdapterInstance")
    deletes = by_operation(entries, "deleteCredential")

    check("timeout scenario acquired exactly one token", len(acquires) == 1,
          "called %d times" % len(acquires))
    check("timeout scenario read the outgoing credential exactly once",
          len(reads) == 1, "called %d times" % len(reads))
    check("timeout scenario created exactly one replacement",
          len(creates) == 1, "called %d times" % len(creates))
    check("timeout scenario repointed every adapter",
          len(patches) == len(EXPECTED_ADAPTER_IDS)
          and {((e.get("body_json") or {}).get("id")) for e in patches}
          == EXPECTED_ADAPTER_IDS,
          "patch ids were %s" % [
              (e.get("body_json") or {}).get("id") for e in patches])
    wrong_patches = [e for e in patches
                     if set(e.get("body_json") or {})
                     != {"id", "resourceKey", "credentialInstanceId"}
                     or (e.get("body_json") or {}).get("credentialInstanceId")
                     != NEW_CREDENTIAL_ID]
    check("timeout scenario used the contracted partial-update shape",
          not wrong_patches,
          "; ".join("seq %d sent %s"
                    % (e["seq"], sorted((e.get("body_json") or {}).keys()))
                    for e in wrong_patches[:4]))
    check("timeout scenario never called deleteCredential", not deletes,
          "called %d times" % len(deletes))
    if not (acquires and reads and creates and patches and listings):
        return

    acquire_body = acquires[0].get("body_json") or {}
    check("a configured auth source was sent when acquiring the timeout token",
          acquire_body == {
              "username": "svc-rotation",
              "password": OLD_PASSWORD,
              "authSource": "Imported LDAP Server",
          }, "sent %s" % json.dumps(acquire_body, sort_keys=True))
    token = (acquires[0].get("response_json") or {}).get("token")
    wrong_auth = [e for e in entries[1:]
                  if e["headers"].get("authorization")
                  != AUTH_PREFIX + str(token)]
    check("timeout scenario authorized every post-acquire request",
          not wrong_auth,
          "; ".join("seq %d sent %r"
                    % (e["seq"], e["headers"].get("authorization"))
                    for e in wrong_auth[:4]))

    last_patch = patches[-1]["seq"]
    first_patch = patches[0]["seq"]
    interleaved_polls = [e for e in listings
                         if first_patch < e["seq"] < last_patch]
    check("timeout scenario finished repointing before it began draining",
          not interleaved_polls,
          "listing calls at seq %s interrupted the patches"
          % [e["seq"] for e in interleaved_polls])
    drain_polls = [e for e in listings if e["seq"] > last_patch]
    check("maxDrainPolls=1 made exactly one post-repoint drain poll",
          len(drain_polls) == 1,
          "made %d post-repoint polls" % len(drain_polls))
    if drain_polls:
        still_bound = (drain_polls[0].get("response_json") or {}).get(
            "adapterInstancesInfoDto", [])
        check("the bounded drain poll still reported outgoing-secret users",
              bool(still_bound), "the poll unexpectedly came back empty")
        check("the rotation stopped immediately after the exhausted drain poll",
              drain_polls[0]["seq"] == entries[-1]["seq"],
              "seq %d followed the drain poll" % entries[-1]["seq"])

    assert_no_empty_values(entries)


# ---------------------------------------------------------------------------

def check_docs():
    for path, label in ((CONTRACT, "docs/contract.json"),
                        (SOURCES, "docs/official_sources.json")):
        if not os.path.isfile(path):
            fatal("%s is missing" % label)
    with open(SOURCES, "r", encoding="utf-8") as handle:
        sources = json.load(handle)
    named = {op["operationId"] for op in sources["operations_used"]}
    with open(CONTRACT, "r", encoding="utf-8") as handle:
        contract = json.load(handle)
    contracted = {op["operationId"] for op in contract["operations"]}
    if named != contracted:
        fatal("docs/official_sources.json and docs/contract.json disagree on "
              "which operations are in play: %s" % (named ^ contracted))


def main():
    if shutil.which("javac") is None or shutil.which("java") is None:
        fatal("a JDK is required on PATH (javac and java)")
    check_docs()

    workdir = tempfile.mkdtemp(prefix="vcfops-rotate-")
    classes = compile_client(workdir)
    success_proc, entries = exercise(classes, workdir, "success")
    timeout_proc, timeout_entries = exercise(
        classes, workdir, "timeout", "--expect-timeout")

    result = None
    for line in success_proc.stdout.splitlines():
        if line.startswith("RESULT "):
            try:
                result = json.loads(line[len("RESULT "):])
            except ValueError:
                result = None

    verify(entries, result, success_proc.stdout, success_proc.stderr,
           success_proc.returncode)
    verify_timeout(timeout_entries, timeout_proc.stdout, timeout_proc.stderr,
                   timeout_proc.returncode)

    print("Success requests observed: %d" % len(entries))
    for entry in entries:
        print("  seq %-2d %-6s %-3d %s"
              % (entry["seq"], entry["method"], entry["status"],
                 entry.get("operationId") or ("UNCONTRACTED " + entry["path"])))
    print("Timeout requests observed: %d" % len(timeout_entries))
    for entry in timeout_entries:
        print("  seq %-2d %-6s %-3d %s"
              % (entry["seq"], entry["method"], entry["status"],
                 entry.get("operationId") or ("UNCONTRACTED " + entry["path"])))
    print("")
    for name, ok in CHECKS:
        print("  [%s] %s" % ("PASS" if ok else "FAIL", name))
    print("")
    if FAILURES:
        print("%d of %d checks failed:" % (len(FAILURES), len(CHECKS)))
        for failure in FAILURES:
            print("  - %s" % failure)
        print("\nRESULT: FAIL")
        shutil.rmtree(workdir, ignore_errors=True)
        sys.exit(1)
    print("All %d checks passed." % len(CHECKS))
    print("\nRESULT: PASS")
    shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
