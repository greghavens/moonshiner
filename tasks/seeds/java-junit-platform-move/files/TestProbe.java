import java.util.ArrayList;
import java.util.List;

/** Protected deterministic lifecycle evidence shared by fixture tests. */
public final class TestProbe {
    private static final List<String> EVENTS = new ArrayList<>();

    private TestProbe() {}

    public static void reset() { EVENTS.clear(); }
    public static void record(String event) { EVENTS.add(event); }
    public static List<String> events() { return List.copyOf(EVENTS); }
}

