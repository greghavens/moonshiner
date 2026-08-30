import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Loopback SDDC Manager stand-in whose callable routes are loaded from docs/contract.json.
 *
 * <p>Only the operations named by the focused contract are served; anything else answers 404 so a
 * client cannot wander off the pinned specification projection. Every received request is recorded
 * verbatim so the acceptance harness can assert the exact wire shape.
 */
public final class MockSddcManager implements AutoCloseable {
    public static final String ACCESS_TOKEN = "fixture-access-token-9f2c";
    public static final String REFRESH_TOKEN_ID = "fixture-refresh-token-id-41ab";
    public static final String TASK_ID = "b6f1d2a1-2f4e-4c1a-9f2a-5c1d9e0a7b31";

    public record Reply(int status, String contentType, String body) {
        public Reply(int status, String body) {
            this(status, "application/json", body);
        }
    }

    private record Operation(String operationId, String method, String path, Pattern pathPattern) {
    }

    public record RecordedRequest(
            String method,
            String rawTarget,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            byte[] body) {
        public List<String> headerValues(String name) {
            for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
                if (entry.getKey().equalsIgnoreCase(name)) {
                    return entry.getValue();
                }
            }
            return List.of();
        }

        public String bodyText() {
            return new String(body, StandardCharsets.UTF_8);
        }
    }

    private static final Pattern OPERATION = Pattern.compile(
            "\\{\\s*\"operationId\"\\s*:\\s*\"([^\"]+)\"\\s*,"
                    + "\\s*\"method\"\\s*:\\s*\"([^\"]+)\"\\s*,"
                    + "\\s*\"path\"\\s*:\\s*\"([^\"]+)\"",
            Pattern.DOTALL);

    private static final List<String> CONTRACT_OPERATION_IDS =
            List.of("createToken", "commissionHosts", "getTask");

    private final HttpServer server;
    private final ExecutorService executor;
    private final List<Operation> operations;
    private final List<Reply> replies;
    private final List<RecordedRequest> requests = Collections.synchronizedList(new ArrayList<>());
    private int replyIndex;

    public MockSddcManager() throws IOException {
        this(defaultReplies());
    }

    public MockSddcManager(List<Reply> replies) throws IOException {
        this.operations = loadContractOperations();
        this.replies = List.copyOf(replies);
        this.server = HttpServer.create(
                new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
        this.executor = Executors.newCachedThreadPool();
        server.setExecutor(executor);
        server.createContext("/", this::handle);
        server.start();
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    public List<RecordedRequest> requests() {
        synchronized (requests) {
            return List.copyOf(requests);
        }
    }

    private void handle(HttpExchange exchange) throws IOException {
        byte[] requestBody = exchange.getRequestBody().readAllBytes();
        Map<String, List<String>> headers = new LinkedHashMap<>();
        exchange.getRequestHeaders().forEach(
                (name, values) -> headers.put(name, List.copyOf(values)));
        RecordedRequest request = new RecordedRequest(
                exchange.getRequestMethod(),
                exchange.getRequestURI().toString(),
                exchange.getRequestURI().getRawPath(),
                exchange.getRequestURI().getRawQuery(),
                Collections.unmodifiableMap(headers),
                requestBody.clone());
        requests.add(request);

        if (!isContractRoute(request)) {
            respond(exchange, new Reply(
                    404, "application/json", "{\"errorCode\":\"ROUTE_OUTSIDE_FOCUSED_CONTRACT\"}"));
            return;
        }

        Reply reply;
        synchronized (this) {
            if (replyIndex >= replies.size()) {
                reply = new Reply(500, "{\"errorCode\":\"UNEXPECTED_EXTRA_REQUEST\"}");
            } else {
                reply = replies.get(replyIndex++);
            }
        }
        respond(exchange, reply);
    }

    private boolean isContractRoute(RecordedRequest request) {
        for (Operation operation : operations) {
            if (operation.method().equals(request.method())
                    && operation.pathPattern().matcher(request.rawPath()).matches()) {
                return true;
            }
        }
        return false;
    }

    private static void respond(HttpExchange exchange, Reply reply) throws IOException {
        byte[] body = reply.body() == null
                ? new byte[0]
                : reply.body().getBytes(StandardCharsets.UTF_8);
        if (reply.contentType() != null) {
            exchange.getResponseHeaders().set("Content-Type", reply.contentType());
        }
        exchange.sendResponseHeaders(reply.status(), body.length);
        exchange.getResponseBody().write(body);
        exchange.close();
    }

    private static List<Operation> loadContractOperations() throws IOException {
        String contract = Files.readString(Path.of("docs", "contract.json"), StandardCharsets.UTF_8);
        Matcher matcher = OPERATION.matcher(contract);
        List<Operation> found = new ArrayList<>();
        while (matcher.find()) {
            found.add(new Operation(
                    matcher.group(1),
                    matcher.group(2).toUpperCase(Locale.ROOT),
                    matcher.group(3),
                    templateToPattern(matcher.group(3))));
        }
        if (found.size() != CONTRACT_OPERATION_IDS.size()
                || CONTRACT_OPERATION_IDS.stream().anyMatch(
                        id -> found.stream().noneMatch(op -> op.operationId().equals(id)))) {
            throw new IOException(
                    "focused contract must name exactly " + CONTRACT_OPERATION_IDS);
        }
        return List.copyOf(found);
    }

    /** Turn an OpenAPI path such as {@code /v1/tasks/{id}} into a single-segment matcher. */
    private static Pattern templateToPattern(String path) {
        StringBuilder regex = new StringBuilder();
        int at = 0;
        while (at < path.length()) {
            int open = path.indexOf('{', at);
            if (open < 0) {
                regex.append(Pattern.quote(path.substring(at)));
                break;
            }
            int close = path.indexOf('}', open);
            if (close < 0) {
                throw new IllegalArgumentException("unterminated path template: " + path);
            }
            if (open > at) {
                regex.append(Pattern.quote(path.substring(at, open)));
            }
            regex.append("[^/]+");
            at = close + 1;
        }
        return Pattern.compile(regex.toString());
    }

    private static List<Reply> defaultReplies() {
        String commissioning = task(TASK_ID, "Commissioning Hosts", "HOST_COMMISSION",
                "Successful", "2026-05-04T09:15:00.000Z", null, null, null);
        return List.of(
                new Reply(201, tokenPair(ACCESS_TOKEN, REFRESH_TOKEN_ID)),
                new Reply(202, commissioning),
                new Reply(200, task(TASK_ID, "Commissioning Hosts", "HOST_COMMISSION",
                        "Pending", "2026-05-04T09:15:00.000Z", null, null, null)),
                new Reply(200, task(TASK_ID, "Commissioning Hosts", "HOST_COMMISSION",
                        "In Progress", "2026-05-04T09:15:00.000Z", null, null, null)),
                new Reply(200, task(TASK_ID, "Commissioning Hosts", "HOST_COMMISSION",
                        " IN_PROGRESS ", "2026-05-04T09:15:00.000Z", null, null, null)),
                new Reply(200, task(TASK_ID, "Commissioning Hosts", "HOST_COMMISSION",
                        "Successful", "2026-05-04T09:15:00.000Z", "2026-05-04T09:21:37.000Z",
                        List.of(resource("2f7a8c10-1f24-4b6d-9d5b-0a5c1e8a3f77",
                                        "sfo01-m01-esx05.rainpole.io", "ESXI"),
                                resource("d1c0b9a8-5c6e-4f2b-8a13-7e9f4c2d6b05",
                                        "sfo01-m01-esx06.rainpole.io", "ESXI")),
                        null)));
    }

    public static String tokenPair(String accessToken, String refreshTokenId) {
        return "{\"accessToken\":" + quote(accessToken)
                + ",\"refreshToken\":{\"id\":" + quote(refreshTokenId) + "}}";
    }

    public static String task(
            String id,
            String name,
            String type,
            String status,
            String creationTimestamp,
            String completionTimestamp,
            List<String> resources,
            List<String> errors) {
        StringBuilder json = new StringBuilder("{\"id\":").append(quote(id))
                .append(",\"name\":").append(quote(name));
        if (type != null) {
            json.append(",\"type\":").append(quote(type));
        }
        json.append(",\"status\":").append(quote(status))
                .append(",\"creationTimestamp\":").append(quote(creationTimestamp));
        if (completionTimestamp != null) {
            json.append(",\"completionTimestamp\":").append(quote(completionTimestamp));
        }
        if (resources != null) {
            json.append(",\"resources\":[").append(String.join(",", resources)).append(']');
        }
        if (errors != null) {
            json.append(",\"errors\":[").append(String.join(",", errors)).append(']');
        }
        return json.append('}').toString();
    }

    public static String pendingTask(String status) {
        return task(TASK_ID, "Commissioning Hosts", "HOST_COMMISSION", status,
                "2026-05-04T09:15:00.000Z", null, null, null);
    }

    public static String resource(String resourceId, String fqdn, String type) {
        return "{\"resourceId\":" + quote(resourceId)
                + ",\"fqdn\":" + quote(fqdn)
                + ",\"type\":" + quote(type) + "}";
    }

    public static String error(String errorCode, String message, String referenceToken) {
        StringBuilder json = new StringBuilder("{\"errorCode\":").append(quote(errorCode))
                .append(",\"message\":").append(quote(message));
        if (referenceToken != null) {
            json.append(",\"referenceToken\":").append(quote(referenceToken));
        }
        return json.append('}').toString();
    }

    private static String quote(String value) {
        return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\"";
    }

    @Override
    public void close() {
        server.stop(0);
        executor.shutdownNow();
    }
}
