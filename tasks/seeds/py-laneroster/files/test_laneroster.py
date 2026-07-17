"""Acceptance tests for the meet program. Run: python3 test_laneroster.py"""

import laneroster

ENTRIES = [
    {"name": "Ada", "seed_time": 31.2},
    {"name": "Bo", "seed_time": None},
    {"name": "Cleo", "seed_time": 29.8},
    {"name": "Dev", "seed_time": 33.0},
    {"name": "Eze", "seed_time": None},
    {"name": "Fay", "seed_time": 30.1},
    {"name": "Gus", "seed_time": 34.5},
]


def test_split_timed_swimmers_always_race_seeded():
    for allow in (True, False):
        seeded, _ = laneroster.split_entries(ENTRIES, allow)
        names = [e["name"] for e in seeded]
        assert names == ["Ada", "Cleo", "Dev", "Fay", "Gus"], (allow, names)


def test_split_untimed_swim_exhibition_when_allowed():
    _, exhibition = laneroster.split_entries(ENTRIES, True)
    assert exhibition == ["Bo", "Eze"], exhibition


def test_split_untimed_sit_out_when_not_allowed():
    seeded, exhibition = laneroster.split_entries(ENTRIES, False)
    assert exhibition == [], exhibition
    assert all(e["seed_time"] is not None for e in seeded), seeded


def test_pack_heats_fastest_first_in_lane_chunks():
    seeded, _ = laneroster.split_entries(ENTRIES, True)
    heats = laneroster.pack_heats(seeded, 3)
    assert heats == [["Cleo", "Fay", "Ada"], ["Dev", "Gus"]], heats


def test_program_layout():
    program = laneroster.meet_program(ENTRIES, 3, True)
    assert program == [
        "Heat 1",
        "  lane 1: Cleo",
        "  lane 2: Fay",
        "  lane 3: Ada",
        "",
        "Heat 2",
        "  lane 1: Dev",
        "  lane 2: Gus",
        "",
        "Exhibition",
        "  Bo",
        "  Eze",
        "",
    ], program


def test_program_without_exhibition_heat():
    program = laneroster.meet_program(ENTRIES, 3, False)
    assert "Exhibition" not in program, program
    assert program[-1] == "", program


def test_empty_signup_sheet():
    assert laneroster.meet_program([], 6, True) == []


def main():
    test_split_timed_swimmers_always_race_seeded()
    test_split_untimed_swim_exhibition_when_allowed()
    test_split_untimed_sit_out_when_not_allowed()
    test_pack_heats_fastest_first_in_lane_chunks()
    test_program_layout()
    test_program_without_exhibition_heat()
    test_empty_signup_sheet()
    print("ok - laneroster")


if __name__ == "__main__":
    main()
