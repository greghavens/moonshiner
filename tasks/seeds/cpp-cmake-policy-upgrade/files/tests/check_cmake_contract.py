from pathlib import Path
import re


root = Path(__file__).resolve().parents[1]
source = (root / "CMakeLists.txt").read_text()

match = re.search(r"cmake_minimum_required\s*\(\s*VERSION\s+([0-9.]+)", source, re.I)
assert match, "cmake_minimum_required(VERSION ...) is required"
version = tuple(int(part) for part in match.group(1).split("."))
assert version >= (3, 21), f"minimum CMake version is still {match.group(1)}"

assert re.search(r"cmake_policy\s*\(\s*SET\s+CMP0077\s+NEW\s*\)", source, re.I), (
    "CMP0077 must be selected explicitly at the project boundary"
)

for forbidden in (
    r"\binclude_directories\s*\(",
    r"\badd_compile_options\s*\(",
    r"\badd_definitions\s*\(",
    r"CMAKE_CXX_FLAGS",
):
    assert not re.search(forbidden, source, re.I), f"global build state remains: {forbidden}"

for required in (
    "target_include_directories(telemetry_codec",
    "$<BUILD_INTERFACE:",
    "$<INSTALL_INTERFACE:",
    "target_compile_features(telemetry_codec",
    "target_compile_options(telemetry_codec",
):
    assert required in source, f"missing target-scoped contract: {required}"

print("CMake target and policy source contract passed")
