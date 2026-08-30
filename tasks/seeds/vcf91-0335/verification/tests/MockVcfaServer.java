import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Loopback-only VCF Automation fixture pinned to docs/contract.json.
 *
 * <p>It routes only the six operations the contract names; every other method and path is
 * answered as "operation not in contract" and still recorded, so the harness can prove that
 * the client stayed inside the contract. Identifiers and the day-2 failure text are generated
 * per run, so no value the client reports back can have been hard-coded from this file.
 */
public final class MockVcfaServer implements AutoCloseable {

    /** One observed request, exactly as it arrived on the wire. */
    public record RecordedRequest(String method, String rawPath, String rawQuery,
                                  Map<String, List<String>> headers, String body) {
        public String header(String name) {
            List<String> values = headers.get(name.toLowerCase(Locale.ROOT));
            return values == null || values.isEmpty() ? null : String.join(",", values);
        }

        @Override
        public String toString() {
            return method + " " + rawPath + (rawQuery == null ? "" : "?" + rawQuery);
        }
    }

    public static final String DEPLOYMENT_NAME = "fin-risk-uat-cluster";
    public static final String PROJECT_NAME = "fin-risk-platform";
    public static final String ORG_ID = "1f6d0c8a-0f4b-4a1e-9d63-3a1c0b7e5d24";

    private static final String TOKEN_PATH_PREFIX = "/tm/oauth/tenant/";
    private static final String TOKEN_PATH_SUFFIX = "/token";
    private static final String PROJECTS_PATH = "/iaas/api/projects";
    private static final String CATALOG_PREFIX = "/catalog/api/items/";
    private static final String CATALOG_SUFFIX = "/request";
    private static final String DEPLOYMENTS_PREFIX = "/deployment/api/deployments/";
    private static final String ACTION_SUFFIX = "/requests";
    private static final String REQUESTS_PREFIX = "/deployment/api/requests/";

    private final Object logLock = new Object();
    private final List<RecordedRequest> requests = new ArrayList<>();
    private final AtomicInteger deploymentPolls = new AtomicInteger();
    private final AtomicInteger requestPolls = new AtomicInteger();
    private final ExecutorService executor;
    private final HttpServer server;

    private final String accessToken;
    private final String projectId;
    private final String deploymentId;
    private final String actionRequestId;
    private final String failureDetail;

    public MockVcfaServer(Path contractPath) throws IOException {
        verifyPinnedContract(contractPath);

        SecureRandom random = new SecureRandom();
        accessToken = "eyJhbGciOiJSUzI1NiJ9." + hex(random, 12);
        projectId = UUID.nameUUIDFromBytes(("project-" + hex(random, 8)).getBytes(StandardCharsets.UTF_8))
                .toString();
        deploymentId = UUID.nameUUIDFromBytes(("deployment-" + hex(random, 8)).getBytes(StandardCharsets.UTF_8))
                .toString();
        actionRequestId = UUID.nameUUIDFromBytes(("request-" + hex(random, 8)).getBytes(StandardCharsets.UTF_8))
                .toString();
        failureDetail = "Action 'Deployment.PowerOff' failed on resource '" + DEPLOYMENT_NAME
                + " / db-node-2': power state transition rejected by the vSphere placement policy"
                + " (correlationId " + hex(random, 8) + ")";

        server = HttpServer.create(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
        executor = Executors.newCachedThreadPool(runnable -> {
            Thread thread = new Thread(runnable, "vcfa-contract-mock");
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

    public String accessToken() {
        return accessToken;
    }

    public String projectId() {
        return projectId;
    }

    public String deploymentId() {
        return deploymentId;
    }

    public String actionRequestId() {
        return actionRequestId;
    }

    /** The verbatim Request.details text the failing day-2 request reports. */
    public String failureDetail() {
        return failureDetail;
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

    private void handle(HttpExchange exchange) throws IOException {
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
        String method = exchange.getRequestMethod();
        String rawPath = exchange.getRequestURI().getRawPath();
        synchronized (logLock) {
            requests.add(new RecordedRequest(method, rawPath, exchange.getRequestURI().getRawQuery(),
                    copyHeaders(exchange), body));
        }

        if ("POST".equals(method)
                && rawPath.startsWith(TOKEN_PATH_PREFIX) && rawPath.endsWith(TOKEN_PATH_SUFFIX)
                && segment(rawPath, TOKEN_PATH_PREFIX, TOKEN_PATH_SUFFIX) != null) {
            handleToken(exchange, body);
            return;
        }

        if (!authorized(exchange)) {
            respond(exchange, 401, "{\"error\":\"unauthorized\"}");
            return;
        }

        if ("POST".equals(method) && PROJECTS_PATH.equals(rawPath)) {
            handleCreateProject(exchange, body);
            return;
        }
        if ("POST".equals(method)
                && rawPath.startsWith(CATALOG_PREFIX) && rawPath.endsWith(CATALOG_SUFFIX)
                && segment(rawPath, CATALOG_PREFIX, CATALOG_SUFFIX) != null) {
            respond(exchange, 200, "[{\"deploymentId\":\"" + deploymentId + "\",\"deploymentName\":\""
                    + DEPLOYMENT_NAME + "\"}]");
            return;
        }
        if ("POST".equals(method)
                && rawPath.startsWith(DEPLOYMENTS_PREFIX) && rawPath.endsWith(ACTION_SUFFIX)) {
            String id = segment(rawPath, DEPLOYMENTS_PREFIX, ACTION_SUFFIX);
            if (!deploymentId.equals(id)) {
                respond(exchange, 404, "{\"error\":\"deployment not found\"}");
                return;
            }
            respond(exchange, 200, requestJson("INPROGRESS", 0, null));
            return;
        }
        if ("GET".equals(method) && rawPath.startsWith(DEPLOYMENTS_PREFIX)) {
            String id = segment(rawPath, DEPLOYMENTS_PREFIX, "");
            if (!deploymentId.equals(id)) {
                respond(exchange, 404, "{\"error\":\"deployment not found\"}");
                return;
            }
            String status = deploymentPolls.getAndIncrement() == 0 ? "CREATE_INPROGRESS" : "CREATE_SUCCESSFUL";
            respond(exchange, 200, "{\"id\":\"" + deploymentId + "\",\"name\":\"" + DEPLOYMENT_NAME
                    + "\",\"projectId\":\"" + projectId + "\",\"orgId\":\"" + ORG_ID
                    + "\",\"status\":\"" + status + "\"}");
            return;
        }
        if ("GET".equals(method) && rawPath.startsWith(REQUESTS_PREFIX)) {
            String id = segment(rawPath, REQUESTS_PREFIX, "");
            if (!actionRequestId.equals(id)) {
                respond(exchange, 404, "{\"error\":\"request not found\"}");
                return;
            }
            if (requestPolls.getAndIncrement() == 0) {
                respond(exchange, 200, requestJson("INPROGRESS", 1, null));
            } else {
                respond(exchange, 200, requestJson("FAILED", 2, failureDetail));
            }
            return;
        }

        respond(exchange, 404, "{\"error\":\"operation not in contract\"}");
    }

    private void handleToken(HttpExchange exchange, String body) throws IOException {
        Map<String, String> form = parseForm(body);
        if (!"refresh_token".equals(form.get("grant_type"))
                || form.get("refresh_token") == null
                || form.get("refresh_token").isEmpty()) {
            respond(exchange, 400, "{\"error\":\"invalid_grant\"}");
            return;
        }
        respond(exchange, 200, "{\"access_token\":\"" + accessToken
                + "\",\"token_type\":\"Bearer\",\"expires_in\":3600}");
    }

    private void handleCreateProject(HttpExchange exchange, String body) throws IOException {
        if (!body.contains("\"name\":\"" + PROJECT_NAME + "\"")) {
            respond(exchange, 400, "{\"error\":\"project name is required\"}");
            return;
        }
        respond(exchange, 201, "{\"id\":\"" + projectId + "\",\"name\":\"" + PROJECT_NAME
                + "\",\"orgId\":\"" + ORG_ID + "\",\"operationTimeout\":1800}");
    }

    private String requestJson(String status, int completedTasks, String details) {
        StringBuilder json = new StringBuilder();
        json.append("{\"id\":\"").append(actionRequestId).append("\"")
                .append(",\"actionId\":\"Deployment.PowerOff\"")
                .append(",\"deploymentId\":\"").append(deploymentId).append("\"")
                .append(",\"name\":\"Power Off\"")
                .append(",\"requestedBy\":\"svc-change-runner@prod-fin\"")
                .append(",\"createdAt\":\"2026-08-11T09:14:02.118Z\"")
                .append(",\"completedTasks\":").append(completedTasks)
                .append(",\"totalTasks\":3")
                .append(",\"cancelable\":false")
                .append(",\"status\":\"").append(status).append("\"");
        if (details != null) {
            json.append(",\"details\":\"").append(escape(details)).append("\"");
        }
        return json.append("}").toString();
    }

    private boolean authorized(HttpExchange exchange) {
        List<String> values = exchange.getRequestHeaders().get("Authorization");
        return values != null && values.size() == 1 && ("Bearer " + accessToken).equals(values.get(0));
    }

    private static String segment(String rawPath, String prefix, String suffix) {
        String middle = rawPath.substring(prefix.length(), rawPath.length() - suffix.length());
        return middle.isEmpty() || middle.contains("/") ? null : middle;
    }

    private static Map<String, String> parseForm(String body) {
        Map<String, String> form = new LinkedHashMap<>();
        for (String pair : body.split("&")) {
            if (pair.isEmpty()) {
                continue;
            }
            int split = pair.indexOf('=');
            String key = split < 0 ? pair : pair.substring(0, split);
            String value = split < 0 ? "" : pair.substring(split + 1);
            form.put(java.net.URLDecoder.decode(key, StandardCharsets.UTF_8),
                    java.net.URLDecoder.decode(value, StandardCharsets.UTF_8));
        }
        return form;
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

    private static String escape(String text) {
        return text.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private static String hex(SecureRandom random, int bytes) {
        byte[] buffer = new byte[bytes];
        random.nextBytes(buffer);
        StringBuilder text = new StringBuilder(bytes * 2);
        for (byte value : buffer) {
            text.append(String.format("%02x", value));
        }
        return text.toString();
    }

    private static void verifyPinnedContract(Path contractPath) throws IOException {
        String contract = Files.readString(contractPath, StandardCharsets.UTF_8);
        requireContractText(contract, "\"is_published_specification\": false");
        requireContractText(contract, "\"portal\": \"https://developer.broadcom.com/xapis\"");
        requireContractText(contract, "\"operationId\": \"exchangeRefreshToken\"");
        requireContractText(contract, "\"operationId\": \"createProject\"");
        requireContractText(contract, "\"operationId\": \"requestCatalogItemInstances\"");
        requireContractText(contract, "\"operationId\": \"getDeploymentById\"");
        requireContractText(contract, "\"operationId\": \"submitDeploymentActionRequest\"");
        requireContractText(contract, "\"operationId\": \"getRequest\"");
        if (count(contract, "\"operationId\"") != 6) {
            throw new IOException("the mock serves exactly the six contract operations");
        }
    }

    private static void requireContractText(String contract, String required) throws IOException {
        if (!contract.contains(required)) {
            throw new IOException("contract is not the pinned VCF Automation reference subset: " + required);
        }
    }

    private static int count(String text, String needle) {
        int count = 0;
        int from = 0;
        while ((from = text.indexOf(needle, from)) >= 0) {
            count++;
            from += needle.length();
        }
        return count;
    }
}
