"""Acceptance tests for the command-line tokenizer. Run: python3 test_cmdlex.py"""


def main():
    from cmdlex import tokenize

    # -- plain words split on runs of spaces and tabs --
    assert tokenize("ls -la /tmp") == ["ls", "-la", "/tmp"]
    assert tokenize("  echo   hi\t\tthere  ") == ["echo", "hi", "there"]
    assert tokenize("") == []
    assert tokenize("   \t  ") == []
    assert tokenize("one") == ["one"]

    # -- single quotes: everything inside is literal, including backslashes --
    assert tokenize("echo 'hello world'") == ["echo", "hello world"]
    assert tokenize(r"echo 'a\nb'") == ["echo", r"a\nb"]
    assert tokenize("echo 'two  spaces'") == ["echo", "two  spaces"]
    assert tokenize("echo '\"'") == ["echo", '"']
    assert tokenize("echo '#not a comment'") == ["echo", "#not a comment"]

    # -- double quotes: spaces preserved; backslash escapes \" and \\ --
    assert tokenize('echo "hello world"') == ["echo", "hello world"]
    assert tokenize('echo "say \\"hi\\""') == ["echo", 'say "hi"']
    assert tokenize('echo "back\\\\slash"') == ["echo", "back\\slash"]
    # before any other character the backslash is kept literally
    assert tokenize('echo "a\\tb"') == ["echo", "a\\tb"]
    assert tokenize('echo "it'"'"'s"') == ["echo", "it's"]

    # -- backslash outside quotes escapes the next character --
    assert tokenize(r"touch a\ b") == ["touch", "a b"]
    assert tokenize(r"echo \'") == ["echo", "'"]
    assert tokenize(r"echo \"") == ["echo", '"']
    assert tokenize(r"echo \\") == ["echo", "\\"]
    assert tokenize(r"echo \#hash") == ["echo", "#hash"]
    assert tokenize(r"echo a\zb") == ["echo", "azb"]  # escape = take next char

    # -- adjacent segments glue into a single token --
    assert tokenize("echo foo'bar'\"baz\"") == ["echo", "foobarbaz"]
    assert tokenize("echo pre' mid 'post") == ["echo", "pre mid post"]
    assert tokenize("echo 'don'\\''t'") == ["echo", "don't"]

    # -- explicitly empty arguments survive --
    assert tokenize("cmd '' \"\"") == ["cmd", "", ""]
    assert tokenize("''") == [""]
    assert tokenize("a ''b") == ["a", "b"]  # empty quotes glued to a word add nothing

    # -- comments: an unquoted # starts one, but only at the start of a token --
    assert tokenize("echo hi # trailing comment") == ["echo", "hi"]
    assert tokenize("# whole line") == []
    assert tokenize("echo hi #") == ["echo", "hi"]
    assert tokenize("echo hi\t# tab before comment") == ["echo", "hi"]
    assert tokenize("echo file#1") == ["echo", "file#1"]  # mid-word # is literal
    assert tokenize('echo "#quoted"') == ["echo", "#quoted"]
    assert tokenize("wget url?x=1#frag") == ["wget", "url?x=1#frag"]

    # -- malformed input raises ValueError --
    for bad in ["echo 'unterminated",
                'echo "unterminated',
                "echo 'a''b",
                'echo "a\\"',          # the escaped quote never closes
                "echo trailing\\"]:    # dangling escape at end of line
        try:
            tokenize(bad)
            assert False, f"tokenize({bad!r}) should raise ValueError"
        except ValueError:
            pass

    # -- a small end-to-end line mixing everything --
    line = "cp -r 'my docs'/notes \"backup \\\"final\\\"\"/notes '' # nightly"
    assert tokenize(line) == ["cp", "-r", "my docs/notes",
                              'backup "final"/notes', ""], tokenize(line)

    print("ok")


if __name__ == "__main__":
    main()
