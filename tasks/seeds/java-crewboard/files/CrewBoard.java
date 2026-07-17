import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Objects;

/**
 * In-memory berth board for the crewing desk. Captains post berths; mariners
 * search them. Matching is deliberately simple: every whitespace-separated
 * query term must appear (case-insensitively) somewhere in the title or the
 * description; results come back freshest first.
 */
public final class CrewBoard {
    private final List<Posting> postings = new ArrayList<>();

    /** Adds a berth. Posting ids are unique across the board's lifetime. */
    public void post(Posting posting) {
        Objects.requireNonNull(posting, "posting");
        for (Posting existing : postings) {
            if (existing.id().equals(posting.id())) {
                throw new IllegalArgumentException("duplicate posting '" + posting.id() + "'");
            }
        }
        postings.add(posting);
    }

    public int size() {
        return postings.size();
    }

    /**
     * All berths matching every term of {@code query}, newest postedDay
     * first, ties by id. A blank/null query matches everything.
     */
    public List<Posting> search(String query) {
        List<String> terms = terms(query);
        List<Posting> out = new ArrayList<>();
        for (Posting p : postings) {
            if (matches(p, terms)) {
                out.add(p);
            }
        }
        out.sort(Comparator.comparingInt(Posting::postedDay).reversed()
                .thenComparing(Posting::id));
        return out;
    }

    static List<String> terms(String query) {
        List<String> terms = new ArrayList<>();
        if (query != null) {
            for (String t : query.trim().toLowerCase(Locale.ROOT).split("\\s+")) {
                if (!t.isEmpty()) {
                    terms.add(t);
                }
            }
        }
        return terms;
    }

    static boolean matches(Posting p, List<String> terms) {
        String title = p.title().toLowerCase(Locale.ROOT);
        String description = p.description().toLowerCase(Locale.ROOT);
        for (String t : terms) {
            if (!title.contains(t) && !description.contains(t)) {
                return false;
            }
        }
        return true;
    }

    List<Posting> postings() {
        return postings;
    }
}
