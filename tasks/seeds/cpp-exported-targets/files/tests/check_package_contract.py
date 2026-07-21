from pathlib import Path
import re


root = Path(__file__).resolve().parents[1]
template = (root / "cmake" / "BeaconQueueConfig.cmake.in").read_text()
source = re.sub(r"#.*", "", template)

macro = re.search(
    r"include\s*\(\s*CMakeFindDependencyMacro\s*\)", source, re.IGNORECASE
)
dependency = re.search(
    r"find_dependency\s*\(\s*Threads(?:\s+REQUIRED)?\s*\)",
    source,
    re.IGNORECASE,
)
targets = re.search(
    r"include\s*\(\s*[\"']?\$\{CMAKE_CURRENT_LIST_DIR\}"
    r"/BeaconQueueTargets\.cmake[\"']?\s*\)",
    source,
    re.IGNORECASE,
)

assert macro, "package config must include CMakeFindDependencyMacro"
assert dependency, "package config must discover Threads with find_dependency"
assert targets, "package config must load BeaconQueueTargets.cmake relatively"
assert macro.start() < dependency.start() < targets.start(), (
    "dependency discovery must be available and run before loading the export"
)
assert not re.search(
    r"add_library\s*\(\s*Threads::Threads", source, re.IGNORECASE
), "package config must not manufacture a placeholder Threads::Threads target"
assert str(root) not in template, "package config contains a source-tree path"

print("package dependency ownership contract passed")
