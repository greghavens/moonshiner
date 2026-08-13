import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Loopback stand-in for the VCF 9.1 vSAN Data Protection snapshot appliance.
 *
 * <p>The route table is built at startup from {@code docs/contract.json}. Only the operations that
 * the contract names are served; every other request is answered with the spec's NotFound error
 * shape. Each request is appended to a JSON Lines log that the verifier reads back.
 *
 * <p>Session behaviour: the first token issued authorises {@link #FIRST_TOKEN_BUDGET} authenticated
 * requests and is rejected from then on, so a run has to recover from a mid-flight expiry. Tokens
 * issued after that are long lived.
 */
public final class MockSnapserviceServer implements AutoCloseable {

    static final String USERNAME = "svc-dataprotection@vsphere.local";
    static final String PASSWORD = "Sn@pSvc-9!lab";
    static final String CLUSTER = "domain-c9";
    static final int FIRST_TOKEN_BUDGET = 4;

    /** Fixture protection groups for {@link #CLUSTER}; only ACTIVE ones are in scope for a sweep. */
    private static final String[][] PROTECTION_GROUPS = {
        {"pg-1001", "finance-tier1", "ACTIVE"},
        {"pg-1002", "hr-records", "ACTIVE"},
        {"pg-1003", "legacy-archive", "PAUSED"},
        {"pg-1004", "web-frontend", "ACTIVE"},
        {"pg-1005", "db-primary", "ACTIVE"},
    };

    private final HttpServer server;
    private final List<Route> routes = new ArrayList<>();
    private final List<String> requestLog = new ArrayList<>();
    private final Map<String, Integer> tokenBudget = new HashMap<>();
    private final Map<String, String> taskToPg = new LinkedHashMap<>();
    private final Map<String, Integer> taskPolls = new HashMap<>();
    private final List<String> createdSnapshots = new ArrayList<>();
    private final String basePath;
    private int tokensIssued;
    private int sequence;

    public MockSnapserviceServer(Path contractPath) throws IOException {
        Object parsed = Json.parse(Files.readString(contractPath, StandardCharsets.UTF_8));
        Map<String, Object> contract = Json.asObject(parsed);
        this.basePath = (String) Json.asObject(contract.get("server")).get("base_path");
        for (Object entry : Json.asArray(contract.get("operations"))) {
            Map<String, Object> operation = Json.asObject(entry);
            routes.add(new Route(
                    (String) operation.get("operationId"),
                    (String) operation.get("method"),
                    (String) operation.get("path")));
        }
        assertServesExactly();

        this.server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        this.server.setExecutor(Executors.newSingleThreadExecutor(runnable -> {
            Thread thread = new Thread(runnable, "mock-snapservice");
            thread.setDaemon(true);
            return thread;
        }));
        this.server.createContext("/", this::dispatch);
        this.server.start();
    }

    /** Fails fast if the contract has drifted away from the operations this mock knows how to serve. */
    private void assertServesExactly() {
        List<String> expected = List.of(
                "Snapservice.Sessions_create",
                "Snapservice.Clusters.ProtectionGroups_list",
                "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task",
                "Snapservice.Tasks_get");
        List<String> actual = new ArrayList<>();
        for (Route route : routes) {
            actual.add(route.operationId);
        }
        List<String> sortedExpected = new ArrayList<>(expected);
        List<String> sortedActual = new ArrayList<>(actual);
        sortedExpected.sort(null);
        sortedActual.sort(null);
        if (!sortedExpected.equals(sortedActual)) {
            throw new IllegalStateException(
                    "docs/contract.json operation set does not match the pinned mock: " + actual);
        }
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort() + basePath;
    }

    public void writeLog(Path destination) throws IOException {
        Files.createDirectories(destination.toAbsolutePath().getParent());
        Files.write(destination, requestLog, StandardCharsets.UTF_8);
    }

    @Override
    public void close() {
        server.stop(0);
    }

    // ---------------------------------------------------------------- dispatch

    private void dispatch(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod().toUpperCase(Locale.ROOT);
        String rawPath = exchange.getRequestURI().getRawPath();
        String rawQuery = exchange.getRequestURI().getRawQuery();
        String body = readBody(exchange);
        String sessionHeader = exchange.getRequestHeaders().getFirst("vmware-api-session-id");
        String authorization = exchange.getRequestHeaders().getFirst("Authorization");
        String contentType = exchange.getRequestHeaders().getFirst("Content-Type");

        Route matched = null;
        Map<String, String> pathParams = Map.of();
        for (Route route : routes) {
            Matcher matcher = route.pattern.matcher(rawPath);
            if (route.method.equals(method) && matcher.matches()) {
                if (route.operationId.endsWith("Snapshots_create$Task") && !hasVmwTask(rawQuery)) {
                    continue;
                }
                matched = route;
                pathParams = route.extract(matcher);
                break;
            }
        }

        Response response;
        if (matched == null) {
            response = notFound("no operation in the pinned contract matches " + method + " " + rawPath);
        } else if (matched.operationId.equals("Snapservice.Sessions_create")) {
            response = createSession(authorization);
        } else {
            response = authenticated(sessionHeader, matched, pathParams, rawQuery, body);
        }

        log(matched, method, rawPath, rawQuery, sessionHeader, authorization, contentType, body, response);
        send(exchange, response);
    }

    private static boolean hasVmwTask(String rawQuery) {
        if (rawQuery == null) {
            return false;
        }
        for (String pair : rawQuery.split("&")) {
            if (pair.equals("vmw-task=true")) {
                return true;
            }
        }
        return false;
    }

    private Response createSession(String authorization) {
        String expected = "Basic " + Base64.getEncoder().encodeToString(
                (USERNAME + ":" + PASSWORD).getBytes(StandardCharsets.UTF_8));
        if (authorization == null || !authorization.equals(expected)) {
            return unauthenticated();
        }
        tokensIssued++;
        String token = "sess-" + String.format("%04d", tokensIssued) + "-9f2c";
        if (tokensIssued == 1) {
            tokenBudget.put(token, FIRST_TOKEN_BUDGET);
        }
        return new Response(201, Json.string(token));
    }

    private Response authenticated(
            String sessionHeader, Route route, Map<String, String> pathParams, String rawQuery, String body) {
        if (sessionHeader == null || !sessionHeader.startsWith("sess-")) {
            return unauthenticated();
        }
        Integer remaining = tokenBudget.get(sessionHeader);
        if (remaining != null) {
            if (remaining <= 0) {
                // Expired part way through the run; the request is rejected before any work is done.
                return unauthenticated();
            }
            tokenBudget.put(sessionHeader, remaining - 1);
        }

        switch (route.operationId) {
            case "Snapservice.Clusters.ProtectionGroups_list":
                return listProtectionGroups(pathParams.get("cluster"), rawQuery);
            case "Snapservice.Clusters.ProtectionGroups.Snapshots_create$Task":
                return createSnapshot(pathParams.get("cluster"), pathParams.get("pg"), body);
            case "Snapservice.Tasks_get":
                return getTask(pathParams.get("task"));
            default:
                return notFound("unhandled operation " + route.operationId);
        }
    }

    private Response listProtectionGroups(String cluster, String rawQuery) {
        if (!CLUSTER.equals(cluster)) {
            return notFound("no cluster associated with " + cluster);
        }
        List<String> states = queryValues(rawQuery, "states");
        StringBuilder items = new StringBuilder();
        for (String[] pg : PROTECTION_GROUPS) {
            if (!states.isEmpty() && !states.contains(pg[2])) {
                continue;
            }
            if (items.length() > 0) {
                items.append(',');
            }
            items.append("{\"pg\":").append(Json.string(pg[0])).append(",\"info\":{")
                    .append("\"name\":").append(Json.string(pg[1])).append(',')
                    .append("\"status\":").append(Json.string(pg[2])).append(',')
                    .append("\"locked\":false,")
                    .append("\"target_entities\":{\"vms\":[]},")
                    .append("\"snapshot_policies\":[],")
                    .append("\"snapshots\":[],")
                    .append("\"vms\":[]}}");
        }
        return new Response(200, "{\"items\":[" + items + "]}");
    }

    private Response createSnapshot(String cluster, String pg, String body) {
        if (!CLUSTER.equals(cluster)) {
            return notFound("no cluster associated with " + cluster);
        }
        boolean known = false;
        for (String[] candidate : PROTECTION_GROUPS) {
            known |= candidate[0].equals(pg);
        }
        if (!known) {
            return notFound("no protection group associated with " + pg);
        }
        Map<String, Object> spec;
        try {
            spec = Json.asObject(Json.parse(body));
        } catch (RuntimeException failure) {
            return new Response(400, "{\"messages\":[{\"id\":\"snapservice.spec.malformed\","
                    + "\"default_message\":\"Request body is not a JSON object.\",\"args\":[]}]}");
        }
        Object name = spec.get("name");
        if (!(name instanceof String) || ((String) name).isEmpty()) {
            return new Response(400, "{\"messages\":[{\"id\":\"snapservice.spec.name.required\","
                    + "\"default_message\":\"CreateSpec.name is required.\",\"args\":[]}]}");
        }
        createdSnapshots.add(pg + "/" + name);
        String task = "task-" + pg + "-" + createdSnapshots.size();
        taskToPg.put(task, pg);
        return new Response(202, Json.string(task));
    }

    private Response getTask(String task) {
        if (!taskToPg.containsKey(task)) {
            return notFound("no task associated with " + task);
        }
        int polls = taskPolls.merge(task, 1, Integer::sum);
        String status = polls >= 2 ? "SUCCEEDED" : "RUNNING";
        int completed = polls >= 2 ? 100 : 40;
        StringBuilder info = new StringBuilder();
        info.append("{\"status\":").append(Json.string(status)).append(',')
                .append("\"cancelable\":false,")
                .append("\"service\":\"com.vmware.snapservice.clusters.protection_groups.snapshots\",")
                .append("\"operation\":\"create\",")
                .append("\"description\":{\"id\":\"snapservice.pg.snapshot.create\",")
                .append("\"default_message\":\"Create protection group snapshot\",\"args\":[]},")
                .append("\"progress\":{\"total\":100,\"completed\":").append(completed).append(',')
                .append("\"message\":{\"id\":\"snapservice.pg.snapshot.progress\",")
                .append("\"default_message\":\"Quiescing members\",\"args\":[]}}");
        if (polls >= 2) {
            info.append(",\"result\":").append(Json.string(taskToPg.get(task) + "-snapshot"));
        }
        info.append('}');
        return new Response(200, info.toString());
    }

    private static Response unauthenticated() {
        return new Response(401, "{\"challenge\":\"Basic realm=\\\"snapservice\\\"\"}");
    }

    private static Response notFound(String message) {
        return new Response(404, "{\"messages\":[{\"id\":\"snapservice.not_found\","
                + "\"default_message\":" + Json.string(message) + ",\"args\":[]}]}");
    }

    // ---------------------------------------------------------------- plumbing

    private static List<String> queryValues(String rawQuery, String key) {
        List<String> values = new ArrayList<>();
        if (rawQuery == null) {
            return values;
        }
        for (String pair : rawQuery.split("&")) {
            int split = pair.indexOf('=');
            String name = split < 0 ? pair : pair.substring(0, split);
            String value = split < 0 ? "" : pair.substring(split + 1);
            if (name.equals(key) && !value.isEmpty()) {
                values.add(value);
            }
        }
        return values;
    }

    private static String readBody(HttpExchange exchange) throws IOException {
        try (InputStream stream = exchange.getRequestBody()) {
            return new String(stream.readAllBytes(), StandardCharsets.UTF_8);
        }
    }

    private void send(HttpExchange exchange, Response response) throws IOException {
        byte[] payload = response.body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        if (response.status == 401) {
            exchange.getResponseHeaders().add("WWW-Authenticate", "Basic realm=\"snapservice\"");
        }
        exchange.sendResponseHeaders(response.status, payload.length);
        try (OutputStream stream = exchange.getResponseBody()) {
            stream.write(payload);
        }
    }

    private void log(
            Route matched, String method, String rawPath, String rawQuery, String sessionHeader,
            String authorization, String contentType, String body, Response response) {
        StringBuilder line = new StringBuilder();
        line.append("{\"seq\":").append(++sequence)
                .append(",\"operation_id\":")
                .append(matched == null ? "null" : Json.string(matched.operationId))
                .append(",\"method\":").append(Json.string(method))
                .append(",\"path\":").append(Json.string(rawPath))
                .append(",\"query\":").append(rawQuery == null ? "null" : Json.string(rawQuery))
                .append(",\"session_header\":")
                .append(sessionHeader == null ? "null" : Json.string(sessionHeader))
                .append(",\"authorization\":")
                .append(authorization == null ? "null" : Json.string(authorization))
                .append(",\"content_type\":")
                .append(contentType == null ? "null" : Json.string(contentType))
                .append(",\"body\":").append(Json.string(body))
                .append(",\"status\":").append(response.status)
                .append(",\"response_body\":").append(Json.string(response.body))
                .append('}');
        requestLog.add(line.toString());
    }

    private static final class Response {
        final int status;
        final String body;

        Response(int status, String body) {
            this.status = status;
            this.body = body;
        }
    }

    private final class Route {
        final String operationId;
        final String method;
        final Pattern pattern;
        final List<String> parameterNames = new ArrayList<>();

        Route(String operationId, String method, String specPath) {
            this.operationId = operationId;
            this.method = method;
            StringBuilder regex = new StringBuilder(Pattern.quote(basePath));
            Matcher matcher = Pattern.compile("\\{([A-Za-z][A-Za-z0-9]*)\\}").matcher(specPath);
            int cursor = 0;
            while (matcher.find()) {
                regex.append(Pattern.quote(specPath.substring(cursor, matcher.start())));
                regex.append("([^/]+)");
                parameterNames.add(matcher.group(1));
                cursor = matcher.end();
            }
            regex.append(Pattern.quote(specPath.substring(cursor)));
            this.pattern = Pattern.compile(regex.toString());
        }

        Map<String, String> extract(Matcher matcher) {
            Map<String, String> values = new LinkedHashMap<>();
            for (int index = 0; index < parameterNames.size(); index++) {
                values.put(parameterNames.get(index), matcher.group(index + 1));
            }
            return values;
        }
    }
}
