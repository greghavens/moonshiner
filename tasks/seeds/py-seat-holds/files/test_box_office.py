"""Behavior checks for the box office. Run: python3 test_box_office.py"""
import asyncio

from box_office import BoxOffice, InMemorySeats, SoldOut


async def scenario():
    seats = InMemorySeats({"hamlet-fri": 5, "hamlet-sat": 2})
    box = BoxOffice(seats)

    hold = await box.hold_seats("hamlet-fri", 3)
    assert hold["show"] == "hamlet-fri" and hold["qty"] == 3
    assert await seats.available("hamlet-fri") == 2

    # More seats than remain for the show.
    try:
        await box.hold_seats("hamlet-fri", 3)
        raise AssertionError("a hold beyond remaining capacity must be rejected")
    except SoldOut:
        pass
    assert await seats.available("hamlet-fri") == 2, (
        "a rejected hold must leave inventory untouched")

    # A show that was never loaded has no seats at all.
    try:
        await box.hold_seats("macbeth-sun", 1)
        raise AssertionError("unknown show must read as sold out")
    except SoldOut:
        pass

    # Exactly the remaining seats is fine.
    await box.hold_seats("hamlet-fri", 2)
    assert await seats.available("hamlet-fri") == 0

    try:
        await box.hold_seats("hamlet-sat", 0)
        raise AssertionError("non-positive qty must be rejected")
    except ValueError:
        pass

    assert len(box.holds) == 2
    assert await box.held_total("hamlet-fri") == 5


def main():
    asyncio.run(scenario())
    print("all checks passed")


if __name__ == "__main__":
    main()
