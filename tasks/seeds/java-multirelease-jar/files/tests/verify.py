#!/usr/bin/env python3
"""Protected release checks for the runtime-greeter multi-release JAR."""

import hashlib
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
JAR = ROOT / "build/libs/runtime-greeter.jar"
PROVIDER = "com/acme/greeter/RuntimeGreeting.class"
VERSIONED_PROVIDER = "META-INF/versions/11/" + PROVIDER
SERVICE = "META-INF/services/com.acme.greeter.GreetingService"
REQUIRED_ENTRIES = {
    "META-INF/MANIFEST.MF",
    SERVICE,
    "com/acme/app/Main.class",
    "com/acme/greeter/GreetingService.class",
    PROVIDER,
    VERSIONED_PROVIDER,
}


# The candidate image deliberately need not contain a JDK.  This deterministic
# test double emits class-shaped fixtures from the checked-in source sets.  It
# lets build.sh and the archive assembler be tested without weakening the
# packaging contract or reaching out to the network.
FAKE_JAVAC = r'''#!/usr/bin/env python3
from pathlib import Path
import re
import struct
import sys


arguments = sys.argv[1:]
try:
    release = int(arguments[arguments.index("--release") + 1])
    destination = Path(arguments[arguments.index("-d") + 1])
except (ValueError, IndexError):
    print("fake javac: expected --release and -d", file=sys.stderr)
    raise SystemExit(2)

sources = [Path(argument) for argument in arguments if argument.endswith(".java")]
if not sources:
    print("fake javac: no Java sources", file=sys.stderr)
    raise SystemExit(2)

for source in sources:
    text = source.read_text(encoding="utf-8")
    package_match = re.search(r"\bpackage\s+([\w.]+)\s*;", text)
    type_match = re.search(r"\bpublic\s+(?:final\s+)?(?:class|interface)\s+(\w+)", text)
    if package_match is None or type_match is None:
        print(f"fake javac: cannot identify public type in {source}", file=sys.stderr)
        raise SystemExit(1)

    binary_name = package_match.group(1) + "." + type_match.group(1)
    message_match = re.search(r'\breturn\s+"([^"\\]*)"\s*;', text)
    message = message_match.group(1) if message_match else ""
    content = (
        struct.pack(">IHH", 0xCAFEBABE, 0, release + 44)
        + f"CLASS:{binary_name}\0MESSAGE:{message}\0".encode("utf-8")
    )
    output = destination / (binary_name.replace(".", "/") + ".class")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
'''


FAKE_JAVA = r'''#!/usr/bin/env python3
from pathlib import Path
import re
import sys
import zipfile


arguments = sys.argv[1:]
version_arguments = [arg for arg in arguments if arg.startswith("-Djdk.util.jar.version=")]
try:
    view = int(version_arguments[0].split("=", 1)[1])
    jar_path = Path(arguments[arguments.index("-cp") + 1])
except (IndexError, ValueError):
    print("fake java: expected a numeric JAR view and -cp", file=sys.stderr)
    raise SystemExit(2)

with zipfile.ZipFile(jar_path) as jar:
    manifest = jar.read("META-INF/MANIFEST.MF").decode("utf-8")
    multi_release = any(
        line.partition(":")[0].strip().lower() == "multi-release"
        and line.partition(":")[2].strip().lower() == "true"
        for line in manifest.splitlines()
    )
    service = jar.read("META-INF/services/com.acme.greeter.GreetingService")
    providers = [
        line.partition("#")[0].strip()
        for line in service.decode("utf-8").splitlines()
        if line.partition("#")[0].strip()
    ]
    if len(providers) != 1:
        print("fake java: expected exactly one configured provider", file=sys.stderr)
        raise SystemExit(1)

    base_name = providers[0].replace(".", "/") + ".class"
    selected = base_name
    selected_version = 0
    if multi_release:
        pattern = re.compile(r"META-INF/versions/(\d+)/" + re.escape(base_name) + r"\Z")
        for name in jar.namelist():
            match = pattern.fullmatch(name)
            if match and selected_version < int(match.group(1)) <= view:
                selected = name
                selected_version = int(match.group(1))

    content = jar.read(selected)
    message_match = re.search(rb"MESSAGE:([^\0]*)\0", content)
    if message_match is None:
        print(f"fake java: selected provider {selected} has no message", file=sys.stderr)
        raise SystemExit(1)
    print(message_match.group(1).decode("utf-8"))
'''


def run(*command, env=None):
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"command failed ({result.returncode}): {' '.join(map(str, command))}\n"
            f"{result.stdout}"
        )
    return result.stdout


def create_fake_jdk(root):
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    for name, content in (("javac", FAKE_JAVAC), ("java", FAKE_JAVA)):
        executable = bin_dir / name
        executable.write_text(content, encoding="utf-8")
        executable.chmod(0o755)


def class_major(content):
    if len(content) < 8 or content[:4] != b"\xca\xfe\xba\xbe":
        raise AssertionError("packaged provider is not a Java class file")
    return struct.unpack(">H", content[6:8])[0]


def manifest_attributes(content):
    attributes = {}
    for line in content.decode("utf-8").splitlines():
        if not line:
            continue
        name, separator, value = line.partition(":")
        if separator:
            attributes[name.strip().lower()] = value.strip()
    return attributes


def configured_providers(content):
    return [
        line.partition("#")[0].strip()
        for line in content.decode("utf-8").splitlines()
        if line.partition("#")[0].strip()
    ]


def check_source_contract():
    checks = {
        "src/main/java/com/acme/greeter/GreetingService.java": (
            r"\bpublic\s+interface\s+GreetingService\b",
            r"\bString\s+message\s*\(\s*\)\s*;",
        ),
        "src/main/java/com/acme/greeter/RuntimeGreeting.java": (
            r"\bclass\s+RuntimeGreeting\s+implements\s+GreetingService\b",
            r'\breturn\s+"base-java8"\s*;',
        ),
        "src/main/java11/com/acme/greeter/RuntimeGreeting.java": (
            r"\bclass\s+RuntimeGreeting\s+implements\s+GreetingService\b",
            r'\breturn\s+"version-java11"\s*;',
        ),
    }
    for relative_path, patterns in checks.items():
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        for pattern in patterns:
            assert re.search(pattern, text), (
                f"{relative_path} no longer satisfies the Java API/runtime contract"
            )


def check_archive():
    with zipfile.ZipFile(JAR) as jar:
        names = jar.namelist()
        missing = REQUIRED_ENTRIES.difference(names)
        assert not missing, f"required JAR entries are missing: {sorted(missing)}"
        for required in REQUIRED_ENTRIES:
            assert names.count(required) == 1, f"duplicate required JAR entry: {required}"

        attributes = manifest_attributes(jar.read("META-INF/MANIFEST.MF"))
        assert attributes.get("multi-release", "").lower() == "true", (
            "manifest does not declare Multi-Release: true"
        )
        assert configured_providers(jar.read(SERVICE)) == [
            "com.acme.greeter.RuntimeGreeting"
        ], "root service descriptor does not configure the expected provider"

        base_provider = jar.read(PROVIDER)
        java11_provider = jar.read(VERSIONED_PROVIDER)
        assert class_major(base_provider) == 52
        assert class_major(java11_provider) == 55
        assert b"MESSAGE:base-java8\0" in base_provider
        assert b"MESSAGE:version-java11\0" in java11_provider


def check_runtime_view(java, version, expected, environment):
    output = run(
        java,
        f"-Djdk.util.jar.version={version}",
        "-cp",
        str(JAR),
        "com.acme.app.Main",
        env=environment,
    )
    assert output == expected + "\n", (
        f"Java {version} view selected {output.strip()!r}, expected {expected!r}"
    )


def main():
    check_source_contract()
    with tempfile.TemporaryDirectory(prefix="runtime-greeter-fake-jdk-") as directory:
        fake_jdk = Path(directory)
        create_fake_jdk(fake_jdk)
        environment = os.environ.copy()
        environment["JAVA_HOME"] = str(fake_jdk)

        run("sh", "build.sh", env=environment)
        check_archive()
        first_digest = hashlib.sha256(JAR.read_bytes()).digest()

        run("sh", "build.sh", env=environment)
        check_archive()
        second_digest = hashlib.sha256(JAR.read_bytes()).digest()
        assert first_digest == second_digest, "successive builds produced different JAR bytes"

        java = str(fake_jdk / "bin/java")
        check_runtime_view(java, 8, "base-java8", environment)
        check_runtime_view(java, 11, "version-java11", environment)

    print("all multi-release JAR checks passed")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, UnicodeError, zipfile.BadZipFile) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
