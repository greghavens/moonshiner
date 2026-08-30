import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Protected acceptance harness for the spec-derived VCF 9.1 domains client.
 * The fake is loopback-only and implements only operationId getDomains.
 */
public final class TestMain {
    static final String DUMMY_TOKEN = "dummy-loopback-token-71a8";
    static final String SOURCE_SHA = "3949fc33339fc5ea1b77eadb258f1cf49aa88e26";
    static int checks;

    static void check(boolean condition, String label) {
        checks++;
        if (!condition) {
            throw new AssertionError("FAIL: " + label);
        }
    }

    record RequestLog(String method, String rawPath, String authorization, String accept) {}

    static final class FakeSddcManager implements AutoCloseable {
        final List<RequestLog> requests = new CopyOnWriteArrayList<>();
        final AtomicInteger responseNumber = new AtomicInteger();
        final HttpServer server;

        FakeSddcManager() throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/v1/domains", this::domains);
            server.createContext("/", this::unknown);
            server.start();
        }

        String baseUrl() {
            return "http://127.0.0.1:" + server.getAddress().getPort();
        }

        List<RequestLog> requestLog() {
            return List.copyOf(requests);
        }

        private void remember(HttpExchange exchange) {
            requests.add(new RequestLog(
                    exchange.getRequestMethod(),
                    exchange.getRequestURI().toString(),
                    exchange.getRequestHeaders().getFirst("Authorization"),
                    exchange.getRequestHeaders().getFirst("Accept")));
        }

        private void domains(HttpExchange exchange) throws IOException {
            remember(exchange);
            if (!exchange.getRequestMethod().equals("GET")) {
                respond(exchange, 405, "{\"message\":\"method not allowed\"}");
                return;
            }
            if (!("Bearer " + DUMMY_TOKEN).equals(
                    exchange.getRequestHeaders().getFirst("Authorization"))) {
                respond(exchange, 401, "{\"message\":\"authorization required\"}");
                return;
            }
            if (!"application/json".equals(exchange.getRequestHeaders().getFirst("Accept"))) {
                respond(exchange, 406, "{\"message\":\"application/json required\"}");
                return;
            }

            String query = exchange.getRequestURI().getRawQuery();
            int page;
            if (query == null || query.equals("pageNumber=0&pageSize=25")) {
                page = query == null ? -1 : 0;
            } else if (query.equals("pageNumber=1&pageSize=25")) {
                page = 1;
            } else if (query.equals("pageNumber=2&pageSize=25")) {
                page = 2;
            } else {
                page = -1;
            }
            if (page < 0) {
                respond(exchange, 400, "{\"message\":\"unexpected pagination query\"}");
                return;
            }

            List<String> elements = new ArrayList<>(pageElements(page));
            // Deliberately alternate orientation on every response. Across two
            // scans every page is observed in both orders.
            if ((responseNumber.incrementAndGet() & 1) == 1) {
                Collections.reverse(elements);
            }
            String body = "{\"elements\":[" + String.join(",", elements)
                    + "],\"pageMetadata\":{\"pageNumber\":" + page
                    + ",\"pageSize\":2,\"totalElements\":6,\"totalPages\":3}}";
            respond(exchange, 200, body);
        }

        private List<String> pageElements(int page) {
            return switch (page) {
                case 0 -> List.of(
                        domain("d-60", "zulu-domain", "VI", "ACTIVE"),
                        domain("d-10", "alpha-domain", "MANAGEMENT", "ACTIVE"));
                case 1 -> List.of(
                        domain("d-40", "echo-domain", "VI", "UPGRADING"),
                        domain("d-30", "charlie-domain", "VI", "ACTIVE"));
                case 2 -> List.of(
                        domain("d-21", "bravo-domain", "VI", "ACTIVE"),
                        domain("d-20", "bravo-domain", "VI", "ACTIVATING"));
                default -> throw new AssertionError("unexpected page");
            };
        }

        private static String domain(String id, String name, String type, String status) {
            // Property order intentionally differs from the record constructor;
            // an ignored nested object ensures the client parses JSON structurally.
            return "{\"status\":\"" + status + "\",\"name\":\"" + name
                    + "\",\"ignored\":{\"nested\":[1,true,null]},\"id\":\"" + id
                    + "\",\"type\":\"" + type + "\"}";
        }

        private void unknown(HttpExchange exchange) throws IOException {
            remember(exchange);
            respond(exchange, 404, "{\"message\":\"operation not in protected contract\"}");
        }

        private static void respond(HttpExchange exchange, int status, String body)
                throws IOException {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, bytes.length);
            try (OutputStream output = exchange.getResponseBody()) {
                output.write(bytes);
            }
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }

    static void checkProtectedContract() throws IOException {
        String contract = Files.readString(Path.of("docs/contract.json"));
        String sources = Files.readString(Path.of("docs/official_sources.json"));
        check(contract.contains("\"kind\": \"projection_from_openapi_specification\""),
                "contract identifies specification derivation");
        check(contract.contains("\"operationId\": \"getDomains\""),
                "contract names exact operationId getDomains");
        check(contract.contains("\"path\": \"/v1/domains\"")
                        && contract.contains("\"schema_ref\": \"#/components/schemas/PageOfDomain\""),
                "contract pins operation path and response schema");
        check(contract.contains("\"pageNumber\"") && contract.contains("\"totalPages\"")
                        && contract.contains("\"totalElements\""),
                "contract pins pagination fields");
        check(sources.contains("\"spec_path\": "
                        + "\"specifications/sddc-manager/sddc-manager-openapi.json\""),
                "provenance pins exact specification path");
        check(sources.contains("\"commit_sha\": \"" + SOURCE_SHA + "\""),
                "provenance pins repository commit");
        check(sources.contains("\"license\": \"Apache-2.0\""),
                "provenance records repository license");
        check(count(sources, "\"operationId\":") == 1
                        && sources.contains("\"operationId\": \"getDomains\""),
                "provenance records each and only used operationId");
    }

    static int count(String text, String needle) {
        int result = 0;
        for (int at = 0; (at = text.indexOf(needle, at)) >= 0; at += needle.length()) {
            result++;
        }
        return result;
    }

    static final String EXPECTED = """
            id	name	type	status
            d-10	alpha-domain	MANAGEMENT	ACTIVE
            d-20	bravo-domain	VI	ACTIVATING
            d-21	bravo-domain	VI	ACTIVE
            d-30	charlie-domain	VI	ACTIVE
            d-40	echo-domain	VI	UPGRADING
            d-60	zulu-domain	VI	ACTIVE
            """;

    public static void main(String[] args) throws Exception {
        checkProtectedContract();

        try (FakeSddcManager fake = new FakeSddcManager()) {
            SddcManagerClient client =
                    new SddcManagerClient(fake.baseUrl(), DUMMY_TOKEN, 25);

            List<SddcManagerClient.Domain> first = client.listDomains();
            check(first.size() == 6, "all three short pages are retrieved");
            check(first.get(0).id().equals("d-10") && first.get(5).id().equals("d-60"),
                    "collection is sorted by name rather than response order");
            check(first.get(1).id().equals("d-20") && first.get(2).id().equals("d-21"),
                    "id is the deterministic tie-breaker for equal names");
            try {
                first.add(new SddcManagerClient.Domain("x", "x", "VI", "ACTIVE"));
                check(false, "returned collection must be unmodifiable");
            } catch (UnsupportedOperationException expected) {
                check(true, "returned collection is unmodifiable");
            }

            StringBuilder out1 = new StringBuilder();
            client.writeInventory(out1);
            check(out1.toString().equals(EXPECTED),
                    "inventory has exact stable header, rows, and order");

            StringBuilder out2 = new StringBuilder();
            client.writeInventory(out2);
            check(out2.toString().equals(EXPECTED) && out2.toString().equals(out1.toString()),
                    "flipped server order cannot change emitted inventory");

            List<RequestLog> log = fake.requestLog();
            check(log.size() == 9, "each of three complete scans requests exactly three pages");
            for (int scan = 0; scan < 3; scan++) {
                for (int page = 0; page < 3; page++) {
                    RequestLog request = log.get(scan * 3 + page);
                    check(request.method().equals("GET"), "contract method is GET");
                    check(request.rawPath().equals(
                                    "/v1/domains?pageNumber=" + page + "&pageSize=25"),
                            "page request uses exact contract path and query");
                    check(request.authorization().equals("Bearer " + DUMMY_TOKEN),
                            "page request carries bearer authorization");
                    check(request.accept().equals("application/json"),
                            "page request accepts JSON");
                }
            }

            try {
                new SddcManagerClient(fake.baseUrl(), DUMMY_TOKEN, 0);
                check(false, "zero page size must be rejected");
            } catch (IllegalArgumentException expected) {
                check(true, "zero page size rejected before HTTP");
            }
            try {
                new SddcManagerClient(fake.baseUrl(), " ", 25);
                check(false, "blank token must be rejected");
            } catch (IllegalArgumentException expected) {
                check(true, "blank token rejected before HTTP");
            }
            check(fake.requestLog().size() == 9, "constructor validation performs no HTTP");
        }

        // A separate fake confirms non-2xx decoding and token secrecy.
        try (FakeSddcManager fake = new FakeSddcManager()) {
            String secret = "wrong-secret-token-d4c2";
            SddcManagerClient bad = new SddcManagerClient(fake.baseUrl(), secret, 25);
            try {
                bad.listDomains();
                check(false, "401 must raise SddcManagerException");
            } catch (SddcManagerClient.SddcManagerException expected) {
                check(expected.statusCode() == 401, "exception exposes HTTP status");
                check(expected.getMessage() != null && !expected.getMessage().contains(secret),
                        "exception message does not expose bearer token");
            }
            check(fake.requestLog().size() == 1, "non-2xx stops pagination immediately");
        }

        System.out.println("OK: " + checks
                + " checks; complete spec-pinned pagination and stable output");
    }
}
