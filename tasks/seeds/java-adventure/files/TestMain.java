import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Objects;

/**
 * Acceptance tests for the story-night adventure engine.
 * Run: java TestMain.java
 */
public final class TestMain {
    private static int passed = 0;
    private static int failed = 0;

    interface Body { void run() throws Exception; }

    private static void test(String name, Body body) {
        try {
            body.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable t) {
            failed++;
            System.out.println("FAIL " + name + ": " + t);
        }
    }

    private static void eq(String what, Object expected, Object actual) {
        if (!Objects.equals(expected, actual)) {
            throw new AssertionError(what + ": expected <" + expected + "> got <" + actual + ">");
        }
    }

    private static void yes(String what, boolean cond) {
        if (!cond) throw new AssertionError(what);
    }

    private static <X extends Throwable> X thrown(Class<X> type, Body body) {
        try {
            body.run();
        } catch (Throwable t) {
            if (type.isInstance(t)) return type.cast(t);
            throw new AssertionError("expected " + type.getSimpleName() + " but got " + t, t);
        }
        throw new AssertionError("expected " + type.getSimpleName() + " but nothing was thrown");
    }

    private static World manor() throws Exception {
        return World.parse(Files.readString(Path.of("worlds", "manor.world")));
    }

    private static World depot() throws Exception {
        return World.parse(Files.readString(Path.of("worlds", "depot.world")));
    }

    private static List<String> names(List<Item> items) {
        return items.stream().map(Item::name).toList();
    }

    /** A tiny valid world used as the base for the parser error cases. */
    private static final String TINY = String.join("\n",
            "room a Room A",
            "desc a First.",
            "room b Room B",
            "desc b Second.",
            "exit a north b",
            "start a",
            "goal b");

    public static void main(String[] args) throws Exception {

        // ---- the typed world model --------------------------------------

        test("world_exposes_typed_rooms", () -> {
            World w = manor();
            eq("room count", 4, w.rooms().size());
            Room hall = w.room("hall");
            eq("title", "Entrance Hall", hall.title());
            eq("description", "Dust sheets cover the furniture.", hall.description());
            eq("exits in compass order", List.of("north", "east"), hall.exitDirections());
            eq("cellar exits", List.of("south", "west"), w.room("cellar").exitDirections());
        });

        test("rooms_hold_typed_items", () -> {
            World w = manor();
            eq("hall items", List.of("lantern"), names(w.room("hall").items()));
            eq("study items", List.of("iron-key"), names(w.room("study").items()));
            eq("vault items", List.of(), names(w.room("vault").items()));
        });

        test("player_starts_at_the_start_room_with_empty_pack", () -> {
            Engine e = new Engine(manor());
            eq("location", "hall", e.player().location().id());
            eq("inventory", List.of(), names(e.player().inventory()));
            yes("not finished", !e.finished());
        });

        // ---- world file validation ---------------------------------------

        test("world_rejects_broken_references", () -> {
            thrown(IllegalArgumentException.class,
                    () -> World.parse(TINY + "\nexit b south nowhere"));
            thrown(IllegalArgumentException.class,
                    () -> World.parse(TINY + "\nitem nowhere rope"));
            thrown(IllegalArgumentException.class,
                    () -> World.parse(TINY + "\ndesc nowhere Third."));
        });

        test("world_rejects_duplicates", () -> {
            thrown(IllegalArgumentException.class,
                    () -> World.parse(TINY + "\nroom a Again"));
            thrown(IllegalArgumentException.class,
                    () -> World.parse(TINY + "\ndesc a Twice."));
            thrown(IllegalArgumentException.class,
                    () -> World.parse(TINY + "\nexit a north a"));
            thrown(IllegalArgumentException.class,
                    () -> World.parse(TINY + "\nitem a rope\nitem b rope"));
        });

        test("world_requires_start_goal_and_descriptions", () -> {
            thrown(IllegalArgumentException.class, () -> World.parse(
                    "room a Room A\ndesc a First.\ngoal a"));
            thrown(IllegalArgumentException.class, () -> World.parse(
                    "room a Room A\ndesc a First.\nstart a"));
            thrown(IllegalArgumentException.class, () -> World.parse(
                    "room a Room A\nroom b Room B\ndesc a First.\nstart a\ngoal b"));
        });

        test("world_rejects_bad_lines_and_directions", () -> {
            thrown(IllegalArgumentException.class,
                    () -> World.parse(TINY + "\nexit a up b"));
            thrown(IllegalArgumentException.class,
                    () -> World.parse(TINY + "\nteleport a b"));
            thrown(IllegalArgumentException.class,
                    () -> World.parse(TINY + "\ndoor a south b ghost-key"));
        });

        // ---- looking around ------------------------------------------------

        test("look_lists_room_items_and_exits", () -> {
            Engine e = new Engine(manor());
            eq("hall block", String.join("\n",
                    "Entrance Hall",
                    "Dust sheets cover the furniture.",
                    "You see: lantern.",
                    "Exits: north, east."), e.exec("look"));
        });

        test("locked_doors_are_flagged_in_the_exit_list", () -> {
            Engine e = new Engine(depot());
            eq("yard block", String.join("\n",
                    "Switch Yard",
                    "Rails cross in every direction.",
                    "Exits: north (locked), west."), e.exec("look"));
        });

        // ---- moving ----------------------------------------------------------

        test("go_returns_the_new_room_block", () -> {
            Engine e = new Engine(manor());
            eq("into the study", String.join("\n",
                    "Oak Study",
                    "Bookshelves line every wall.",
                    "You see: iron-key.",
                    "Exits: south."), e.exec("go north"));
            eq("player moved", "study", e.player().location().id());
        });

        test("walls_and_locked_doors_refuse", () -> {
            Engine e = new Engine(manor());
            eq("no passage", "You can't go west.", e.exec("go west"));
            e.exec("go east");
            eq("locked door", "The door to the south is locked.", e.exec("go south"));
            eq("still in the cellar", "cellar", e.player().location().id());
        });

        // ---- items ------------------------------------------------------------

        test("take_and_drop_move_items_between_room_and_pack", () -> {
            Engine e = new Engine(manor());
            eq("take", "Taken.", e.exec("take lantern"));
            eq("hall block after take", String.join("\n",
                    "Entrance Hall",
                    "Dust sheets cover the furniture.",
                    "Exits: north, east."), e.exec("look"));
            e.exec("go east");
            eq("drop", "Dropped.", e.exec("drop lantern"));
            eq("cellar shows both items", String.join("\n",
                    "Damp Cellar",
                    "Something drips in the dark.",
                    "You see: lantern, rope.",
                    "Exits: south (locked), west."), e.exec("look"));
        });

        test("inventory_is_sorted_and_honest", () -> {
            Engine e = new Engine(manor());
            eq("empty pack", "You carry nothing.", e.exec("inventory"));
            e.exec("take lantern");
            e.exec("go north");
            e.exec("take iron-key");
            eq("two items", "You carry: iron-key, lantern.", e.exec("inventory"));
        });

        test("missing_items_get_polite_refusals", () -> {
            Engine e = new Engine(manor());
            eq("take absent", "There is no rope here.", e.exec("take rope"));
            eq("drop absent", "You don't have rope.", e.exec("drop rope"));
        });

        // ---- doors -------------------------------------------------------------

        test("unlock_needs_the_right_key_in_the_pack", () -> {
            Engine e = new Engine(manor());
            e.exec("go east");
            eq("no key", "You need iron-key.", e.exec("unlock south"));
            eq("still locked", "The door to the south is locked.", e.exec("go south"));
        });

        test("unlock_opens_the_door_for_good", () -> {
            Engine e = new Engine(manor());
            e.exec("go north");
            e.exec("take iron-key");
            e.exec("go south");
            e.exec("go east");
            eq("unlock", "You unlock the door with iron-key.", e.exec("unlock south"));
            eq("exit list drops the flag", String.join("\n",
                    "Damp Cellar",
                    "Something drips in the dark.",
                    "You see: rope.",
                    "Exits: south, west."), e.exec("look"));
            eq("already open", "There is no locked door to the south.", e.exec("unlock south"));
        });

        test("unlock_without_a_door_is_refused", () -> {
            Engine e = new Engine(manor());
            eq("plain exit", "There is no locked door to the north.", e.exec("unlock north"));
            eq("bare wall", "There is no locked door to the west.", e.exec("unlock west"));
        });

        // ---- winning -------------------------------------------------------------

        test("reaching_the_goal_ends_the_story", () -> {
            Engine e = new Engine(manor());
            e.exec("go north");
            e.exec("take iron-key");
            e.exec("go south");
            e.exec("go east");
            e.exec("unlock south");
            eq("the vault", String.join("\n",
                    "Hidden Vault",
                    "Gold glitters behind cobwebs.",
                    "Exits: none.",
                    "*** You made it! ***"), e.exec("go south"));
            yes("finished", e.finished());
            eq("story over", "The story is over.", e.exec("look"));
            eq("story over for moves too", "The story is over.", e.exec("go north"));
        });

        test("unknown_commands_are_shrugged_off", () -> {
            Engine e = new Engine(manor());
            eq("gibberish", "I don't understand.", e.exec("dance"));
            eq("bare go", "I don't understand.", e.exec("go"));
            eq("extra words", "I don't understand.", e.exec("take the lantern"));
        });

        // ---- save / load ------------------------------------------------------------

        test("save_string_format_is_pinned", () -> {
            Engine e = new Engine(manor());
            e.exec("take lantern");
            e.exec("go east");
            e.exec("drop lantern");
            e.exec("go west");
            e.exec("go north");
            e.exec("take iron-key");
            eq("state string",
                    "loc=study;inv=iron-key;open=;rooms=cellar:lantern+rope,hall:,study:,vault:",
                    e.save());
        });

        test("save_records_opened_doors", () -> {
            Engine e = new Engine(manor());
            e.exec("go north");
            e.exec("take iron-key");
            e.exec("go south");
            e.exec("take lantern");
            e.exec("go east");
            e.exec("unlock south");
            eq("state string",
                    "loc=cellar;inv=iron-key,lantern;open=cellar/south;rooms=cellar:rope,hall:,study:,vault:",
                    e.save());
        });

        test("restore_resumes_the_same_story", () -> {
            World w = manor();
            Engine e = Engine.restore(w,
                    "loc=study;inv=iron-key;open=;rooms=cellar:lantern+rope,hall:,study:,vault:");
            eq("look after restore", String.join("\n",
                    "Oak Study",
                    "Bookshelves line every wall.",
                    "Exits: south."), e.exec("look"));
            eq("pack survived", "You carry: iron-key.", e.exec("inventory"));
            eq("round trip",
                    "loc=study;inv=iron-key;open=;rooms=cellar:lantern+rope,hall:,study:,vault:",
                    e.save());
        });

        test("restore_keeps_doors_open", () -> {
            Engine e = Engine.restore(manor(),
                    "loc=cellar;inv=iron-key,lantern;open=cellar/south;rooms=cellar:rope,hall:,study:,vault:");
            eq("straight through", String.join("\n",
                    "Hidden Vault",
                    "Gold glitters behind cobwebs.",
                    "Exits: none.",
                    "*** You made it! ***"), e.exec("go south"));
        });

        test("restore_validates_the_state_string", () -> {
            thrown(IllegalArgumentException.class, () -> Engine.restore(manor(),
                    "loc=nowhere;inv=;open=;rooms=cellar:rope,hall:lantern,study:iron-key,vault:"));
            thrown(IllegalArgumentException.class, () -> Engine.restore(manor(), "garbage"));
            // the lantern cannot be in the pack and the hall at once
            thrown(IllegalArgumentException.class, () -> Engine.restore(manor(),
                    "loc=hall;inv=lantern;open=;rooms=cellar:rope,hall:lantern,study:iron-key,vault:"));
            // every world item must be somewhere
            thrown(IllegalArgumentException.class, () -> Engine.restore(manor(),
                    "loc=hall;inv=;open=;rooms=cellar:rope,hall:,study:iron-key,vault:"));
            // opened doors must exist
            thrown(IllegalArgumentException.class, () -> Engine.restore(manor(),
                    "loc=hall;inv=lantern;open=hall/north;rooms=cellar:rope,hall:,study:iron-key,vault:"));
        });

        test("saving_a_finished_story_is_an_error", () -> {
            Engine e = Engine.restore(manor(),
                    "loc=cellar;inv=iron-key,lantern;open=cellar/south;rooms=cellar:rope,hall:,study:,vault:");
            e.exec("go south");
            thrown(IllegalStateException.class, () -> e.save());
        });

        // ---- a second world on the same engine ----------------------------------------

        test("depot_world_plays_to_the_end", () -> {
            Engine e = new Engine(depot());
            eq("shed", String.join("\n",
                    "Engine Shed",
                    "A cold locomotive sleeps here.",
                    "You see: brass-token.",
                    "Exits: east."), e.exec("go west"));
            eq("take", "Taken.", e.exec("take brass-token"));
            e.exec("go east");
            eq("unlock", "You unlock the door with brass-token.", e.exec("unlock north"));
            eq("office", String.join("\n",
                    "Dispatch Office",
                    "Timetables flutter on a corkboard.",
                    "Exits: none.",
                    "*** You made it! ***"), e.exec("go north"));
            yes("finished", e.finished());
        });

        System.out.println(passed + " passed, " + failed + " failed");
        if (failed > 0) System.exit(1);
    }
}
