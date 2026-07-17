"""Acceptance checks for jsonpath.py. Run: python3 test_jsonpath.py"""
import copy

from jsonpath import get, put, delete, PathError


def sample():
    return {
        "services": {
            "web": [
                {"host": "w1", "port": 8000},
                {"host": "w2", "port": 8001},
                {"host": "w3", "port": 8002, "tags": ["blue", "canary"]},
            ],
            "db": {"host": "pg1", "port": 5432},
        },
        "metrics": {"cpu.pct": 93.5, "disk[0]": "sda", "a\\b": "backslash"},
        "grid": [[1, 2], [3, 4]],
    }


# ------------------------------------------------------------------- get

def test_get_basic_paths():
    d = sample()
    assert get(d, "services.db.port") == 5432
    assert get(d, "services.web[2].port") == 8002
    assert get(d, "services.web[2].tags[1]") == "canary"
    assert get(d, "grid[1][0]") == 3
    assert get(d, "services.web[-1].host") == "w3"
    assert get(["a", "b"], "[0]") == "a"
    assert get([{"id": 7}], "[0].id") == 7


def test_get_escaped_keys():
    d = sample()
    assert get(d, "metrics.cpu\\.pct") == 93.5
    assert get(d, "metrics.disk\\[0]") == "sda"
    assert get(d, "metrics.a\\\\b") == "backslash"


def test_get_missing_raises_patherror():
    d = sample()
    for path in ["nope", "services.cache", "services.web[9]",
                 "services.web[-4]", "services.db.port.x",
                 "services.db[0]", "grid.rows"]:
        try:
            get(d, path)
            assert False, "get(%r) did not raise" % path
        except PathError:
            pass


def test_get_default_covers_failed_lookups():
    d = sample()
    assert get(d, "services.cache", "none") == "none"
    assert get(d, "services.web[9]", -1) == -1
    assert get(d, "services.db.port.x", None) is None
    assert get(d, "services.db.port", "kept") == 5432


def test_malformed_paths_are_valueerror_even_with_default():
    d = sample()
    bad = ["", "a..b", "a.", ".a", "a[", "a[]", "a[x]", "a[1.5]",
           "a[0]b", "a\\", "grid[ 1]", "grid[+1]"]
    for path in bad:
        try:
            get(d, path)
            assert False, "get(%r) did not raise" % path
        except ValueError:
            pass
        try:
            get(d, path, "default")
            assert False, "get(%r, default) did not raise" % path
        except ValueError:
            pass
    # PathError must not be a ValueError in disguise: defaults must NOT
    # apply to malformed paths but MUST apply to failed lookups.
    assert get(d, "totally.absent", 0) == 0


# ------------------------------------------------------------------- put

def test_put_replaces_and_adds_without_create():
    d = sample()
    put(d, "services.db.port", 5433)
    assert d["services"]["db"]["port"] == 5433
    put(d, "services.db.replica", "pg2")          # new final key: no create needed
    assert d["services"]["db"]["replica"] == "pg2"
    put(d, "services.web[0].port", 9000)
    assert d["services"]["web"][0]["port"] == 9000
    put(d, "grid[1][-1]", 44)
    assert d["grid"][1] == [3, 44]


def test_put_missing_intermediate_needs_create():
    d = sample()
    try:
        put(d, "alerts.email.to", "ops@example.com")
        assert False, "put built intermediates without create"
    except PathError:
        pass
    put(d, "alerts.email.to", "ops@example.com", create=True)
    assert d["alerts"] == {"email": {"to": "ops@example.com"}}
    # create must not clobber intermediates that already exist
    put(d, "services.db.pool.size", 10, create=True)
    assert d["services"]["db"]["host"] == "pg1"
    assert d["services"]["db"]["pool"] == {"size": 10}


def test_put_list_append_only_at_len_with_create():
    d = sample()
    try:
        put(d, "grid[2]", [5, 6])
        assert False, "appended without create"
    except PathError:
        pass
    put(d, "grid[2]", [5, 6], create=True)
    assert d["grid"][2] == [5, 6]
    try:
        put(d, "grid[5]", [9, 9], create=True)
        assert False, "put padded a list"
    except PathError:
        pass


def test_put_type_mismatch():
    d = sample()
    try:
        put(d, "services.web.primary", "w1")       # key into a list
        assert False, "keyed into a list"
    except PathError:
        pass
    try:
        put(d, "services.db[0]", "x")              # index into a dict
        assert False, "indexed into a dict"
    except PathError:
        pass


# ---------------------------------------------------------------- delete

def test_delete_key_and_element():
    d = sample()
    delete(d, "services.db.port")
    assert "port" not in d["services"]["db"]
    delete(d, "services.web[1]")
    assert [w["host"] for w in d["services"]["web"]] == ["w1", "w3"]
    delete(d, "metrics.cpu\\.pct")
    assert "cpu.pct" not in d["metrics"]


def test_delete_missing():
    d = sample()
    before = copy.deepcopy(d)
    for path in ["services.cache", "services.web[9]", "nope.deep.down"]:
        try:
            delete(d, path)
            assert False, "delete(%r) did not raise" % path
        except PathError:
            pass
        delete(d, path, missing_ok=True)           # silent no-op
    assert d == before
    try:
        delete(d, "a..b", missing_ok=True)         # malformed still explodes
        assert False, "missing_ok hid a malformed path"
    except ValueError:
        pass


def test_mutation_is_in_place():
    d = sample()
    alias = d["services"]
    put(d, "services.db.port", 6000)
    assert alias["db"]["port"] == 6000
    snapshot = copy.deepcopy(d)
    assert get(d, "services.web[0].host") == "w1"
    assert d == snapshot, "get mutated the document"


CHECKS = [
    test_get_basic_paths,
    test_get_escaped_keys,
    test_get_missing_raises_patherror,
    test_get_default_covers_failed_lookups,
    test_malformed_paths_are_valueerror_even_with_default,
    test_put_replaces_and_adds_without_create,
    test_put_missing_intermediate_needs_create,
    test_put_list_append_only_at_len_with_create,
    test_put_type_mismatch,
    test_delete_key_and_element,
    test_delete_missing,
    test_mutation_is_in_place,
]


def main():
    failures = 0
    for t in CHECKS:
        try:
            t()
        except Exception as e:
            failures += 1
            print("FAIL %s: %s: %s" % (t.__name__, type(e).__name__, e))
        else:
            print("ok   %s" % t.__name__)
    if failures:
        print("\n%d check(s) failed" % failures)
        raise SystemExit(1)
    print("\nall %d checks passed" % len(CHECKS))


if __name__ == "__main__":
    main()
