import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

final class MockVcfServer implements AutoCloseable {
    enum Behavior {
        SUCCESS,
        MATCHES_FIRST,
        MISSING_PROJECT_MATCH,
        MISSING_CATALOG_MATCH,
        TOKEN_ERROR,
        CATALOG_RETRY_ERROR,
        DEPLOYMENT_ERROR,
        BLOCK_TOKEN
    }

    record RequestEntry(
            String operation,
            String method,
            String path,
            String rawQuery,
            String authorization,
            String contentType,
            String body,
            int status,
            Set<String> identifiersReturned) {}

    private static final Map<String, String> CONTRACT_ROUTES = Map.of(
            "exchangeRefreshToken", "POST /oidc/oauth2/token",
            "getProjects", "GET /iaas/api/projects",
            "getCatalogItems", "GET /catalog/api/items",
            "requestCatalogItemInstances", "POST /catalog/api/items/{id}/request");

    private final HttpServer server;
    private final List<RequestEntry> requestLog = new ArrayList<>();
    private final String expectedBasicAuthorization;
    private final String refreshToken;
    private final String projectName;
    private final String catalogItemName;
    private final String projectId;
    private final String projectDecoyId;
    private final String catalogItemId;
    private final String catalogDecoyId;
    private final String deploymentId;
    private final Behavior behavior;
    private final CountDownLatch blockedTokenSeen = new CountDownLatch(1);
    private final CountDownLatch releaseBlockedToken = new CountDownLatch(1);
    private final Set<String> issuedAccessTokens = new java.util.HashSet<>();
    private int tokenSerial;
    private String tokenRejectedForExpiry;

    MockVcfServer(
            String expectedBasicAuthorization,
            String refreshToken,
            String projectName,
            String catalogItemName) throws IOException {
        this(expectedBasicAuthorization, refreshToken, projectName, catalogItemName, Behavior.SUCCESS);
    }

    MockVcfServer(
            String expectedBasicAuthorization,
            String refreshToken,
            String projectName,
            String catalogItemName,
            Behavior behavior) throws IOException {
        this.expectedBasicAuthorization = expectedBasicAuthorization;
        this.refreshToken = refreshToken;
        this.projectName = projectName;
        this.catalogItemName = catalogItemName;
        this.behavior = behavior;
        String nonce = UUID.randomUUID().toString();
        this.projectId = "project-" + nonce + "/R&D +";
        this.projectDecoyId = "project-" + UUID.randomUUID() + "/R&D +";
        this.catalogItemId = "catalog-" + UUID.randomUUID() + "/tier A+beta";
        this.catalogDecoyId = "catalog-" + UUID.randomUUID() + "/tier A+beta";
        this.deploymentId = "deployment-" + nonce;
        InetAddress ipv4Loopback = InetAddress.getByAddress(new byte[] {127, 0, 0, 1});
        this.server = HttpServer.create(new InetSocketAddress(ipv4Loopback, 0), 0);
        this.server.createContext("/", this::handle);
    }

    void start() {
        server.start();
    }

    URI baseUri() {
        return URI.create("http://127.0.0.1:" + server.getAddress().getPort());
    }

    String projectId() {
        return projectId;
    }

    String catalogItemId() {
        return catalogItemId;
    }

    String deploymentId() {
        return deploymentId;
    }

    synchronized List<RequestEntry> requestLog() {
        return List.copyOf(requestLog);
    }

    static Map<String, String> contractRoutes() {
        return CONTRACT_ROUTES;
    }

    static Map<String, List<String>> parseQuery(String rawQuery) {
        Map<String, List<String>> result = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return result;
        }
        for (String pair : rawQuery.split("&")) {
            String[] parts = pair.split("=", 2);
            String key = decode(parts[0]);
            String value = parts.length == 2 ? decode(parts[1]) : "";
            result.computeIfAbsent(key, ignored -> new ArrayList<>()).add(value);
        }
        return result;
    }

    boolean awaitBlockedTokenRequest() throws InterruptedException {
        return blockedTokenSeen.await(5, TimeUnit.SECONDS);
    }

    void releaseBlockedTokenResponse() {
        releaseBlockedToken.countDown();
    }

    private void handle(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod();
        String path = exchange.getRequestURI().getPath();
        if (method.equals("POST") && path.equals("/oidc/oauth2/token")) {
            exchangeToken(exchange);
        } else if (method.equals("GET") && path.equals("/iaas/api/projects")) {
            getProjects(exchange);
        } else if (method.equals("GET") && path.equals("/catalog/api/items")) {
            getCatalogItems(exchange);
        } else if (method.equals("POST")
                && path.startsWith("/catalog/api/items/")
                && path.endsWith("/request")) {
            requestCatalogItem(exchange);
        } else {
            send(exchange, "uncontracted", 404, "{\"error\":\"uncontracted operation\"}", Set.of());
        }
    }

    private void exchangeToken(HttpExchange exchange) throws IOException {
        String body = readBody(exchange);
        Map<String, List<String>> form = parseQuery(body);
        if (!expectedBasicAuthorization.equals(header(exchange, "Authorization"))
                || !hasSingle(form, "grant_type", "refresh_token")
                || !hasSingle(form, "refresh_token", refreshToken)
                || !mediaType(exchange).equals("application/x-www-form-urlencoded")) {
            send(exchange, "exchangeRefreshToken", 400, "{\"error\":\"invalid_request\"}", Set.of(), body);
            return;
        }
        if (behavior == Behavior.TOKEN_ERROR) {
            send(exchange, "exchangeRefreshToken", 503,
                    "{\"error\":\"token_service_unavailable\"}", Set.of(), body);
            return;
        }
        if (behavior == Behavior.BLOCK_TOKEN) {
            blockedTokenSeen.countDown();
            try {
                releaseBlockedToken.await();
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
                send(exchange, "exchangeRefreshToken", 500,
                        "{\"error\":\"mock_interrupted\"}", Set.of(), body);
                return;
            }
        }
        String token;
        synchronized (this) {
            token = "access-" + (++tokenSerial) + "-" + UUID.randomUUID();
            issuedAccessTokens.add(token);
        }
        send(exchange, "exchangeRefreshToken", 200,
                "{\"access_token\":\"" + json(token)
                        + "\",\"token_type\":\"Bearer\",\"expires_in\":1}", Set.of(), body);
    }

    private void getProjects(HttpExchange exchange) throws IOException {
        if (!authorized(exchange)) {
            send(exchange, "getProjects", 401, "{\"error\":\"unauthorized\"}", Set.of());
            return;
        }
        Map<String, List<String>> query = parseQuery(exchange.getRequestURI().getRawQuery());
        String expectedFilter = "name eq '" + projectName.replace("'", "''") + "'";
        if (!hasSingle(query, "$filter", expectedFilter)) {
            send(exchange, "getProjects", 400, "{\"error\":\"bad query\"}", Set.of());
            return;
        }
        if (behavior == Behavior.MISSING_PROJECT_MATCH) {
            send(exchange, "getProjects", 200,
                    "{\"content\":[{\"id\":\"" + json(projectDecoyId) + "\",\"name\":\""
                            + json(projectName + " (archived)")
                            + "\"}],\"totalElements\":1,\"numberOfElements\":1}",
                    Set.of(projectDecoyId));
            return;
        }
        if (behavior == Behavior.MATCHES_FIRST) {
            send(exchange, "getProjects", 200,
                    "{\"content\":[{\"id\":\"" + json(projectId) + "\",\"name\":\""
                            + json(projectName) + "\"},{\"id\":\"" + json(projectDecoyId)
                            + "\",\"name\":\"" + json(projectName + " (archived)")
                            + "\"}],\"totalElements\":2,\"numberOfElements\":2}",
                    Set.of(projectId, projectDecoyId));
            return;
        }
        send(exchange, "getProjects", 200,
                "{\"content\":[{\"id\":\"" + json(projectDecoyId) + "\",\"name\":\""
                        + json(projectName + " (archived)") + "\"},{\"id\":\""
                        + json(projectId) + "\",\"name\":\"" + json(projectName)
                        + "\"}],\"totalElements\":2,\"numberOfElements\":2}",
                Set.of(projectDecoyId, projectId));
    }

    private void getCatalogItems(HttpExchange exchange) throws IOException {
        String bearer = bearer(exchange);
        if (bearer == null || !isIssued(bearer)) {
            send(exchange, "getCatalogItems", 401, "{\"error\":\"unauthorized\"}", Set.of());
            return;
        }
        synchronized (this) {
            if (tokenRejectedForExpiry == null) {
                tokenRejectedForExpiry = bearer;
                send(exchange, "getCatalogItems", 401, "{\"error\":\"expired_token\"}", Set.of());
                return;
            }
            if (tokenRejectedForExpiry.equals(bearer)) {
                send(exchange, "getCatalogItems", 401, "{\"error\":\"expired_token\"}", Set.of());
                return;
            }
        }
        Map<String, List<String>> query = parseQuery(exchange.getRequestURI().getRawQuery());
        if (!hasSingle(query, "search", catalogItemName)
                || !hasSingle(query, "projects", projectId)) {
            send(exchange, "getCatalogItems", 400, "{\"error\":\"bad query\"}", Set.of());
            return;
        }
        if (behavior == Behavior.CATALOG_RETRY_ERROR) {
            send(exchange, "getCatalogItems", 503,
                    "{\"error\":\"catalog_service_unavailable\"}", Set.of());
            return;
        }
        if (behavior == Behavior.MISSING_CATALOG_MATCH) {
            send(exchange, "getCatalogItems", 200,
                    "{\"content\":[{\"id\":\"" + json(catalogDecoyId) + "\",\"name\":\""
                            + json(catalogItemName + " (archived)")
                            + "\"}],\"totalElements\":1,\"numberOfElements\":1}",
                    Set.of(catalogDecoyId));
            return;
        }
        if (behavior == Behavior.MATCHES_FIRST) {
            send(exchange, "getCatalogItems", 200,
                    "{\"content\":[{\"id\":\"" + json(catalogItemId) + "\",\"name\":\""
                            + json(catalogItemName) + "\"},{\"id\":\"" + json(catalogDecoyId)
                            + "\",\"name\":\"" + json(catalogItemName + " (archived)")
                            + "\"}],\"totalElements\":2,\"numberOfElements\":2}",
                    Set.of(catalogItemId, catalogDecoyId));
            return;
        }
        send(exchange, "getCatalogItems", 200,
                "{\"content\":[{\"id\":\"" + json(catalogDecoyId) + "\",\"name\":\""
                        + json(catalogItemName + " (archived)") + "\"},{\"id\":\""
                        + json(catalogItemId) + "\",\"name\":\"" + json(catalogItemName)
                        + "\"}],\"totalElements\":2,\"numberOfElements\":2}",
                Set.of(catalogDecoyId, catalogItemId));
    }

    private void requestCatalogItem(HttpExchange exchange) throws IOException {
        String body = readBody(exchange);
        if (!authorizedAfterExpiry(exchange)) {
            send(exchange, "requestCatalogItemInstances", 401,
                    "{\"error\":\"unauthorized\"}", Set.of(), body);
            return;
        }
        String prefix = "/catalog/api/items/";
        String encodedId = exchange.getRequestURI().getRawPath()
                .substring(prefix.length(), exchange.getRequestURI().getRawPath().length() - "/request".length());
        String usedItemId = decode(encodedId);
        String usedProjectId = jsonField(body, "projectId");
        String usedDeploymentName = jsonField(body, "deploymentName");
        if (!mediaType(exchange).equals("application/json")
                || !catalogItemId.equals(usedItemId)
                || !projectId.equals(usedProjectId)
                || usedDeploymentName == null) {
            send(exchange, "requestCatalogItemInstances", 422,
                    "{\"error\":\"identifier not returned by lookup or invalid body\"}", Set.of(), body);
            return;
        }
        if (behavior == Behavior.DEPLOYMENT_ERROR) {
            send(exchange, "requestCatalogItemInstances", 403,
                    "{\"error\":\"request_forbidden\"}", Set.of(), body);
            return;
        }
        send(exchange, "requestCatalogItemInstances", 200,
                "[{\"deploymentId\":\"" + json(deploymentId) + "\",\"deploymentName\":\""
                        + json(usedDeploymentName) + "\"}]", Set.of(deploymentId), body);
    }

    private boolean authorized(HttpExchange exchange) {
        String token = bearer(exchange);
        return token != null && isIssued(token);
    }

    private boolean authorizedAfterExpiry(HttpExchange exchange) {
        String token = bearer(exchange);
        synchronized (this) {
            return token != null && issuedAccessTokens.contains(token)
                    && tokenRejectedForExpiry != null && !tokenRejectedForExpiry.equals(token);
        }
    }

    private synchronized boolean isIssued(String token) {
        return issuedAccessTokens.contains(token);
    }

    private static String bearer(HttpExchange exchange) {
        String value = header(exchange, "Authorization");
        return value.startsWith("Bearer ") ? value.substring("Bearer ".length()) : null;
    }

    private static String header(HttpExchange exchange, String name) {
        String value = exchange.getRequestHeaders().getFirst(name);
        return value == null ? "" : value;
    }

    private static String mediaType(HttpExchange exchange) {
        return header(exchange, "Content-Type").split(";", 2)[0].trim().toLowerCase();
    }

    private static boolean hasSingle(Map<String, List<String>> values, String key, String expected) {
        return values.containsKey(key) && values.get(key).equals(List.of(expected));
    }

    private static String readBody(HttpExchange exchange) throws IOException {
        return new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
    }

    private void send(HttpExchange exchange, String operation, int status, String body, Set<String> returned)
            throws IOException {
        send(exchange, operation, status, body, returned, "");
    }

    private void send(
            HttpExchange exchange,
            String operation,
            int status,
            String responseBody,
            Set<String> returned,
            String requestBody) throws IOException {
        RequestEntry entry = new RequestEntry(
                operation,
                exchange.getRequestMethod(),
                exchange.getRequestURI().getPath(),
                exchange.getRequestURI().getRawQuery(),
                header(exchange, "Authorization"),
                header(exchange, "Content-Type"),
                requestBody,
                status,
                Set.copyOf(returned));
        synchronized (this) {
            requestLog.add(entry);
        }
        byte[] bytes = responseBody.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, bytes.length);
        exchange.getResponseBody().write(bytes);
        exchange.close();
    }

    private static String jsonField(String body, String field) {
        java.util.regex.Matcher matcher = java.util.regex.Pattern.compile(
                "\\\"" + java.util.regex.Pattern.quote(field)
                        + "\\\"\\s*:\\s*\\\"((?:\\\\.|[^\\\"\\\\])*)\\\"")
                .matcher(body);
        return matcher.find() ? unescapeJson(matcher.group(1)) : null;
    }

    private static String unescapeJson(String value) {
        StringBuilder result = new StringBuilder();
        boolean escaped = false;
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            if (!escaped && c == '\\') {
                escaped = true;
            } else if (escaped) {
                result.append(switch (c) {
                    case '\"' -> '\"';
                    case '\\' -> '\\';
                    case 'n' -> '\n';
                    case 'r' -> '\r';
                    case 't' -> '\t';
                    default -> c;
                });
                escaped = false;
            } else {
                result.append(c);
            }
        }
        return result.toString();
    }

    private static String json(String value) {
        return value.replace("\\", "\\\\")
                .replace("\"", "\\\"")
                .replace("\n", "\\n")
                .replace("\r", "\\r")
                .replace("\t", "\\t");
    }

    private static String decode(String value) {
        return URLDecoder.decode(value, StandardCharsets.UTF_8);
    }

    @Override
    public void close() {
        server.stop(0);
    }
}
