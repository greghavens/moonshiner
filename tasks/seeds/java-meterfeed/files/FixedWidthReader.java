import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Reads fixed-width record files by absolute column offsets.
 *
 * This is the reader the nightly meter-import job has used since the Herald
 * head-end went live: register each field's span once, then feed it the whole
 * file. Values come back as trimmed strings keyed by field name, one map per
 * non-blank line.
 */
public final class FixedWidthReader {
    private final List<String> names = new ArrayList<>();
    private final List<int[]> spans = new ArrayList<>();

    /**
     * Registers a field whose value is the characters [start, start+len),
     * trimmed. Returns this reader so registrations chain.
     */
    public FixedWidthReader field(String name, int start, int len) {
        if (name == null || name.isEmpty()) {
            throw new IllegalArgumentException("field name must not be empty");
        }
        if (start < 0 || len <= 0) {
            throw new IllegalArgumentException("bad span for field '" + name + "'");
        }
        if (names.contains(name)) {
            throw new IllegalArgumentException("duplicate field '" + name + "'");
        }
        names.add(name);
        spans.add(new int[] {start, len});
        return this;
    }

    /**
     * Reads every non-blank line of {@code content}. Lines are separated by
     * '\n'; a trailing '\r' is tolerated (the head-end FTP drop flips between
     * unix and dos endings depending on which node exported). Blank lines are
     * skipped. A line too short for a registered field aborts the read with
     * an IllegalArgumentException naming the 1-based physical line.
     */
    public List<Map<String, String>> read(String content) {
        if (names.isEmpty()) {
            throw new IllegalStateException("no fields registered");
        }
        List<Map<String, String>> rows = new ArrayList<>();
        if (content == null || content.isEmpty()) {
            return rows;
        }
        String[] lines = content.split("\n", -1);
        for (int i = 0; i < lines.length; i++) {
            String line = lines[i];
            if (line.endsWith("\r")) {
                line = line.substring(0, line.length() - 1);
            }
            if (line.isEmpty()) {
                continue;
            }
            Map<String, String> row = new LinkedHashMap<>();
            for (int f = 0; f < names.size(); f++) {
                int start = spans.get(f)[0];
                int len = spans.get(f)[1];
                if (start + len > line.length()) {
                    throw new IllegalArgumentException("line " + (i + 1) + " is " + line.length()
                            + " chars, field '" + names.get(f) + "' needs " + (start + len));
                }
                row.put(names.get(f), line.substring(start, start + len).trim());
            }
            rows.add(row);
        }
        return rows;
    }
}
