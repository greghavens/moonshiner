"""Service-bay allocation: three stands, first free one wins."""

BAYS = ("B1", "B2", "B3")


def assign_bay(order, taken=[]):
    """Put the order on the first free stand.

    `taken` is the list of stands already occupied right now; the chosen
    stand is appended to it so a caller walking one morning's queue can
    keep passing the same list.
    """
    for bay in BAYS:
        if bay not in taken:
            taken.append(bay)
            order["bay"] = bay
            return bay
    raise RuntimeError("no free bay — clear the rack first")
