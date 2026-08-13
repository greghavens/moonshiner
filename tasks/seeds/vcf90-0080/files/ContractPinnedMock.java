import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** A loopback-only fixture for exactly the two operations named by docs/contract.json. */
public final class ContractPinnedMock implements AutoCloseable {
    public static final String CREATE_OPERATION_ID = "createMaintenanceSchedules";
    public static final String UPDATE_OPERATION_ID = "updateMaintenanceSchedules";
    public static final String WIRE_PATH = "/suite-api/api/maintenanceschedules";
    public static final String CREATED_ID = "11111111-1111-1111-1111-111111111111";

    private static final String DEFAULT_CREATE_RESPONSE = "{\"id\":\"" + CREATED_ID
            + "\",\"key\":\"nightly-maintenance\",\"schedule\":{"
            + "\"hour\":2,\"minuteOfTheHour\":15,\"duration\":60,"
            + "\"scheduleType\":\"ONCE\"}}";

    public record RequestEntry(
            String method,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            byte[] body) {
        public RequestEntry {
            body = body.clone();
        }

        public String bodyUtf8() {
            return new String(body, StandardCharsets.UTF_8);
        }

        public String firstHeader(String name) {
            for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
                if (entry.getKey().equalsIgnoreCase(name) && !entry.getValue().isEmpty()) {
                    return entry.getValue().get(0);
                }
            }
            return null;
        }
    }

    private final HttpServer server;
    private final ExecutorService executor;
    private final int createStatus;
    private final String createResponseBody;
    private final int updateStatus;
    private final String updateResponseBody;
    private final CopyOnWriteArrayList<RequestEntry> requestLog = new CopyOnWriteArrayList<>();

    public ContractPinnedMock(Path contractPath) throws IOException {
        this(contractPath, 201, DEFAULT_CREATE_RESPONSE, 404, "");
    }

    public ContractPinnedMock(
            Path contractPath,
            int createStatus,
            String createResponseBody,
            int updateStatus,
            String updateResponseBody) throws IOException {
        assertPinnedContract(contractPath);
        this.createStatus = createStatus;
        this.createResponseBody = createResponseBody;
        this.updateStatus = updateStatus;
        this.updateResponseBody = updateResponseBody;
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext(WIRE_PATH, this::handle);
        executor = Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "contract-pinned-vcf-mock");
            thread.setDaemon(true);
            return thread;
        });
        server.setExecutor(executor);
    }

    private static void assertPinnedContract(Path contractPath) throws IOException {
        String contract = Files.readString(contractPath, StandardCharsets.UTF_8);
        List<String> pins = List.of(
                "\"tag\": \"9.0.0.0\"",
                "\"commit\": \"85151f6b1bb58f13b6ac0304bfec53904bea085f\"",
                "\"basePath\": \"/suite-api\"",
                "\"operationId\": \"" + CREATE_OPERATION_ID + "\"",
                "\"operationId\": \"" + UPDATE_OPERATION_ID + "\"",
                "\"path\": \"/api/maintenanceschedules\"");
        for (String pin : pins) {
            if (!contract.contains(pin)) {
                throw new IllegalArgumentException("contract fixture is missing pin: " + pin);
            }
        }
    }

    public void start() {
        server.start();
    }

    public URI applianceUri() {
        return URI.create("http://127.0.0.1:" + server.getAddress().getPort());
    }

    public List<RequestEntry> requestLog() {
        return List.copyOf(requestLog);
    }

    private void handle(HttpExchange exchange) throws IOException {
        byte[] requestBody = exchange.getRequestBody().readAllBytes();
        Headers incoming = exchange.getRequestHeaders();
        Map<String, List<String>> copiedHeaders = new java.util.LinkedHashMap<>();
        incoming.forEach((key, values) -> copiedHeaders.put(key, new ArrayList<>(values)));
        requestLog.add(new RequestEntry(
                exchange.getRequestMethod(),
                exchange.getRequestURI().getRawPath(),
                exchange.getRequestURI().getRawQuery(),
                Map.copyOf(copiedHeaders),
                requestBody));

        if (!WIRE_PATH.equals(exchange.getRequestURI().getRawPath())
                || exchange.getRequestURI().getRawQuery() != null) {
            exchange.sendResponseHeaders(404, -1);
            exchange.close();
            return;
        }

        if ("POST".equals(exchange.getRequestMethod())) {
            sendJsonResponse(exchange, createStatus, createResponseBody);
            return;
        }

        if ("PUT".equals(exchange.getRequestMethod())) {
            sendJsonResponse(exchange, updateStatus, updateResponseBody);
            return;
        }

        exchange.getResponseHeaders().set("Allow", "POST, PUT");
        exchange.sendResponseHeaders(405, -1);
        exchange.close();
    }

    private static void sendJsonResponse(HttpExchange exchange, int status, String body)
            throws IOException {
        if (body.isEmpty()) {
            exchange.sendResponseHeaders(status, -1);
        } else {
            byte[] response = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(status, response.length);
            exchange.getResponseBody().write(response);
        }
        exchange.close();
    }

    @Override
    public void close() {
        server.stop(0);
        executor.shutdownNow();
    }
}
