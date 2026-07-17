import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Warp plan for one loom dressing: sections, colorways, threading blocks. */
public class LoomPlan {
    private final List<Section> sections = new ArrayList<>();
    private final Map colorways = new HashMap();

    public void addSection(String name, int ends) {
        if (ends <= 0) {
            throw new IllegalArgumentException("a section needs at least one end");
        }
        sections.add(new Section(name, ends));
    }

    public void assignColorway(String sectionName, String color) {
        colorways.put(sectionName, color);
    }

    /** Sections with no assigned colorway weave in the natural yarn. */
    public String colorwayFor(String sectionName) {
        Object color = colorways.get(sectionName);
        return color == null ? "natural" : (String) color;
    }

    public int totalEnds() {
        List<Integer> perSection = new ArrayList<>();
        for (Section s : sections) {
            perSection.add(s.ends());
        }
        return countTotal(perSection);
    }

    public int draftCount() {
        List<String> drafts = new ArrayList<>();
        for (Section s : sections) {
            drafts.add(s.name());
        }
        return countTotal(drafts);
    }

    /** Section names in the order they will be beamed. */
    public List<String> beamingOrder() {
        List<String> order = new ArrayList<>();
        for (Section s : sections) {
            order.add(s.name());
        }
        return order;
    }

    private static int countTotal(List<Integer> endsPerSection) {
        int total = 0;
        for (int ends : endsPerSection) {
            total += ends;
        }
        return total;
    }

    private static int countTotal(List<String> drafts) {
        return drafts.size();
    }

    static <T> List<T> sequence(T... steps) {
        return List.of(steps);
    }

    /** The standard point-twill threading blocks every plan starts from. */
    public List<List<String>> threadingBlocks() {
        return sequence(List.of("1", "2", "3", "4"), List.of("4", "3", "2", "1"));
    }
}

class Section {
    private final String name;
    private final int ends;

    Section(String name, int ends) {
        this.name = name;
        this.ends = ends;
    }

    String name() {
        return name;
    }

    int ends() {
        return ends;
    }
}
