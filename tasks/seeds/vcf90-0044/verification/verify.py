#!/usr/bin/env python3
"""Protected verification for the vcfvm VM-reconfiguration package.

Boots the contract-pinned loopback vCenter (mock_vcenter.py) on 127.0.0.1,
drives `python3 -m vcfvm` through seven scenarios, then reads the mock's request
log and asserts the exact wire shape of every request, including that unset
optional properties are omitted rather than sent as null or empty.

No live VMware endpoint is contacted.  Credentials are fixture dummies.

This file is protected.  Do not modify it.
"""

import ast
import base64
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(ROOT, "_verification")
CONTRACT_PATH = os.path.join(ROOT, "docs", "contract.json")
SOURCES_PATH = os.path.join(ROOT, "docs", "official_sources.json")

BASE_PATH = "/api"
USERNAME = "verify@vsphere.local"
PASSWORD = "fixture-not-a-real-password"
VM = "vm-3041"
MISSING_VM = "vm-9999"
NETWORK = "network-1105"
MISSING_NETWORK = "network-9999"
HOST_FREE_MIB = 6144
GIB = 1024 ** 3

STEP_ORDER = ["power_state", "memory", "disk", "nic", "power_on"]
STEP_OPERATION = {
    "power_state": "Vcenter.Vm.Power_get",
    "memory": "Vcenter.Vm.Hardware.Memory_update",
    "disk": "Vcenter.Vm.Hardware.Disk_create",
    "nic": "Vcenter.Vm.Hardware.Ethernet_create",
    "power_on": "Vcenter.Vm.Power_start",
}

CHECKS = 0
FAILURES = []


def check(label, condition, detail=None):
    global CHECKS
    CHECKS += 1
    if condition:
        return True
    FAILURES.append(label)
    print("FAIL %s" % label)
    if detail:
        for line in str(detail).splitlines():
            print("     " + line)
    return False


def check_eq(label, expected, actual):
    if expected == actual:
        return check(label, True)
    return check(label, False, "expected: %s\nactual:   %s" % (_show(expected), _show(actual)))


def _show(value):
    try:
        return json.dumps(value, sort_keys=True)
    except TypeError:
        return repr(value)


# ---------------------------------------------------------------------------
# mock lifecycle
# ---------------------------------------------------------------------------

class Mock:
    def __init__(self, name, host_free_mib=HOST_FREE_MIB, contract=CONTRACT_PATH,
                 disk_serial=2000, nic_serial=4000, power_state="POWERED_OFF"):
        self.name = name
        self.log_path = os.path.join(WORK, name + ".requests.jsonl")
        self.port_path = os.path.join(WORK, name + ".port")
        for path in (self.log_path, self.port_path):
            if os.path.exists(path):
                os.remove(path)
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "mock_vcenter.py"),
             "--contract", contract,
             "--log", self.log_path,
             "--port-file", self.port_path,
             "--host-free-memory-mib", str(host_free_mib),
             "--disk-serial", str(disk_serial),
             "--nic-serial", str(nic_serial),
             "--power-state", power_state],
            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.port = self._await_port()
        self.base_url = "http://127.0.0.1:%d" % self.port

    def _await_port(self):
        deadline = time.time() + 20
        while time.time() < deadline:
            if os.path.exists(self.port_path):
                with open(self.port_path, "r", encoding="utf-8") as handle:
                    text = handle.read().strip()
                if text:
                    return int(text)
            if self.proc.poll() is not None:
                out, err = self.proc.communicate()
                raise SystemExit("mock_vcenter.py exited early (%s)\n%s\n%s"
                                 % (self.proc.returncode, out, err))
            time.sleep(0.05)
        raise SystemExit("mock_vcenter.py did not report a port within 20s")

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)

    def records(self):
        if not os.path.exists(self.log_path):
            return []
        out = []
        with open(self.log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out


def run_cli(name, mock, vm, options, contract=None):
    report_path = os.path.join(WORK, name + ".report.json")
    if os.path.exists(report_path):
        os.remove(report_path)
    argv = [sys.executable, "-m", "vcfvm",
            "--base-url", mock.base_url,
            "--username", USERNAME,
            "--password", PASSWORD,
            "--vm", vm,
            "--report", report_path] + options
    if contract:
        argv += ["--contract", contract]
    env = dict(os.environ)
    env["PYTHONPATH"] = ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(argv, cwd=ROOT, env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    except subprocess.TimeoutExpired:
        check(name + ": CLI finished within 90s", False)
        return None, None
    report = None
    if check(name + ": wrote %s" % os.path.basename(report_path), os.path.exists(report_path),
             "stdout:\n%s\nstderr:\n%s" % (proc.stdout, proc.stderr)):
        with open(report_path, "r", encoding="utf-8") as handle:
            try:
                report = json.load(handle)
            except ValueError as err:
                check(name + ": report is valid JSON", False, err)
    combined = proc.stdout + proc.stderr
    check(name + ": never prints the password", PASSWORD not in combined)
    if report is not None:
        check(name + ": never stores the password in the report",
              PASSWORD not in json.dumps(report))
    return proc, report


# ---------------------------------------------------------------------------
# request assertions
# ---------------------------------------------------------------------------

def basic_header():
    blob = base64.b64encode(("%s:%s" % (USERNAME, PASSWORD)).encode("utf-8")).decode("ascii")
    return "Basic " + blob


def check_request(label, record, method, path, query, status, security, body,
                  base_path=BASE_PATH):
    check_eq(label + ": method", method, record["method"])
    check_eq(label + ": path", base_path + path, record["path"])
    check_eq(label + ": query string", query, record["query"])
    check_eq(label + ": response status", status, record["status"])
    headers = record["headers"]
    if security == "basic_auth":
        check_eq(label + ": Authorization header", basic_header(), headers.get("authorization"))
        check(label + ": sends no vmware-api-session-id header",
              "vmware-api-session-id" not in headers,
              "header present: %r" % headers.get("vmware-api-session-id"))
    else:
        check(label + ": sends a vmware-api-session-id header",
              bool(headers.get("vmware-api-session-id")))
        check(label + ": sends no Authorization header", "authorization" not in headers,
              "header present: %r" % headers.get("authorization"))
    if body is None:
        check(label + ": carries no request body", not record["body_raw"],
              "body: %r" % record["body_raw"])
        check(label + ": carries no Content-Type header", "content-type" not in headers,
              "header present: %r" % headers.get("content-type"))
    else:
        check_eq(label + ": request body", body, record["body"])
        media = (headers.get("content-type") or "").split(";")[0].strip().lower()
        check_eq(label + ": Content-Type", "application/json", media)


def check_session_consistency(name, records):
    tokens = {r["headers"].get("vmware-api-session-id")
              for r in records if r["operation_id"] != "Cis.Session_create"}
    check(name + ": reuses a single session for every authenticated call",
          len(tokens) == 1 and None not in tokens, "tokens seen: %s" % sorted(map(str, tokens)))


def check_sequence(name, records, expected):
    check_eq(name + ": request sequence", expected, [r["operation_id"] for r in records])
    return len(records) == len(expected)


# ---------------------------------------------------------------------------
# report assertions
# ---------------------------------------------------------------------------

def check_report(name, report, vm, outcome, statuses, results, error):
    if report is None:
        return
    check_eq(name + ": report keys", ["outcome", "steps", "vm"], sorted(report))
    check_eq(name + ": report vm", vm, report.get("vm"))
    check_eq(name + ": report outcome", outcome, report.get("outcome"))
    steps = report.get("steps")
    if not check(name + ": report lists five steps in order",
                 isinstance(steps, list) and len(steps) == len(STEP_ORDER), _show(steps)):
        return
    check_eq(name + ": report step order", STEP_ORDER, [s.get("name") for s in steps])
    for step in steps:
        stepname = step.get("name")
        if stepname not in STEP_ORDER:
            continue
        label = "%s: step %s" % (name, stepname)
        want_keys = ["name", "operation_id", "status"]
        if stepname in results:
            want_keys.append("result")
        if error and stepname == error[0]:
            want_keys.append("error")
        check_eq(label + " keys", sorted(want_keys), sorted(step))
        check_eq(label + " operation_id", STEP_OPERATION[stepname], step.get("operation_id"))
        check_eq(label + " status", statuses[stepname], step.get("status"))
        if stepname in results:
            check_eq(label + " result", results[stepname], step.get("result"))
        if error and stepname == error[0]:
            check_eq(label + " error", error[1], step.get("error"))


# ---------------------------------------------------------------------------
# static checks on the delivered package
# ---------------------------------------------------------------------------

def check_package_is_stdlib_only():
    package = os.path.join(ROOT, "vcfvm")
    if not check("vcfvm/ is a Python package", os.path.isfile(os.path.join(package, "__init__.py")),
                 "expected %s" % os.path.join(package, "__init__.py")):
        return
    sources = []
    for dirpath, dirnames, filenames in os.walk(package):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        sources.extend(os.path.join(dirpath, f) for f in filenames if f.endswith(".py"))
    check("vcfvm/ contains Python sources", bool(sources))
    stdlib = getattr(sys, "stdlib_module_names", None)
    allowed = set(stdlib) | {"vcfvm"} if stdlib else None
    offenders = {}
    imports_urllib_request = False
    subprocess_aliases = set()
    subprocess_functions = set()
    os_aliases = set()
    os_shell_functions = set()
    curl_shellouts = []
    for path in sorted(sources):
        with open(path, "r", encoding="utf-8") as handle:
            try:
                tree = ast.parse(handle.read(), filename=path)
            except SyntaxError as err:
                check("vcfvm module parses: %s" % os.path.relpath(path, ROOT), False, err)
                continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
                if "urllib.request" in names:
                    imports_urllib_request = True
                for alias in node.names:
                    if alias.name == "subprocess":
                        subprocess_aliases.add(alias.asname or alias.name)
                    elif alias.name == "os":
                        os_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
                if node.module == "urllib" and any(alias.name == "request"
                                                   for alias in node.names):
                    imports_urllib_request = True
                if node.module == "subprocess":
                    subprocess_functions.update(alias.asname or alias.name
                                                for alias in node.names)
                elif node.module == "os":
                    os_shell_functions.update(alias.asname or alias.name for alias in node.names
                                              if alias.name in ("system", "popen"))
            for name in names:
                top = name.split(".")[0]
                if allowed is not None and top not in allowed:
                    offenders.setdefault(os.path.relpath(path, ROOT), set()).add(top)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = node.func
            is_shell = (isinstance(called, ast.Name)
                        and called.id in subprocess_functions | os_shell_functions)
            if isinstance(called, ast.Attribute) and isinstance(called.value, ast.Name):
                is_shell = is_shell or called.value.id in subprocess_aliases
                is_shell = is_shell or (called.value.id in os_aliases
                                        and called.attr in ("system", "popen"))
            arguments = list(node.args) + [keyword.value for keyword in node.keywords]
            if is_shell and any(_literal_mentions_curl(value)
                                for arg in arguments for value in ast.walk(arg)):
                curl_shellouts.append(os.path.relpath(path, ROOT))
    if allowed is None:  # pragma: no cover - only on interpreters older than 3.10
        check("vcfvm imports only the standard library", True)
    else:
        check("vcfvm imports only the standard library", not offenders,
              "\n".join("%s -> %s" % (k, ", ".join(sorted(v)))
                        for k, v in sorted(offenders.items())))
    check("vcfvm uses urllib.request as its HTTP transport", imports_urllib_request)
    check("vcfvm does not shell out to curl", not curl_shellouts,
          "curl shell-out found in: %s" % ", ".join(sorted(set(curl_shellouts))))


def _literal_mentions_curl(node):
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
        return False
    for word in node.value.lower().split():
        command = word.strip("'\";|").rsplit("/", 1)[-1]
        if command in ("curl", "curl.exe"):
            return True
    return False


def check_protected_docs():
    with open(CONTRACT_PATH, "r", encoding="utf-8") as handle:
        contract = json.load(handle)
    with open(SOURCES_PATH, "r", encoding="utf-8") as handle:
        sources = json.load(handle)
    sha = "85151f6b1bb58f13b6ac0304bfec53904bea085f"
    spec_path = "specifications/vsphere/openapi/automation/vcenter.yaml"
    check_eq("contract pins the 9.0.0.0 tag", "9.0.0.0", contract["source"]["tag"])
    check_eq("contract pins the tag commit", sha, contract["source"]["commitSha"])
    check_eq("contract pins the vcenter.yaml spec path", spec_path, contract["source"]["specPath"])
    check_eq("contract base path", BASE_PATH, contract["basePath"])
    check_eq("official_sources pins the same commit", sha,
             sources["specification"]["repository_commit_sha"])
    check_eq("official_sources pins the same spec path", spec_path,
             sources["specification"]["spec_path"])
    contract_ops = [op["operationId"] for op in contract["operations"]]
    check_eq("official_sources records every contract operationId",
             sorted(contract_ops), sorted(o["operationId"] for o in sources["operations"]))
    check_eq("contract names the seven in-scope operations",
             sorted(list(STEP_OPERATION.values()) + ["Cis.Session_create", "Cis.Session_delete"]),
             sorted(contract_ops))
    return contract


# ---------------------------------------------------------------------------
# scenarios
# ---------------------------------------------------------------------------

def scenario_full():
    name = "full"
    mock = Mock(name)
    try:
        options = ["--memory-mib", "8192", "--hot-add-enabled",
                   "--disk-capacity-gib", "40", "--disk-name", "payments-db-data",
                   "--disk-scsi-bus", "0", "--disk-scsi-unit", "1",
                   "--nic-network", NETWORK, "--nic-type", "VMXNET3", "--nic-start-connected"]
        proc, report = run_cli(name, mock, VM, options)
        records = mock.records()
    finally:
        mock.stop()
    if proc is not None:
        check_eq(name + ": exit code signals the failed change", 1, proc.returncode)
    ordered = ["Cis.Session_create", "Vcenter.Vm.Power_get", "Vcenter.Vm.Hardware.Memory_update",
               "Vcenter.Vm.Hardware.Disk_create", "Vcenter.Vm.Hardware.Ethernet_create",
               "Vcenter.Vm.Power_start", "Cis.Session_delete"]
    if check_sequence(name, records, ordered):
        check_session_consistency(name, records)
        check_request(name + " session create", records[0], "POST", "/session", {}, 201,
                      "basic_auth", None)
        check_request(name + " power get", records[1], "GET", "/vcenter/vm/%s/power" % VM, {},
                      200, "api_key_auth", None)
        check_request(name + " memory update", records[2], "PATCH",
                      "/vcenter/vm/%s/hardware/memory" % VM, {}, 204, "api_key_auth",
                      {"size_mib": 8192, "hot_add_enabled": True})
        check_request(name + " disk create", records[3], "POST",
                      "/vcenter/vm/%s/hardware/disk" % VM, {}, 201, "api_key_auth",
                      {"type": "SCSI", "scsi": {"bus": 0, "unit": 1},
                       "new_vmdk": {"name": "payments-db-data", "capacity": 40 * GIB}})
        check_request(name + " ethernet create", records[4], "POST",
                      "/vcenter/vm/%s/hardware/ethernet" % VM, {}, 201, "api_key_auth",
                      {"type": "VMXNET3", "start_connected": True,
                       "backing": {"type": "STANDARD_PORTGROUP", "network": NETWORK}})
        check_request(name + " power start", records[5], "POST", "/vcenter/vm/%s/power" % VM,
                      {"action": "start"}, 500, "api_key_auth", None)
        check_request(name + " session delete", records[6], "DELETE", "/session", {}, 204,
                      "api_key_auth", None)
    check_report(
        name, report, VM, "failed",
        {"power_state": "succeeded", "memory": "succeeded", "disk": "succeeded",
         "nic": "succeeded", "power_on": "failed"},
        {"power_state": {"state": "POWERED_OFF"}, "memory": {"memory_mib": 8192},
         "disk": {"disk": "2001"}, "nic": {"nic": "4001"}},
        ("power_on", {
            "http_status": 500,
            "error_type": "UNABLE_TO_ALLOCATE_RESOURCE",
            "message": ("The host does not have sufficient memory resources to satisfy the "
                        "reservation for virtual machine payments-db-01: 8192 MiB requested, "
                        "6144 MiB available."),
        }))


def scenario_minimal():
    name = "minimal"
    mock = Mock(name, disk_serial=2718, nic_serial=3141)
    try:
        options = ["--memory-mib", "8192", "--disk-capacity-gib", "40",
                   "--nic-network", NETWORK]
        proc, report = run_cli(name, mock, VM, options)
        records = mock.records()
    finally:
        mock.stop()
    if proc is not None:
        check_eq(name + ": exit code signals the failed change", 1, proc.returncode)
    ordered = ["Cis.Session_create", "Vcenter.Vm.Power_get", "Vcenter.Vm.Hardware.Memory_update",
               "Vcenter.Vm.Hardware.Disk_create", "Vcenter.Vm.Hardware.Ethernet_create",
               "Vcenter.Vm.Power_start", "Cis.Session_delete"]
    if check_sequence(name, records, ordered):
        check_request(name + " memory update", records[2], "PATCH",
                      "/vcenter/vm/%s/hardware/memory" % VM, {}, 204, "api_key_auth",
                      {"size_mib": 8192})
        check_request(name + " disk create", records[3], "POST",
                      "/vcenter/vm/%s/hardware/disk" % VM, {}, 201, "api_key_auth",
                      {"new_vmdk": {"capacity": 40 * GIB}})
        check_request(name + " ethernet create", records[4], "POST",
                      "/vcenter/vm/%s/hardware/ethernet" % VM, {}, 201, "api_key_auth",
                      {"backing": {"type": "STANDARD_PORTGROUP", "network": NETWORK}})
        check_request(name + " power start", records[5], "POST", "/vcenter/vm/%s/power" % VM,
                      {"action": "start"}, 500, "api_key_auth", None)
        check_request(name + " session delete", records[6], "DELETE", "/session", {}, 204,
                      "api_key_auth", None)
    check_report(
        name, report, VM, "failed",
        {"power_state": "succeeded", "memory": "succeeded", "disk": "succeeded",
         "nic": "succeeded", "power_on": "failed"},
        {"power_state": {"state": "POWERED_OFF"}, "memory": {"memory_mib": 8192},
         "disk": {"disk": "2719"}, "nic": {"nic": "3142"}},
        ("power_on", {
            "http_status": 500,
            "error_type": "UNABLE_TO_ALLOCATE_RESOURCE",
            "message": ("The host does not have sufficient memory resources to satisfy the "
                        "reservation for virtual machine payments-db-01: 8192 MiB requested, "
                        "6144 MiB available."),
        }))


def scenario_unknown_vm():
    name = "unknown-vm"
    mock = Mock(name)
    try:
        options = ["--memory-mib", "8192", "--disk-capacity-gib", "40",
                   "--nic-network", NETWORK]
        proc, report = run_cli(name, mock, MISSING_VM, options)
        records = mock.records()
    finally:
        mock.stop()
    if proc is not None:
        check_eq(name + ": exit code signals the failed change", 1, proc.returncode)
    ordered = ["Cis.Session_create", "Vcenter.Vm.Power_get", "Cis.Session_delete"]
    if check_sequence(name, records, ordered):
        check_request(name + " power get", records[1], "GET",
                      "/vcenter/vm/%s/power" % MISSING_VM, {}, 404, "api_key_auth", None)
        check_request(name + " session delete", records[2], "DELETE", "/session", {}, 204,
                      "api_key_auth", None)
    check_report(
        name, report, MISSING_VM, "failed",
        {"power_state": "failed", "memory": "not_attempted", "disk": "not_attempted",
         "nic": "not_attempted", "power_on": "not_attempted"},
        {},
        ("power_state", {
            "http_status": 404,
            "error_type": "NOT_FOUND",
            "message": "The virtual machine %r could not be found." % MISSING_VM,
        }))


def scenario_success():
    name = "success"
    mock = Mock(name)
    try:
        options = ["--memory-mib", "4096", "--disk-capacity-gib", "40",
                   "--nic-network", NETWORK]
        proc, report = run_cli(name, mock, VM, options)
        records = mock.records()
    finally:
        mock.stop()
    if proc is not None:
        check_eq(name + ": exit code signals a clean change", 0, proc.returncode)
    ordered = ["Cis.Session_create", "Vcenter.Vm.Power_get", "Vcenter.Vm.Hardware.Memory_update",
               "Vcenter.Vm.Hardware.Disk_create", "Vcenter.Vm.Hardware.Ethernet_create",
               "Vcenter.Vm.Power_start", "Cis.Session_delete"]
    if check_sequence(name, records, ordered):
        check_request(name + " memory update", records[2], "PATCH",
                      "/vcenter/vm/%s/hardware/memory" % VM, {}, 204, "api_key_auth",
                      {"size_mib": 4096})
        check_request(name + " power start", records[5], "POST", "/vcenter/vm/%s/power" % VM,
                      {"action": "start"}, 204, "api_key_auth", None)
    check_report(
        name, report, VM, "succeeded",
        {"power_state": "succeeded", "memory": "succeeded", "disk": "succeeded",
         "nic": "succeeded", "power_on": "succeeded"},
        {"power_state": {"state": "POWERED_OFF"}, "memory": {"memory_mib": 4096},
         "disk": {"disk": "2001"}, "nic": {"nic": "4001"},
         "power_on": {"state": "POWERED_ON"}},
        None)


def scenario_running_memory_failure():
    """Power state must come from the endpoint, and a failed second step stops the plan."""
    name = "running-memory-failure"
    mock = Mock(name, power_state="POWERED_ON")
    try:
        options = ["--memory-mib", "4096", "--hot-add-enabled",
                   "--disk-capacity-gib", "40", "--nic-network", NETWORK]
        proc, report = run_cli(name, mock, VM, options)
        records = mock.records()
    finally:
        mock.stop()
    if proc is not None:
        check_eq(name + ": exit code signals the failed change", 1, proc.returncode)
    ordered = ["Cis.Session_create", "Vcenter.Vm.Power_get",
               "Vcenter.Vm.Hardware.Memory_update", "Cis.Session_delete"]
    if check_sequence(name, records, ordered):
        check_session_consistency(name, records)
        check_request(name + " power get", records[1], "GET",
                      "/vcenter/vm/%s/power" % VM, {}, 200, "api_key_auth", None)
        check_request(name + " memory update", records[2], "PATCH",
                      "/vcenter/vm/%s/hardware/memory" % VM, {}, 400, "api_key_auth",
                      {"size_mib": 4096, "hot_add_enabled": True})
        check_request(name + " session delete", records[3], "DELETE", "/session", {}, 204,
                      "api_key_auth", None)
    check_report(
        name, report, VM, "failed",
        {"power_state": "succeeded", "memory": "failed", "disk": "not_attempted",
         "nic": "not_attempted", "power_on": "not_attempted"},
        {"power_state": {"state": "POWERED_ON"}},
        ("memory", {
            "http_status": 400,
            "error_type": "NOT_ALLOWED_IN_CURRENT_STATE",
            "message": ("hot_add_enabled may only be changed while the virtual machine "
                        "is powered off."),
        }))


def scenario_nic_failure():
    """A middle-step failure leaves earlier changes landed and stops power-on."""
    name = "nic-failure"
    mock = Mock(name, disk_serial=7300, nic_serial=9100)
    try:
        options = ["--memory-mib", "4096", "--disk-capacity-gib", "40",
                   "--nic-network", MISSING_NETWORK]
        proc, report = run_cli(name, mock, VM, options)
        records = mock.records()
    finally:
        mock.stop()
    if proc is not None:
        check_eq(name + ": exit code signals the failed change", 1, proc.returncode)
    ordered = ["Cis.Session_create", "Vcenter.Vm.Power_get",
               "Vcenter.Vm.Hardware.Memory_update", "Vcenter.Vm.Hardware.Disk_create",
               "Vcenter.Vm.Hardware.Ethernet_create", "Cis.Session_delete"]
    if check_sequence(name, records, ordered):
        check_session_consistency(name, records)
        check_request(name + " ethernet create", records[4], "POST",
                      "/vcenter/vm/%s/hardware/ethernet" % VM, {}, 404, "api_key_auth",
                      {"backing": {"type": "STANDARD_PORTGROUP",
                                   "network": MISSING_NETWORK}})
        check_request(name + " session delete", records[5], "DELETE", "/session", {}, 204,
                      "api_key_auth", None)
    check_report(
        name, report, VM, "failed",
        {"power_state": "succeeded", "memory": "succeeded", "disk": "succeeded",
         "nic": "failed", "power_on": "not_attempted"},
        {"power_state": {"state": "POWERED_OFF"}, "memory": {"memory_mib": 4096},
         "disk": {"disk": "7301"}},
        ("nic", {
            "http_status": 404,
            "error_type": "NOT_FOUND",
            "message": "The network %r could not be found." % MISSING_NETWORK,
        }))


def scenario_relocated_base_path():
    """The routing must come out of the contract file, not out of hard-coded strings.

    The same seven operations are served under a different server base path; a
    client that reads --contract follows it, a client with baked-in URLs does not.
    """
    name = "relocated"
    variant_path = os.path.join(WORK, "relocated-contract.json")
    with open(CONTRACT_PATH, "r", encoding="utf-8") as handle:
        variant = json.load(handle)
    base_path = "/automation/api"
    variant["basePath"] = base_path
    with open(variant_path, "w", encoding="utf-8") as handle:
        json.dump(variant, handle, indent=2)

    mock = Mock(name, contract=variant_path)
    try:
        options = ["--memory-mib", "8192", "--disk-capacity-gib", "40",
                   "--nic-network", NETWORK]
        proc, report = run_cli(name, mock, VM, options, contract=variant_path)
        records = mock.records()
    finally:
        mock.stop()
    if proc is not None:
        check_eq(name + ": exit code signals the failed change", 1, proc.returncode)
    ordered = ["Cis.Session_create", "Vcenter.Vm.Power_get", "Vcenter.Vm.Hardware.Memory_update",
               "Vcenter.Vm.Hardware.Disk_create", "Vcenter.Vm.Hardware.Ethernet_create",
               "Vcenter.Vm.Power_start", "Cis.Session_delete"]
    if check_sequence(name, records, ordered):
        check_request(name + " session create", records[0], "POST", "/session", {}, 201,
                      "basic_auth", None, base_path=base_path)
        check_request(name + " memory update", records[2], "PATCH",
                      "/vcenter/vm/%s/hardware/memory" % VM, {}, 204, "api_key_auth",
                      {"size_mib": 8192}, base_path=base_path)
        check_request(name + " power start", records[5], "POST", "/vcenter/vm/%s/power" % VM,
                      {"action": "start"}, 500, "api_key_auth", None, base_path=base_path)
    check_report(
        name, report, VM, "failed",
        {"power_state": "succeeded", "memory": "succeeded", "disk": "succeeded",
         "nic": "succeeded", "power_on": "failed"},
        {"power_state": {"state": "POWERED_OFF"}, "memory": {"memory_mib": 8192},
         "disk": {"disk": "2001"}, "nic": {"nic": "4001"}},
        ("power_on", {
            "http_status": 500,
            "error_type": "UNABLE_TO_ALLOCATE_RESOURCE",
            "message": ("The host does not have sufficient memory resources to satisfy the "
                        "reservation for virtual machine payments-db-01: 8192 MiB requested, "
                        "6144 MiB available."),
        }))


def main():
    if os.path.isdir(WORK):
        shutil.rmtree(WORK)
    os.makedirs(WORK)
    check_protected_docs()
    check_package_is_stdlib_only()
    scenario_full()
    scenario_minimal()
    scenario_unknown_vm()
    scenario_success()
    scenario_running_memory_failure()
    scenario_nic_failure()
    scenario_relocated_base_path()
    print("")
    print("checks: %d, failures: %d" % (CHECKS, len(FAILURES)))
    if FAILURES:
        print("FAILED")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
