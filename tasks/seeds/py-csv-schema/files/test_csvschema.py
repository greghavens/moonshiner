"""Acceptance tests for CSV schema inference. Run: python3 test_csvschema.py"""


def col(schema, name):
    matches = [c for c in schema if c["name"] == name]
    assert len(matches) == 1, f"expected exactly one column {name!r} in {schema}"
    return matches[0]


def main():
    from csvschema import infer_schema

    # -- one clean column of each type --
    header = ["id", "price", "active", "signup", "note"]
    rows = [
        ["1", "9.99", "true", "2026-01-15", "hello"],
        ["2", "0.5", "False", "2026-02-01", "42nd street"],
        ["30", "-3.25", "TRUE", "2026-12-31", ""],
    ]
    schema = infer_schema(header, rows)
    assert [c["name"] for c in schema] == header  # column order preserved
    assert col(schema, "id") == {"name": "id", "type": "int", "nullable": False}
    assert col(schema, "price") == {"name": "price", "type": "float", "nullable": False}
    assert col(schema, "active") == {"name": "active", "type": "bool", "nullable": False}
    assert col(schema, "signup") == {"name": "signup", "type": "date", "nullable": False}
    assert col(schema, "note") == {"name": "note", "type": "str", "nullable": True}

    # -- int accepts signs; values are stripped before classification --
    schema = infer_schema(["n"], [["+5"], ["-12"], [" 42 "], ["0"]])
    assert col(schema, "n") == {"name": "n", "type": "int", "nullable": False}

    # -- leading zeros mean identifier, not number (zip codes!) --
    schema = infer_schema(["zip"], [["02139"], ["10001"]])
    assert col(schema, "zip")["type"] == "str"
    schema = infer_schema(["k"], [["0"], ["10"]])  # a lone 0 is still an int
    assert col(schema, "k")["type"] == "int"

    # -- float forms: decimals and exponents --
    schema = infer_schema(["x"], [["3.14"], ["-0.5"], ["1e6"], ["2.5E-3"]])
    assert col(schema, "x")["type"] == "float"
    # things Python's float() swallows that we must NOT call numbers
    for v in ["NaN", "inf", "-Infinity", "1_000", ".", "1.2.3"]:
        schema = infer_schema(["x"], [[v]])
        assert col(schema, "x")["type"] == "str", (v, schema)
    schema = infer_schema(["x"], [["1_0"]])
    assert col(schema, "x")["type"] == "str"  # int() would happily parse this

    # -- bool is exactly true/false, any case; yes/no and 0/1 are not bool --
    schema = infer_schema(["b"], [["true"], ["FALSE"], ["True"]])
    assert col(schema, "b")["type"] == "bool"
    schema = infer_schema(["b"], [["yes"], ["no"]])
    assert col(schema, "b")["type"] == "str"
    schema = infer_schema(["b"], [["0"], ["1"]])
    assert col(schema, "b")["type"] == "int"

    # -- dates are strict, zero-padded ISO and must exist on the calendar --
    schema = infer_schema(["d"], [["2026-02-30"]])
    assert col(schema, "d")["type"] == "str"
    schema = infer_schema(["d"], [["2026-13-01"]])
    assert col(schema, "d")["type"] == "str"
    schema = infer_schema(["d"], [["2026-2-3"]])
    assert col(schema, "d")["type"] == "str"
    schema = infer_schema(["d"], [["2024-02-29"]])  # leap day is real
    assert col(schema, "d")["type"] == "date"

    # -- nulls: empty (after strip), null, na, n/a in any case --
    schema = infer_schema(["v"], [["7"], [""], ["NULL"], ["na"], ["N/A"], ["  "]])
    assert col(schema, "v") == {"name": "v", "type": "int", "nullable": True}
    # a column of nothing but nulls
    schema = infer_schema(["v"], [[""], ["null"]])
    assert col(schema, "v") == {"name": "v", "type": "str", "nullable": True}
    # no data rows at all
    schema = infer_schema(["a", "b"], [])
    assert col(schema, "a") == {"name": "a", "type": "str", "nullable": True}

    # -- promotion: int+float widens to float; anything else collapses to str --
    schema = infer_schema(["x"], [["1"], ["2.5"], ["3"]])
    assert col(schema, "x")["type"] == "float"
    schema = infer_schema(["x"], [["1"], ["true"]])
    assert col(schema, "x")["type"] == "str"
    schema = infer_schema(["x"], [["2026-01-01"], ["7"]])
    assert col(schema, "x")["type"] == "str"
    schema = infer_schema(["x"], [["3.5"], ["2026-01-01"]])
    assert col(schema, "x")["type"] == "str"
    schema = infer_schema(["x"], [["1.5"], ["oops"], [""]])
    assert col(schema, "x") == {"name": "x", "type": "str", "nullable": True}

    # -- sampling: only the first sample_size rows drive the decision --
    rows = [["1"], ["2"], ["not a number"]]
    schema = infer_schema(["n"], rows, sample_size=2)
    assert col(schema, "n")["type"] == "int"
    schema = infer_schema(["n"], rows)  # default: look at everything
    assert col(schema, "n")["type"] == "str"
    schema = infer_schema(["n"], rows, sample_size=50)  # bigger than data is fine
    assert col(schema, "n")["type"] == "str"

    # -- structural validation --
    try:
        infer_schema(["a", "b"], [["1", "2"], ["3"]])
        assert False, "ragged row should raise ValueError"
    except ValueError:
        pass
    try:
        infer_schema(["a", "a"], [["1", "2"]])
        assert False, "duplicate column names should raise ValueError"
    except ValueError:
        pass

    # -- a realistic mixed table, all rules at once --
    header = ["order_id", "sku", "qty", "unit_price", "gift", "shipped_on"]
    rows = [
        ["1001", "A-77", "2", "19.99", "false", "2026-03-02"],
        ["1002", "B-01", "1", "5", "true", ""],
        ["1003", "007X", "3", "7.25", "false", "2026-03-04"],
        ["1004", "C-11", "", "12.00", "true", "2026-03-05"],
    ]
    schema = infer_schema(header, rows)
    assert col(schema, "order_id") == {"name": "order_id", "type": "int", "nullable": False}
    assert col(schema, "sku")["type"] == "str"
    assert col(schema, "qty") == {"name": "qty", "type": "int", "nullable": True}
    assert col(schema, "unit_price") == {"name": "unit_price", "type": "float", "nullable": False}
    assert col(schema, "gift")["type"] == "bool"
    assert col(schema, "shipped_on") == {"name": "shipped_on", "type": "date", "nullable": True}

    print("ok")


if __name__ == "__main__":
    main()
