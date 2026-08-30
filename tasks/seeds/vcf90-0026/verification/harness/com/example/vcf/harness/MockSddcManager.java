package com.example.vcf.harness;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Loopback-only mock of the VMware Cloud Foundation 9.0 SDDC Manager REST API.
 *
 * <p>PROTECTED HARNESS FILE — do not modify.
 *
 * <p>The mock is pinned to the operation set named by {@code docs/contract.json}. It serves exactly
 * these five operations of the 9.0.0.0 {@code sddc-manager-openapi.json} specification and nothing
 * else:
 *
 * <ul>
 *   <li>{@code createToken} — POST /v1/tokens
 *   <li>{@code refreshAccessToken} — PATCH /v1/tokens/access-token/refresh
 *   <li>{@code getBundles} — GET /v1/bundles
 *   <li>{@code startBundleDownloadByID} — PATCH /v1/bundles/{id}
 *   <li>{@code getTask} — GET /v1/tasks/{id}
 * </ul>
 *
 * <p>Any other path returns 404; a known path with an unexpected method returns 405; an unknown
 * query parameter name on {@code getBundles} returns 400. Every request — accepted or rejected — is
 * appended to the request log as one JSON object per line.
 *
 * <p>Token lifecycle: the access token minted by {@code createToken} authorises exactly
 * {@link #FIRST_TOKEN_BUDGET} protected calls. The next protected call presenting it fails with 401
 * and error code {@code TOKEN_EXPIRED}. A token minted by {@code refreshAccessToken} does not
 * expire during a run.
 */
public final class MockSddcManager {

    public static final String USERNAME = "administrator@vsphere.local";
    public static final String PASSWORD = "VMw@re1!VMw@re1!";

    public static final String FIRST_ACCESS_TOKEN = "eyJhbGciOiJSUzI1NiJ9.vcf90-access-1";
    public static final String SECOND_ACCESS_TOKEN = "eyJhbGciOiJSUzI1NiJ9.vcf90-access-2";
    public static final String REFRESH_TOKEN_ID = "0a5f38ec-4c1b-4d0e-9a2f-6b1c7d20e455";

    /** Protected calls the first access token survives: getBundles, the download PATCH, one poll. */
    public static final int FIRST_TOKEN_BUDGET = 3;

    /** Successful {@code getTask} calls before the task reports a terminal status. */
    public static final int POLLS_BEFORE_TERMINAL = 2;

    public static final List<String> TERMINAL_TASK_STATUSES =
            List.of("SUCCESSFUL", "FAILED", "CANCELLED", "COMPLETED_WITH_WARNING", "SKIPPED");

    public static final String PENDING_BUNDLE_ID = "b7c2f4d1-91a5-4f7e-8c33-2ad905e61f48";
    public static final String TASK_ID = "d1e93c07-2b48-4a6c-bf51-90cc4e7ab223";

    private static final List<String> GET_BUNDLES_QUERY_PARAMS =
            List.of("productType", "isCompliant", "bundleType");

    private final Path requestLog;
    private final String terminalTaskStatus;
    private HttpServer server;
    private ExecutorService executor;
    private int seq;
    private int firstTokenCallsUsed;
    private int successfulPolls;
    private boolean refreshed;
    private boolean downloadStarted;

    public MockSddcManager(Path requestLog) {
        this(requestLog, "SUCCESSFUL");
    }

    public MockSddcManager(Path requestLog, String terminalTaskStatus) {
        if (!TERMINAL_TASK_STATUSES.contains(terminalTaskStatus)) {
            throw new IllegalArgumentException("not a terminal Task.status: " + terminalTaskStatus);
        }
        this.requestLog = requestLog;
        this.terminalTaskStatus = terminalTaskStatus;
    }

    public synchronized int start() throws IOException {
        Files.createDirectories(requestLog.toAbsolutePath().getParent());
        Files.deleteIfExists(requestLog);
        Files.createFile(requestLog);
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        executor = Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "mock-sddc-manager");
            thread.setDaemon(true);
            return thread;
        });
        server.setExecutor(executor);
        server.createContext("/", this::dispatch);
        server.start();
        return server.getAddress().getPort();
    }

    public synchronized void stop() {
        if (server != null) {
            server.stop(0);
            server = null;
        }
        if (executor != null) {
            executor.shutdownNow();
            executor = null;
        }
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    private void dispatch(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod().toUpperCase(Locale.ROOT);
        String path = exchange.getRequestURI().getRawPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        String body = new String(readAll(exchange.getRequestBody()), StandardCharsets.UTF_8);
        String authorization = exchange.getRequestHeaders().getFirst("Authorization");
        String contentType = exchange.getRequestHeaders().getFirst("Content-Type");
        String accept = exchange.getRequestHeaders().getFirst("Accept");

        Response response;
        String operationId;
        try {
            Route route = route(method, path);
            operationId = route == null ? null : route.operationId();
            response = route == null ? notFoundOrNotAllowed(method, path) : handle(route, rawQuery, body, authorization);
        } catch (RuntimeException e) {
            operationId = null;
            response = new Response(500, error("INTERNAL_SERVER_ERROR", String.valueOf(e.getMessage())));
        }

        synchronized (this) {
            seq++;
            Map<String, Object> entry = new LinkedHashMap<>();
            entry.put("seq", (long) seq);
            entry.put("operationId", operationId);
            entry.put("method", method);
            entry.put("path", path);
            entry.put("rawQuery", rawQuery);
            entry.put("query", parseQuery(rawQuery));
            entry.put("authorization", authorization);
            entry.put("contentType", contentType);
            entry.put("accept", accept);
            entry.put("body", body);
            entry.put("status", (long) response.status());
            Files.writeString(
                    requestLog,
                    MiniJson.write(entry) + System.lineSeparator(),
                    StandardCharsets.UTF_8,
                    StandardOpenOption.APPEND);
        }

        byte[] payload = MiniJson.write(response.body()).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.sendResponseHeaders(response.status(), payload.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(payload);
        }
    }

    private Response handle(Route route, String rawQuery, String body, String authorization) {
        switch (route.operationId()) {
            case "createToken":
                return createToken(body);
            case "refreshAccessToken":
                return refreshAccessToken(body);
            case "getBundles": {
                Response denied = authorise(authorization);
                return denied != null ? denied : getBundles(rawQuery);
            }
            case "startBundleDownloadByID": {
                Response denied = authorise(authorization);
                return denied != null ? denied : startBundleDownload(route.pathId(), body);
            }
            case "getTask": {
                Response denied = authorise(authorization);
                return denied != null ? denied : getTask(route.pathId());
            }
            default:
                throw new IllegalStateException("unrouted operation " + route.operationId());
        }
    }

    private Response createToken(String body) {
        Object parsed;
        try {
            parsed = MiniJson.parse(body);
        } catch (RuntimeException e) {
            return new Response(400, error("BAD_REQUEST", "Request body is not valid JSON"));
        }
        if (!(parsed instanceof Map)) {
            return new Response(400, error("BAD_REQUEST", "Expected a TokenCreationSpec object"));
        }
        Map<String, Object> spec = MiniJson.asObject(parsed);
        if (!USERNAME.equals(spec.get("username")) || !PASSWORD.equals(spec.get("password"))) {
            return new Response(400, error("INVALID_CREDENTIALS", "Username or password is incorrect"));
        }
        Map<String, Object> pair = MiniJson.obj(
                "accessToken", FIRST_ACCESS_TOKEN,
                "refreshToken", MiniJson.obj("id", REFRESH_TOKEN_ID));
        return new Response(201, pair);
    }

    private Response refreshAccessToken(String body) {
        Object parsed;
        try {
            parsed = MiniJson.parse(body);
        } catch (RuntimeException e) {
            return new Response(400, error("BAD_REQUEST", "Request body is not a JSON string"));
        }
        if (!(parsed instanceof String)) {
            return new Response(
                    400, error("BAD_REQUEST", "Request body must be the refresh token id as a JSON string"));
        }
        if (!REFRESH_TOKEN_ID.equals(parsed)) {
            return new Response(404, error("REFRESH_TOKEN_NOT_FOUND", "No such refresh token"));
        }
        refreshed = true;
        return new Response(200, SECOND_ACCESS_TOKEN);
    }

    private Response getBundles(String rawQuery) {
        Map<String, Object> query = parseQuery(rawQuery);
        for (String name : query.keySet()) {
            if (!GET_BUNDLES_QUERY_PARAMS.contains(name)) {
                return new Response(400, error("BAD_REQUEST", "Unknown query parameter '" + name + "'"));
            }
        }
        String productType = firstValue(query, "productType");
        String bundleType = firstValue(query, "bundleType");
        String isCompliant = firstValue(query, "isCompliant");

        List<Object> elements = new ArrayList<>();
        for (Map<String, Object> bundle : bundleFixtures()) {
            if (productType != null && !productType.isEmpty() && !productType.equals(componentType(bundle))) {
                continue;
            }
            if (bundleType != null && !bundleType.isEmpty() && !bundleType.equals(bundle.get("type"))) {
                continue;
            }
            if (isCompliant != null && !isCompliant.isEmpty()
                    && !Boolean.valueOf(isCompliant).equals(bundle.get("isCompliant"))) {
                continue;
            }
            elements.add(bundle);
        }
        Map<String, Object> page = MiniJson.obj(
                "elements", elements,
                "pageMetadata", MiniJson.obj(
                        "pageNumber", 0L,
                        "pageSize", (long) elements.size(),
                        "totalElements", (long) elements.size(),
                        "totalPages", 1L));
        return new Response(200, page);
    }

    private Response startBundleDownload(String bundleId, String body) {
        if (!PENDING_BUNDLE_ID.equals(bundleId)) {
            return new Response(
                    409,
                    error("BUNDLE_NOT_DOWNLOADABLE", "Bundle " + bundleId + " is not in a downloadable state"));
        }
        Object parsed;
        try {
            parsed = MiniJson.parse(body);
        } catch (RuntimeException e) {
            return new Response(400, error("BAD_REQUEST", "Request body is not valid JSON"));
        }
        if (!(parsed instanceof Map)) {
            return new Response(400, error("BAD_REQUEST", "Expected a BundleUpdateSpec object"));
        }
        Object downloadSpec = MiniJson.asObject(parsed).get("bundleDownloadSpec");
        if (!(downloadSpec instanceof Map)) {
            return new Response(400, error("BAD_REQUEST", "bundleDownloadSpec is required"));
        }
        if (downloadStarted) {
            return new Response(409, error("BUNDLE_DOWNLOAD_IN_PROGRESS", "A download is already running"));
        }
        downloadStarted = true;
        return new Response(202, task("IN_PROGRESS"));
    }

    private Response getTask(String taskId) {
        if (!TASK_ID.equals(taskId)) {
            return new Response(404, error("TASK_NOT_FOUND", "No task with id " + taskId));
        }
        if (!downloadStarted) {
            return new Response(404, error("TASK_NOT_FOUND", "No task with id " + taskId));
        }
        successfulPolls++;
        String status = successfulPolls == 1
                ? "PENDING"
                : successfulPolls > POLLS_BEFORE_TERMINAL ? terminalTaskStatus : "IN_PROGRESS";
        return new Response(200, task(status));
    }

    private Response authorise(String authorization) {
        if (authorization == null || !authorization.startsWith("Bearer ")) {
            return new Response(401, error("UNAUTHORIZED", "A bearer access token is required"));
        }
        String token = authorization.substring("Bearer ".length()).trim();
        if (SECOND_ACCESS_TOKEN.equals(token)) {
            return refreshed ? null : new Response(401, error("UNAUTHORIZED", "Unknown access token"));
        }
        if (!FIRST_ACCESS_TOKEN.equals(token)) {
            return new Response(401, error("UNAUTHORIZED", "Unknown access token"));
        }
        if (firstTokenCallsUsed >= FIRST_TOKEN_BUDGET) {
            return new Response(
                    401, error("TOKEN_EXPIRED", "The access token has expired; refresh it and retry"));
        }
        firstTokenCallsUsed++;
        return null;
    }

    private Map<String, Object> task(String status) {
        Map<String, Object> t = MiniJson.obj(
                "id", TASK_ID,
                "name", "Downloading bundle " + PENDING_BUNDLE_ID,
                "type", "BUNDLE_DOWNLOAD",
                "status", status,
                "creationTimestamp", "2025-03-04T09:12:44.000Z",
                "isCancellable", Boolean.TRUE,
                "isRetryable", Boolean.FALSE);
        if ("SUCCESSFUL".equals(status)) {
            t.put("completionTimestamp", "2025-03-04T09:19:02.000Z");
        }
        t.put("resources", List.of(MiniJson.obj(
                "resourceId", PENDING_BUNDLE_ID,
                "type", "BUNDLE",
                "name", "VMware Cloud Foundation Update 9.0.1.0")));
        return t;
    }

    private List<Map<String, Object>> bundleFixtures() {
        List<Map<String, Object>> bundles = new ArrayList<>();
        bundles.add(bundle(
                "3f7d1a6b-08c9-4b52-a0f1-5d4e2c9b7710",
                "SDDC_MANAGER",
                "SDDC_MANAGER",
                "SUCCESSFUL",
                "9.0.0.1",
                1842L));
        bundles.add(bundle(
                "9c41e0aa-77b3-4f18-bd6e-3f2a8c05d931",
                "SDDC_MANAGER",
                "SDDC_MANAGER",
                "RECALLED",
                "9.0.0.2",
                1907L));
        bundles.add(bundle(
                PENDING_BUNDLE_ID,
                "SDDC_MANAGER",
                "SDDC_MANAGER",
                "PENDING",
                "9.0.1.0",
                2311L));
        bundles.add(bundle(
                "5ea08c93-1d47-4a90-8b2c-c6f7e41d0a52",
                "VCENTER",
                "VMWARE_SOFTWARE",
                "PENDING",
                "8.0.3.00500",
                10422L));
        return bundles;
    }

    private Map<String, Object> bundle(
            String id, String componentType, String type, String downloadStatus, String version, long sizeMb) {
        return MiniJson.obj(
                "id", id,
                "type", type,
                "description", componentType + " upgrade bundle " + version,
                "vendor", "VMware",
                "version", version,
                "sizeMB", sizeMb,
                "isCompliant", Boolean.TRUE,
                "isCumulative", Boolean.FALSE,
                "downloadStatus", downloadStatus,
                "releasedDate", "2025-02-18T00:00:00.000Z",
                "components", List.of(MiniJson.obj(
                        "type", componentType,
                        "description", componentType + " " + version,
                        "vendor", "VMware",
                        "toVersion", version)));
    }

    private static String componentType(Map<String, Object> bundle) {
        List<Object> components = MiniJson.asArray(bundle.get("components"));
        return (String) MiniJson.asObject(components.get(0)).get("type");
    }

    private Route route(String method, String path) {
        if (path.equals("/v1/tokens")) {
            return method.equals("POST") ? new Route("createToken", null) : null;
        }
        if (path.equals("/v1/tokens/access-token/refresh")) {
            return method.equals("PATCH") ? new Route("refreshAccessToken", null) : null;
        }
        if (path.equals("/v1/bundles")) {
            return method.equals("GET") ? new Route("getBundles", null) : null;
        }
        if (path.startsWith("/v1/bundles/") && path.indexOf('/', "/v1/bundles/".length()) < 0) {
            String id = decode(path.substring("/v1/bundles/".length()));
            return method.equals("PATCH") ? new Route("startBundleDownloadByID", id) : null;
        }
        if (path.startsWith("/v1/tasks/") && path.indexOf('/', "/v1/tasks/".length()) < 0) {
            String id = decode(path.substring("/v1/tasks/".length()));
            return method.equals("GET") ? new Route("getTask", id) : null;
        }
        return null;
    }

    private Response notFoundOrNotAllowed(String method, String path) {
        for (String candidate : List.of("GET", "POST", "PUT", "PATCH", "DELETE")) {
            if (!candidate.equals(method) && route(candidate, path) != null) {
                return new Response(
                        405, error("METHOD_NOT_ALLOWED", method + " is not defined for " + path));
            }
        }
        return new Response(
                404,
                error(
                        "NOT_FOUND",
                        path + " is not one of the five operations this mock serves"));
    }

    private static Map<String, Object> parseQuery(String rawQuery) {
        Map<String, Object> query = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return query;
        }
        for (String pair : rawQuery.split("&", -1)) {
            int eq = pair.indexOf('=');
            String name = decode(eq < 0 ? pair : pair.substring(0, eq));
            String value = eq < 0 ? "" : decode(pair.substring(eq + 1));
            @SuppressWarnings("unchecked")
            List<Object> values = (List<Object>) query.computeIfAbsent(name, k -> new ArrayList<>());
            values.add(value);
        }
        return query;
    }

    private static String firstValue(Map<String, Object> query, String name) {
        Object values = query.get(name);
        if (values == null) {
            return null;
        }
        List<Object> list = MiniJson.asArray(values);
        return list.isEmpty() ? null : String.valueOf(list.get(0));
    }

    private static String decode(String s) {
        return URLDecoder.decode(s, StandardCharsets.UTF_8);
    }

    private static Map<String, Object> error(String errorCode, String message) {
        return MiniJson.obj(
                "errorCode", errorCode,
                "errorType", "VALIDATION_FAILED",
                "message", message,
                "referenceToken", "MOCK-" + errorCode);
    }

    private static byte[] readAll(InputStream in) throws IOException {
        return in.readAllBytes();
    }

    private record Route(String operationId, String pathId) {}

    private record Response(int status, Object body) {}
}
