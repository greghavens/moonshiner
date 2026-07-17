import pathlib
import shutil
import sys

# Compile the module fresh on every run so results never depend on stale
# bytecode left behind by an earlier invocation.
sys.dont_write_bytecode = True
shutil.rmtree(pathlib.Path(__file__).resolve().parent / "__pycache__", ignore_errors=True)

import gaugelog

READING_LINES = [
    "2026-07-14 06:00 STN-042 stage=3.82m flow=124.500",
    "2026-07-14 06:15 STN-042 stage=3.91m flow=131.250",
    "2026-07-14 06:00 STN-007 stage=1.20m flow=18.000",
    "2026-07-14 06:15 STN-007 stage=1.18m flow=17.500",
    "2026-07-14 06:30 STN-042 stage=3.88m flow=129.000",
]

FEED = (
    "# county hydrology feed 2026-07-14\n"
    + READING_LINES[0] + "\n"
    + READING_LINES[1] + "\n"
    + READING_LINES[2] + "\n"
    + "  # mid-file annotation\n"
    + READING_LINES[3] + "\n"
    + "\n"
    + READING_LINES[4] + "\n"
)


def expect_value_error(line):
    try:
        gaugelog.parse_line(line)
    except ValueError:
        return
    raise AssertionError("line should have been rejected: %r" % line)


def main():
    r = gaugelog.parse_line("2026-07-14 06:15 STN-042 stage=3.91m flow=131.250")
    assert r == {
        "date": "2026-07-14",
        "time": "06:15",
        "station": "STN-042",
        "stage": 3.91,
        "flow": 131.25,
    }, r

    # leading/trailing whitespace is tolerated, garbage is not
    r2 = gaugelog.parse_line("  2026-07-14 06:00 STN-007 stage=1.20m flow=18.000  \n")
    assert r2["station"] == "STN-007" and r2["stage"] == 1.2, r2
    expect_value_error("gibberish")
    expect_value_error("2026-07-14 06:00 STN-042 stage=3m flow=124.500")
    expect_value_error("2026-07-14 06:00 STN-042 stage=3X82m flow=124.500")
    expect_value_error("2026-07-14 06:00 STN-042 stage=3.82m flow=124.500 extra")

    assert gaugelog.is_station("STN-042") is True
    assert gaugelog.is_station("STN-42") is False
    assert gaugelog.is_station("STN-0421") is False
    assert gaugelog.is_station("stn-042") is False
    assert gaugelog.is_station(" STN-042") is False

    rows = gaugelog.readings(FEED)
    assert len(rows) == 5, rows
    assert rows[0]["station"] == "STN-042" and rows[0]["time"] == "06:00"
    assert rows[3]["station"] == "STN-007" and rows[3]["time"] == "06:15"

    peaks = gaugelog.daily_peak(READING_LINES)
    assert peaks == {"STN-042": 3.91, "STN-007": 1.2}, peaks

    expected_sheet = (
        "station\ttime\tstage_m\tflow\n"
        "STN-007\t06:00\t1.20\t18.000\n"
        "STN-007\t06:15\t1.18\t17.500\n"
        "STN-042\t06:00\t3.82\t124.500\n"
        "STN-042\t06:15\t3.91\t131.250\n"
        "STN-042\t06:30\t3.88\t129.000"
    )
    assert gaugelog.sheet(READING_LINES) == expected_sheet, gaugelog.sheet(READING_LINES)

    print("ok")


if __name__ == "__main__":
    main()
