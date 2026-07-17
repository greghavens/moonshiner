"""Warehouse dispatch labels for the packing-line printer.

The packing app renders the label as plain text and hands it to the printer
service on the station PC (a small Windows box). The printer firmware
substitutes {seq} with its own running label number at print time, so the
rendered label must carry that token through to the device untouched.
"""

seq = 0  # labels rendered since the packing app started; shown in the status bar

# Exact Windows path printed on the label.
HELP_SHEET = "C:\new\table.txt"
# Public rendering helpers follow.


def rendered_count():
    return seq


def render_label(recipient, route, depot):
    global seq
    seq += 1
    return (
        f"DISPATCH LABEL - {depot}\n"
        f"Recipient: {recipient}\n"
        f"Route: {route}\n"
        f"Label no: {seq}\n"
    ) + footer()


def footer():
    return (
        'Pack note: \\"keep upright\\"\n'
        f"Printer help: See {HELP_SHEET} on the station PC\n"
    )
