import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Loopback mock of the VCF Operations for Networks 9.1 API, pinned to
 * docs/contract.json. It serves ONLY the five operations the contract names:
 *
 *   create               POST   /api/ni/auth/token
 *   addApplication       POST   /api/ni/groups/applications
 *   addTier              POST   /api/ni/groups/applications/{id}/tiers
 *   listApplicationTiers GET    /api/ni/groups/applications/{id}/tiers
 *   delete               DELETE /api/ni/auth/token
 *
 * Anything else is answered 404 "operation not in contract" and still logged,
 * so the verifier can detect off-contract traffic.
 *
 * Every request is appended to a JSON Lines request log that the test reads.
 * Bodies are logged verbatim so the verifier can assert the exact wire shape,
 * including which optional keys were present.
 */
public final class MockVcfOnServer {

    static final String BASE = "/api/ni";
    static final String TOKEN = "Mgs2YX0ZSY+gHW6RYypeeA==";
    static final long TOKEN_EXPIRY = 1609459200000L;
    static final String USERNAME = "onboarding-svc@local";
    static final String PASSWORD = "Ins1ght!-Onboard";
    static final String APP_ENTITY_ID = "18230:561:271275765";

    /** The one security group the fixture's inventory actually contains. */
    static final String KNOWN_SECURITY_GROUP = "18230:82:604573173";

    private static final Pattern TIERS_PATH =
            Pattern.compile("^" + Pattern.quote(BASE) + "/groups/applications/([^/]+)/tiers$");

    private final HttpServer server;
    private final Path logPath;
    private final AtomicInteger seq = new AtomicInteger();
    private final Object lock = new Object();

    /** Tiers that were actually created, in creation order. */
    private final List<String[]> createdTiers = new ArrayList<>();
    private boolean tokenIssued = false;
    private boolean tokenRevoked = false;
    private int tierIdCounter = 1266458745;

    public MockVcfOnServer(Path logPath) throws IOException {
        this.logPath = logPath;
        Files.createDirectories(logPath.toAbsolutePath().getParent());
        Files.write(logPath, new byte[0]);
        this.server = HttpServer.create(
                new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        this.server.createContext("/", this::dispatch);
        this.server.setExecutor(null);
    }

    public void start() {
        server.start();
    }

    public void stop() {
        server.stop(0);
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    // ---------------------------------------------------------------- routing

    private void dispatch(HttpExchange ex) throws IOException {
        String method = ex.getRequestMethod();
        String path = ex.getRequestURI().getPath();
        String query = ex.getRequestURI().getRawQuery();
        byte[] bodyBytes = readAll(ex.getRequestBody());
        String body = new String(bodyBytes, StandardCharsets.UTF_8);

        String opId = resolveOperation(method, path);
        Reply reply;
        try {
            reply = handle(opId, method, path, ex, body, bodyBytes.length);
        } catch (RuntimeException e) {
            reply = new Reply(500, apiError(500, "mock failure: " + e));
        }

        log(method, path, query, opId, ex, body, bodyBytes.length, reply.status);

        byte[] out = reply.body == null
                ? new byte[0]
                : reply.body.getBytes(StandardCharsets.UTF_8);
        if (out.length > 0) {
            ex.getResponseHeaders().add("Content-Type", "application/json");
            ex.sendResponseHeaders(reply.status, out.length);
            try (OutputStream os = ex.getResponseBody()) {
                os.write(out);
            }
        } else {
            ex.sendResponseHeaders(reply.status, -1);
            ex.close();
        }
    }

    /** Maps a method+path onto a contract operationId, or null when off-contract. */
    private static String resolveOperation(String method, String path) {
        if (path.equals(BASE + "/auth/token")) {
            if (method.equals("POST")) return "create";
            if (method.equals("DELETE")) return "delete";
            return null;
        }
        if (path.equals(BASE + "/groups/applications") && method.equals("POST")) {
            return "addApplication";
        }
        if (TIERS_PATH.matcher(path).matches()) {
            if (method.equals("POST")) return "addTier";
            if (method.equals("GET")) return "listApplicationTiers";
        }
        return null;
    }

    // --------------------------------------------------------------- handlers

    private Reply handle(String opId, String method, String path, HttpExchange ex,
                         String body, int bodyLen) {
        if (opId == null) {
            return new Reply(404, apiError(404,
                    "operation not in contract: " + method + " " + path));
        }
        String auth = header(ex, "Authorization");

        switch (opId) {
            case "create":
                return handleCreate(ex, body, bodyLen);
            case "delete": {
                Reply denied = requireAuth(auth);
                if (denied != null) return denied;
                synchronized (lock) {
                    tokenRevoked = true;
                }
                return new Reply(204, null);
            }
            case "addApplication": {
                Reply denied = requireAuth(auth);
                if (denied != null) return denied;
                return handleAddApplication(ex, body, bodyLen);
            }
            case "addTier": {
                Reply denied = requireAuth(auth);
                if (denied != null) return denied;
                return handleAddTier(pathId(path), ex, body, bodyLen);
            }
            case "listApplicationTiers": {
                Reply denied = requireAuth(auth);
                if (denied != null) return denied;
                if (bodyLen > 0) {
                    return new Reply(400, apiError(400,
                            "listApplicationTiers takes no request body"));
                }
                return handleListTiers(pathId(path));
            }
            default:
                return new Reply(404, apiError(404, "unrouted operation " + opId));
        }
    }

    private Reply handleCreate(HttpExchange ex, String body, int bodyLen) {
        if (header(ex, "Authorization") != null) {
            return new Reply(400, apiError(400,
                    "create must not send an Authorization header"));
        }
        Reply badType = requireJsonContentType(ex, bodyLen);
        if (badType != null) return badType;

        Map<String, Object> req = Json.parseObject(body);
        if (req == null) {
            return new Reply(400, apiError(400, "request body is not a JSON object"));
        }
        Reply extra = rejectUnknownKeys(req, "UserCredential",
                "username", "password", "domain");
        if (extra != null) return extra;

        Object user = req.get("username");
        Object pass = req.get("password");
        if (!(user instanceof String) || !(pass instanceof String)) {
            return new Reply(400, apiError(400, "username and password are required"));
        }
        if (!USERNAME.equals(user) || !PASSWORD.equals(pass)) {
            return new Reply(401, apiError(401, "invalid credentials"));
        }
        synchronized (lock) {
            tokenIssued = true;
            tokenRevoked = false;
        }
        return new Reply(200, "{\"token\":" + Json.str(TOKEN)
                + ",\"expiry\":" + TOKEN_EXPIRY + "}");
    }

    private Reply handleAddApplication(HttpExchange ex, String body, int bodyLen) {
        Reply badType = requireJsonContentType(ex, bodyLen);
        if (badType != null) return badType;

        Map<String, Object> req = Json.parseObject(body);
        if (req == null) {
            return new Reply(400, apiError(400, "request body is not a JSON object"));
        }
        Reply extra = rejectUnknownKeys(req, "ApplicationRequest", "name");
        if (extra != null) return extra;

        Object name = req.get("name");
        if (!(name instanceof String) || ((String) name).isEmpty()) {
            return new Reply(400, apiError(400, "name is required"));
        }
        StringBuilder sb = new StringBuilder();
        sb.append("{\"entity_id\":").append(Json.str(APP_ENTITY_ID));
        sb.append(",\"name\":").append(Json.str((String) name));
        sb.append(",\"entity_type\":\"Application\"");
        sb.append(",\"create_time\":1509410056733");
        sb.append(",\"created_by\":").append(Json.str(USERNAME));
        sb.append(",\"tier_count\":0");
        sb.append("}");
        return new Reply(201, sb.toString());
    }

    @SuppressWarnings("unchecked")
    private Reply handleAddTier(String appId, HttpExchange ex, String body, int bodyLen) {
        if (!APP_ENTITY_ID.equals(appId)) {
            return new Reply(404, apiError(404, "application not found: " + appId));
        }
        Reply badType = requireJsonContentType(ex, bodyLen);
        if (badType != null) return badType;

        Map<String, Object> req = Json.parseObject(body);
        if (req == null) {
            return new Reply(400, apiError(400, "request body is not a JSON object"));
        }
        Reply extra = rejectUnknownKeys(req, "TierRequest", "name", "entity_id",
                "group_membership_criteria", "member_list", "source_group_entity_id");
        if (extra != null) return extra;

        Object name = req.get("name");
        if (!(name instanceof String) || ((String) name).isEmpty()) {
            return new Reply(400, apiError(400, "tier name is required"));
        }
        String tierName = (String) name;

        synchronized (lock) {
            for (String[] existing : createdTiers) {
                if (existing[1].equals(tierName)) {
                    return new Reply(400, apiError(400,
                            "a tier named '" + tierName + "' already exists in this application"));
                }
            }
        }

        Object criteriaObj = req.get("group_membership_criteria");
        if (criteriaObj != null) {
            if (!(criteriaObj instanceof List)) {
                return new Reply(400, apiError(400,
                        "group_membership_criteria must be an array"));
            }
            for (Object item : (List<Object>) criteriaObj) {
                if (!(item instanceof Map)) {
                    return new Reply(400, apiError(400,
                            "group_membership_criteria entries must be objects"));
                }
                Map<String, Object> crit = (Map<String, Object>) item;
                Reply bad = validateCriteria(crit);
                if (bad != null) return bad;
            }
        }

        String tierId;
        synchronized (lock) {
            tierId = "18230:562:" + (tierIdCounter++);
            createdTiers.add(new String[]{tierId, tierName});
        }

        StringBuilder sb = new StringBuilder();
        sb.append("{\"entity_id\":").append(Json.str(tierId));
        sb.append(",\"name\":").append(Json.str(tierName));
        sb.append(",\"entity_type\":\"Tier\"");
        sb.append(",\"application\":{\"entity_id\":").append(Json.str(APP_ENTITY_ID));
        sb.append(",\"entity_type\":\"Application\"}");
        sb.append("}");
        return new Reply(201, sb.toString());
    }

    @SuppressWarnings("unchecked")
    private Reply validateCriteria(Map<String, Object> crit) {
        Reply extra = rejectUnknownKeys(crit, "GroupMembershipCriteria", "membership_type",
                "search_membership_criteria", "ip_address_membership_criteria");
        if (extra != null) return extra;

        Object type = crit.get("membership_type");
        if (!(type instanceof String)) {
            return new Reply(400, apiError(400, "membership_type is required"));
        }
        if ("SearchMembershipCriteria".equals(type)) {
            if (crit.containsKey("ip_address_membership_criteria")) {
                return new Reply(400, apiError(400,
                        "ip_address_membership_criteria must be omitted when membership_type "
                                + "is SearchMembershipCriteria"));
            }
            Object smc = crit.get("search_membership_criteria");
            if (!(smc instanceof Map)) {
                return new Reply(400, apiError(400,
                        "search_membership_criteria is required for SearchMembershipCriteria"));
            }
            Map<String, Object> sm = (Map<String, Object>) smc;
            Reply se = rejectUnknownKeys(sm, "SearchMembershipCriteria", "entity_type", "filter");
            if (se != null) return se;
            Object filter = sm.get("filter");
            if (!(filter instanceof String)) {
                return new Reply(400, apiError(400, "filter is required"));
            }
            // Inventory check: only one security group exists in this fixture.
            Matcher m = Pattern.compile("security_groups\\.entity_id\\s*=\\s*'([^']*)'")
                    .matcher((String) filter);
            if (m.find() && !KNOWN_SECURITY_GROUP.equals(m.group(1))) {
                return new Reply(400, apiErrorWithDetail(400,
                        "Invalid membership criteria: no entity matches filter",
                        400,
                        "security group '" + m.group(1) + "' does not exist in the inventory",
                        "group_membership_criteria[0].search_membership_criteria.filter"));
            }
            return null;
        }
        if ("IPAddressMembershipCriteria".equals(type)) {
            if (crit.containsKey("search_membership_criteria")) {
                return new Reply(400, apiError(400,
                        "search_membership_criteria must be omitted when membership_type "
                                + "is IPAddressMembershipCriteria"));
            }
            Object ipc = crit.get("ip_address_membership_criteria");
            if (!(ipc instanceof Map)) {
                return new Reply(400, apiError(400,
                        "ip_address_membership_criteria is required for IPAddressMembershipCriteria"));
            }
            Map<String, Object> ip = (Map<String, Object>) ipc;
            Reply ie = rejectUnknownKeys(ip, "IpAddressMembershipCriteria", "ip_addresses");
            if (ie != null) return ie;
            Object addrs = ip.get("ip_addresses");
            if (!(addrs instanceof List) || ((List<Object>) addrs).isEmpty()) {
                return new Reply(400, apiError(400, "ip_addresses must be a non-empty array"));
            }
            return null;
        }
        return new Reply(400, apiError(400, "unsupported membership_type: " + type));
    }

    private Reply handleListTiers(String appId) {
        if (!APP_ENTITY_ID.equals(appId)) {
            return new Reply(404, apiError(404, "application not found: " + appId));
        }
        StringBuilder sb = new StringBuilder("{\"results\":[");
        synchronized (lock) {
            for (int i = 0; i < createdTiers.size(); i++) {
                if (i > 0) sb.append(',');
                String[] t = createdTiers.get(i);
                sb.append("{\"entity_id\":").append(Json.str(t[0]));
                sb.append(",\"name\":").append(Json.str(t[1]));
                sb.append(",\"entity_type\":\"Tier\"");
                sb.append(",\"application\":{\"entity_id\":").append(Json.str(APP_ENTITY_ID));
                sb.append(",\"entity_type\":\"Application\"}}");
            }
        }
        sb.append("]}");
        return new Reply(200, sb.toString());
    }

    // ----------------------------------------------------------------- checks

    private Reply requireAuth(String auth) {
        if (auth == null) {
            return new Reply(401, apiError(401, "missing Authorization header"));
        }
        if (!auth.equals("NetworkInsight " + TOKEN)) {
            return new Reply(401, apiError(401,
                    "Authorization must be 'NetworkInsight {token}' with a valid token"));
        }
        synchronized (lock) {
            if (!tokenIssued || tokenRevoked) {
                return new Reply(401, apiError(401, "token is not active"));
            }
        }
        return null;
    }

    private Reply requireJsonContentType(HttpExchange ex, int bodyLen) {
        if (bodyLen == 0) {
            return new Reply(400, apiError(400, "a JSON request body is required"));
        }
        String ct = header(ex, "Content-Type");
        if (ct == null || !ct.toLowerCase().startsWith("application/json")) {
            return new Reply(415, apiError(415,
                    "Content-Type must be application/json, got: " + ct));
        }
        return null;
    }

    private static Reply rejectUnknownKeys(Map<String, Object> obj, String schema,
                                           String... allowed) {
        outer:
        for (String key : obj.keySet()) {
            for (String a : allowed) {
                if (a.equals(key)) continue outer;
            }
            return new Reply(400, apiError(400,
                    "unknown field '" + key + "' for schema " + schema));
        }
        return null;
    }

    private static String pathId(String path) {
        Matcher m = TIERS_PATH.matcher(path);
        return m.matches() ? urlDecode(m.group(1)) : null;
    }

    private static String urlDecode(String s) {
        try {
            return java.net.URLDecoder.decode(s, StandardCharsets.UTF_8);
        } catch (Exception e) {
            return s;
        }
    }

    private static String header(HttpExchange ex, String name) {
        List<String> v = ex.getRequestHeaders().get(name);
        return (v == null || v.isEmpty()) ? null : v.get(0);
    }

    private static String apiError(int code, String message) {
        return "{\"code\":" + code + ",\"message\":" + Json.str(message) + "}";
    }

    private static String apiErrorWithDetail(int code, String message,
                                             int detailCode, String detailMessage,
                                             String target) {
        return "{\"code\":" + code + ",\"message\":" + Json.str(message)
                + ",\"details\":[{\"code\":" + detailCode
                + ",\"message\":" + Json.str(detailMessage)
                + ",\"target\":[" + Json.str(target) + "]}]}";
    }

    // -------------------------------------------------------------------- log

    private void log(String method, String path, String query, String opId,
                     HttpExchange ex, String body, int bodyLen, int status)
            throws IOException {
        Map<String, String> headers = new LinkedHashMap<>();
        headers.put("authorization", header(ex, "Authorization"));
        headers.put("content-type", header(ex, "Content-Type"));
        headers.put("accept", header(ex, "Accept"));

        StringBuilder sb = new StringBuilder();
        sb.append('{');
        sb.append("\"seq\":").append(seq.incrementAndGet());
        sb.append(",\"method\":").append(Json.str(method));
        sb.append(",\"path\":").append(Json.str(path));
        sb.append(",\"query\":").append(query == null ? "null" : Json.str(query));
        sb.append(",\"operation_id\":").append(opId == null ? "null" : Json.str(opId));
        sb.append(",\"headers\":{");
        boolean first = true;
        for (Map.Entry<String, String> e : headers.entrySet()) {
            if (!first) sb.append(',');
            first = false;
            sb.append(Json.str(e.getKey())).append(':')
              .append(e.getValue() == null ? "null" : Json.str(e.getValue()));
        }
        sb.append('}');
        sb.append(",\"body_present\":").append(bodyLen > 0);
        sb.append(",\"body_raw\":").append(bodyLen > 0 ? Json.str(body) : "null");
        sb.append(",\"response_status\":").append(status);
        sb.append('}');
        sb.append('\n');

        synchronized (lock) {
            Files.writeString(logPath, sb.toString(), StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        }
    }

    private static byte[] readAll(InputStream in) throws IOException {
        return in.readAllBytes();
    }

    private static final class Reply {
        final int status;
        final String body;

        Reply(int status, String body) {
            this.status = status;
            this.body = body;
        }
    }

    /** Minimal JSON reader/escaper — enough for the mock's own needs. */
    static final class Json {
        static String str(String s) {
            StringBuilder sb = new StringBuilder("\"");
            for (int i = 0; i < s.length(); i++) {
                char c = s.charAt(i);
                switch (c) {
                    case '"': sb.append("\\\""); break;
                    case '\\': sb.append("\\\\"); break;
                    case '\n': sb.append("\\n"); break;
                    case '\r': sb.append("\\r"); break;
                    case '\t': sb.append("\\t"); break;
                    case '\b': sb.append("\\b"); break;
                    case '\f': sb.append("\\f"); break;
                    default:
                        if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                        else sb.append(c);
                }
            }
            return sb.append('"').toString();
        }

        @SuppressWarnings("unchecked")
        static Map<String, Object> parseObject(String s) {
            try {
                P p = new P(s);
                p.ws();
                Object v = p.value();
                p.ws();
                if (p.i != p.s.length()) return null;
                return (v instanceof Map) ? (Map<String, Object>) v : null;
            } catch (RuntimeException e) {
                return null;
            }
        }

        private static final class P {
            final String s;
            int i;

            P(String s) { this.s = s; }

            void ws() {
                while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++;
            }

            char peek() {
                if (i >= s.length()) throw new IllegalStateException("eof");
                return s.charAt(i);
            }

            Object value() {
                ws();
                char c = peek();
                if (c == '{') return object();
                if (c == '[') return array();
                if (c == '"') return string();
                if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
                if (s.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
                if (s.startsWith("null", i)) { i += 4; return null; }
                return number();
            }

            Map<String, Object> object() {
                Map<String, Object> m = new LinkedHashMap<>();
                i++; // {
                ws();
                if (peek() == '}') { i++; return m; }
                while (true) {
                    ws();
                    String k = string();
                    ws();
                    if (peek() != ':') throw new IllegalStateException("expected :");
                    i++;
                    m.put(k, value());
                    ws();
                    char c = peek();
                    if (c == ',') { i++; continue; }
                    if (c == '}') { i++; return m; }
                    throw new IllegalStateException("expected , or }");
                }
            }

            List<Object> array() {
                List<Object> l = new ArrayList<>();
                i++; // [
                ws();
                if (peek() == ']') { i++; return l; }
                while (true) {
                    l.add(value());
                    ws();
                    char c = peek();
                    if (c == ',') { i++; continue; }
                    if (c == ']') { i++; return l; }
                    throw new IllegalStateException("expected , or ]");
                }
            }

            String string() {
                if (peek() != '"') throw new IllegalStateException("expected string");
                i++;
                StringBuilder sb = new StringBuilder();
                while (true) {
                    char c = s.charAt(i++);
                    if (c == '"') return sb.toString();
                    if (c == '\\') {
                        char e = s.charAt(i++);
                        switch (e) {
                            case 'n': sb.append('\n'); break;
                            case 't': sb.append('\t'); break;
                            case 'r': sb.append('\r'); break;
                            case 'b': sb.append('\b'); break;
                            case 'f': sb.append('\f'); break;
                            case 'u':
                                sb.append((char) Integer.parseInt(s.substring(i, i + 4), 16));
                                i += 4;
                                break;
                            default: sb.append(e);
                        }
                    } else {
                        sb.append(c);
                    }
                }
            }

            Object number() {
                int start = i;
                while (i < s.length() && "+-.eE0123456789".indexOf(s.charAt(i)) >= 0) i++;
                String n = s.substring(start, i);
                if (n.isEmpty()) throw new IllegalStateException("bad number");
                if (n.contains(".") || n.contains("e") || n.contains("E")) {
                    return Double.parseDouble(n);
                }
                return Long.parseLong(n);
            }
        }
    }
}
