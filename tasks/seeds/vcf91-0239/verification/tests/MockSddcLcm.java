import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Loopback mock of the VCF 9.1 SDDC LCM Service, pinned to docs/contract.json.
 *
 * It binds 127.0.0.1 on an ephemeral port and serves ONLY the three operations the
 * contract names -- getComponents, getTasks and generateComponentSupportBundle. Any
 * other method/path pair is answered 404 and counted in {@link #unknownRequests}.
 * Every request is appended to {@link #log} before it is handled, so the acceptance
 * verifier can assert the exact wire shape after the fact.
 *
 * The mock deliberately does NOT de-duplicate submissions: each accepted POST creates
 * a brand new task and bumps {@link #supportBundlePosts}. Preventing the duplicate is
 * the client's job.
 *
 * PROTECTED FIXTURE -- do not modify this file.
 */
public final class MockSddcLcm {

    /** Dummy bearer credential. Not a real token; nothing here talks to VMware. */
    public static final String BEARER_TOKEN = "dummy-sddc-lcm-token-9f2c7b";

    // ---------------------------------------------------------------- request log

    /** One observed HTTP request. */
    public static final class Entry {
        public final String op;
        public final String method;
        public final String path;
        public final String rawQuery;
        public final String body;
        public final Map<String, String> headers;
        public int status;

        Entry(String op, String method, String path, String rawQuery, String body,
              Map<String, String> headers) {
            this.op = op;
            this.method = method;
            this.path = path;
            this.rawQuery = rawQuery;
            this.body = body;
            this.headers = Collections.unmodifiableMap(headers);
        }

        /** Header lookup, case-insensitive; null when absent. */
        public String header(String name) {
            return headers.get(name.toLowerCase(Locale.ROOT));
        }

        public boolean hasHeader(String name) {
            return headers.containsKey(name.toLowerCase(Locale.ROOT));
        }

        /** Path plus raw query exactly as it arrived on the wire. */
        public String target() {
            return rawQuery == null || rawQuery.isEmpty() ? path : path + "?" + rawQuery;
        }

        @Override
        public String toString() {
            return method + " " + target() + " -> " + status;
        }
    }

    public final List<Entry> log = Collections.synchronizedList(new ArrayList<>());
    public int unknownRequests = 0;
    public int supportBundlePosts = 0;

    // ------------------------------------------------------------- server state

    /** Components returned by getComponents when scope=FLEET. Mutable by the test. */
    public final List<Map<String, String>> fleetComponents = new ArrayList<>();

    /** Task store, insertion-ordered; that order is also the paging order. */
    public final Map<String, Map<String, String>> tasks = new LinkedHashMap<>();

    /** Page size the mock applies to getTasks. */
    public int pageSize = 50;

    /** Makes one successful getTasks response omit the metadata needed to finish the scan. */
    public boolean omitTaskPageMetadataOnce = false;

    /** Status the mock uses for an accepted submission (contract says 202). */
    public int postSuccessStatus = 202;

    /** Programmed one-shot faults, consumed in order. */
    public final Deque<Fault> componentsFaults = new ArrayDeque<>();
    public final Deque<Fault> tasksFaults = new ArrayDeque<>();
    public final Deque<Fault> postFaults = new ArrayDeque<>();

    public static final class Fault {
        final int status;
        final String code;
        final String message;

        public Fault(int status, String code, String message) {
            this.status = status;
            this.code = code;
            this.message = message;
        }
    }

    private HttpServer server;
    private int taskSeq = 0;
    private int refSeq = 0;

    // ------------------------------------------------------------------ lifecycle

    /** Starts the mock and returns its base URL, e.g. {@code http://127.0.0.1:41235}. */
    public String start() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", this::dispatch);
        server.setExecutor(null);
        server.start();
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    public void stop() {
        if (server != null) {
            server.stop(0);
            server = null;
        }
    }

    /** Registers a FLEET-scoped component. */
    public void addFleetComponent(String id, String fqdn, String componentType) {
        Map<String, String> c = new LinkedHashMap<>();
        c.put("id", id);
        c.put("fqdn", fqdn);
        c.put("componentType", componentType);
        c.put("scope", "FLEET");
        fleetComponents.add(c);
    }

    /** Seeds a task the way the service would have recorded it. Returns the task id. */
    public String seedTask(String componentId, String correlationId, String status,
                           String defaultMessage) {
        return storeTask(componentId, correlationId, status, defaultMessage);
    }

    /** Mutates a stored task's status, e.g. to simulate a run finishing or failing. */
    public void setTaskStatus(String taskId, String status) {
        Map<String, String> t = tasks.get(taskId);
        if (t == null) {
            throw new IllegalArgumentException("no such task: " + taskId);
        }
        t.put("status", status);
    }

    private String storeTask(String componentId, String correlationId, String status,
                             String defaultMessage) {
        String id = String.format("11111111-2222-4333-8444-%012d", ++taskSeq);
        Map<String, String> t = new LinkedHashMap<>();
        t.put("id", id);
        t.put("name", "component_support_bundle_generate");
        t.put("status", status);
        t.put("type", "support-bundle-generate");
        t.put("createdBy", "lcm-pipeline");
        t.put("resourceId", componentId);
        t.put("resourceType", "COMPONENT");
        t.put("createTime", "2026-08-03T09:1" + (taskSeq % 10) + ":00.000Z");
        t.put("correlationId", correlationId);
        t.put("defaultMessage", defaultMessage == null
                ? "Support bundle generation for component " + componentId
                : defaultMessage);
        tasks.put(id, t);
        return id;
    }

    // ------------------------------------------------------------------ dispatch

    private static final Pattern SUPPORT_BUNDLES =
            Pattern.compile("^/v1/components/([^/]+)/support-bundles$");

    private void dispatch(HttpExchange ex) throws IOException {
        String method = ex.getRequestMethod();
        String path = ex.getRequestURI().getRawPath();
        String rawQuery = ex.getRequestURI().getRawQuery();
        byte[] raw = ex.getRequestBody().readAllBytes();
        String body = new String(raw, StandardCharsets.UTF_8);

        Map<String, String> headers = new LinkedHashMap<>();
        ex.getRequestHeaders().forEach((k, v) -> {
            if (v != null && !v.isEmpty()) {
                headers.put(k.toLowerCase(Locale.ROOT), v.get(0));
            }
        });

        Matcher bundleMatch = SUPPORT_BUNDLES.matcher(path);
        boolean isBundlePost = "POST".equals(method) && bundleMatch.matches();
        String op;
        if ("GET".equals(method) && "/v1/components".equals(path)) {
            op = "getComponents";
        } else if ("GET".equals(method) && "/v1/tasks".equals(path)) {
            op = "getTasks";
        } else if (isBundlePost) {
            op = "generateComponentSupportBundle";
        } else {
            op = "<not-in-contract>";
        }

        Entry entry = new Entry(op, method, path, rawQuery, body, headers);
        log.add(entry);

        try {
            String auth = entry.header("authorization");
            if (!("Bearer " + BEARER_TOKEN).equals(auth)) {
                respondError(ex, entry, 401, "LCM_UNAUTHORIZED",
                        "Missing or invalid bearer token.");
                return;
            }
            switch (op) {
                case "getComponents" -> handleGetComponents(ex, entry);
                case "getTasks" -> handleGetTasks(ex, entry);
                case "generateComponentSupportBundle" ->
                        handleGenerate(ex, entry, bundleMatch.group(1));
                default -> {
                    unknownRequests++;
                    respondError(ex, entry, 404, "LCM_OPERATION_NOT_SERVED",
                            "This mock serves only the operations named in docs/contract.json: "
                                    + "getComponents, getTasks, generateComponentSupportBundle. "
                                    + "Received " + method + " " + path + ".");
                }
            }
        } finally {
            ex.close();
        }
    }

    // ---------------------------------------------------------------- operations

    private void handleGetComponents(HttpExchange ex, Entry entry) throws IOException {
        Fault fault = componentsFaults.poll();
        if (fault != null) {
            respondError(ex, entry, fault.status, fault.code, fault.message);
            return;
        }
        Map<String, String> q = parseQuery(entry.rawQuery);
        String bad = rejectUnexpected(q.keySet(), Set.of("scope"));
        if (bad != null) {
            respondError(ex, entry, 400, "LCM_INVALID_QUERY_PARAM", bad);
            return;
        }
        String scope = q.get("scope");
        if (scope == null) {
            respondError(ex, entry, 400, "LCM_INVALID_QUERY_PARAM",
                    "The pinned contract requires scope=FLEET on getComponents; none was sent.");
            return;
        }
        if (!"FLEET".equals(scope)) {
            respondError(ex, entry, 400, "LCM_INVALID_QUERY_PARAM",
                    "The pinned contract requires scope=FLEET on getComponents; got " + scope + ".");
            return;
        }

        StringBuilder sb = new StringBuilder("{\"components\":[");
        for (int i = 0; i < fleetComponents.size(); i++) {
            Map<String, String> c = fleetComponents.get(i);
            if (i > 0) {
                sb.append(',');
            }
            sb.append("{\"id\":").append(str(c.get("id")))
              .append(",\"componentType\":").append(str(c.get("componentType")))
              .append(",\"deploymentType\":\"OVA\"")
              .append(",\"version\":\"9.1.0.0\"")
              .append(",\"size\":\"Medium\"")
              .append(",\"fqdn\":").append(str(c.get("fqdn")))
              .append(",\"scope\":").append(str(c.get("scope")))
              .append(",\"nodes\":[]}");
        }
        sb.append("]}");
        respond(ex, entry, 200, sb.toString());
    }

    private void handleGetTasks(HttpExchange ex, Entry entry) throws IOException {
        Fault fault = tasksFaults.poll();
        if (fault != null) {
            respondError(ex, entry, fault.status, fault.code, fault.message);
            return;
        }
        Map<String, String> q = parseQuery(entry.rawQuery);
        String bad = rejectUnexpected(q.keySet(), Set.of("resourceId", "resourceType", "pageNumber"));
        if (bad != null) {
            respondError(ex, entry, 400, "LCM_INVALID_QUERY_PARAM", bad);
            return;
        }
        String resourceId = q.get("resourceId");
        String resourceType = q.get("resourceType");
        if (resourceId == null || resourceType == null) {
            respondError(ex, entry, 400, "LCM_INVALID_QUERY_PARAM",
                    "getTasks requires resourceId and resourceType per the pinned contract.");
            return;
        }
        int page = 0;
        if (q.containsKey("pageNumber")) {
            try {
                page = Integer.parseInt(q.get("pageNumber"));
            } catch (NumberFormatException nfe) {
                respondError(ex, entry, 400, "LCM_INVALID_QUERY_PARAM",
                        "pageNumber must be an integer, got " + q.get("pageNumber") + ".");
                return;
            }
            if (page < 0) {
                respondError(ex, entry, 400, "LCM_INVALID_QUERY_PARAM",
                        "pageNumber must be zero-based and non-negative, got " + page + ".");
                return;
            }
        }

        List<Map<String, String>> matched = new ArrayList<>();
        for (Map<String, String> t : tasks.values()) {
            if (resourceId.equals(t.get("resourceId")) && resourceType.equals(t.get("resourceType"))) {
                matched.add(t);
            }
        }
        int total = matched.size();
        int totalPages = total == 0 ? 0 : (total + pageSize - 1) / pageSize;
        int from = Math.min(page * pageSize, total);
        int to = Math.min(from + pageSize, total);

        StringBuilder sb = new StringBuilder("{\"elements\":[");
        for (int i = from; i < to; i++) {
            if (i > from) {
                sb.append(',');
            }
            sb.append(taskSummaryJson(matched.get(i)));
        }
        sb.append(']');
        if (omitTaskPageMetadataOnce) {
            omitTaskPageMetadataOnce = false;
        } else {
            sb.append(",\"pageMetadata\":{\"pageNumber\":").append(page)
              .append(",\"pageSize\":").append(pageSize)
              .append(",\"totalElements\":").append(total)
              .append(",\"totalPages\":").append(totalPages)
              .append('}');
        }
        sb.append('}');
        respond(ex, entry, 200, sb.toString());
    }

    private void handleGenerate(HttpExchange ex, Entry entry, String componentId)
            throws IOException {
        if (entry.rawQuery != null && !entry.rawQuery.isEmpty()) {
            respondError(ex, entry, 400, "LCM_INVALID_QUERY_PARAM",
                    "generateComponentSupportBundle takes no query parameters; got ?"
                            + entry.rawQuery + ".");
            return;
        }
        String ct = entry.header("content-type");
        if (ct == null || !ct.toLowerCase(Locale.ROOT).startsWith("application/json")) {
            respondError(ex, entry, 415, "LCM_UNSUPPORTED_MEDIA_TYPE",
                    "Content-Type must be application/json, got " + ct + ".");
            return;
        }
        Object parsed;
        try {
            parsed = Json.parse(entry.body);
        } catch (RuntimeException re) {
            respondError(ex, entry, 400, "LCM_MALFORMED_BODY",
                    "Request body is not valid JSON: " + re.getMessage());
            return;
        }
        if (!(parsed instanceof Map)) {
            respondError(ex, entry, 400, "LCM_MALFORMED_BODY",
                    "Request body must be a ComponentSupportBundleSpec object.");
            return;
        }
        boolean known = false;
        for (Map<String, String> c : fleetComponents) {
            if (c.get("id").equals(componentId)) {
                known = true;
                break;
            }
        }

        Fault fault = postFaults.poll();
        if (fault != null) {
            respondError(ex, entry, fault.status, fault.code, fault.message);
            return;
        }
        if (!known) {
            respondError(ex, entry, 404, "LCM_COMPONENT_NOT_FOUND",
                    "No component with id " + componentId + ".");
            return;
        }

        supportBundlePosts++;
        String correlationId = entry.header("x-correlation-id");
        String taskId = storeTask(componentId, correlationId, "PENDING", null);
        respond(ex, entry, postSuccessStatus, taskJson(tasks.get(taskId)));
    }

    // ------------------------------------------------------------------- payloads

    private String taskSummaryJson(Map<String, String> t) {
        return "{\"id\":" + str(t.get("id"))
                + ",\"name\":" + str(t.get("name"))
                + ",\"description\":{\"id\":\"com.broadcom.lcm.ops.supportbundle.generate\""
                + ",\"defaultMessage\":" + str(t.get("defaultMessage"))
                + ",\"localizedMessage\":" + str(t.get("defaultMessage"))
                + ",\"args\":{\"componentId\":" + str(t.get("resourceId")) + "}}"
                + ",\"status\":" + str(t.get("status"))
                + ",\"type\":" + str(t.get("type"))
                + ",\"createdBy\":" + str(t.get("createdBy"))
                + ",\"resourceId\":" + str(t.get("resourceId"))
                + ",\"resourceType\":" + str(t.get("resourceType"))
                + ",\"createTime\":" + str(t.get("createTime"))
                + ",\"correlationId\":" + str(t.get("correlationId"))
                + ",\"retriable\":false,\"cancellable\":true}";
    }

    private String taskJson(Map<String, String> t) {
        String summary = taskSummaryJson(t);
        return summary.substring(0, summary.length() - 1)
                + ",\"stages\":[],\"subTasks\":[],\"messages\":[]}";
    }

    private void respondError(HttpExchange ex, Entry entry, int status, String code,
                              String message) throws IOException {
        String ref = String.format("ref-%04d", ++refSeq);
        String payload = "{\"code\":" + str(code)
                + ",\"message\":{\"id\":\"com.broadcom.lcm.error\",\"defaultMessage\":" + str(message)
                + ",\"localizedMessage\":" + str(message) + ",\"args\":{}}"
                + ",\"resolution\":{\"id\":\"com.broadcom.lcm.error.resolution\""
                + ",\"defaultMessage\":\"Inspect the request against docs/contract.json.\""
                + ",\"localizedMessage\":\"Inspect the request against docs/contract.json.\""
                + ",\"args\":{}}"
                + ",\"referenceId\":" + str(ref)
                + ",\"timestamp\":\"2026-08-03T09:30:00.000Z\"}";
        respond(ex, entry, status, payload);
    }

    private void respond(HttpExchange ex, Entry entry, int status, String payload)
            throws IOException {
        entry.status = status;
        byte[] out = payload.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "application/json");
        ex.sendResponseHeaders(status, out.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(out);
        }
    }

    // -------------------------------------------------------------------- helpers

    private static String rejectUnexpected(Set<String> got, Set<String> allowed) {
        for (String k : got) {
            if (!allowed.contains(k)) {
                if ("correlationId".equals(k)) {
                    return "getTasks has no correlationId query parameter in the SDDC LCM 9.1 "
                            + "specification; correlation matching is a client-side scan.";
                }
                return "Query parameter '" + k + "' is not part of the pinned contract for this "
                        + "operation (allowed: " + allowed + ").";
            }
        }
        return null;
    }

    /** Parses a raw query string into first-value-wins decoded pairs. */
    public static Map<String, String> parseQuery(String rawQuery) {
        Map<String, String> out = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return out;
        }
        for (String pair : rawQuery.split("&", -1)) {
            if (pair.isEmpty()) {
                continue;
            }
            int eq = pair.indexOf('=');
            String k = eq < 0 ? pair : pair.substring(0, eq);
            String v = eq < 0 ? "" : pair.substring(eq + 1);
            out.putIfAbsent(URLDecoder.decode(k, StandardCharsets.UTF_8),
                    URLDecoder.decode(v, StandardCharsets.UTF_8));
        }
        return out;
    }

    private static String str(String s) {
        if (s == null) {
            return "null";
        }
        StringBuilder sb = new StringBuilder("\"");
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"' -> sb.append("\\\"");
                case '\\' -> sb.append("\\\\");
                case '\n' -> sb.append("\\n");
                case '\r' -> sb.append("\\r");
                case '\t' -> sb.append("\\t");
                default -> {
                    if (c < 0x20) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
                }
            }
        }
        return sb.append('"').toString();
    }

    // ------------------------------------------------------------- tiny JSON reader

    /**
     * Minimal JSON reader shared with the verifier. Produces Map, List, String,
     * Double, Boolean and null. Throws on trailing garbage or malformed input.
     */
    public static final class Json {
        private final String s;
        private int i;

        private Json(String s) {
            this.s = s;
        }

        public static Object parse(String text) {
            Json p = new Json(text);
            p.ws();
            Object v = p.value();
            p.ws();
            if (p.i != text.length()) {
                throw new IllegalArgumentException("trailing content at offset " + p.i);
            }
            return v;
        }

        @SuppressWarnings("unchecked")
        public static Map<String, Object> object(String text) {
            Object v = parse(text);
            if (!(v instanceof Map)) {
                throw new IllegalArgumentException("expected a JSON object");
            }
            return (Map<String, Object>) v;
        }

        private void ws() {
            while (i < s.length() && Character.isWhitespace(s.charAt(i))) {
                i++;
            }
        }

        private Object value() {
            if (i >= s.length()) {
                throw new IllegalArgumentException("unexpected end of input");
            }
            char c = s.charAt(i);
            switch (c) {
                case '{':
                    return obj();
                case '[':
                    return arr();
                case '"':
                    return string();
                case 't':
                    expect("true");
                    return Boolean.TRUE;
                case 'f':
                    expect("false");
                    return Boolean.FALSE;
                case 'n':
                    expect("null");
                    return null;
                default:
                    return number();
            }
        }

        private void expect(String lit) {
            if (!s.startsWith(lit, i)) {
                throw new IllegalArgumentException("expected " + lit + " at offset " + i);
            }
            i += lit.length();
        }

        private Map<String, Object> obj() {
            Map<String, Object> m = new LinkedHashMap<>();
            i++;
            ws();
            if (i < s.length() && s.charAt(i) == '}') {
                i++;
                return m;
            }
            while (true) {
                ws();
                String k = string();
                ws();
                if (i >= s.length() || s.charAt(i) != ':') {
                    throw new IllegalArgumentException("expected ':' at offset " + i);
                }
                i++;
                ws();
                m.put(k, value());
                ws();
                if (i >= s.length()) {
                    throw new IllegalArgumentException("unterminated object");
                }
                char c = s.charAt(i++);
                if (c == '}') {
                    return m;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or '}' at offset " + (i - 1));
                }
            }
        }

        private List<Object> arr() {
            List<Object> l = new ArrayList<>();
            i++;
            ws();
            if (i < s.length() && s.charAt(i) == ']') {
                i++;
                return l;
            }
            while (true) {
                ws();
                l.add(value());
                ws();
                if (i >= s.length()) {
                    throw new IllegalArgumentException("unterminated array");
                }
                char c = s.charAt(i++);
                if (c == ']') {
                    return l;
                }
                if (c != ',') {
                    throw new IllegalArgumentException("expected ',' or ']' at offset " + (i - 1));
                }
            }
        }

        private String string() {
            if (i >= s.length() || s.charAt(i) != '"') {
                throw new IllegalArgumentException("expected a string at offset " + i);
            }
            i++;
            StringBuilder sb = new StringBuilder();
            while (true) {
                if (i >= s.length()) {
                    throw new IllegalArgumentException("unterminated string");
                }
                char c = s.charAt(i++);
                if (c == '"') {
                    return sb.toString();
                }
                if (c != '\\') {
                    sb.append(c);
                    continue;
                }
                char e = s.charAt(i++);
                switch (e) {
                    case '"' -> sb.append('"');
                    case '\\' -> sb.append('\\');
                    case '/' -> sb.append('/');
                    case 'b' -> sb.append('\b');
                    case 'f' -> sb.append('\f');
                    case 'n' -> sb.append('\n');
                    case 'r' -> sb.append('\r');
                    case 't' -> sb.append('\t');
                    case 'u' -> {
                        sb.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                        i += 4;
                    }
                    default -> throw new IllegalArgumentException("bad escape \\" + e);
                }
            }
        }

        private Double number() {
            int start = i;
            while (i < s.length() && "+-.eE0123456789".indexOf(s.charAt(i)) >= 0) {
                i++;
            }
            if (start == i) {
                throw new IllegalArgumentException("expected a value at offset " + i);
            }
            return Double.valueOf(s.substring(start, i));
        }
    }
}
