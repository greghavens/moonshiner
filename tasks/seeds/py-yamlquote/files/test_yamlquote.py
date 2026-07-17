# test_yamlquote.py — acceptance tests for the YAML quoting linter.
# Every expectation below is what PyYAML 6.0.2's safe loader actually does
# to the bare scalar; the linter must agree with the loader, not with the
# YAML 1.1 spec sheet.
import datetime

from yamlquote import audit, classify, needs_quotes, plain_parse


def test_plain_parse_returns_the_loader_value():
    assert plain_parse("0755") == 493
    assert plain_parse("19:30") == 1170
    assert plain_parse("12:34:56") == 45296
    assert plain_parse("NO") is False
    assert plain_parse("1.10") == 1.1
    assert plain_parse("") is None
    assert plain_parse("2026-02-03") == datetime.date(2026, 2, 3)
    assert plain_parse("1_000") == 1000
    assert plain_parse("0b1010") == 10
    assert plain_parse("-0") == 0 and isinstance(plain_parse("-0"), int)
    assert plain_parse("8080") == 8080


def test_boolean_lookalikes():
    for s in ["no", "No", "NO", "yes", "on", "off", "Off", "true", "TRUE", "False"]:
        assert classify(s) == "bool", s
    # PyYAML does NOT resolve single letters, unlike the YAML 1.1 spec table.
    for s in ["y", "Y", "n", "N"]:
        assert classify(s) == "str", s
    # ...and multi-word or suffixed lookalikes stay strings too.
    for s in ["no way", "true-ish"]:
        assert classify(s) == "str", s


def test_number_lookalikes():
    for s in ["0755", "-0755", "0x1A", "0b1010", "1_000", "00", "8080", "+42", "-0"]:
        assert classify(s) == "int", s
    for s in ["1.10", ".5", "6.", "-3.5", ".inf", ".NaN"]:
        assert classify(s) == "float", s
    # The loader leaves all of these alone — the linter must not cry wolf.
    for s in ["1e3", "0o755", "090", "1.2.3", "v1.10", "0.0.0.0", "127.0.0.1"]:
        assert classify(s) == "str", s


def test_sexagesimal_times_are_ints():
    for s in ["19:30", "1:30", "12:34:56"]:
        assert classify(s) == "int", s
    assert classify("3:60") == "str"  # 60 is not a valid last component


def test_null_lookalikes():
    for s in ["null", "Null", "NULL", "~", ""]:
        assert classify(s) == "null", s


def test_timestamps():
    assert classify("2026-02-03") == "timestamp"
    assert classify("2026-02-03 04:05:06") == "timestamp"
    # Matches the date pattern but is not a real date: the loader blows up,
    # so unquoted it is broken outright, not merely retyped.
    assert classify("2026-13-01") == "invalid"


def test_scalars_the_loader_rejects_or_restructures():
    for s in ["a\tb", "@handle", "!tag", "*ref", "%TAG", "="]:
        assert classify(s) == "invalid", s
    for s in ["[x]", "{a: 1}", "a: b", "- item"]:
        assert classify(s) == "other", s


def test_reparsed_strings_survive_as_different_strings():
    assert classify("a # b") == "reparsed"       # comment swallows the tail
    assert classify(" padded ") == "reparsed"    # plain scalars are stripped
    assert classify("|") == "reparsed"           # block-scalar introducer
    assert classify("NO ") == "bool"             # stripped first, THEN retyped


def test_safe_strings_need_no_quotes():
    for s in ["widget-a", "N/A", "a,b", "hello world", "not#comment",
              'a"b', "a'b", "don't"]:
        assert classify(s) == "str", s
        assert needs_quotes(s) is False, s


def test_needs_quotes_agrees_with_classify():
    for s in ["no", "1.10", "0755", "19:30", "", "2026-02-03", "a # b",
              "a\tb", "[x]", "widget-a", "y"]:
        assert needs_quotes(s) is (classify(s) != "str"), s


def test_audit_reports_only_risky_values_sorted_by_key():
    cfg = {
        "country": "NO",
        "version": "1.10",
        "umask": "0755",
        "reply": "n",
        "toggle": "off",
        "shift_end": "19:30",
        "release_day": "2026-02-03",
        "note": "rotate  # monthly",
        "name": "widget-a",
        "empty": "",
    }
    assert audit(cfg) == [
        {"key": "country", "value": "NO", "becomes": "bool"},
        {"key": "empty", "value": "", "becomes": "null"},
        {"key": "note", "value": "rotate  # monthly", "becomes": "reparsed"},
        {"key": "release_day", "value": "2026-02-03", "becomes": "timestamp"},
        {"key": "shift_end", "value": "19:30", "becomes": "int"},
        {"key": "toggle", "value": "off", "becomes": "bool"},
        {"key": "umask", "value": "0755", "becomes": "int"},
        {"key": "version", "value": "1.10", "becomes": "float"},
    ]


def test_audit_is_quiet_on_a_safe_mapping():
    assert audit({"name": "widget-a", "owner": "dana", "reply": "y"}) == []
    assert audit({}) == []
