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

final class MockVsanServer implements AutoCloseable {
    static final String CREATE_OPERATION_ID =
            "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task";
    static final String GET_TASK_OPERATION_ID = "Snapservice.Tasks_get";

    private static final String CREATE_PREFIX = "/api/snapservice/clusters/";
    private static final String CREATE_SUFFIX = "/snapshots";
    private static final String TASK_PREFIX = "/api/snapservice/tasks/";
    private static final String TASK_ID = "task-42";
    private static final String[] NON_TERMINAL_STATES = {
            "PENDING", "RUNNING", "BLOCKED"
    };

    private final HttpServer server;
    private final String terminalState;
    private final List<RequestRecord> requests = new ArrayList<>();
    private int taskPolls;

    private MockVsanServer(Path contractPath, String terminalState) throws IOException {
        assertPinnedContract(contractPath);
        this.terminalState = terminalState;
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", this::handle);
    }

    static MockVsanServer start(Path contractPath) throws IOException {
        return start(contractPath, "SUCCEEDED");
    }

    static MockVsanServer startFailing(Path contractPath) throws IOException {
        return start(contractPath, "FAILED");
    }

    private static MockVsanServer start(Path contractPath, String terminalState) throws IOException {
        MockVsanServer mock = new MockVsanServer(contractPath, terminalState);
        mock.server.start();
        return mock;
    }

    URI apiBaseUri() {
        return URI.create("http://127.0.0.1:" + server.getAddress().getPort() + "/api");
    }

    synchronized List<RequestRecord> requestLog() {
        return List.copyOf(requests);
    }

    @Override
    public void close() {
        server.stop(0);
    }

    private void handle(HttpExchange exchange) throws IOException {
        byte[] requestBody = exchange.getRequestBody().readAllBytes();
        String rawPath = exchange.getRequestURI().getRawPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        synchronized (this) {
            requests.add(new RequestRecord(
                    exchange.getRequestMethod(),
                    rawPath,
                    rawQuery,
                    exchange.getRequestHeaders(),
                    new String(requestBody, StandardCharsets.UTF_8)));
        }

        if (isCreateOperation(exchange.getRequestMethod(), rawPath, rawQuery)) {
            send(exchange, 202, "\"" + TASK_ID + "\"");
            return;
        }
        if (isGetTaskOperation(exchange.getRequestMethod(), rawPath, rawQuery)) {
            String state;
            synchronized (this) {
                state = taskPolls < NON_TERMINAL_STATES.length
                        ? NON_TERMINAL_STATES[taskPolls]
                        : terminalState;
                taskPolls++;
            }
            send(exchange, 200, taskBody(state));
            return;
        }
        send(exchange, 404, "{\"error\":\"operation not in pinned contract\"}");
    }

    private static boolean isCreateOperation(String method, String rawPath, String rawQuery) {
        if (!"POST".equals(method) || !"vmw-task=true".equals(rawQuery)
                || !rawPath.startsWith(CREATE_PREFIX) || !rawPath.endsWith(CREATE_SUFFIX)) {
            return false;
        }
        String variables = rawPath.substring(
                CREATE_PREFIX.length(), rawPath.length() - CREATE_SUFFIX.length());
        int separator = variables.indexOf("/protection-groups/");
        return separator > 0
                && separator + "/protection-groups/".length() < variables.length()
                && variables.indexOf('/', separator + "/protection-groups/".length()) < 0;
    }

    private static boolean isGetTaskOperation(String method, String rawPath, String rawQuery) {
        return "GET".equals(method)
                && rawQuery == null
                && rawPath.equals(TASK_PREFIX + TASK_ID);
    }

    private static String taskBody(String status) {
        return "{"
                + "\"cancelable\":false,"
                + "\"description\":{\"args\":[],\"default_message\":\"Snapshot "
                + status + "\",\"id\":\"mock.snapshot." + status.toLowerCase() + "\"},"
                + "\"operation\":\"" + CREATE_OPERATION_ID + "\","
                + "\"service\":\"com.vmware.snapservice\","
                + "\"status\":\"" + status + "\""
                + "}";
    }

    private static void send(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    private static void assertPinnedContract(Path contractPath) throws IOException {
        String contract = Files.readString(contractPath, StandardCharsets.UTF_8);
        for (String required : List.of(
                "\"operationId\": \"" + CREATE_OPERATION_ID + "\"",
                "\"operationId\": \"" + GET_TASK_OPERATION_ID + "\"",
                "\"path\": \"/snapservice/clusters/{cluster}/protection-groups/{pg}/snapshots?vmw-task=true\"",
                "\"path\": \"/snapservice/tasks/{task}\"")) {
            if (!contract.contains(required)) {
                throw new IOException("contract fixture is not pinned to required operation: " + required);
            }
        }
    }

    record RequestRecord(
            String method,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            String body) {
        RequestRecord {
            headers = Map.copyOf(headers);
        }
    }
}
