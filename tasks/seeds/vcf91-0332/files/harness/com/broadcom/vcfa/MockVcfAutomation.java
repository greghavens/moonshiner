package com.broadcom.vcfa;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.BufferedWriter;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * A loopback stand-in for a VCF Automation 9.1 appliance, pinned to docs/contract.json.
 *
 * <p>It serves the three operations the contract names and nothing else: any other method or path
 * answers 404. Request bodies and query strings are validated against the field lists the contract
 * transcribed from the xAPIs reference, so an undocumented key or an optional field sent as an
 * empty string is rejected rather than quietly accepted.
 *
 * <p>Token expiry is driven by a use counter, not by the clock, so a run is reproducible: the first
 * access token authorises exactly {@value #FIRST_TOKEN_USES} Deployment API calls and every call
 * after that answers 401 until a fresh token is obtained.
 *
 * <p>Every request is appended to a JSON Lines log that the verifier reads.
 *
 * <p>Do not modify. The verifier restores this file from a pristine copy before it compiles.
 */
public final class MockVcfAutomation implements AutoCloseable {

    /** The refresh token the appliance was provisioned with. */
    public static final String REFRESH_TOKEN = "rt-8c1f0a9e-4d2b-11f0-9a3e-0242ac120002";

    /** Deployment API calls authorised by the very first access token, after which it is expired. */
    public static final int FIRST_TOKEN_USES = 2;

    private static final String LOGIN_PATH = "/csp/gateway/am/api/login/oauth";
    private static final String DEPLOYMENTS_PATH = "/deployment/api/deployments";

    private static final Set<String> LOGIN_FIELDS = new LinkedHashSet<>(Arrays.asList(
            "grant_type", "state", "refresh_token", "code", "redirect_uri",
            "client_id", "client_secret", "scope", "orgId"));
    private static final Set<String> LOGIN_REQUIRED_FIELDS = new LinkedHashSet<>(Arrays.asList(
            "grant_type", "state"));
    private static final Set<String> DEPLOYMENT_QUERY_PARAMS = new LinkedHashSet<>(Arrays.asList(
            "page", "size", "sort"));
    private static final Set<String> UPDATE_FIELDS = new LinkedHashSet<>(Arrays.asList(
            "name", "description", "iconId"));

    private static final Pattern UUID_PATTERN = Pattern.compile(
            "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}");

    private final HttpServer server;
    private final BufferedWriter log;
    private final Map<String, Map<String, Object>> deployments = new LinkedHashMap<>();
    private final List<String> deploymentOrder = new ArrayList<>();

    private int sequence;
    private int tokensIssued;
    private String activeToken;
    private int activeTokenRemainingUses;

    public MockVcfAutomation(Path logPath) throws IOException {
        Files.createDirectories(logPath.toAbsolutePath().getParent());
        this.log = Files.newBufferedWriter(logPath, StandardCharsets.UTF_8,
                StandardOpenOption.CREATE, StandardOpenOption.WRITE, StandardOpenOption.TRUNCATE_EXISTING);
        seedDeployments();
        this.server = HttpServer.create(
                new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        this.server.createContext("/", this::dispatch);
        this.server.setExecutor(null); // sequential, so the request log is a total order
        this.server.start();
    }

    /** The loopback base URL the client should talk to. */
    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    @Override
    public void close() throws IOException {
        server.stop(0);
        log.flush();
        log.close();
    }

    // ------------------------------------------------------------- seed data

    private void seedDeployments() {
        addDeployment("dep-01", "web-tier-prod", "Front-end web tier for the storefront",
                "CREATE_SUCCESSFUL", "2026-07-30T09:14:22.410Z");
        addDeployment("dep-02", "web-tier-stage", "Staging mirror of the storefront web tier",
                "UPDATE_SUCCESSFUL", "2026-07-28T16:02:51.117Z");
        addDeployment("dep-03", "payments-api", "Payments service and its managed Postgres",
                "CREATE_SUCCESSFUL", "2026-07-27T11:47:03.902Z");
        addDeployment("dep-04", "search-cluster", "Elasticsearch cluster backing catalogue search",
                "CREATE_SUCCESSFUL", "2026-07-24T08:31:19.556Z");
        addDeployment("dep-05", "batch-etl", "Nightly ETL workers",
                "UPDATE_SUCCESSFUL", "2026-07-21T22:10:44.031Z");
        addDeployment("dep-06", "observability", "Log and metric collectors",
                "CREATE_SUCCESSFUL", "2026-07-19T05:55:12.788Z");
        addDeployment("dep-07", "sandbox-lab", "Developer sandbox, leased weekly",
                "CREATE_SUCCESSFUL", "2026-07-15T13:26:37.245Z");
    }

    private void addDeployment(String id, String name, String description, String status, String createdAt) {
        Map<String, Object> d = new LinkedHashMap<>();
        d.put("id", id);
        d.put("name", name);
        d.put("description", description);
        d.put("status", status);
        d.put("iconId", "1b3f5a70-0c6d-4f2b-9a51-8ee0c7b1d4a2");
        d.put("projectId", "prj-11d0f6a2-3c5b-4a19-8f30-6c2e91b74c88");
        d.put("orgId", "org-7f2c9d14-5b83-4e07-9c62-3a1de84b0f95");
        d.put("createdAt", createdAt);
        d.put("lastUpdatedAt", createdAt);
        deployments.put(id, d);
        deploymentOrder.add(id);
    }

    // ------------------------------------------------------------- dispatching

    private void dispatch(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod();
        String path = exchange.getRequestURI().getPath();
        String query = exchange.getRequestURI().getRawQuery();
        String authorization = exchange.getRequestHeaders().getFirst("Authorization");
        String contentType = exchange.getRequestHeaders().getFirst("Content-Type");
        String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);

        Reply reply;
        try {
            reply = route(method, path, query, authorization, contentType, body);
        } catch (RuntimeException e) {
            reply = json(500, Map.of("message", "mock failure: " + e));
        }

        record(method, path, query, authorization, contentType, body, reply.status);

        byte[] payload = reply.body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.sendResponseHeaders(reply.status, payload.length);
        try (OutputStream out = exchange.getResponseBody()) {
            out.write(payload);
        }
    }

    private Reply route(String method, String path, String query,
                        String authorization, String contentType, String body) {
        if (LOGIN_PATH.equals(path)) {
            if (!"POST".equals(method)) {
                return notInContract(method, path);
            }
            return login(contentType, body);
        }
        if (DEPLOYMENTS_PATH.equals(path)) {
            if (!"GET".equals(method)) {
                return notInContract(method, path);
            }
            return listDeployments(query, authorization);
        }
        if (path.startsWith(DEPLOYMENTS_PATH + "/")) {
            String tail = path.substring(DEPLOYMENTS_PATH.length() + 1);
            if (!tail.isEmpty() && tail.indexOf('/') < 0 && "PATCH".equals(method)) {
                return patchDeployment(tail, query, authorization, contentType, body);
            }
            return notInContract(method, path);
        }
        return notInContract(method, path);
    }

    private Reply notInContract(String method, String path) {
        return json(404, Map.of(
                "message", method + " " + path + " is not one of the operations named in docs/contract.json",
                "errorCode", "OPERATION_NOT_IN_CONTRACT"));
    }

    // ------------------------------------------------------------- operations

    private Reply login(String contentType, String body) {
        if (contentType == null || !contentType.toLowerCase(Locale.ROOT).startsWith("application/json")) {
            return badRequest("exchangeRefreshToken requires Content-Type: application/json, got " + contentType);
        }
        Map<String, Object> request;
        try {
            request = Json.asObject(Json.parse(body));
        } catch (RuntimeException e) {
            return badRequest("request body is not a JSON object: " + e.getMessage());
        }

        Reply shape = checkFields("exchangeRefreshToken", request, LOGIN_FIELDS, LOGIN_REQUIRED_FIELDS);
        if (shape != null) {
            return shape;
        }
        if (!"refresh_token".equals(request.get("grant_type"))) {
            return badRequest("grant_type must be \"refresh_token\", got " + Json.write(request.get("grant_type")));
        }
        if (!REFRESH_TOKEN.equals(request.get("refresh_token"))) {
            return json(400, Map.of("message", "invalid refresh token", "errorCode", "INVALID_GRANT"));
        }

        tokensIssued++;
        activeToken = "at-" + tokensIssued + "-9f4c2a6b";
        activeTokenRemainingUses = tokensIssued == 1 ? FIRST_TOKEN_USES : Integer.MAX_VALUE;

        Map<String, Object> response = new LinkedHashMap<>();
        response.put("scope", "csp:org_owner customer_number:VCF-9-1");
        response.put("access_token", activeToken);
        response.put("refresh_token", REFRESH_TOKEN);
        response.put("id_token", "id-" + tokensIssued + "-3d81e7c0");
        response.put("token_type", "bearer");
        response.put("expires_in", 1800);
        return json(200, response);
    }

    private Reply listDeployments(String query, String authorization) {
        Map<String, String> params = parseQuery(query);
        for (String name : params.keySet()) {
            if (!DEPLOYMENT_QUERY_PARAMS.contains(name)) {
                return badRequest("listDeployments does not accept the query parameter \"" + name + "\"");
            }
        }
        Reply auth = authorize(authorization);
        if (auth != null) {
            return auth;
        }

        int page;
        int size;
        try {
            page = params.containsKey("page") ? Integer.parseInt(params.get("page")) : 0;
            size = params.containsKey("size") ? Integer.parseInt(params.get("size")) : 20;
        } catch (NumberFormatException e) {
            return badRequest("page and size must be integers");
        }
        if (page < 0) {
            return badRequest("page must be zero-based (0..N)");
        }
        if (size < 1 || size > 2000) {
            return badRequest("size must be between 1 and 2000");
        }

        int total = deploymentOrder.size();
        int totalPages = (total + size - 1) / size;
        int from = Math.min(page * size, total);
        int to = Math.min(from + size, total);

        List<Object> content = new ArrayList<>();
        for (String id : deploymentOrder.subList(from, to)) {
            content.add(new LinkedHashMap<>(deployments.get(id)));
        }

        Map<String, Object> sort = new LinkedHashMap<>();
        sort.put("sorted", true);
        sort.put("unsorted", false);
        sort.put("empty", false);

        Map<String, Object> pageable = new LinkedHashMap<>();
        pageable.put("pageNumber", page);
        pageable.put("pageSize", size);
        pageable.put("offset", from);
        pageable.put("sort", sort);
        pageable.put("paged", true);
        pageable.put("unpaged", false);

        Map<String, Object> envelope = new LinkedHashMap<>();
        envelope.put("content", content);
        envelope.put("empty", content.isEmpty());
        envelope.put("first", page == 0);
        envelope.put("last", page >= totalPages - 1);
        envelope.put("number", page);
        envelope.put("numberOfElements", content.size());
        envelope.put("pageable", pageable);
        envelope.put("size", size);
        envelope.put("sort", sort);
        envelope.put("totalElements", total);
        envelope.put("totalPages", totalPages);
        return json(200, envelope);
    }

    private Reply patchDeployment(String deploymentId, String query, String authorization,
                                  String contentType, String body) {
        if (query != null && !query.isEmpty()) {
            return badRequest("patchDeployment does not accept query parameters");
        }
        if (contentType == null || !contentType.toLowerCase(Locale.ROOT).startsWith("application/json")) {
            return badRequest("patchDeployment requires Content-Type: application/json, got " + contentType);
        }
        Reply auth = authorize(authorization);
        if (auth != null) {
            return auth;
        }
        Map<String, Object> deployment = deployments.get(deploymentId);
        if (deployment == null) {
            return json(404, Map.of("message", "deployment " + deploymentId + " not found",
                    "errorCode", "NOT_FOUND"));
        }

        Map<String, Object> update;
        try {
            update = Json.asObject(Json.parse(body));
        } catch (RuntimeException e) {
            return badRequest("request body is not a JSON object: " + e.getMessage());
        }
        Reply shape = checkFields("patchDeployment", update, UPDATE_FIELDS, Set.of());
        if (shape != null) {
            return shape;
        }
        if (update.isEmpty()) {
            return badRequest("DeploymentUpdate must carry at least one of name, description, iconId");
        }
        if (update.containsKey("name")) {
            String name = String.valueOf(update.get("name"));
            if (name.length() > 900) {
                return badRequest("name exceeds maxLength 900");
            }
        }
        if (update.containsKey("description")
                && String.valueOf(update.get("description")).length() > 2000) {
            return badRequest("description exceeds maxLength 2000");
        }
        if (update.containsKey("iconId")
                && !UUID_PATTERN.matcher(String.valueOf(update.get("iconId"))).matches()) {
            return badRequest("iconId must be a uuid");
        }

        for (String key : UPDATE_FIELDS) {
            if (update.containsKey(key)) {
                deployment.put(key, update.get(key));
            }
        }
        deployment.put("status", "UPDATE_SUCCESSFUL");
        deployment.put("lastUpdatedAt", "2026-08-11T10:05:00.000Z");
        return json(200, new LinkedHashMap<>(deployment));
    }

    // ------------------------------------------------------------- validation

    /**
     * Enforces the contract's wire rules: no key outside the documented field list, every required
     * field present, and no optional field sent as null or as an empty string instead of omitted.
     */
    private Reply checkFields(String operationId, Map<String, Object> request,
                              Set<String> allowed, Set<String> required) {
        for (Map.Entry<String, Object> entry : request.entrySet()) {
            String key = entry.getKey();
            if (!allowed.contains(key)) {
                return badRequest(operationId + " does not define the field \"" + key + "\"");
            }
            Object value = entry.getValue();
            if (value == null) {
                return badRequest("field \"" + key + "\" was sent as null; a field with no value is omitted, "
                        + "not sent empty");
            }
            if (value instanceof String s && s.isEmpty()) {
                return badRequest("field \"" + key + "\" was sent as an empty string; a field with no value "
                        + "is omitted, not sent empty");
            }
        }
        for (String key : required) {
            if (!request.containsKey(key)) {
                return badRequest(operationId + " requires the field \"" + key + "\"");
            }
        }
        return null;
    }

    private Reply authorize(String authorization) {
        if (authorization == null || !authorization.startsWith("Bearer ")) {
            return json(401, Map.of("message", "Authorization header must carry a bearer access token",
                    "errorCode", "UNAUTHORIZED"));
        }
        String presented = authorization.substring("Bearer ".length()).trim();
        if (activeToken == null || !activeToken.equals(presented) || activeTokenRemainingUses <= 0) {
            return json(401, Map.of("message", "The access token has expired or is not valid",
                    "errorCode", "UNAUTHORIZED"));
        }
        if (activeTokenRemainingUses != Integer.MAX_VALUE) {
            activeTokenRemainingUses--;
        }
        return null;
    }

    // ------------------------------------------------------------- plumbing

    private static Map<String, String> parseQuery(String rawQuery) {
        Map<String, String> out = new LinkedHashMap<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return out;
        }
        for (String pair : rawQuery.split("&")) {
            if (pair.isEmpty()) {
                continue;
            }
            int eq = pair.indexOf('=');
            String name = eq < 0 ? pair : pair.substring(0, eq);
            String value = eq < 0 ? "" : pair.substring(eq + 1);
            out.put(decode(name), decode(value));
        }
        return out;
    }

    private static String decode(String s) {
        return java.net.URLDecoder.decode(s, StandardCharsets.UTF_8);
    }

    private void record(String method, String path, String query, String authorization,
                        String contentType, String body, int status) throws IOException {
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("seq", ++sequence);
        entry.put("method", method);
        entry.put("path", path);
        entry.put("query", query);
        entry.put("authorization", authorization);
        entry.put("contentType", contentType);
        entry.put("body", body.isEmpty() ? null : body);
        entry.put("status", status);
        log.write(Json.write(entry));
        log.newLine();
        log.flush();
    }

    private static Reply json(int status, Map<String, ?> body) {
        return new Reply(status, Json.write(body));
    }

    private static Reply badRequest(String message) {
        return json(400, Map.of("message", message, "errorCode", "BAD_REQUEST"));
    }

    private record Reply(int status, String body) {}
}
