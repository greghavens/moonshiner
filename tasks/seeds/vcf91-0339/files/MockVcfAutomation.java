import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * Contract-pinned loopback implementation of only the three operations listed
 * in docs/contract.json. Every request is copied into an in-memory log before
 * routing so TestMain can assert the actual bytes placed on the socket.
 */
final class MockVcfAutomation implements AutoCloseable {
    record LoggedRequest(
            String method,
            String rawPath,
            String rawQuery,
            Map<String, List<String>> headers,
            String body) {
        String firstHeader(String name) {
            List<String> values = headers.get(name.toLowerCase(Locale.ROOT));
            return values == null || values.isEmpty() ? null : values.get(0);
        }
    }

    private final HttpServer server;
    private final ExecutorService executor;
    private final List<LoggedRequest> requests = new ArrayList<>();
    private final List<List<String>> collectionResponseOrders = new ArrayList<>();
    private final CountDownLatch interruptDetailReceived = new CountDownLatch(1);
    private final CountDownLatch releaseInterruptDetail = new CountDownLatch(1);
    private int okDetailReads;
    private int failedDetailReads;
    private int collectionReads;
    private Integer nextCollectionStatus;
    private String nextCollectionBody;

    MockVcfAutomation() throws IOException {
        WireVerifier.verifyProtectedContract();
        server = HttpServer.create(
                new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
        executor = Executors.newCachedThreadPool();
        server.setExecutor(executor);
        server.createContext("/", this::handle);
        server.start();
    }

    String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    synchronized List<LoggedRequest> requestLog() {
        return List.copyOf(requests);
    }

    synchronized List<List<String>> collectionResponseOrders() {
        return collectionResponseOrders.stream().map(List::copyOf).toList();
    }

    synchronized void failNextCollection(int status, String body) {
        nextCollectionStatus = status;
        nextCollectionBody = body;
    }

    boolean awaitInterruptDetailRequest() throws InterruptedException {
        return interruptDetailReceived.await(5, TimeUnit.SECONDS);
    }

    void releaseInterruptDetailResponse() {
        releaseInterruptDetail.countDown();
    }

    private void handle(HttpExchange exchange) throws IOException {
        byte[] requestBytes = exchange.getRequestBody().readAllBytes();
        log(exchange, requestBytes);

        String method = exchange.getRequestMethod();
        String path = exchange.getRequestURI().getRawPath();
        String query = exchange.getRequestURI().getRawQuery();
        if (query != null) {
            respond(exchange, 404, "{\"error\":\"query not named by the focused contract\"}");
            return;
        }

        if ("POST".equals(method) && "/catalog/api/items/catalog%20ok/request".equals(path)) {
            respond(exchange, 200,
                    "[{\"deploymentId\":\"dep-ok\","
                            + "\"deploymentName\":\"accepted-placeholder\"}]");
            return;
        }
        if ("POST".equals(method) && "/catalog/api/items/catalog-fail/request".equals(path)) {
            respond(exchange, 200,
                    "[{\"deploymentId\":\"dep-fail\",\"deploymentName\":\"broken-app\"}]");
            return;
        }
        if ("POST".equals(method)
                && "/catalog/api/items/caf%C3%A9%2F%E9%9B%AA/request".equalsIgnoreCase(path)) {
            respond(exchange, 200,
                    "[{\"deploymentId\":\"dep-min\",\"deploymentName\":\"minimal\"}]");
            return;
        }
        if ("POST".equals(method) && "/catalog/api/items/catalog-empty/request".equals(path)) {
            respond(exchange, 200, "[]");
            return;
        }
        if ("POST".equals(method) && "/catalog/api/items/catalog-many/request".equals(path)) {
            respond(exchange, 200,
                    "[{\"deploymentId\":\"raw-response-secret-a\"},"
                            + "{\"deploymentId\":\"raw-response-secret-b\"}]");
            return;
        }
        if ("POST".equals(method) && "/catalog/api/items/catalog-blank/request".equals(path)) {
            respond(exchange, 200, "[{\"deploymentId\":\" \\t\"}]");
            return;
        }
        if ("POST".equals(method) && "/catalog/api/items/catalog-api-error/request".equals(path)) {
            respond(exchange, 202,
                    "{\"error\":\"raw-response-secret loopback-token-91\"}");
            return;
        }
        if ("POST".equals(method)
                && "/catalog/api/items/catalog-detail-error/request".equals(path)) {
            respond(exchange, 200, "[{\"deploymentId\":\"dep-http-error\"}]");
            return;
        }
        if ("POST".equals(method)
                && "/catalog/api/items/catalog-interrupt/request".equals(path)) {
            respond(exchange, 200, "[{\"deploymentId\":\"dep-interrupt\"}]");
            return;
        }
        if ("GET".equals(method) && "/deployment/api/deployments/dep-ok".equals(path)) {
            respondOkDetail(exchange);
            return;
        }
        if ("GET".equals(method) && "/deployment/api/deployments/dep-fail".equals(path)) {
            respondFailedDetail(exchange);
            return;
        }
        if ("GET".equals(method) && "/deployment/api/deployments/dep-min".equals(path)) {
            respond(exchange, 200, deploymentJson(
                    "dep-min", "minimal", "project-7", "CREATE_SUCCESSFUL",
                    "SUCCESSFUL", "ready"));
            return;
        }
        if ("GET".equals(method) && "/deployment/api/deployments/dep-http-error".equals(path)) {
            respond(exchange, 503,
                    "{\"error\":\"raw-response-secret loopback-token-91\"}");
            return;
        }
        if ("GET".equals(method) && "/deployment/api/deployments/dep-interrupt".equals(path)) {
            respondInterruptDetail(exchange);
            return;
        }
        if ("GET".equals(method) && "/deployment/api/deployments".equals(path)) {
            respondCollection(exchange);
            return;
        }

        // No convenience health route or undocumented endpoint is exposed.
        respond(exchange, 404, "{\"error\":\"operation not present in contract\"}");
    }

    private synchronized void respondOkDetail(HttpExchange exchange) throws IOException {
        okDetailReads++;
        if (okDetailReads < 3) {
            respond(exchange, 200, deploymentJson(
                    "dep-ok", "edge-app", "project-7", "CREATE_INPROGRESS",
                    "INPROGRESS", "still working"));
        } else {
            respond(exchange, 200, deploymentJson(
                    "dep-ok", "edge-app", "project-7", "CREATE_SUCCESSFUL",
                    "SUCCESSFUL", "ready \\u2713"));
        }
    }

    private synchronized void respondFailedDetail(HttpExchange exchange) throws IOException {
        failedDetailReads++;
        if (failedDetailReads == 1) {
            respond(exchange, 200, deploymentJson(
                    "dep-fail", "broken-app", "project-7", "CREATE_INPROGRESS",
                    "INPROGRESS", "allocating"));
        } else {
            respond(exchange, 200, deploymentJson(
                    "dep-fail", "broken-app", "project-7", "CREATE_FAILED",
                    "FAILED", "datastore exhausted"));
        }
    }

    private synchronized void respondCollection(HttpExchange exchange) throws IOException {
        if (nextCollectionStatus != null) {
            int status = nextCollectionStatus;
            String body = nextCollectionBody;
            nextCollectionStatus = null;
            nextCollectionBody = null;
            respond(exchange, status, body);
            return;
        }
        collectionReads++;
        String alphaA = deploymentJson(
                "dep-a", "Alpha", "project-7", "CREATE_SUCCESSFUL", "SUCCESSFUL", "ready");
        String alphaB = deploymentJson(
                "dep-b", "Alpha", "project-8", "CREATE_SUCCESSFUL", "SUCCESSFUL", "ready");
        String zulu = deploymentJson(
                "dep-z", "Zulu", "project-7", "CREATE_SUCCESSFUL", "SUCCESSFUL", "ready");
        if ((collectionReads & 1) == 1) {
            collectionResponseOrders.add(List.of("dep-z", "dep-b", "dep-a"));
            respond(exchange, 200, "{\"content\":[" + zulu + "," + alphaB + "," + alphaA
                    + "],\"totalElements\":3}");
        } else {
            collectionResponseOrders.add(List.of("dep-a", "dep-z", "dep-b"));
            respond(exchange, 200, "{\"content\":[" + alphaA + "," + zulu + "," + alphaB
                    + "],\"totalElements\":3}");
        }
    }

    private void respondInterruptDetail(HttpExchange exchange) throws IOException {
        interruptDetailReceived.countDown();
        try {
            releaseInterruptDetail.await();
        } catch (InterruptedException interrupted) {
            Thread.currentThread().interrupt();
            throw new IOException("mock detail response interrupted", interrupted);
        }
        respond(exchange, 200, deploymentJson(
                "dep-interrupt", "interrupt-me", "project-7", "CREATE_INPROGRESS",
                "INPROGRESS", "still working"));
    }

    private static String deploymentJson(
            String id,
            String name,
            String projectId,
            String status,
            String requestStatus,
            String requestDetails) {
        return "{\"id\":\"" + id + "\",\"name\":\"" + name
                + "\",\"projectId\":\"" + projectId + "\",\"status\":\"" + status
                + "\",\"lastRequest\":{\"status\":\"" + requestStatus
                + "\",\"details\":\"" + requestDetails + "\"}}";
    }

    private synchronized void log(HttpExchange exchange, byte[] body) {
        Map<String, List<String>> headers = new LinkedHashMap<>();
        Headers incoming = exchange.getRequestHeaders();
        incoming.forEach((name, values) ->
                headers.put(name.toLowerCase(Locale.ROOT), List.copyOf(values)));
        requests.add(new LoggedRequest(
                exchange.getRequestMethod(),
                exchange.getRequestURI().getRawPath(),
                exchange.getRequestURI().getRawQuery(),
                Map.copyOf(headers),
                new String(body, StandardCharsets.UTF_8)));
    }

    private static void respond(HttpExchange exchange, int status, String body) throws IOException {
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    @Override
    public void close() {
        releaseInterruptDetail.countDown();
        server.stop(0);
        executor.shutdownNow();
    }
}
