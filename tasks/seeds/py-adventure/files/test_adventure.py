"""Acceptance tests for the data-driven adventure engine. Run: python3 test_adventure.py"""
import json
import subprocess
import sys


def run_cli(world, script):
    p = subprocess.run([sys.executable, "adventure.py", world],
                       input=script, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, (p.returncode, p.stderr)
    return p.stdout


def world_or_error(world):
    from adventure import Game
    try:
        Game(world)
    except ValueError:
        return "error"
    return "ok"


TINY = {
    "start": "a",
    "victory": {"room": "b", "item": "gem"},
    "rooms": {
        "a": {"name": "A", "desc": "room a.",
              "exits": {"east": "b"}, "items": ["gem", "key"],
              "locked": {"east": "key"}},
        "b": {"name": "B", "desc": "room b.",
              "exits": {"west": "a"}, "items": []},
    },
}


def main():
    from adventure import Game, load

    # -- world validation: broken data must not load --
    assert world_or_error(TINY) == "ok"

    bad = json.loads(json.dumps(TINY))
    bad["start"] = "nowhere"
    assert world_or_error(bad) == "error"
    bad = json.loads(json.dumps(TINY))
    bad["rooms"]["a"]["exits"]["north"] = "void"
    assert world_or_error(bad) == "error"
    bad = json.loads(json.dumps(TINY))
    bad["rooms"]["a"]["locked"] = {"south": "key"}
    assert world_or_error(bad) == "error"
    bad = json.loads(json.dumps(TINY))
    bad["victory"] = {"room": "void", "item": "gem"}
    assert world_or_error(bad) == "error"
    bad = json.loads(json.dumps(TINY))
    del bad["rooms"]["b"]["name"]
    assert world_or_error(bad) == "error"

    # -- engine state: locks, keys, victory only when carrying the item --
    g = Game(json.loads(json.dumps(TINY)))
    assert g.location == "a" and g.inventory == [] and not g.won
    assert g.execute("go east") == "The east door is locked."
    assert g.execute("unlock east") == "You don't have the key for that."
    assert g.execute("take key") == "You take the key."
    assert g.execute("unlock east") == "You unlock the east door."
    assert g.execute("unlock east") == "The east door isn't locked."
    reply = g.execute("go east")
    assert g.location == "b"
    assert not g.won and "You win!" not in reply   # right room, wrong item
    g.execute("go west")
    assert g.execute("take gem") == "You take the gem."
    assert not g.won                               # right item, wrong room
    reply = g.execute("go east")
    assert reply.endswith("You win!") and g.won

    # -- `use` on the matching key opens the door too --
    g4 = Game(json.loads(json.dumps(TINY)))
    g4.execute("take key")
    assert g4.execute("use key") == "You use the key. The east door unlocks."
    assert g4.execute("go east").startswith("B")

    # -- CLI world 1: locked cellar, fetch the key, win back on the porch --
    script1 = ("go north\ngo down\ngo east\ntake iron key\ngo west\n"
               "unlock down\ngo down\ntake silver medal\ngo up\ngo south\n"
               "ignored after win\n")
    assert run_cli("world1.json", script1) == (
        "Front Porch\nPaint peels off the railing.\nitems: doormat\n"
        "exits: north\n> go north\nEntry Hall\n"
        "A draft moves the dust in slow circles.\n"
        "exits: down (locked), east, south\n> go down\n"
        "The down door is locked.\n> go east\nKitchen\n"
        "Someone left the drawer open.\nitems: iron key, chipped mug\n"
        "exits: west\n> take iron key\nYou take the iron key.\n> go west\n"
        "Entry Hall\nA draft moves the dust in slow circles.\n"
        "exits: down (locked), east, south\n> unlock down\n"
        "You unlock the down door.\n> go down\nCellar\n"
        "Cold air and the smell of earth.\nitems: silver medal\nexits: up\n"
        "> take silver medal\nYou take the silver medal.\n> go up\n"
        "Entry Hall\nA draft moves the dust in slow circles.\n"
        "exits: down, east, south\n> go south\nFront Porch\n"
        "Paint peels off the railing.\nitems: doormat\nexits: north\n"
        "You win!\n")

    # -- CLI world 2: same engine, different world, `use` opens the stair --
    script2 = ("take rusty key\ngo east\ngo north\nunlock north\ngo north\n"
               "take brass key\ninventory\ngo south\ngo east\nuse brass key\n"
               "go up\ntake signal lamp\n")
    assert run_cli("world2.json", script2) == (
        "Windward Beach\nGulls argue over the tide line.\nitems: rusty key\n"
        "exits: east\n> take rusty key\nYou take the rusty key.\n> go east\n"
        "Shore Path\nCrushed shells crunch underfoot.\n"
        "exits: east, north (locked), west\n> go north\n"
        "The north door is locked.\n> unlock north\n"
        "You unlock the north door.\n> go north\nKeeper's Shack\n"
        "Nets and tar and old rope.\nitems: brass key, old net\n"
        "exits: south\n> take brass key\nYou take the brass key.\n"
        "> inventory\nYou carry: rusty key, brass key\n> go south\n"
        "Shore Path\nCrushed shells crunch underfoot.\n"
        "exits: east, north, west\n> go east\nLighthouse Base\n"
        "A spiral stair winds up into the dark.\nexits: up (locked), west\n"
        "> use brass key\nYou use the brass key. The up door unlocks.\n"
        "> go up\nLamp Room\nSalt haze over a wide, grey sea.\n"
        "items: signal lamp\nexits: down\n> take signal lamp\n"
        "You take the signal lamp.\nYou win!\n")

    # -- CLI: every refusal message, then take/use/drop round trip --
    script3 = ("dance\ngo up\ntake lamp\ndrop hat\nunlock north\n"
               "unlock east\nuse doormat\ntake doormat\nuse doormat\n"
               "drop doormat\nlook\ninventory\n")
    assert run_cli("world1.json", script3) == (
        "Front Porch\nPaint peels off the railing.\nitems: doormat\n"
        "exits: north\n> dance\nI don't understand.\n> go up\n"
        "You can't go that way.\n> take lamp\nThere is no lamp here.\n"
        "> drop hat\nYou don't have the hat.\n> unlock north\n"
        "The north door isn't locked.\n> unlock east\n"
        "There is no door to the east.\n> use doormat\n"
        "You don't have the doormat.\n> take doormat\n"
        "You take the doormat.\n> use doormat\nNothing happens.\n"
        "> drop doormat\nYou drop the doormat.\n> look\nFront Porch\n"
        "Paint peels off the railing.\nitems: doormat\nexits: north\n"
        "> inventory\nYou carry nothing.\n")

    print("all adventure tests passed")


if __name__ == "__main__":
    main()
