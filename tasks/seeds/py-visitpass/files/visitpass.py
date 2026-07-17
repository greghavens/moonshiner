"""Front-desk visitor slips for the lobby badge printer.

The desk app renders the slip as plain text and hands it to the printer
service on the reception PC (a small Windows box). The printer firmware
substitutes {seq} with its own running badge number at print time, so the
rendered slip must carry that token through to the device untouched.
"""

seq = 0  # slips rendered since the desk app started; shown in the status bar

HELP_SHEET = "C:\new\table.txt"


def rendered_count():
    return seq


def render_slip(name, host, site):
    global seq
    seq += 1
    return (
        f"VISITOR PASS - {site}\n"
        f"Name: {name}\n"
        f"Host: {host}\n"
        f"Badge no: {seq}\n"
    ) + footer()


def footer():
    return (
        'Present this pass when asked \\"who are you visiting?\\"\n'
        f"Lost badge? See {HELP_SHEET} on the desk PC\n"
    )
