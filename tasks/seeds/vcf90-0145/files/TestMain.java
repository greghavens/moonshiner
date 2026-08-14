import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Protected acceptance harness. Its loopback server exposes only the three
 * operations pinned by docs/contract.json and retains a readable request log.
 */
public final class TestMain {

    private static final String SHA = "85151f6b1bb58f13b6ac0304bfec53904bea085f";
    private static final String ENTITY_ID = "18230:902:993642895";
    private static final String TOKEN = "dummy-network-insight-token";
    private static final String NICKNAME = "West \"Primary\"\nVC";
    private static final String NOTES = "managed\\by\tVCF";
    private static final String USERNAME = "svc-vcf-networks";
    private static final String PASSWORD = "p@ss\"word\\value";

    private static final List<Route> CONTRACT_ROUTES = List.of(
            new Route("updateVcenter", "PUT", "/api/ni/data-sources/vcenters/" + ENTITY_ID),
            new Route("acceptCertificate", "PUT", "/api/ni/data-sources/accept-certificate/" + ENTITY_ID),
            new Route("enableVcenter", "POST", "/api/ni/data-sources/vcenters/" + ENTITY_ID + "/enable"));

    private static int passed;
    private static int failed;

    record Route(String operationId, String method, String path) {
    }

    record LoggedRequest(
            String method,
            String rawPath,
            String rawQuery,
            String authorization,
            String accept,
            String contentType,
            String body) {
    }

    static final class ContractMock implements AutoCloseable {
        final List<LoggedRequest> requestLog = Collections.synchronizedList(new ArrayList<>());
        final HttpServer server;
        final int updateStatus;
        final int certificateStatus;
        final int enableStatus;

        ContractMock() throws IOException {
            this(200, 204, 500);
        }

        ContractMock(int updateStatus, int certificateStatus, int enableStatus) throws IOException {
            this.updateStatus = updateStatus;
            this.certificateStatus = certificateStatus;
            this.enableStatus = enableStatus;
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", this::handle);
            server.start();
        }

        URI apiBaseUri() {
            return URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/api/ni");
        }

        List<LoggedRequest> snapshot() {
            synchronized (requestLog) {
                return List.copyOf(requestLog);
            }
        }

        private void handle(HttpExchange exchange) throws IOException {
            String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            LoggedRequest request = new LoggedRequest(
                    exchange.getRequestMethod(),
                    exchange.getRequestURI().getRawPath(),
                    exchange.getRequestURI().getRawQuery(),
                    exchange.getRequestHeaders().getFirst("Authorization"),
                    exchange.getRequestHeaders().getFirst("Accept"),
                    exchange.getRequestHeaders().getFirst("Content-Type"),
                    body);
            requestLog.add(request);

            Route route = CONTRACT_ROUTES.stream()
                    .filter(r -> r.method().equals(request.method()) && r.path().equals(request.rawPath()))
                    .findFirst()
                    .orElse(null);
            if (route == null) {
                respond(exchange, 404, null);
                return;
            }
            switch (route.operationId()) {
                case "updateVcenter" -> respond(exchange, updateStatus,
                        updateStatus == 200
                                ? "{\"entity_id\":\"" + ENTITY_ID + "\",\"nickname\":\"updated\"}"
                                : null);
                case "acceptCertificate" -> respond(exchange, certificateStatus, null);
                case "enableVcenter" -> respond(exchange, enableStatus, null);
                default -> throw new AssertionError("mock route is not pinned to the contract");
            }
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }

    private static void respond(HttpExchange exchange, int status, String body) throws IOException {
        if (body == null) {
            exchange.sendResponseHeaders(status, -1);
            exchange.close();
            return;
        }
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream output = exchange.getResponseBody()) {
            output.write(bytes);
        }
    }

    private static void contractAndMockArePinned() throws Exception {
        String contract = Files.readString(Path.of("docs/contract.json"), StandardCharsets.UTF_8);
        String sources = Files.readString(Path.of("docs/official_sources.json"), StandardCharsets.UTF_8);
        check(sources.contains("\"commit_sha\": \"" + SHA + "\""), "spec commit is pinned");
        check(sources.contains("specifications/vcf-operations/vcf-operations-for-networks-openapi.yaml"),
                "spec path is pinned");
        check(count(contract, "\"operationId\"") == 3, "contract contains exactly three operations");
        check(count(sources, "\"operationId\"") == 3, "source provenance names every operation once");
        for (Route route : CONTRACT_ROUTES) {
            check(contract.contains("\"operationId\": \"" + route.operationId() + "\""),
                    "contract names " + route.operationId());
            check(sources.contains("\"operationId\": \"" + route.operationId() + "\""),
                    "provenance names " + route.operationId());
            check(contract.contains("\"operationId\": \"" + route.operationId()
                            + "\",\n      \"method\": \"" + route.method() + "\""),
                    "mock method pinned for " + route.operationId());
            check(contract.contains("\"wire_path\": \"" + templatePath(route.operationId()) + "\""),
                    "wire path pinned for " + route.operationId());
        }

        try (ContractMock mock = new ContractMock()) {
            URI unnamed = URI.create("http://127.0.0.1:" + mock.server.getAddress().getPort()
                    + "/api/ni/info/version");
            HttpResponse<String> response = HttpClient.newHttpClient().send(
                    HttpRequest.newBuilder(unnamed).GET().build(), HttpResponse.BodyHandlers.ofString());
            check(response.statusCode() == 404, "mock rejects operations absent from the contract");
        }
    }

    private static String templatePath(String operationId) {
        return switch (operationId) {
            case "updateVcenter" -> "/api/ni/data-sources/vcenters/{id}";
            case "acceptCertificate" -> "/api/ni/data-sources/accept-certificate/{id}";
            case "enableVcenter" -> "/api/ni/data-sources/vcenters/{id}/enable";
            default -> throw new AssertionError(operationId);
        };
    }

    private static void partialFailurePreservesResultsAndWireShape() throws Exception {
        try (ContractMock mock = new ContractMock()) {
            OperationsForNetworksClient client = new OperationsForNetworksClient(
                    mock.apiBaseUri(), TOKEN, HttpClient.newHttpClient());
            OperationsForNetworksClient.VCenterChange change =
                    new OperationsForNetworksClient.VCenterChange(NICKNAME, null, USERNAME, null);

            OperationsForNetworksClient.ChangeReport report =
                    client.applyVCenterChange(ENTITY_ID, change);

            check(ENTITY_ID.equals(report.dataSourceId()), "report keeps the data source id");
            check(!report.completed(), "later failure marks report incomplete");
            check(report.steps().size() == 3, "all three attempted steps are reported");
            assertStep(report.steps().get(0), "updateVcenter", 200, true);
            assertStep(report.steps().get(1), "acceptCertificate", 204, true);
            assertStep(report.steps().get(2), "enableVcenter", 500, false);

            List<LoggedRequest> log = mock.snapshot();
            check(log.size() == 3, "request log contains exactly the named operations");
            assertCommon(log.get(0), CONTRACT_ROUTES.get(0));
            assertCommon(log.get(1), CONTRACT_ROUTES.get(1));
            assertCommon(log.get(2), CONTRACT_ROUTES.get(2));

            LoggedRequest update = log.get(0);
            check("application/json".equals(update.contentType()), "update content type");
            Map<String, Object> updateBody = JsonProbe.object(JsonProbe.parse(update.body()));
            check(updateBody.keySet().equals(java.util.Set.of("nickname", "credentials")),
                    "update body has exactly the set top-level fields: " + updateBody.keySet());
            check(NICKNAME.equals(updateBody.get("nickname")), "nickname JSON value and escaping");
            Map<String, Object> credentials = JsonProbe.object(updateBody.get("credentials"));
            check(credentials.keySet().equals(java.util.Set.of("username")),
                    "credentials omit unset password: " + credentials.keySet());
            check(USERNAME.equals(credentials.get("username")), "credentials username");
            check(!update.body().contains("\"notes\"") && !update.body().contains("\"password\""),
                    "unset optional fields are absent from raw JSON");

            check(log.get(1).body().isEmpty() && log.get(1).contentType() == null,
                    "acceptCertificate has no body or content type");
            check(log.get(2).body().isEmpty() && log.get(2).contentType() == null,
                    "enableVcenter has no body or content type");
        }
    }

    private static void successfulChangeIncludesEverySetField() throws Exception {
        try (ContractMock mock = new ContractMock(200, 204, 200)) {
            OperationsForNetworksClient client = new OperationsForNetworksClient(
                    mock.apiBaseUri(), TOKEN, HttpClient.newHttpClient());
            OperationsForNetworksClient.VCenterChange change =
                    new OperationsForNetworksClient.VCenterChange(
                            NICKNAME, NOTES, USERNAME, PASSWORD);

            OperationsForNetworksClient.ChangeReport report =
                    client.applyVCenterChange(ENTITY_ID, change);

            check(report.completed(), "three successful operations complete the report");
            check(report.steps().size() == 3, "successful report contains all three steps");
            assertStep(report.steps().get(0), "updateVcenter", 200, true);
            assertStep(report.steps().get(1), "acceptCertificate", 204, true);
            assertStep(report.steps().get(2), "enableVcenter", 200, true);

            List<LoggedRequest> log = mock.snapshot();
            check(log.size() == 3, "successful change sends exactly three requests");
            for (int i = 0; i < log.size(); i++) {
                assertCommon(log.get(i), CONTRACT_ROUTES.get(i));
            }
            Map<String, Object> updateBody = JsonProbe.object(JsonProbe.parse(log.get(0).body()));
            check(updateBody.keySet().equals(java.util.Set.of("nickname", "notes", "credentials")),
                    "all set top-level update fields are present: " + updateBody.keySet());
            check(NICKNAME.equals(updateBody.get("nickname")), "set nickname round trips through JSON");
            check(NOTES.equals(updateBody.get("notes")), "set notes round trips through JSON");
            Map<String, Object> credentials = JsonProbe.object(updateBody.get("credentials"));
            check(credentials.keySet().equals(java.util.Set.of("username", "password")),
                    "all set credential fields are present: " + credentials.keySet());
            check(USERNAME.equals(credentials.get("username")), "set username round trips through JSON");
            check(PASSWORD.equals(credentials.get("password")), "set password round trips through JSON");
            check(log.get(1).body().isEmpty() && log.get(1).contentType() == null,
                    "successful acceptCertificate has no body or content type");
            check(log.get(2).body().isEmpty() && log.get(2).contentType() == null,
                    "successful enableVcenter has no body or content type");
        }
    }

    private static void updateFailureStopsAndReturnsReport() throws Exception {
        try (ContractMock mock = new ContractMock(400, 204, 200)) {
            OperationsForNetworksClient client = new OperationsForNetworksClient(
                    mock.apiBaseUri(), TOKEN, HttpClient.newHttpClient());
            OperationsForNetworksClient.VCenterChange change =
                    new OperationsForNetworksClient.VCenterChange(null, null, null, null);

            OperationsForNetworksClient.ChangeReport report =
                    client.applyVCenterChange(ENTITY_ID, change);

            check(!report.completed(), "update failure leaves report incomplete");
            check(report.steps().size() == 1, "update failure reports only the attempted operation");
            assertStep(report.steps().get(0), "updateVcenter", 400, false);

            List<LoggedRequest> log = mock.snapshot();
            check(log.size() == 1, "update failure prevents dependent requests");
            assertCommon(log.get(0), CONTRACT_ROUTES.get(0));
            check("application/json".equals(log.get(0).contentType()),
                    "empty update still has JSON content type");
            Map<String, Object> updateBody = JsonProbe.object(JsonProbe.parse(log.get(0).body()));
            check(updateBody.isEmpty(), "unset update fields produce an empty JSON object");
        }
    }

    private static void certificateFailurePreservesUpdateAndStops() throws Exception {
        try (ContractMock mock = new ContractMock(200, 500, 200)) {
            OperationsForNetworksClient client = new OperationsForNetworksClient(
                    mock.apiBaseUri(), TOKEN, HttpClient.newHttpClient());
            OperationsForNetworksClient.VCenterChange change =
                    new OperationsForNetworksClient.VCenterChange(NICKNAME, null, null, null);

            OperationsForNetworksClient.ChangeReport report =
                    client.applyVCenterChange(ENTITY_ID, change);

            check(!report.completed(), "certificate failure leaves report incomplete");
            check(report.steps().size() == 2, "certificate failure reports two attempted operations");
            assertStep(report.steps().get(0), "updateVcenter", 200, true);
            assertStep(report.steps().get(1), "acceptCertificate", 500, false);

            List<LoggedRequest> log = mock.snapshot();
            check(log.size() == 2, "certificate failure prevents enable request");
            assertCommon(log.get(0), CONTRACT_ROUTES.get(0));
            assertCommon(log.get(1), CONTRACT_ROUTES.get(1));
            check(log.get(1).body().isEmpty() && log.get(1).contentType() == null,
                    "failed acceptCertificate has no body or content type");
        }
    }

    private static void assertStep(
            OperationsForNetworksClient.StepResult result,
            String operationId,
            int status,
            boolean succeeded) {
        check(operationId.equals(result.operationId()), "reported operation " + operationId);
        check(result.statusCode() == status, operationId + " actual status " + status);
        check(result.succeeded() == succeeded, operationId + " success flag");
    }

    private static void assertCommon(LoggedRequest request, Route route) {
        check(route.method().equals(request.method()), route.operationId() + " method");
        check(route.path().equals(request.rawPath()), route.operationId() + " path");
        check(request.rawQuery() == null, route.operationId() + " has no query string");
        check(("NetworkInsight " + TOKEN).equals(request.authorization()),
                route.operationId() + " authorization wire value");
        check("application/json".equals(request.accept()), route.operationId() + " accept header");
    }

    private static int count(String source, String needle) {
        int count = 0;
        for (int at = 0; (at = source.indexOf(needle, at)) >= 0; at += needle.length()) {
            count++;
        }
        return count;
    }

    private static void check(boolean condition, String label) {
        if (!condition) {
            throw new AssertionError(label);
        }
    }

    private static void run(String name, Checked test) {
        try {
            test.run();
            passed++;
            System.out.println("PASS " + name);
        } catch (Throwable failure) {
            failed++;
            System.out.println("FAIL " + name + ": " + failure);
        }
    }

    public static void main(String[] args) {
        run("contract_and_mock_are_pinned", TestMain::contractAndMockArePinned);
        run("partial_failure_preserves_results_and_wire_shape",
                TestMain::partialFailurePreservesResultsAndWireShape);
        run("successful_change_includes_every_set_field",
                TestMain::successfulChangeIncludesEverySetField);
        run("update_failure_stops_and_returns_report",
                TestMain::updateFailureStopsAndReturnsReport);
        run("certificate_failure_preserves_update_and_stops",
                TestMain::certificateFailurePreservesUpdateAndStops);
        System.out.println("checks: " + passed + " passed, " + failed + " failed");
        if (failed != 0) {
            System.exit(1);
        }
    }

    @FunctionalInterface
    interface Checked {
        void run() throws Exception;
    }

    /** Independent JSON reader used only by the protected wire verifier. */
    static final class JsonProbe {
        private final String source;
        private int position;

        private JsonProbe(String source) {
            this.source = source;
        }

        static Object parse(String source) {
            JsonProbe reader = new JsonProbe(source);
            reader.whitespace();
            Object value = reader.value();
            reader.whitespace();
            if (reader.position != source.length()) {
                throw new AssertionError("trailing JSON at " + reader.position);
            }
            return value;
        }

        @SuppressWarnings("unchecked")
        static Map<String, Object> object(Object value) {
            if (!(value instanceof Map<?, ?>)) {
                throw new AssertionError("expected JSON object, got " + value);
            }
            return (Map<String, Object>) value;
        }

        private Object value() {
            whitespace();
            return switch (peek()) {
                case '{' -> objectValue();
                case '"' -> string();
                case 'n' -> nullValue();
                case 't' -> literal("true", Boolean.TRUE);
                case 'f' -> literal("false", Boolean.FALSE);
                default -> number();
            };
        }

        private Map<String, Object> objectValue() {
            expect('{');
            whitespace();
            Map<String, Object> result = new LinkedHashMap<>();
            if (peek() == '}') {
                position++;
                return result;
            }
            while (true) {
                whitespace();
                String key = string();
                whitespace();
                expect(':');
                if (result.containsKey(key)) {
                    throw new AssertionError("duplicate JSON key " + key);
                }
                result.put(key, value());
                whitespace();
                char separator = next();
                if (separator == '}') {
                    return result;
                }
                if (separator != ',') {
                    throw new AssertionError("bad JSON object separator");
                }
            }
        }

        private String string() {
            expect('"');
            StringBuilder result = new StringBuilder();
            while (true) {
                char c = next();
                if (c == '"') {
                    return result.toString();
                }
                if (c != '\\') {
                    if (c < 0x20) {
                        throw new AssertionError("raw control character in JSON string");
                    }
                    result.append(c);
                    continue;
                }
                char escaped = next();
                switch (escaped) {
                    case '"', '\\', '/' -> result.append(escaped);
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> {
                        if (position + 4 > source.length()) {
                            throw new AssertionError("short unicode escape");
                        }
                        result.append((char) Integer.parseInt(source.substring(position, position + 4), 16));
                        position += 4;
                    }
                    default -> throw new AssertionError("bad JSON escape \\" + escaped);
                }
            }
        }

        private Object nullValue() {
            return literal("null", null);
        }

        private Object literal(String text, Object value) {
            if (!source.startsWith(text, position)) {
                throw new AssertionError("bad JSON literal");
            }
            position += text.length();
            return value;
        }

        private Double number() {
            int start = position;
            while (position < source.length()
                    && "-+0123456789.eE".indexOf(source.charAt(position)) >= 0) {
                position++;
            }
            if (start == position) {
                throw new AssertionError("expected JSON value at " + position);
            }
            return Double.valueOf(source.substring(start, position));
        }

        private void whitespace() {
            while (position < source.length() && Character.isWhitespace(source.charAt(position))) {
                position++;
            }
        }

        private char peek() {
            if (position >= source.length()) {
                throw new AssertionError("unexpected end of JSON");
            }
            return source.charAt(position);
        }

        private char next() {
            char value = peek();
            position++;
            return value;
        }

        private void expect(char expected) {
            if (next() != expected) {
                throw new AssertionError("expected " + expected + " at " + (position - 1));
            }
        }
    }
}
