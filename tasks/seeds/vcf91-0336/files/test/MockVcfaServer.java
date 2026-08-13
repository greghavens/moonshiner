import com.example.vcfa.Json;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Loopback stand-in for a VCF Automation 9.1 appliance.
 *
 * <p>PROTECTED FILE - the harness owns this. Do not edit it.
 *
 * <p>The route table is not hand-written: it is built at startup from the {@code operations} array
 * in {@code docs/contract.json}, so the mock serves exactly the operations the contract names and
 * nothing else. Any other path is answered 404 and any contract path reached with the wrong method
 * is answered 405, which is what makes an off-contract call visible instead of silently working.
 *
 * <p>Every request is appended to a JSONL log the test reads back with {@link #readLog()}.
 *
 * <p>Binds 127.0.0.1 on an ephemeral port. No external network is involved.
 */
public final class MockVcfaServer implements AutoCloseable {

    /** One contract operation, compiled into a matcher. */
    private record Route(String operationId, String method, Pattern pattern, List<String> paramNames) {}

    private final List<Route> routes = new ArrayList<>();
    private final Path logPath;
    private final String expectedToken;
    private final Map<String, List<Map<String, Object>>> deployments = new LinkedHashMap<>();

    private HttpServer server;
    private int requestSeq;
    private int requestIdSeq;

    public MockVcfaServer(Path contractPath, Path logPath, String expectedToken) throws IOException {
        this.logPath = logPath;
        this.expectedToken = expectedToken;
        Files.deleteIfExists(logPath);
        Files.createDirectories(logPath.toAbsolutePath().getParent());
        Files.createFile(logPath);
        loadRoutesFromContract(contractPath);
    }

    @SuppressWarnings("unchecked")
    private void loadRoutesFromContract(Path contractPath) throws IOException {
        Object parsed = Json.parse(Files.readString(contractPath, StandardCharsets.UTF_8));
        Map<String, Object> contract = (Map<String, Object>) parsed;
        List<Object> operations = (List<Object>) contract.get("operations");
        if (operations == null || operations.isEmpty()) {
            throw new IllegalStateException("contract declares no operations: " + contractPath);
        }
        for (Object o : operations) {
            Map<String, Object> op = (Map<String, Object>) o;
            String operationId = (String) op.get("operationId");
            String method = ((String) op.get("method")).toUpperCase(Locale.ROOT);
            String template = (String) op.get("pathTemplate");
            List<String> paramNames = new ArrayList<>();
            StringBuilder regex = new StringBuilder("^");
            Matcher m = Pattern.compile("\\{([^}]+)}").matcher(template);
            int last = 0;
            while (m.find()) {
                regex.append(Pattern.quote(template.substring(last, m.start())));
                regex.append("([^/]+)");
                paramNames.add(m.group(1));
                last = m.end();
            }
            regex.append(Pattern.quote(template.substring(last)));
            regex.append("$");
            routes.add(new Route(operationId, method, Pattern.compile(regex.toString()), paramNames));
        }
    }

    /** Registers a deployment and the actions its precheck will report. */
    public MockVcfaServer withDeployment(String deploymentId, List<Map<String, Object>> actions) {
        deployments.put(deploymentId, actions);
        return this;
    }

    /** Builds a ResourceAction as the contract describes it. */
    public static Map<String, Object> action(String id, String name, boolean valid) {
        Map<String, Object> a = new LinkedHashMap<>();
        a.put("actionType", "RESOURCE_ACTION");
        a.put("dependents", List.of());
        a.put("description", name + " the deployment");
        a.put("displayName", name.contains(".") ? name.substring(name.indexOf('.') + 1) : name);
        a.put("id", id);
        a.put("name", name);
        a.put("orgId", "org-8f1c2b0a");
        a.put("projectId", "proj-5d3e77a1");
        a.put("valid", valid);
        return a;
    }

    public MockVcfaServer start() throws IOException {
        server =
                HttpServer.create(
                        new InetSocketAddress(InetAddress.getByName("127.0.0.1"), 0), 0);
        server.setExecutor(Executors.newSingleThreadExecutor());
        server.createContext("/", this::handle);
        server.start();
        return this;
    }

    public String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    @Override
    public void close() {
        if (server != null) {
            server.stop(0);
        }
    }

    // ------------------------------------------------------------- dispatch

    private void handle(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod().toUpperCase(Locale.ROOT);
        String path = exchange.getRequestURI().getPath();
        String query = exchange.getRequestURI().getRawQuery();
        String body;
        try (InputStream in = exchange.getRequestBody()) {
            body = new String(in.readAllBytes(), StandardCharsets.UTF_8);
        }

        Route matched = null;
        boolean pathKnown = false;
        Map<String, String> pathParams = new LinkedHashMap<>();
        for (Route r : routes) {
            Matcher m = r.pattern().matcher(path);
            if (!m.matches()) {
                continue;
            }
            pathKnown = true;
            if (r.method().equals(method)) {
                matched = r;
                for (int i = 0; i < r.paramNames().size(); i++) {
                    pathParams.put(r.paramNames().get(i), m.group(i + 1));
                }
                break;
            }
        }

        Response response;
        if (matched == null) {
            response = pathKnown
                    ? error(405, "method " + method + " is not part of the contract for " + path)
                    : error(404, "no contract operation serves " + path);
        } else if (!authorized(exchange)) {
            response = error(401, "missing or wrong bearer token");
        } else if (matched.operationId().equals("getDeploymentActions")) {
            response = getDeploymentActions(pathParams.get("deploymentId"));
        } else if (matched.operationId().equals("submitDeploymentActionRequest")) {
            response = submitDeploymentActionRequest(exchange, pathParams.get("deploymentId"), body);
        } else {
            response = error(501, "contract operation " + matched.operationId() + " has no handler");
        }

        log(exchange, method, path, query, body, matched, response.status);

        byte[] payload = response.body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(response.status, payload.length);
        exchange.getResponseBody().write(payload);
        exchange.close();
    }

    private boolean authorized(HttpExchange exchange) {
        String header = exchange.getRequestHeaders().getFirst("Authorization");
        return header != null && header.equals("Bearer " + expectedToken);
    }

    // ----------------------------------------------------------- operations

    private Response getDeploymentActions(String deploymentId) {
        List<Map<String, Object>> actions = deployments.get(deploymentId);
        if (actions == null) {
            return error(404, "deployment " + deploymentId + " not found");
        }
        return new Response(200, Json.write(actions));
    }

    @SuppressWarnings("unchecked")
    private Response submitDeploymentActionRequest(
            HttpExchange exchange, String deploymentId, String body) {
        String contentType = exchange.getRequestHeaders().getFirst("Content-Type");
        if (contentType == null || !contentType.toLowerCase(Locale.ROOT).startsWith("application/json")) {
            return error(415, "Content-Type must be application/json but was " + contentType);
        }
        List<Map<String, Object>> actions = deployments.get(deploymentId);
        if (actions == null) {
            return error(404, "deployment " + deploymentId + " not found");
        }

        Object parsed;
        try {
            parsed = Json.parse(body);
        } catch (RuntimeException e) {
            return error(400, "body is not valid JSON: " + e.getMessage());
        }
        if (!(parsed instanceof Map)) {
            return error(400, "body must be a ResourceActionRequest object");
        }
        Map<String, Object> request = (Map<String, Object>) parsed;
        for (String key : request.keySet()) {
            if (!key.equals("actionId") && !key.equals("inputs") && !key.equals("reason")) {
                return error(400, "ResourceActionRequest has no property " + key);
            }
        }

        Object actionId = request.get("actionId");
        Map<String, Object> action = null;
        for (Map<String, Object> a : actions) {
            if (a.get("id").equals(actionId)) {
                action = a;
                break;
            }
        }
        if (action == null) {
            return error(404, "action " + actionId + " not found on deployment " + deploymentId);
        }
        if (!Boolean.TRUE.equals(action.get("valid"))) {
            return error(409, "action " + actionId + " is not valid for the current state");
        }

        Map<String, Object> created = new LinkedHashMap<>();
        created.put("id", String.format("req-%04d", ++requestIdSeq));
        created.put("name", String.valueOf(action.get("name")));
        created.put("deploymentId", deploymentId);
        created.put("actionId", actionId);
        created.put("requestedBy", "harness@example.test");
        created.put("createdAt", "2026-08-11T00:00:00.000Z");
        created.put("status", "INITIALIZATION");
        created.put("cancelable", Boolean.TRUE);
        created.put("completedTasks", 0);
        created.put("totalTasks", 3);
        if (request.containsKey("inputs")) {
            created.put("inputs", request.get("inputs"));
        }
        return new Response(200, Json.write(created));
    }

    // ------------------------------------------------------------- logging

    private void log(
            HttpExchange exchange,
            String method,
            String path,
            String query,
            String body,
            Route matched,
            int status)
            throws IOException {
        Map<String, Object> headers = new LinkedHashMap<>();
        for (Map.Entry<String, List<String>> e : exchange.getRequestHeaders().entrySet()) {
            headers.put(e.getKey().toLowerCase(Locale.ROOT), e.getValue().get(0));
        }
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("seq", ++requestSeq);
        entry.put("method", method);
        entry.put("path", path);
        entry.put("query", query);
        entry.put("operationId", matched == null ? null : matched.operationId());
        entry.put("headers", headers);
        entry.put("body", body);
        entry.put("responseStatus", status);
        Files.writeString(
                logPath,
                Json.write(entry) + System.lineSeparator(),
                StandardCharsets.UTF_8,
                StandardOpenOption.APPEND);
    }

    /** Reads the request log back, oldest first. */
    @SuppressWarnings("unchecked")
    public List<Map<String, Object>> readLog() throws IOException {
        List<Map<String, Object>> entries = new ArrayList<>();
        for (String line : Files.readAllLines(logPath, StandardCharsets.UTF_8)) {
            if (!line.isBlank()) {
                entries.add((Map<String, Object>) Json.parse(line));
            }
        }
        return entries;
    }

    private static Response error(int status, String message) {
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("message", message);
        payload.put("statusCode", status);
        return new Response(status, Json.write(payload));
    }

    private record Response(int status, String body) {}
}
