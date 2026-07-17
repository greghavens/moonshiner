import sys

sys.dont_write_bytecode = True

import dispatchlabel


def main():
    assert dispatchlabel.rendered_count() == 0

    label = dispatchlabel.render_label("Ada Voss", "M. Cole", "Harbour Depot")
    expected = (
        "DISPATCH LABEL - Harbour Depot\n"
        "Recipient: Ada Voss\n"
        "Route: M. Cole\n"
        "Label no: {seq}\n"
        'Pack note: "keep upright"\n'
        "Printer help: See C:\\new\\table.txt on the station PC\n"
    )
    assert label == expected, "rendered label differs from what the printer expects:\n%r" % label

    # the session counter still ticks for the status bar
    assert dispatchlabel.rendered_count() == 1
    label2 = dispatchlabel.render_label("Ben Okafor", "R. Chen", "Harbour Depot")
    assert dispatchlabel.rendered_count() == 2
    assert "Recipient: Ben Okafor\n" in label2
    assert "Label no: {seq}\n" in label2, "printer token must survive every render"

    # the help sheet lives at a fixed spot on the packing-station PC
    assert dispatchlabel.HELP_SHEET == "C:\\new\\table.txt", repr(dispatchlabel.HELP_SHEET)

    # the label is exactly six print lines, no surprise wrapping
    assert label.count("\n") == 6 and label.endswith("\n"), repr(label)

    print("ok")


if __name__ == "__main__":
    main()
