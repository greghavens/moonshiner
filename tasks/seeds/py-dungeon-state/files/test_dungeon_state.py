"""Behavior checks for the dungeon run state manager.

Run: python3 test_dungeon_state.py
"""
from dungeon_state import STARTING_STATE, GameState


def test_mutators_and_status():
    game = GameState("Brenna")
    game.advance_turn()
    game.take_damage(5)
    game.heal(2)
    game.pick_up("rusty key")
    game.earn_gold(30)
    game.spend_gold(10)
    game.enter_room("armory")
    game.descend()

    p = game.player()
    assert p["hp"] == 17, p
    assert p["gold"] == 20, p
    assert p["inventory"] == ["torch", "rusty key"], p
    assert game.state["floor"] == 2
    assert game.state["visited"] == ["entrance", "armory"]
    assert game.status_line() == "Brenna — floor 2, turn 1, hp 17, gold 20, 2 items"


def test_rollback_restores_the_whole_turn():
    game = GameState("Brenna")
    game.earn_gold(50)
    game.pick_up("buckler")
    game.checkpoint()

    # a fight that goes badly
    game.advance_turn()
    game.take_damage(19)
    game.pick_up("cursed idol")
    game.spend_gold(35)
    game.enter_room("crypt")
    game.descend()
    game.rollback()

    p = game.player()
    assert game.state["floor"] == 1, game.state["floor"]
    assert game.state["turn"] == 0, game.state["turn"]
    assert p["hp"] == 20, f"hp should be back to pre-fight value, got {p['hp']}"
    assert p["gold"] == 50, f"gold should be back to pre-fight value, got {p['gold']}"
    assert p["inventory"] == ["torch", "buckler"], (
        f"bag should match the checkpoint, got {p['inventory']}"
    )
    assert game.state["visited"] == ["entrance"], game.state["visited"]


def test_checkpoints_stack():
    game = GameState("Kel")
    game.checkpoint()          # before floor 1 boss
    game.take_damage(4)
    game.pick_up("iron key")
    game.checkpoint()          # before opening the vault
    game.take_damage(6)
    game.drop("torch")

    assert game.checkpoint_depth() == 2
    game.rollback()
    p = game.player()
    assert p["hp"] == 16 and p["inventory"] == ["torch", "iron key"], p
    game.rollback()
    p = game.player()
    assert p["hp"] == 20 and p["inventory"] == ["torch"], p
    assert game.checkpoint_depth() == 0


def test_each_run_starts_fresh():
    first = GameState("Brenna")
    first.take_damage(12)
    first.pick_up("emerald")
    first.enter_room("larder")
    first.earn_gold(99)

    second = GameState("Kel")
    p = second.player()
    assert p["hp"] == 20, f"a new run must start at full hp, got {p['hp']}"
    assert p["inventory"] == ["torch"], (
        f"a new run must start with just the torch, got {p['inventory']}"
    )
    assert p["gold"] == 0, p
    assert second.state["visited"] == ["entrance"], second.state["visited"]

    second.pick_up("lockpick")
    assert "lockpick" not in first.player()["inventory"], (
        "two concurrent runs must not share a bag"
    )
    assert STARTING_STATE["player"] == {"hp": 20, "gold": 0, "inventory": ["torch"]}, (
        f"starting template drifted: {STARTING_STATE['player']}"
    )


def main():
    test_mutators_and_status()
    test_rollback_restores_the_whole_turn()
    test_checkpoints_stack()
    test_each_run_starts_fresh()
    print("all checks passed")


if __name__ == "__main__":
    main()
