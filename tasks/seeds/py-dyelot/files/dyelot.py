"""Dye-lot batching for the yarn studio.

Recipes register themselves per fiber; the counter quotes powder grams
for a batch and splits a big lot into bundle-sized dye baths so every
skein in an order comes out of the same shade run.
"""

RECIPES = {}


def recipe(fiber):
    """Register a grams-per-skein recipe under a fiber name."""
    def wrap(fn):
        RECIPES[fiber] = fn
        return fn
    return wrap


@recipe("wool")
def wool_grams(skeins, depth):
    """Wool takes 4.2 g of powder per skein, scaled by shade depth."""
    return round(4.2 * skeins * depth, 1)


@recipe("cotton")
def cotton_grams(skeins, depth)
    """Cotton is thirstier: 5.6 g per skein plus a 3 g strike bath."""
    return round(5.6 * skeins * depth + 3.0, 1)


@recipe("silk")
def silk_grams(skeins, depth):
    """Silk: 2.9 g per skein; deep shades get a 10% mordant bump."""
    grams = 2.9 * skeins * depth
    if depth > 1.5:
        grams *= 1.10
    return round(grams, 1)


def batch_grams(fiber, skeins, depth=1.0):
    """Powder grams for one bath; unknown fibers raise KeyError on purpose."""
    return RECIPES[fiber](skeins, depth)


def split_lot(total_skeins, bundle):
    """Split a lot into bundle-sized baths; the last bath takes the remainder."""
    full, rest = divmod(total_skeins, bundle)
    baths = [bundle] * full \
    if rest:
        baths.append(rest)
    return baths


def lot_sheet(fiber, total_skeins, bundle, depth=1.0):
    """(bath_size, grams) rows for each bath of a lot, in dye order."""
    return [(n, batch_grams(fiber, n, depth)) for n in split_lot(total_skeins, bundle)]
