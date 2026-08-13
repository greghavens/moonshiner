import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Dependency-free, single-file client for the VCF 9.1 vSAN Data Protection snapshot appliance.
 *
 * <p>The wire contract is pinned in {@code docs/contract.json}, which is derived from
 * {@code specifications/vsan-data-protection/vsan-data-protection-openapi.yaml} in the
 * vmware/vcf-api-specs repository. Four operations are in scope:
 *
 * <ul>
 *   <li>{@code Snapservice.Sessions_create}
 *   <li>{@code Snapservice.Clusters.ProtectionGroups_list}
 *   <li>{@code Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task}
 *   <li>{@code Snapservice.Tasks_get}
 * </ul>
 */
public final class SnapserviceSweepClient implements AutoCloseable {

    private static final String SESSION_HEADER = "vmware-api-session-id";
    private static final Duration POLL_INTERVAL = Duration.ofMillis(25);
    private static final int MAX_POLLS_PER_TASK = 4;

    private final String baseUrl;
    private final String username;
    private final String password;
    private final HttpClient http;

    private String sessionToken;

    public SnapserviceSweepClient(String baseUrl, String username, String password) {
        this.baseUrl = baseUrl.endsWith("/") ? baseUrl.substring(0, baseUrl.length() - 1) : baseUrl;
        this.username = username;
        this.password = password;
        this.http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
    }

    /**
     * Takes a one-time snapshot of every ACTIVE protection group in {@code cluster} and waits for
     * each snapshot task to reach a terminal state.
     *
     * @param namePrefix snapshot names are {@code namePrefix + "-" + protectionGroupIdentifier}
     * @param retentionByProtectionGroup retention for the protection groups that have one; a
     *     protection group absent from this map is snapshotted with no retention period
     */
    public SweepResult sweepCluster(
            String cluster, String namePrefix, Map<String, RetentionPeriod> retentionByProtectionGroup)
            throws IOException, InterruptedException {
        // TODO: implement the sweep.
        throw new UnsupportedOperationException("sweepCluster is not implemented yet");
    }

    // ------------------------------------------------------------- operations

    /** {@code Snapservice.Sessions_create}: exchanges basic credentials for a session token. */
    public String login() throws IOException, InterruptedException {
        String credentials = Base64.getEncoder().encodeToString(
                (username + ":" + password).getBytes(StandardCharsets.UTF_8));
        HttpRequest request = HttpRequest.newBuilder(URI.create(baseUrl + "/snapservice/sessions"))
                .header("Authorization", "Basic " + credentials)
                .POST(HttpRequest.BodyPublishers.noBody())
                .build();
        HttpResponse<String> response = http.send(request, HttpResponse.BodyHandlers.ofString());
        if (response.statusCode() != 201) {
            throw new SnapserviceException("Snapservice.Sessions_create", response.statusCode(), response.body());
        }
        sessionToken = (String) Jsonish.parse(response.body());
        return sessionToken;
    }

    /** {@code Snapservice.Clusters.ProtectionGroups_list}: returns the matching protection group ids. */
    public List<String> listProtectionGroups(
            String cluster, List<String> pgs, List<String> names, List<String> states)
            throws IOException, InterruptedException {
        StringBuilder query = new StringBuilder();
        appendRepeated(query, "pgs", pgs);
        appendRepeated(query, "names", names);
        appendRepeated(query, "states", states);
        String path = "/snapservice/clusters/" + encodePathSegment(cluster) + "/protection-groups";
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(baseUrl + path + "?" + query)).GET();

        HttpResponse<String> response = send(builder);
        if (response.statusCode() != 200) {
            throw new SnapserviceException(
                    "Snapservice.Clusters.ProtectionGroups_list", response.statusCode(), response.body());
        }
        List<String> identifiers = new ArrayList<>();
        for (Object item : Jsonish.asArray(Jsonish.asObject(Jsonish.parse(response.body())).get("items"))) {
            identifiers.add((String) Jsonish.asObject(item).get("pg"));
        }
        return identifiers;
    }

    /**
     * {@code Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task}: starts a one-time
     * protection group snapshot and returns the task identifier.
     */
    public String createProtectionGroupSnapshot(
            String cluster, String protectionGroup, String snapshotName, RetentionPeriod retention)
            throws IOException, InterruptedException {
        StringBuilder spec = new StringBuilder("{\"name\":").append(Jsonish.string(snapshotName));
        spec.append(",\"retention\":");
        if (retention == null) {
            spec.append("null");
        } else {
            spec.append("{\"unit\":").append(Jsonish.string(retention.unit))
                    .append(",\"duration\":").append(retention.duration).append('}');
        }
        spec.append('}');

        String path = "/snapservice/clusters/" + encodePathSegment(cluster)
                + "/protection-groups/" + encodePathSegment(protectionGroup) + "/snapshots";
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(baseUrl + path + "?vmw-task=true"))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(spec.toString(), StandardCharsets.UTF_8));

        HttpResponse<String> response = send(builder);
        if (response.statusCode() != 202) {
            throw new SnapserviceException(
                    "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task",
                    response.statusCode(), response.body());
        }
        return (String) Jsonish.parse(response.body());
    }

    /** {@code Snapservice.Tasks_get}: returns the current status of a snapservice task. */
    public String getTaskStatus(String taskId) throws IOException, InterruptedException {
        HttpRequest.Builder builder = HttpRequest.newBuilder(
                URI.create(baseUrl + "/snapservice/tasks/" + encodePathSegment(taskId))).GET();
        HttpResponse<String> response = send(builder);
        if (response.statusCode() != 200) {
            throw new SnapserviceException("Snapservice.Tasks_get", response.statusCode(), response.body());
        }
        return (String) Jsonish.asObject(Jsonish.parse(response.body())).get("status");
    }

    // ---------------------------------------------------------------- transport

    /** Sends {@code builder} authenticated with the current session token. */
    private HttpResponse<String> send(HttpRequest.Builder builder)
            throws IOException, InterruptedException {
        if (sessionToken == null) {
            login();
        }
        builder.setHeader(SESSION_HEADER, sessionToken);
        return http.send(builder.build(), HttpResponse.BodyHandlers.ofString());
    }

    private static void appendRepeated(StringBuilder query, String name, List<String> values) {
        if (query.length() > 0) {
            query.append('&');
        }
        if (values == null || values.isEmpty()) {
            query.append(name).append('=');
            return;
        }
        for (int index = 0; index < values.size(); index++) {
            if (index > 0) {
                query.append('&');
            }
            query.append(name).append('=').append(encodeQueryValue(values.get(index)));
        }
    }

    private static String encodeQueryValue(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private static String encodePathSegment(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8).replace("+", "%20");
    }

    @Override
    public void close() {
        sessionToken = null;
    }

    // ------------------------------------------------------------------ types

    /** {@code Snapservice.RetentionPeriod}. */
    public static final class RetentionPeriod {
        public final String unit;
        public final long duration;

        public RetentionPeriod(String unit, long duration) {
            this.unit = unit;
            this.duration = duration;
        }
    }

    /** One protection group's outcome within a sweep. */
    public static final class SweepEntry {
        public final String protectionGroup;
        public final String snapshotName;
        public final String taskId;
        public final String status;

        public SweepEntry(String protectionGroup, String snapshotName, String taskId, String status) {
            this.protectionGroup = protectionGroup;
            this.snapshotName = snapshotName;
            this.taskId = taskId;
            this.status = status;
        }
    }

    /** Outcome of a whole sweep, in protection group listing order. */
    public static final class SweepResult {
        public final List<SweepEntry> entries;
        public final int sessionsCreated;

        public SweepResult(List<SweepEntry> entries, int sessionsCreated) {
            this.entries = List.copyOf(entries);
            this.sessionsCreated = sessionsCreated;
        }
    }

    /** Raised when the appliance answers an operation with an unexpected status. */
    public static final class SnapserviceException extends IOException {
        public final String operationId;
        public final int status;

        public SnapserviceException(String operationId, int status, String body) {
            super(operationId + " failed with HTTP " + status + ": " + body);
            this.operationId = operationId;
            this.status = status;
        }
    }

    // ------------------------------------------------------------------- json

    /** Minimal JSON reader/escaper so the client stays dependency-free. */
    static final class Jsonish {
        private final String text;
        private int cursor;

        private Jsonish(String text) {
            this.text = text;
        }

        static Object parse(String text) {
            Jsonish parser = new Jsonish(text);
            parser.skipWhitespace();
            Object value = parser.readValue();
            parser.skipWhitespace();
            if (parser.cursor != text.length()) {
                throw new IllegalArgumentException("trailing content at offset " + parser.cursor);
            }
            return value;
        }

        @SuppressWarnings("unchecked")
        static Map<String, Object> asObject(Object value) {
            if (!(value instanceof Map)) {
                throw new IllegalArgumentException("expected a JSON object but found " + value);
            }
            return (Map<String, Object>) value;
        }

        @SuppressWarnings("unchecked")
        static List<Object> asArray(Object value) {
            if (!(value instanceof List)) {
                throw new IllegalArgumentException("expected a JSON array but found " + value);
            }
            return (List<Object>) value;
        }

        static String string(String value) {
            StringBuilder out = new StringBuilder("\"");
            for (int index = 0; index < value.length(); index++) {
                char character = value.charAt(index);
                switch (character) {
                    case '"' -> out.append("\\\"");
                    case '\\' -> out.append("\\\\");
                    case '\n' -> out.append("\\n");
                    case '\r' -> out.append("\\r");
                    case '\t' -> out.append("\\t");
                    default -> {
                        if (character < 0x20) {
                            out.append(String.format("\\u%04x", (int) character));
                        } else {
                            out.append(character);
                        }
                    }
                }
            }
            return out.append('"').toString();
        }

        private Object readValue() {
            return switch (peek()) {
                case '{' -> readObject();
                case '[' -> readArray();
                case '"' -> readString();
                case 't', 'f' -> readBoolean();
                case 'n' -> readNull();
                default -> readNumber();
            };
        }

        private Map<String, Object> readObject() {
            Map<String, Object> members = new LinkedHashMap<>();
            expect('{');
            skipWhitespace();
            if (peek() == '}') {
                cursor++;
                return members;
            }
            while (true) {
                skipWhitespace();
                String key = readString();
                skipWhitespace();
                expect(':');
                skipWhitespace();
                members.put(key, readValue());
                skipWhitespace();
                char next = text.charAt(cursor++);
                if (next == '}') {
                    return members;
                }
                if (next != ',') {
                    throw new IllegalArgumentException("expected ',' or '}' at offset " + (cursor - 1));
                }
            }
        }

        private List<Object> readArray() {
            List<Object> elements = new ArrayList<>();
            expect('[');
            skipWhitespace();
            if (peek() == ']') {
                cursor++;
                return elements;
            }
            while (true) {
                skipWhitespace();
                elements.add(readValue());
                skipWhitespace();
                char next = text.charAt(cursor++);
                if (next == ']') {
                    return elements;
                }
                if (next != ',') {
                    throw new IllegalArgumentException("expected ',' or ']' at offset " + (cursor - 1));
                }
            }
        }

        private String readString() {
            expect('"');
            StringBuilder out = new StringBuilder();
            while (true) {
                char character = text.charAt(cursor++);
                if (character == '"') {
                    return out.toString();
                }
                if (character != '\\') {
                    out.append(character);
                    continue;
                }
                char escape = text.charAt(cursor++);
                switch (escape) {
                    case '"' -> out.append('"');
                    case '\\' -> out.append('\\');
                    case '/' -> out.append('/');
                    case 'b' -> out.append('\b');
                    case 'f' -> out.append('\f');
                    case 'n' -> out.append('\n');
                    case 'r' -> out.append('\r');
                    case 't' -> out.append('\t');
                    case 'u' -> {
                        out.append((char) Integer.parseInt(text.substring(cursor, cursor + 4), 16));
                        cursor += 4;
                    }
                    default -> throw new IllegalArgumentException("bad escape at offset " + (cursor - 1));
                }
            }
        }

        private Boolean readBoolean() {
            if (text.startsWith("true", cursor)) {
                cursor += 4;
                return Boolean.TRUE;
            }
            if (text.startsWith("false", cursor)) {
                cursor += 5;
                return Boolean.FALSE;
            }
            throw new IllegalArgumentException("bad literal at offset " + cursor);
        }

        private Object readNull() {
            if (!text.startsWith("null", cursor)) {
                throw new IllegalArgumentException("bad literal at offset " + cursor);
            }
            cursor += 4;
            return null;
        }

        private Number readNumber() {
            int start = cursor;
            while (cursor < text.length() && "+-0123456789.eE".indexOf(text.charAt(cursor)) >= 0) {
                cursor++;
            }
            String literal = text.substring(start, cursor);
            if (literal.contains(".") || literal.contains("e") || literal.contains("E")) {
                return Double.valueOf(literal);
            }
            return Long.valueOf(literal);
        }

        private char peek() {
            if (cursor >= text.length()) {
                throw new IllegalArgumentException("unexpected end of input");
            }
            return text.charAt(cursor);
        }

        private void expect(char expected) {
            if (peek() != expected) {
                throw new IllegalArgumentException("expected '" + expected + "' at offset " + cursor);
            }
            cursor++;
        }

        private void skipWhitespace() {
            while (cursor < text.length() && Character.isWhitespace(text.charAt(cursor))) {
                cursor++;
            }
        }
    }
}
