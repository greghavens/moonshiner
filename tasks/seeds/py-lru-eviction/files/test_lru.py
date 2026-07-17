"""Zero-dependency checks for LRUCache. Run: python3 test_lru.py"""
from lru import LRUCache

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(name)
        print(f"FAIL {name}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {name}")


c = LRUCache(2)
c.put("a", 1)
c.put("b", 2)
check("basic get", c.get("a"), 1)

# 'a' was just read, so 'b' is now the least recently used and must go.
c.put("c", 3)
check("evicts LRU (b)", c.get("b"), None)
check("keeps recently-read (a)", c.get("a"), 1)
check("keeps newest (c)", c.get("c"), 3)

c2 = LRUCache(2)
c2.put("x", 1)
c2.put("y", 2)
c2.put("x", 10)
check("overwrite keeps size", len(c2), 2)
check("overwrite updates value", c2.get("x"), 10)
c2.put("z", 3)  # y is now the LRU (x was re-put above)
check("evicts y after overwrite", c2.get("y"), None)
check("keeps x after overwrite", c2.get("x"), 10)

if FAILURES:
    print(f"\n{len(FAILURES)} check(s) failed")
    raise SystemExit(1)
print("\nall checks passed")
