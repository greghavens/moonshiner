"""Acceptance tests for the binary record codec. Run: python3 test_binrec.py"""


def expect(exc_type, fn, *args):
    try:
        fn(*args)
    except exc_type as e:
        return e
    raise AssertionError(f"{fn}{args!r} should raise {exc_type.__name__}")


def main():
    from binrec import Schema, SchemaError, PackError, UnpackError

    # all three are ValueErrors so callers can catch broadly
    assert issubclass(SchemaError, ValueError)
    assert issubclass(PackError, ValueError)
    assert issubclass(UnpackError, ValueError)

    # -- layout: declaration order, big-endian ints, NUL-padded strings,
    #    consecutive flags packed LSB-first into shared bytes --
    schema = Schema([
        ("id", "uint32"),
        ("kind", "uint8"),
        ("name", "str8"),
        ("active", "flag"),
        ("admin", "flag"),
        ("deleted", "flag"),
        ("score", "uint16"),
    ])
    assert schema.size == 4 + 1 + 8 + 1 + 2

    record = {
        "id": 0x01020304, "kind": 7, "name": "bob",
        "active": True, "admin": False, "deleted": True, "score": 0x0A0B,
    }
    wire = schema.pack(record)
    assert isinstance(wire, bytes) and len(wire) == schema.size
    assert wire == (b"\x01\x02\x03\x04"          # id, big-endian
                    b"\x07"                       # kind
                    b"bob\x00\x00\x00\x00\x00"    # name, NUL padded to 8
                    b"\x05"                       # flags: bit0 active, bit2 deleted
                    b"\x0a\x0b"), wire            # score
    assert schema.unpack(wire) == record

    # -- boundary values round-trip --
    nums = Schema([("a", "uint8"), ("b", "uint16"), ("c", "uint32")])
    assert nums.size == 7
    top = {"a": 255, "b": 65535, "c": 4294967295}
    assert nums.unpack(nums.pack(top)) == top
    zero = {"a": 0, "b": 0, "c": 0}
    assert nums.pack(zero) == b"\x00" * 7
    assert nums.unpack(b"\x00" * 7) == zero

    # -- nine flags need two bytes; bit 0 of each byte comes first --
    many = Schema([(f"f{i}", "flag") for i in range(9)])
    assert many.size == 2
    rec = {f"f{i}": False for i in range(9)}
    rec["f0"] = True
    rec["f8"] = True
    assert many.pack(rec) == b"\x01\x01"
    assert many.unpack(b"\x01\x01") == rec

    # -- a non-flag field ends the run: the next flag starts a fresh byte --
    split = Schema([("a", "flag"), ("n", "uint8"), ("b", "flag")])
    assert split.size == 3
    assert split.pack({"a": True, "n": 0xAB, "b": True}) == b"\x01\xab\x01"

    # -- strings are UTF-8; a full-width value uses all N bytes --
    s = Schema([("tag", "str8")])
    assert s.pack({"tag": "exactly8"}) == b"exactly8"
    assert s.unpack(b"exactly8") == {"tag": "exactly8"}
    assert s.unpack(s.pack({"tag": "café"})) == {"tag": "café"}   # 5 UTF-8 bytes
    assert s.pack({"tag": ""}) == b"\x00" * 8

    # -- schema validation --
    expect(SchemaError, Schema, [])
    expect(SchemaError, Schema, [("x", "int8")])          # unknown type
    expect(SchemaError, Schema, [("x", "str")])           # missing length
    expect(SchemaError, Schema, [("x", "str0")])
    expect(SchemaError, Schema, [("x", "strfoo")])
    expect(SchemaError, Schema, [("x", "uint8"), ("x", "flag")])  # duplicate name
    expect(SchemaError, Schema, [("", "uint8")])          # empty name

    # -- pack validation: names in messages, strict types --
    e = expect(PackError, nums.pack, {"a": 1, "b": 2})            # missing c
    assert "c" in str(e)
    e = expect(PackError, nums.pack, {"a": 1, "b": 2, "c": 3, "d": 4})
    assert "d" in str(e)                                          # unexpected key
    e = expect(PackError, nums.pack, {"a": 256, "b": 0, "c": 0})  # out of range
    assert "a" in str(e)
    expect(PackError, nums.pack, {"a": -1, "b": 0, "c": 0})
    expect(PackError, nums.pack, {"a": 0, "b": 65536, "c": 0})
    expect(PackError, nums.pack, {"a": 0, "b": 0, "c": 4294967296})
    expect(PackError, nums.pack, {"a": True, "b": 0, "c": 0})     # bool is not a uint
    expect(PackError, nums.pack, {"a": 1.0, "b": 0, "c": 0})      # float is not a uint
    expect(PackError, split.pack, {"a": 1, "n": 0, "b": True})    # 1 is not a bool
    expect(PackError, s.pack, {"tag": "ninechars"})               # too long encoded
    expect(PackError, s.pack, {"tag": "olé" * 3})                 # 12 UTF-8 bytes
    expect(PackError, s.pack, {"tag": "a\x00b"})                  # NUL is padding only
    expect(PackError, s.pack, {"tag": 42})

    # -- unpack validation --
    expect(UnpackError, nums.unpack, b"\x00" * 6)                 # too short
    expect(UnpackError, nums.unpack, b"\x00" * 8)                 # too long
    expect(UnpackError, s.unpack, b"\xff\xfe\x00\x00\x00\x00\x00\x00")  # bad UTF-8
    expect(UnpackError, split.unpack, b"\x02\x00\x01")            # stray flag bit
    expect(UnpackError, many.unpack, b"\x01\xf0")                 # bits past f8

    print("ok")


if __name__ == "__main__":
    main()
