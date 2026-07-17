"""State manager for the dungeon-crawler prototype.

One GameState per run. All mutation goes through the methods below so the
turn engine can checkpoint() before anything risky (opening a chest,
fighting, stepping on a rune) and rollback() when the attempt goes badly —
that's how "undo turn" and death-retries work. Checkpoints stack, so a
rollback returns to the most recent one.
"""

STARTING_STATE = {
    "floor": 1,
    "turn": 0,
    "player": {"hp": 20, "gold": 0, "inventory": ["torch"]},
    "visited": ["entrance"],
}


class GameState:
    def __init__(self, hero_name):
        self.hero_name = hero_name
        self.state = dict(STARTING_STATE)
        self._checkpoints = []

    # -- turn engine hooks -------------------------------------------------

    def checkpoint(self):
        """Snapshot the current state; rollback() returns to it."""
        self._checkpoints.append(dict(self.state))

    def rollback(self):
        """Restore the most recent checkpoint."""
        if not self._checkpoints:
            raise RuntimeError("no checkpoint to roll back to")
        self.state = self._checkpoints.pop()

    def checkpoint_depth(self):
        return len(self._checkpoints)

    # -- mutations ---------------------------------------------------------

    def advance_turn(self):
        self.state["turn"] += 1

    def take_damage(self, amount):
        self.state["player"]["hp"] -= amount
        return self.state["player"]["hp"]

    def heal(self, amount):
        self.state["player"]["hp"] = min(20, self.state["player"]["hp"] + amount)

    def pick_up(self, item):
        self.state["player"]["inventory"].append(item)

    def drop(self, item):
        self.state["player"]["inventory"].remove(item)

    def earn_gold(self, amount):
        self.state["player"]["gold"] += amount

    def spend_gold(self, amount):
        if amount > self.state["player"]["gold"]:
            raise ValueError("not enough gold")
        self.state["player"]["gold"] -= amount

    def enter_room(self, room):
        if room not in self.state["visited"]:
            self.state["visited"].append(room)

    def descend(self):
        self.state["floor"] += 1

    # -- views ---------------------------------------------------------------

    def player(self):
        return dict(self.state["player"])

    def status_line(self):
        p = self.state["player"]
        return (f"{self.hero_name} — floor {self.state['floor']}, "
                f"turn {self.state['turn']}, hp {p['hp']}, gold {p['gold']}, "
                f"{len(p['inventory'])} items")
