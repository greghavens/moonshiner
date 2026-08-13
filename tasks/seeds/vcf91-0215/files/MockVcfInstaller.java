import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
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
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Loopback-only mock whose callable routes are loaded from docs/contract.json. */
public final class MockVcfInstaller implements AutoCloseable {
    public record Reply(int status, String contentType, String body) {
        public Reply(int status, String body) {
            this(status, "application/json", body);
        }
    }

    private record Operation(String operationId, String method, String path) {
    }

    public record RecordedRequest(
            String method,
            String rawTarget,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            byte[] body,
            long receivedNanos) {
        public List<String> headerValues(String name) {
            for (Map.Entry<String, List<String>> entry : headers.entrySet()) {
                if (entry.getKey().equalsIgnoreCase(name)) {
                    return entry.getValue();
                }
            }
            return List.of();
        }
    }

    private static final Pattern OPERATION = Pattern.compile(
            "\\{\\s*\"operationId\"\\s*:\\s*\"([^\"]+)\"\\s*,"
                    + "\\s*\"method\"\\s*:\\s*\"([^\"]+)\"\\s*,"
                    + "\\s*\"path\"\\s*:\\s*\"([^\"]+)\"",
            Pattern.DOTALL);

    private final HttpServer server;
    private final ExecutorService executor;
    private final List<Operation> operations;
    private final List<Reply> replies;
    private final int blockedReplyIndex;
    private final CountDownLatch blockedResponseEntered = new CountDownLatch(1);
    private final CountDownLatch releaseBlockedResponse = new CountDownLatch(1);
    private final List<RecordedRequest> requests = Collections.synchronizedList(new ArrayList<>());
    private int replyIndex;

    public MockVcfInstaller() throws IOException {
        this(defaultReplies(), -1);
    }

    public MockVcfInstaller(List<Reply> replies) throws IOException {
        this(replies, -1);
    }

    public MockVcfInstaller(List<Reply> replies, int blockedReplyIndex) throws IOException {
        this.operations = loadContractOperations();
        this.replies = List.copyOf(replies);
        if (blockedReplyIndex < -1 || blockedReplyIndex >= replies.size()) {
            throw new IllegalArgumentException("blocked reply index is outside the reply list");
        }
        this.blockedReplyIndex = blockedReplyIndex;
        this.server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
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

    public void awaitBlockedResponse() throws InterruptedException {
        if (!blockedResponseEntered.await(10, TimeUnit.SECONDS)) {
            throw new AssertionError("timed out waiting for the contract mock's blocked response");
        }
    }

    public void releaseBlockedResponse() {
        releaseBlockedResponse.countDown();
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
                requestBody.clone(),
                System.nanoTime());
        requests.add(request);

        if (!isContractRoute(request)) {
            respond(exchange, new Reply(404, "application/json", "{\"error\":\"route not in focused contract\"}"));
            return;
        }

        Reply reply;
        int selectedReplyIndex;
        synchronized (this) {
            selectedReplyIndex = replyIndex;
            if (replyIndex >= replies.size()) {
                reply = new Reply(500, "application/json", "{\"error\":\"unexpected extra request\"}");
            } else {
                reply = replies.get(replyIndex++);
            }
        }
        if (selectedReplyIndex == blockedReplyIndex) {
            blockedResponseEntered.countDown();
            try {
                releaseBlockedResponse.await();
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                exchange.close();
                return;
            }
        }
        respond(exchange, reply);
    }

    private boolean isContractRoute(RecordedRequest request) {
        if (request.rawQuery() != null) {
            return false;
        }
        for (Operation operation : operations) {
            if (!operation.method().equals(request.method())) {
                continue;
            }
            String template = operation.path();
            int marker = template.indexOf("{id}");
            if (marker < 0) {
                if (template.equals(request.rawPath())) {
                    return true;
                }
                continue;
            }
            String prefix = template.substring(0, marker);
            String suffix = template.substring(marker + 4);
            if (request.rawPath().startsWith(prefix) && request.rawPath().endsWith(suffix)) {
                String segment = request.rawPath().substring(
                        prefix.length(), request.rawPath().length() - suffix.length());
                if (!segment.isEmpty() && !segment.contains("/")) {
                    return true;
                }
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
                    matcher.group(3)));
        }
        if (found.size() != 2
                || found.stream().noneMatch(op -> op.operationId().equals("updateProxyConfiguration"))
                || found.stream().noneMatch(op -> op.operationId().equals("getTask"))) {
            throw new IOException("focused contract must name exactly updateProxyConfiguration and getTask");
        }
        return List.copyOf(found);
    }

    private static List<Reply> defaultReplies() {
        return List.of(
                new Reply(202, task("proxy/task 17", "Pending")),
                new Reply(200, task("proxy/task 17", "In Progress")),
                new Reply(200, task("proxy/task 17", "Queued")),
                new Reply(200, task("proxy/task 17", "Successful")));
    }

    public static String task(String id, String status) {
        return "{\"id\":" + jsonString(id)
                + ",\"name\":\"Update Proxy\",\"status\":" + jsonString(status)
                + ",\"creationTimestamp\":\"2026-07-01T12:00:00Z\"}";
    }

    private static String jsonString(String value) {
        StringBuilder json = new StringBuilder().append('"');
        for (int index = 0; index < value.length(); index++) {
            char ch = value.charAt(index);
            switch (ch) {
                case '"' -> json.append("\\\"");
                case '\\' -> json.append("\\\\");
                case '\b' -> json.append("\\b");
                case '\f' -> json.append("\\f");
                case '\n' -> json.append("\\n");
                case '\r' -> json.append("\\r");
                case '\t' -> json.append("\\t");
                default -> {
                    if (ch < 0x20) {
                        json.append(String.format(Locale.ROOT, "\\u%04x", (int) ch));
                    } else {
                        json.append(ch);
                    }
                }
            }
        }
        return json.append('"').toString();
    }

    @Override
    public void close() {
        releaseBlockedResponse();
        server.stop(0);
        executor.shutdownNow();
    }
}
