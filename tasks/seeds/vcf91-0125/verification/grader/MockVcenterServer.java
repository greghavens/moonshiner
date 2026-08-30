import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Contract-pinned loopback vCenter. It exposes only the operations projected in
 * docs/contract.json and keeps a request log directly readable by TestMain.
 */
public final class MockVcenterServer implements AutoCloseable {
    private final ContractBinding binding;
    private final HttpServer server;
    private final ExecutorService executor;
    private final CopyOnWriteArrayList<LoggedRequest> requests =
            new CopyOnWriteArrayList<>();
    private final AtomicInteger sequence = new AtomicInteger();
    private final AtomicInteger oldLoginCount = new AtomicInteger();
    private final AtomicInteger replacementLoginCount = new AtomicInteger();
    private final Set<String> activeSessions = ConcurrentHashMap.newKeySet();
    private final CountDownLatch oldClusterStarted = new CountDownLatch(1);
    private final CountDownLatch releaseOldCluster = new CountDownLatch(1);
    private final CountDownLatch replacementCreated = new CountDownLatch(1);
    private final CountDownLatch oldSessionDeleted = new CountDownLatch(1);

    private final String username;
    private final String oldPassword;
    private final String replacementPassword;
    private final String oldSessionId;
    private final String replacementSessionId;
    private final String clusterId;

    public MockVcenterServer(Path contractPath) throws IOException {
        binding = ContractBinding.load(contractPath);
        String nonce = UUID.randomUUID().toString().replace("-", "");
        username = "rotation-svc-" + nonce.substring(0, 8);
        oldPassword = "old-" + nonce.substring(8, 20) + "!";
        replacementPassword = "new-" + nonce.substring(20) + "?";
        oldSessionId = "old-session-" + nonce.substring(0, 16);
        replacementSessionId = "replacement-session-" + nonce.substring(16);
        clusterId = "domain-c-" + nonce.substring(5, 17);

        server = HttpServer.create(
                new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
        executor = Executors.newFixedThreadPool(8);
        server.setExecutor(executor);
        server.createContext(binding.basePath() + "/", new Handler());
        server.start();
    }

    public URI apiBaseUri() {
        return URI.create("http://127.0.0.1:" + server.getAddress().getPort()
                + binding.basePath() + "/");
    }

    public String username() {
        return username;
    }

    public String oldPassword() {
        return oldPassword;
    }

    public String replacementPassword() {
        return replacementPassword;
    }

    public String oldSessionId() {
        return oldSessionId;
    }

    public String replacementSessionId() {
        return replacementSessionId;
    }

    public String clusterId() {
        return clusterId;
    }

    public List<LoggedRequest> requestLogSnapshot() {
        return List.copyOf(requests);
    }

    public boolean awaitOldClusterStarted(Duration timeout)
            throws InterruptedException {
        return oldClusterStarted.await(
                timeout.toMillis(), TimeUnit.MILLISECONDS);
    }

    public boolean awaitReplacementCreated(Duration timeout)
            throws InterruptedException {
        return replacementCreated.await(
                timeout.toMillis(), TimeUnit.MILLISECONDS);
    }

    public boolean awaitOldSessionDeleted(Duration timeout)
            throws InterruptedException {
        return oldSessionDeleted.await(
                timeout.toMillis(), TimeUnit.MILLISECONDS);
    }

    public void releaseOldCluster() {
        releaseOldCluster.countDown();
    }

    @Override
    public void close() {
        releaseOldCluster.countDown();
        server.stop(0);
        executor.shutdownNow();
    }

    private final class Handler implements HttpHandler {
        @Override
        public void handle(HttpExchange exchange) throws IOException {
            byte[] body = exchange.getRequestBody().readAllBytes();
            String method = exchange.getRequestMethod();
            String path = exchange.getRequestURI().getRawPath();
            String query = exchange.getRequestURI().getRawQuery();
            String operationId = binding.operationId(method, path);
            LoggedRequest logged = new LoggedRequest(
                    sequence.incrementAndGet(),
                    operationId,
                    method,
                    path,
                    query,
                    exchange.getRequestHeaders().getFirst("Accept"),
                    exchange.getRequestHeaders().getFirst("Authorization"),
                    exchange.getRequestHeaders().getFirst(
                            "vmware-api-session-id"),
                    exchange.getRequestHeaders().getFirst("Content-Type"),
                    exchange.getRequestHeaders().getFirst("Transfer-Encoding"),
                    exchange.getRequestHeaders().getFirst("Content-Length"),
                    body.length);
            requests.add(logged);

            if (operationId == null) {
                sendJson(exchange, 404, "{\"error\":\"operation not in contract\"}");
                return;
            }
            switch (operationId) {
                case "Cis.Session_create" -> createSession(exchange, logged);
                case "Vcenter.Cluster_list" -> listClusters(exchange, logged);
                case "Cis.Session_delete" -> deleteSession(exchange, logged);
                default -> sendJson(
                        exchange, 404, "{\"error\":\"operation not served\"}");
            }
        }
    }

    private void createSession(HttpExchange exchange, LoggedRequest request)
            throws IOException {
        if (request.rawQuery() != null
                || request.bodyBytes() != 0
                || !"application/json".equals(request.accept())
                || request.contentType() != null
                || request.transferEncoding() != null
                || request.sessionId() != null) {
            sendJson(exchange, 400, "{\"error\":\"bad session-create wire\"}");
            return;
        }

        String oldAuthorization = basic(username, oldPassword);
        String replacementAuthorization = basic(username, replacementPassword);
        if (oldAuthorization.equals(request.authorization())
                && oldLoginCount.getAndIncrement() == 0) {
            activeSessions.add(oldSessionId);
            sendJsonString(exchange, 201, oldSessionId);
            return;
        }
        if (replacementAuthorization.equals(request.authorization())
                && replacementLoginCount.getAndIncrement() == 0) {
            activeSessions.add(replacementSessionId);
            sendJsonString(exchange, 201, replacementSessionId);
            replacementCreated.countDown();
            return;
        }
        sendJson(exchange, 401, "{\"error\":\"bad credentials\"}");
    }

    private void listClusters(HttpExchange exchange, LoggedRequest request)
            throws IOException {
        if (request.bodyBytes() != 0
                || !"application/json".equals(request.accept())
                || request.authorization() != null
                || request.contentType() != null
                || request.transferEncoding() != null
                || request.sessionId() == null) {
            sendJson(exchange, 400, "{\"error\":\"bad cluster-list wire\"}");
            return;
        }

        if (oldSessionId.equals(request.sessionId())) {
            oldClusterStarted.countDown();
            try {
                if (!releaseOldCluster.await(10, TimeUnit.SECONDS)) {
                    sendJson(exchange, 500, "{\"error\":\"test release timeout\"}");
                    return;
                }
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                sendJson(exchange, 500, "{\"error\":\"mock interrupted\"}");
                return;
            }
        }

        if (!activeSessions.contains(request.sessionId())) {
            sendJson(exchange, 401, "{\"error\":\"session retired\"}");
            return;
        }
        if (!oldSessionId.equals(request.sessionId())
                && !replacementSessionId.equals(request.sessionId())) {
            sendJson(exchange, 401, "{\"error\":\"unknown session\"}");
            return;
        }
        sendJson(exchange, 200, clusterBody());
    }

    private void deleteSession(HttpExchange exchange, LoggedRequest request)
            throws IOException {
        if (request.rawQuery() != null
                || request.bodyBytes() != 0
                || !"application/json".equals(request.accept())
                || request.authorization() != null
                || request.contentType() != null
                || request.transferEncoding() != null
                || request.sessionId() == null) {
            sendJson(exchange, 400, "{\"error\":\"bad session-delete wire\"}");
            return;
        }
        if (!activeSessions.remove(request.sessionId())) {
            sendJson(exchange, 401, "{\"error\":\"session not active\"}");
            return;
        }
        if (oldSessionId.equals(request.sessionId())) {
            oldSessionDeleted.countDown();
        }
        exchange.sendResponseHeaders(204, -1);
        exchange.close();
    }

    private String clusterBody() {
        return "[{\"cluster\":\"" + clusterId
                + "\",\"name\":\"Rotation cluster\","
                + "\"ha_enabled\":true,\"drs_enabled\":false}]";
    }

    private static String basic(String username, String password) {
        String value = username + ":" + password;
        return "Basic " + Base64.getEncoder().encodeToString(
                value.getBytes(StandardCharsets.UTF_8));
    }

    private static void sendJsonString(
            HttpExchange exchange, int status, String value) throws IOException {
        sendJson(exchange, status, "\"" + value + "\"");
    }

    private static void sendJson(
            HttpExchange exchange, int status, String body) throws IOException {
        byte[] encoded = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set(
                "Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, encoded.length);
        exchange.getResponseBody().write(encoded);
        exchange.close();
    }

    public record LoggedRequest(
            int sequence,
            String operationId,
            String method,
            String rawPath,
            String rawQuery,
            String accept,
            String authorization,
            String sessionId,
            String contentType,
            String transferEncoding,
            String contentLength,
            int bodyBytes) {
    }

    private record Operation(String operationId, String method, String path) {
    }

    private record ContractBinding(
            String basePath, Map<String, Operation> operations) {
        private static final Pattern BASE_PATH = Pattern.compile(
                "\\\"basePath\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"");
        private static final Pattern OPERATION = Pattern.compile(
                "\\{\\s*\\\"operationId\\\"\\s*:\\s*\\\"([^\\\"]+)\\\""
                        + "\\s*,\\s*\\\"method\\\"\\s*:\\s*\\\"([^\\\"]+)\\\""
                        + "\\s*,\\s*\\\"path\\\"\\s*:\\s*\\\"([^\\\"]+)\\\"",
                Pattern.DOTALL);

        static ContractBinding load(Path contractPath) throws IOException {
            String json = Files.readString(contractPath, StandardCharsets.UTF_8);
            Matcher baseMatcher = BASE_PATH.matcher(json);
            if (!baseMatcher.find()) {
                throw new IOException("contract must contain one basePath");
            }
            String basePath = baseMatcher.group(1);
            if (baseMatcher.find()) {
                throw new IOException("contract must contain one basePath");
            }
            if (!"/api".equals(basePath)) {
                throw new IOException("contract base path is not /api");
            }

            Matcher operationMatcher = OPERATION.matcher(json);
            Map<String, Operation> operations = new LinkedHashMap<>();
            List<String> operationIds = new ArrayList<>();
            while (operationMatcher.find()) {
                Operation operation = new Operation(
                        operationMatcher.group(1),
                        operationMatcher.group(2),
                        operationMatcher.group(3));
                String key = operation.method() + " "
                        + basePath + operation.path();
                if (operations.put(key, operation) != null) {
                    throw new IOException("duplicate contract route");
                }
                operationIds.add(operation.operationId());
            }
            if (!operationIds.equals(List.of(
                    "Cis.Session_create",
                    "Vcenter.Cluster_list",
                    "Cis.Session_delete"))) {
                throw new IOException("contract operation set changed");
            }
            if (!json.contains("\"name\": \"vmware-api-session-id\"")
                    || !json.contains("\"scheme\": \"basic\"")) {
                throw new IOException("contract security binding changed");
            }
            return new ContractBinding(basePath, Map.copyOf(operations));
        }

        String operationId(String method, String rawPath) {
            Operation operation = operations.get(method + " " + rawPath);
            return operation == null ? null : operation.operationId();
        }
    }
}
