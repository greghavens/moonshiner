import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

public final class TestMain {
    private static final String EXPECTED_CONTRACT_SHA256 =
            "a7a89654f38783a61e991d6de8c64c74d520ac96f3d7e21012cb340c8dcbcf8b";
    private static final String OPERATION_PATH = "/api/ni/entities/vms";
    private static final String TOKEN = "seed-token";

    public static void main(String[] args) throws Exception {
        assertPinnedContract();
        try (MockServer mock = new MockServer()) {
            VcfOperationsNetworksClient client =
                    new VcfOperationsNetworksClient(mock.baseUri(), TOKEN);

            List<VcfOperationsNetworksClient.VmSnapshot> actual =
                    client.listAllVms(2, 1_700_000_000L, 1_700_000_100L);
            List<VcfOperationsNetworksClient.VmSnapshot> expected = List.of(
                    vm("vm-alpha", "AzureVM", 10),
                    vm("vm-alpha", "VirtualMachine", 10),
                    vm("vm-alpha", "VirtualMachine", 20),
                    vm("vm-beta", "VirtualMachine", 30),
                    vm("vm-zeta", "VirtualMachine", 40));
            check(expected.equals(actual), "complete, stable VM order", expected, actual);

            List<VcfOperationsNetworksClient.VmSnapshot> noOptions =
                    client.listAllVms(null, null, null);
            check(List.of(vm("vm-plain", "VirtualMachine", 50)).equals(noOptions),
                    "all-unset result", List.of(vm("vm-plain", "VirtualMachine", 50)), noOptions);

            boolean rejected = false;
            try {
                client.listAllVms(13, null, null);
            } catch (IOException expectedFailure) {
                rejected = true;
            }
            check(rejected, "non-2xx response", "IOException", "no exception");

            assertWireLog(mock.requestLog());
        }
        System.out.println("PASS: listVms pagination, stable order, and exact wire contract");
    }

    private static VcfOperationsNetworksClient.VmSnapshot vm(
            String id, String type, long time) {
        return new VcfOperationsNetworksClient.VmSnapshot(id, type, time);
    }

    private static void assertPinnedContract() throws Exception {
        Path contract = Path.of("docs", "contract.json");
        byte[] bytes = Files.readAllBytes(contract);
        String digest = hex(MessageDigest.getInstance("SHA-256").digest(bytes));
        check(EXPECTED_CONTRACT_SHA256.equals(digest),
                "contract fixture SHA-256", EXPECTED_CONTRACT_SHA256, digest);
        String text = new String(bytes, StandardCharsets.UTF_8);
        check(text.contains("\"operationId\": \"listVms\""),
                "contract operationId", "listVms", "missing");
        check(text.contains("\"url\": \"/api/ni\""),
                "contract server path", "/api/ni", "missing");
    }

    private static void assertWireLog(List<RequestRecord> log) {
        List<String> expectedTargets = List.of(
                OPERATION_PATH + "?size=2&start_time=1700000000&end_time=1700000100",
                OPERATION_PATH + "?size=2&cursor=p%C3%A5%20ge%2B%2F%3D&start_time=1700000000&end_time=1700000100",
                OPERATION_PATH + "?size=2&cursor=ZmluYWwrLw%3D%3D&start_time=1700000000&end_time=1700000100",
                OPERATION_PATH,
                OPERATION_PATH + "?size=13");
        check(log.size() == expectedTargets.size(),
                "request count", expectedTargets.size(), log.size());

        for (int i = 0; i < log.size(); i++) {
            RequestRecord request = log.get(i);
            check("GET".equals(request.method()),
                    "request " + i + " method", "GET", request.method());
            check(expectedTargets.get(i).equals(request.target()),
                    "request " + i + " target", expectedTargets.get(i), request.target());
            check(("NetworkInsight " + TOKEN).equals(request.authorization()),
                    "request " + i + " Authorization",
                    "NetworkInsight " + TOKEN, request.authorization());
            check("application/json".equals(request.accept()),
                    "request " + i + " Accept", "application/json", request.accept());
            check(request.body().isEmpty(),
                    "request " + i + " body", "<empty>", request.body());
            check(request.contentType() == null,
                    "request " + i + " Content-Type", null, request.contentType());
        }

        check(log.get(0).target().indexOf("cursor") < 0,
                "first request omits cursor", "no cursor", log.get(0).target());
        check(log.get(3).target().indexOf('?') < 0,
                "all unset optionals are omitted", OPERATION_PATH, log.get(3).target());
    }

    private static String hex(byte[] bytes) {
        StringBuilder result = new StringBuilder(bytes.length * 2);
        for (byte value : bytes) {
            result.append(String.format(Locale.ROOT, "%02x", value & 0xff));
        }
        return result.toString();
    }

    private static void check(boolean condition, String label, Object expected, Object actual) {
        if (!condition) {
            throw new AssertionError(label + ": expected " + expected + ", got " + actual);
        }
    }

    private record RequestRecord(
            String method,
            String target,
            String authorization,
            String accept,
            String contentType,
            String body) {}

    private static final class MockServer implements AutoCloseable {
        private final HttpServer server;
        private final List<RequestRecord> requestLog = new ArrayList<>();

        private MockServer() throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", this::handle);
            server.start();
        }

        private URI baseUri() {
            return URI.create("http://127.0.0.1:" + server.getAddress().getPort());
        }

        private List<RequestRecord> requestLog() {
            return List.copyOf(requestLog);
        }

        private void handle(HttpExchange exchange) throws IOException {
            byte[] requestBody = exchange.getRequestBody().readAllBytes();
            URI uri = exchange.getRequestURI();
            String target = uri.getRawPath()
                    + (uri.getRawQuery() == null ? "" : "?" + uri.getRawQuery());
            Headers headers = exchange.getRequestHeaders();
            requestLog.add(new RequestRecord(
                    exchange.getRequestMethod(),
                    target,
                    headers.getFirst("Authorization"),
                    headers.getFirst("Accept"),
                    headers.getFirst("Content-Type"),
                    new String(requestBody, StandardCharsets.UTF_8)));

            if (!"GET".equals(exchange.getRequestMethod())
                    || !OPERATION_PATH.equals(uri.getPath())) {
                send(exchange, 404, "{\"error\":\"operation not in pinned contract\"}");
                return;
            }
            if ((OPERATION_PATH + "?size=13").equals(target)) {
                send(exchange, 503, "{\"error\":\"fixture service unavailable\"}");
                return;
            }

            String response = switch (target) {
                case "/api/ni/entities/vms?size=2&start_time=1700000000&end_time=1700000100" ->
                        page("[{\"entity_id\":\"vm-zeta\",\"entity_type\":\"VirtualMachine\",\"time\":40},"
                                + "{\"entity_id\":\"vm-alpha\",\"entity_type\":\"VirtualMachine\",\"time\":20}]",
                                "på ge+/=", 5);
                case "/api/ni/entities/vms?size=2&cursor=p%C3%A5%20ge%2B%2F%3D&start_time=1700000000&end_time=1700000100" ->
                        page("[{\"entity_id\":\"vm-beta\",\"entity_type\":\"VirtualMachine\",\"time\":30},"
                                + "{\"entity_id\":\"vm-alpha\",\"entity_type\":\"VirtualMachine\",\"time\":10}]",
                                "ZmluYWwrLw==", 5);
                case "/api/ni/entities/vms?size=2&cursor=ZmluYWwrLw%3D%3D&start_time=1700000000&end_time=1700000100" ->
                        page("[{\"entity_id\":\"vm-alpha\",\"entity_type\":\"AzureVM\",\"time\":10}]",
                                "   ", 5);
                case "/api/ni/entities/vms" ->
                        page("[{\"entity_id\":\"vm-plain\",\"entity_type\":\"VirtualMachine\",\"time\":50}]",
                                null, 1);
                default -> null;
            };

            if (response == null) {
                send(exchange, 400, "{\"error\":\"wire shape does not match pinned contract fixture\"}");
            } else {
                send(exchange, 200, response);
            }
        }

        private static String page(String results, String cursor, int totalCount) {
            return "{\"results\":" + results
                    + (cursor == null ? "" : ",\"cursor\":\"" + cursor + "\"")
                    + ",\"total_count\":" + totalCount
                    + ",\"start_time\":1700000000,\"end_time\":1700000100}";
        }

        private static void send(HttpExchange exchange, int status, String body) throws IOException {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        }

        @Override
        public void close() {
            server.stop(0);
        }
    }
}
