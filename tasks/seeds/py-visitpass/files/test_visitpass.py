import sys

sys.dont_write_bytecode = True

import visitpass


def main():
    assert visitpass.rendered_count() == 0

    slip = visitpass.render_slip("Ada Voss", "M. Cole", "Harbour Office")
    expected = (
        "VISITOR PASS - Harbour Office\n"
        "Name: Ada Voss\n"
        "Host: M. Cole\n"
        "Badge no: {seq}\n"
        'Present this pass when asked "who are you visiting?"\n'
        "Lost badge? See C:\\new\\table.txt on the desk PC\n"
    )
    assert slip == expected, "rendered slip differs from what the printer expects:\n%r" % slip

    # the session counter still ticks for the status bar
    assert visitpass.rendered_count() == 1
    slip2 = visitpass.render_slip("Ben Okafor", "R. Chen", "Harbour Office")
    assert visitpass.rendered_count() == 2
    assert "Name: Ben Okafor\n" in slip2
    assert "Badge no: {seq}\n" in slip2, "printer token must survive every render"

    # the help sheet lives at a fixed spot on the reception PC
    assert visitpass.HELP_SHEET == "C:\\new\\table.txt", repr(visitpass.HELP_SHEET)

    # the slip is exactly six print lines, no surprise wrapping
    assert slip.count("\n") == 6 and slip.endswith("\n"), repr(slip)

    print("ok")


if __name__ == "__main__":
    main()
