"""Zero-dependency checks for merge_configs. Run: python3 test_merge.py"""
from merge import merge_configs

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(name)
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


merged = merge_configs({"db": {"host": "localhost", "port": 5432}},
                       {"db": {"port": 6543}})
check("override applied", merged["db"]["port"], 6543)
check("untouched keys preserved", merged["db"]["host"], "localhost")

# A later, unrelated merge must not see the first call's keys.
other = merge_configs({"name": "svc"}, {})
check("no state leaks between calls", sorted(other.keys()), ["name"])

# The base config must never be mutated by a merge.
base = {"opts": {"debug": False}}
merge_configs(base, {"opts": {"debug": True}})
check("base not mutated", base["opts"]["debug"], False)

if FAILURES:
    print(f"\n{len(FAILURES)} check(s) failed")
    raise SystemExit(1)
print("\nall checks passed")
