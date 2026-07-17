"""Acceptance tests for the INI config loader. Run: python3 test_iniconf.py"""


def expect_error(text, *needles):
    from iniconf import load_ini, IniError
    try:
        load_ini(text)
    except IniError as e:
        msg = str(e)
        for needle in needles:
            assert needle in msg, f"error {msg!r} should mention {needle!r}"
        return
    assert False, f"expected IniError for {text!r}"


def main():
    from iniconf import load_ini, IniError

    assert issubclass(IniError, ValueError), "IniError must subclass ValueError"

    # -- plain parsing: sections, comments, blanks, whitespace stripping --
    cfg = load_ini(
        "; deployment config\n"
        "[server]\n"
        "host = example.com\n"
        "  port=8080\n"
        "\n"
        "# hash comments too\n"
        "[paths]\n"
        "root = /srv/app\n"
        "empty =\n"
    )
    assert cfg == {
        "server": {"host": "example.com", "port": "8080"},
        "paths": {"root": "/srv/app", "empty": ""},
    }, cfg

    # -- values keep internal '=' and internal spaces --
    cfg = load_ini("[q]\nfilter = status = open\n")
    assert cfg["q"]["filter"] == "status = open", cfg

    # -- last duplicate key wins; duplicate sections merge --
    cfg = load_ini("[s]\nk = first\nk = second\n[t]\nx = 9\n[s]\nb = 2\n")
    assert cfg["s"] == {"k": "second", "b": "2"}, cfg
    assert cfg["t"] == {"x": "9"}, cfg

    # -- interpolation: same-section shorthand and section.key form --
    cfg = load_ini(
        "[server]\n"
        "host = example.com\n"
        "port = 8080\n"
        "[paths]\n"
        "root = /srv/app\n"
        "static = ${root}/static\n"
        "url = https://${server.host}:${server.port}/\n"
    )
    assert cfg["paths"]["static"] == "/srv/app/static", cfg
    assert cfg["paths"]["url"] == "https://example.com:8080/", cfg

    # -- multi-hop chains resolve fully --
    cfg = load_ini("[a]\none = 1\ntwo = ${one}2\nthree = ${two}3\n")
    assert cfg["a"]["three"] == "123", cfg

    # -- forward references (target defined later in the file) work --
    cfg = load_ini("[a]\ngreeting = hello ${name}\nname = world\n")
    assert cfg["a"]["greeting"] == "hello world", cfg
    cfg = load_ini("[a]\nx = ${b.y}!\n[b]\ny = ok\n")
    assert cfg["a"]["x"] == "ok!", cfg

    # -- $$ escapes a literal dollar; lone $ passes through --
    cfg = load_ini("[m]\nroot = /srv\nmotd = costs $$5 at ${root}\nplain = 100% $sign\n")
    assert cfg["m"]["motd"] == "costs $5 at /srv", cfg
    assert cfg["m"]["plain"] == "100% $sign", cfg
    # $$ must not itself trigger interpolation
    cfg = load_ini("[m]\nweird = $${notaref}\n")
    assert cfg["m"]["weird"] == "${notaref}", cfg

    # -- syntax errors carry the 1-based line number --
    expect_error("key = 1\n", "line 1")               # key before any section
    expect_error("[s]\nno equals sign here\n", "line 2")
    expect_error("[unclosed\n", "line 1")

    # -- bad reference syntax --
    expect_error("[s]\nx = ${}\n")                    # empty reference
    expect_error("[s]\nx = ${oops\n")                 # unterminated reference

    # -- unknown references name the fully qualified key --
    expect_error("[s]\nx = ${nope}\n", "s.nope")
    expect_error("[s]\nx = ${other.k}\n", "other.k")

    # -- cycles are detected and reported as a chain --
    expect_error("[a]\nx = ${b.y}!\n[b]\ny = ${a.x}?\n", "a.x -> b.y -> a.x")
    expect_error("[a]\nx = pre ${x} post\n", "a.x -> a.x")
    # three-node cycle
    expect_error(
        "[a]\nx = ${b.y}\n[b]\ny = ${c.z}\n[c]\nz = ${a.x}\n",
        "a.x -> b.y -> c.z -> a.x",
    )

    # -- a diamond is NOT a cycle: two paths to the same key are fine --
    cfg = load_ini(
        "[base]\nname = app\n"
        "[d]\nleft = ${base.name}-l\nright = ${base.name}-r\nboth = ${left}+${right}\n"
    )
    assert cfg["d"]["both"] == "app-l+app-r", cfg

    # -- empty input is an empty config --
    assert load_ini("") == {}
    assert load_ini("\n; only a comment\n") == {}

    print("all iniconf checks passed")


if __name__ == "__main__":
    main()
