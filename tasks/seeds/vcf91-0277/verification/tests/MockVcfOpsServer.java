import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Loopback-only VCF Operations contract fixture.
 *
 * <p>The route table is built from {@code docs/contract.json}: the mock answers the
 * operations that contract names and nothing else. Every request that reaches the
 * server is recorded first, including the ones it then rejects, so the harness can
 * assert the exact wire shape. No VMware endpoint is contacted.
 */
public final class MockVcfOpsServer implements AutoCloseable {

    public record RecordedRequest(String method, String rawPath, String rawQuery,
                                  Map<String, List<String>> headers, String body) {
        public String header(String name) {
            List<String> values = headers.get(name.toLowerCase(Locale.ROOT));
            return values == null || values.isEmpty() ? null : String.join(",", values);
        }

        public boolean hasHeader(String name) {
            return headers.containsKey(name.toLowerCase(Locale.ROOT));
        }
    }

    /** One inventory row of the fixture estate. */
    private record Row(String identifier, String name, String adapterKindKey,
                       String resourceKindKey, String resourceHealth) {}

    /**
     * Fixture estate, deliberately declared in an order that is neither the expected
     * output order nor sorted, so page arrival order cannot stand in for the contract's
     * stable ordering requirement.
     */
    private static final List<Row> ESTATE = List.of(
            new Row("e7d6c5b4-a392-4817-9f0e-1d2c3b4a5968", "shared-name",
                    "VMWARE", "VirtualMachine", "RED"),
            new Row("4c3b2a19-8877-4665-9443-2211ffee0099", "tier0-edge-gw",
                    "NSXT", "Tier0Gateway", "GREEN"),
            new Row("5f9a1c34-7d21-4e08-9b6f-0a1c2d3e4f50", "web tier/01",
                    "VMWARE", "VirtualMachine", "GREEN"),
            new Row("6d5c4b3a-2918-4706-b5e4-d3c2b1a09988", "esx-02.vcf.local",
                    "VMWARE", "HostSystem", "YELLOW"),
            new Row("9c8b7a65-4321-4fed-8cba-0987654321fe", "cluster-café",
                    "VMWARE", "ClusterComputeResource", "GREEN"),
            new Row("1b2c3d4e-5f60-4718-8293-a4b5c6d7e8f9", "db-node-\"primary\"",
                    "VMWARE", "VirtualMachine", "YELLOW"),
            new Row("0a1b2c3d-4e5f-4607-8819-2a3b4c5d6e7f", "overlay-tz",
                    "NSXT", "TransportZone", "GREEN"),
            new Row("2a3b4c5d-6e7f-4801-9213-45566778899a", "shared-name",
                    "VMWARE", "VirtualMachine", "GREY"),
            new Row("8f7e6d5c-4b3a-4291-8807-6f5e4d3c2b1a", "ds-gold",
                    "VMWARE", "Datastore", "GREEN"),
            new Row("3e4d5c6b-7a89-4012-b345-6789abcdef01", "esx-01.vcf.local",
                    "VMWARE", "HostSystem", "GREEN"));

    private final Object logLock = new Object();
    private final List<RecordedRequest> requests = new ArrayList<>();
    private final ExecutorService executor;
    private final HttpServer server;

    private final String basePath;
    private final Set<String> routes;
    private final String tokenPrefix;
    private final String expectedUsername;
    private final String expectedPassword;
    private final int faultOnPage;

    private volatile String issuedToken;

    public MockVcfOpsServer(Path contractPath, String username, String password) throws IOException {
        this(contractPath, username, password, -1);
    }

    public MockVcfOpsServer(Path contractPath, String username, String password, int faultOnPage)
            throws IOException {
        Map<String, Object> contract = pinnedContract(contractPath);
        this.basePath = (String) contract.get("basePath");
        this.tokenPrefix = securityPrefix(contract);
        this.routes = contractRoutes(contract, this.basePath);
        this.expectedUsername = username;
        this.expectedPassword = password;
        this.faultOnPage = faultOnPage;

        server = HttpServer.create(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
        executor = Executors.newCachedThreadPool(runnable -> {
            Thread thread = new Thread(runnable, "vcf-ops-contract-mock");
            thread.setDaemon(true);
            return thread;
        });
        server.setExecutor(executor);
        server.createContext("/", this::handle);
        server.start();
    }

    public URI baseUri() {
        return URI.create("http://127.0.0.1:" + server.getAddress().getPort());
    }

    /** Runtime-only token minted by acquireToken; never present in any source file. */
    public String issuedToken() {
        return issuedToken;
    }

    public List<RecordedRequest> requestLog() {
        synchronized (logLock) {
            return List.copyOf(requests);
        }
    }

    @Override
    public void close() {
        server.stop(0);
        executor.shutdownNow();
    }

    // ---------------------------------------------------------------- routing

    private void handle(HttpExchange exchange) throws IOException {
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        String rawPath = exchange.getRequestURI().getRawPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        String method = exchange.getRequestMethod();
        RecordedRequest recorded = new RecordedRequest(
                method, rawPath, rawQuery, copyHeaders(exchange), body);
        synchronized (logLock) {
            requests.add(recorded);
            logLock.notifyAll();
        }

        if (!routes.contains(method + " " + rawPath)) {
            respond(exchange, 404, "{\"message\":\"operation not named by docs/contract.json\"}");
            return;
        }
        if (rawPath.equals(basePath + "/api/auth/token/acquire")) {
            acquireToken(exchange, body);
            return;
        }
        getResources(exchange, recorded, rawQuery);
    }

    private void acquireToken(HttpExchange exchange, String body) throws IOException {
        Object parsed;
        try {
            parsed = Json.parse(body);
        } catch (RuntimeException malformed) {
            respond(exchange, 400, "{\"message\":\"malformed username-password body\"}");
            return;
        }
        if (!(parsed instanceof Map<?, ?> raw)) {
            respond(exchange, 400, "{\"message\":\"username-password body must be an object\"}");
            return;
        }
        for (Object key : raw.keySet()) {
            if (!Set.of("username", "password", "authSource").contains(key)) {
                respond(exchange, 400, "{\"message\":\"unknown property: " + key + "\"}");
                return;
            }
        }
        if (raw.containsKey("authSource")) {
            Object authSource = raw.get("authSource");
            if (!(authSource instanceof String text) || text.isBlank()) {
                respond(exchange, 400,
                        "{\"message\":\"optional authSource must be omitted, never sent empty\"}");
                return;
            }
        }
        if (!expectedUsername.equals(raw.get("username"))
                || !expectedPassword.equals(raw.get("password"))) {
            respond(exchange, 401, "{\"message\":\"authentication failed\"}");
            return;
        }
        issuedToken = "ops-" + UUID.randomUUID();
        respond(exchange, 200, "{\"token\":\"" + issuedToken + "\",\"validity\":1893456000000,"
                + "\"expiresAt\":\"2030-01-01T00:00:00.000Z\",\"roles\":[\"ContentAdmin\"]}");
    }

    private void getResources(HttpExchange exchange, RecordedRequest recorded, String rawQuery)
            throws IOException {
        String authorization = recorded.header("authorization");
        if (issuedToken == null || !(tokenPrefix + issuedToken).equals(authorization)) {
            respond(exchange, 401, "{\"message\":\"missing or invalid Authorization credential\"}");
            return;
        }

        List<Map.Entry<String, String>> query = parseQuery(rawQuery);
        List<String> names = valuesOf(query, "name");
        List<String> adapterKinds = valuesOf(query, "adapterKind");
        List<String> resourceKinds = valuesOf(query, "resourceKind");
        int page = intOf(query, "page", 0);
        int pageSize = intOf(query, "pageSize", 1000);
        if (page < 0 || pageSize < 1) {
            respond(exchange, 400, "{\"message\":\"invalid paging parameters\"}");
            return;
        }
        if (page == faultOnPage) {
            respond(exchange, 503, "{\"message\":\"collector temporarily unavailable\"}");
            return;
        }

        List<Row> matched = new ArrayList<>();
        for (Row row : ESTATE) {
            if (!names.isEmpty() && !names.contains(row.name())) {
                continue;
            }
            if (!adapterKinds.isEmpty() && !adapterKinds.contains(row.adapterKindKey())) {
                continue;
            }
            if (!resourceKinds.isEmpty() && !resourceKinds.contains(row.resourceKindKey())) {
                continue;
            }
            matched.add(row);
        }

        int from = Math.min(page * pageSize, matched.size());
        int to = Math.min(from + pageSize, matched.size());
        StringBuilder json = new StringBuilder();
        json.append("{\"pageInfo\":{\"page\":").append(page)
                .append(",\"pageSize\":").append(pageSize)
                .append(",\"totalCount\":").append(matched.size())
                .append("},\"resourceList\":[");
        for (int index = from; index < to; index++) {
            if (index > from) {
                json.append(',');
            }
            json.append(resourceJson(matched.get(index)));
        }
        json.append("]}");
        respond(exchange, 200, json.toString());
    }

    private static String resourceJson(Row row) {
        return "{\"creationTime\":1747108356000"
                + ",\"description\":" + jsonString("collector note: \"identifier\":\"decoy-"
                        + row.resourceKindKey() + "\" is quoted prose, not a member")
                + ",\"identifier\":" + jsonString(row.identifier())
                + ",\"resourceKey\":{\"adapterKindKey\":" + jsonString(row.adapterKindKey())
                + ",\"name\":" + jsonString(row.name())
                + ",\"resourceIdentifiers\":[]"
                + ",\"resourceKindKey\":" + jsonString(row.resourceKindKey()) + "}"
                + ",\"resourceHealth\":" + jsonString(row.resourceHealth())
                + ",\"resourceHealthValue\":87.5"
                + ",\"resourceStatusStates\":[{\"adapterInstanceId\":"
                + jsonString(row.identifier())
                + ",\"resourceState\":\"STARTED\",\"resourceStatus\":\"DATA_RECEIVING\""
                + ",\"statusMessage\":\"\"}]"
                + ",\"dtEnabled\":true"
                + ",\"badges\":[]"
                + ",\"links\":[{\"href\":\"/suite-api/api/resources/" + row.identifier()
                + "\",\"rel\":\"SELF\",\"name\":\"linkToSelf\"}]}";
    }

    // ------------------------------------------------------- contract pinning

    private static Map<String, Object> pinnedContract(Path contractPath) throws IOException {
        Object parsed = Json.parse(Files.readString(contractPath, StandardCharsets.UTF_8));
        if (!(parsed instanceof Map<?, ?> map)) {
            throw new IOException("docs/contract.json is not a JSON object");
        }
        @SuppressWarnings("unchecked")
        Map<String, Object> contract = (Map<String, Object>) map;
        Object source = contract.get("source");
        if (!(source instanceof Map<?, ?> sourceMap)
                || !"c3f3b52c845dd967cabbc21680e893292077d5ba".equals(sourceMap.get("commit_sha"))
                || !"specifications/vcf-operations/vcf-operations-openapi.json"
                        .equals(sourceMap.get("spec_path"))) {
            throw new IOException("contract is not pinned to the selected specification revision");
        }
        if (!"/suite-api".equals(contract.get("basePath"))) {
            throw new IOException("contract basePath differs from the specification server url");
        }
        return contract;
    }

    private static String securityPrefix(Map<String, Object> contract) throws IOException {
        if (!(contract.get("security") instanceof Map<?, ?> security)
                || !"header".equals(security.get("in"))
                || !"Authorization".equals(security.get("name"))
                || !(security.get("value_template") instanceof String template)
                || !template.endsWith("{token}")) {
            throw new IOException("contract security scheme is not the pinned apiKey header");
        }
        return template.substring(0, template.length() - "{token}".length());
    }

    private static Set<String> contractRoutes(Map<String, Object> contract, String basePath)
            throws IOException {
        if (!(contract.get("operations") instanceof List<?> operations) || operations.size() != 2) {
            throw new IOException("contract must name exactly the two VCF Operations operations");
        }
        Set<String> routes = new LinkedHashSet<>();
        Set<String> operationIds = new LinkedHashSet<>();
        for (Object entry : operations) {
            if (!(entry instanceof Map<?, ?> operation)) {
                throw new IOException("contract operation entry is not an object");
            }
            Object id = operation.get("operationId");
            Object method = operation.get("method");
            Object path = operation.get("path");
            if (!(id instanceof String) || !(method instanceof String)
                    || !(path instanceof String)) {
                throw new IOException("contract operation entry is incomplete");
            }
            operationIds.add((String) id);
            routes.add(((String) method).toUpperCase(Locale.ROOT) + " " + basePath + path);
        }
        if (!operationIds.equals(Set.of("acquireToken", "getResources"))) {
            throw new IOException("contract operationIds are not the pinned pair: " + operationIds);
        }
        return Set.copyOf(routes);
    }

    // ------------------------------------------------------------- utilities

    private static List<Map.Entry<String, String>> parseQuery(String rawQuery) {
        List<Map.Entry<String, String>> pairs = new ArrayList<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return pairs;
        }
        for (String piece : rawQuery.split("&", -1)) {
            int split = piece.indexOf('=');
            String key = split < 0 ? piece : piece.substring(0, split);
            String value = split < 0 ? "" : piece.substring(split + 1);
            pairs.add(Map.entry(percentDecode(key), percentDecode(value)));
        }
        return pairs;
    }

    private static String percentDecode(String text) {
        java.io.ByteArrayOutputStream bytes = new java.io.ByteArrayOutputStream();
        for (int index = 0; index < text.length(); index++) {
            char current = text.charAt(index);
            if (current == '%' && index + 2 < text.length()) {
                bytes.write(Integer.parseInt(text.substring(index + 1, index + 3), 16));
                index += 2;
            } else {
                byte[] encoded = String.valueOf(current).getBytes(StandardCharsets.UTF_8);
                bytes.write(encoded, 0, encoded.length);
            }
        }
        return bytes.toString(StandardCharsets.UTF_8);
    }

    private static List<String> valuesOf(List<Map.Entry<String, String>> query, String key) {
        List<String> values = new ArrayList<>();
        for (Map.Entry<String, String> pair : query) {
            if (pair.getKey().equals(key)) {
                values.add(pair.getValue());
            }
        }
        return values;
    }

    private static int intOf(List<Map.Entry<String, String>> query, String key, int fallback) {
        List<String> values = valuesOf(query, key);
        if (values.isEmpty()) {
            return fallback;
        }
        try {
            return Integer.parseInt(values.get(values.size() - 1));
        } catch (NumberFormatException notANumber) {
            return -1;
        }
    }

    private static Map<String, List<String>> copyHeaders(HttpExchange exchange) {
        Map<String, List<String>> copy = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach((name, values) ->
                copy.put(name.toLowerCase(Locale.ROOT), List.copyOf(values)));
        return Map.copyOf(copy);
    }

    private static void respond(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    /** Emits a JSON string literal, escaping every non-ASCII character as \\uXXXX. */
    private static String jsonString(String value) {
        StringBuilder out = new StringBuilder("\"");
        for (int index = 0; index < value.length(); index++) {
            char current = value.charAt(index);
            switch (current) {
                case '"' -> out.append("\\\"");
                case '\\' -> out.append("\\\\");
                case '\n' -> out.append("\\n");
                case '\r' -> out.append("\\r");
                case '\t' -> out.append("\\t");
                default -> {
                    if (current < 0x20 || current > 0x7e) {
                        out.append(String.format("\\u%04x", (int) current));
                    } else {
                        out.append(current);
                    }
                }
            }
        }
        return out.append('"').toString();
    }

    /** Minimal JSON reader used only by this fixture. */
    static final class Json {
        private final String text;
        private int at;

        private Json(String text) {
            this.text = text;
        }

        static Object parse(String text) {
            Json reader = new Json(text);
            reader.skipWhitespace();
            Object value = reader.readValue();
            reader.skipWhitespace();
            if (reader.at != text.length()) {
                throw new IllegalArgumentException("trailing JSON content at " + reader.at);
            }
            return value;
        }

        private Object readValue() {
            skipWhitespace();
            char current = text.charAt(at);
            return switch (current) {
                case '{' -> readObject();
                case '[' -> readArray();
                case '"' -> readString();
                case 't' -> readLiteral("true", Boolean.TRUE);
                case 'f' -> readLiteral("false", Boolean.FALSE);
                case 'n' -> readLiteral("null", null);
                default -> readNumber();
            };
        }

        private Map<String, Object> readObject() {
            Map<String, Object> members = new LinkedHashMap<>();
            at++;
            skipWhitespace();
            if (text.charAt(at) == '}') {
                at++;
                return members;
            }
            while (true) {
                skipWhitespace();
                String key = readString();
                skipWhitespace();
                expect(':');
                members.put(key, readValue());
                skipWhitespace();
                char next = text.charAt(at++);
                if (next == '}') {
                    return members;
                }
                if (next != ',') {
                    throw new IllegalArgumentException("expected , or } at " + at);
                }
            }
        }

        private List<Object> readArray() {
            List<Object> items = new ArrayList<>();
            at++;
            skipWhitespace();
            if (text.charAt(at) == ']') {
                at++;
                return items;
            }
            while (true) {
                items.add(readValue());
                skipWhitespace();
                char next = text.charAt(at++);
                if (next == ']') {
                    return items;
                }
                if (next != ',') {
                    throw new IllegalArgumentException("expected , or ] at " + at);
                }
            }
        }

        private String readString() {
            expect('"');
            StringBuilder out = new StringBuilder();
            while (true) {
                char current = text.charAt(at++);
                if (current == '"') {
                    return out.toString();
                }
                if (current != '\\') {
                    out.append(current);
                    continue;
                }
                char escape = text.charAt(at++);
                switch (escape) {
                    case '"', '\\', '/' -> out.append(escape);
                    case 'b' -> out.append('\b');
                    case 'f' -> out.append('\f');
                    case 'n' -> out.append('\n');
                    case 'r' -> out.append('\r');
                    case 't' -> out.append('\t');
                    case 'u' -> {
                        out.append((char) Integer.parseInt(text.substring(at, at + 4), 16));
                        at += 4;
                    }
                    default -> throw new IllegalArgumentException("bad escape \\" + escape);
                }
            }
        }

        private Object readLiteral(String literal, Object value) {
            if (!text.startsWith(literal, at)) {
                throw new IllegalArgumentException("bad literal at " + at);
            }
            at += literal.length();
            return value;
        }

        private Double readNumber() {
            int start = at;
            while (at < text.length() && "+-.eE0123456789".indexOf(text.charAt(at)) >= 0) {
                at++;
            }
            return Double.valueOf(text.substring(start, at));
        }

        private void expect(char expected) {
            if (text.charAt(at++) != expected) {
                throw new IllegalArgumentException("expected " + expected + " at " + (at - 1));
            }
        }

        private void skipWhitespace() {
            while (at < text.length() && Character.isWhitespace(text.charAt(at))) {
                at++;
            }
        }
    }
}
