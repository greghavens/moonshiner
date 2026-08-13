package com.example.vcf.harness;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.InputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Base64;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.Executors;

/**
 * Loopback stand-in for a vCenter Server appliance, pinned to {@code docs/contract.json}.
 *
 * <p>The route table is built from the contract at construction time: the mock serves the three
 * operations the contract names and nothing else. Any other method/path/query combination is
 * answered with 404 and counted as {@link #unmatchedCount()}, so a client that wanders outside
 * the contract is caught rather than quietly tolerated.
 *
 * <p>Every request that arrives — matched or not — is appended to {@link #log()}.
 *
 * <p>The server binds the loopback interface on an ephemeral port. It never reaches the network.
 */
public final class MockVcenter {

    // ------------------------------------------------------------- recording

    /** One request as it actually arrived on the wire. */
    public static final class Recorded {
        public int seq;
        public String method;
        public String rawPath;
        public String decodedPath;
        /** Raw query string, or {@code null} when the request carried no {@code ?} at all. */
        public String rawQuery;
        /** Decoded query parameters in wire order; a bare {@code ?flag} yields value "". */
        public List<String[]> queryParams = new ArrayList<>();
        /** Header names lower-cased. */
        public Map<String, List<String>> headers = new LinkedHashMap<>();
        public String body = "";
        public Map<String, String> pathParams = new LinkedHashMap<>();
        /** Contract operationId this request was served as, or {@code null} if it matched none. */
        public String operationId;
        public String rejection;
        public int responseStatus;

        public String header(String name) {
            List<String> values = headers.get(name.toLowerCase(Locale.ROOT));
            return values == null || values.isEmpty() ? null : values.get(0);
        }

        public boolean hasHeader(String name) {
            return headers.containsKey(name.toLowerCase(Locale.ROOT));
        }

        /** All values recorded for {@code name}, empty when the parameter was absent. */
        public List<String> queryValues(String name) {
            List<String> out = new ArrayList<>();
            for (String[] pair : queryParams) {
                if (pair[0].equals(name)) {
                    out.add(pair[1]);
                }
            }
            return out;
        }

        public List<String> queryNames() {
            List<String> out = new ArrayList<>();
            for (String[] pair : queryParams) {
                out.add(pair[0]);
            }
            return out;
        }

        public String describe() {
            return "#" + seq + " " + method + " " + rawPath + (rawQuery == null ? "" : "?" + rawQuery)
                    + " -> " + responseStatus
                    + (operationId == null ? " [no contract operation matched]" : " [" + operationId + "]")
                    + (rejection == null ? "" : " (" + rejection + ")");
        }

        Map<String, Object> toJson() {
            List<Object> params = new ArrayList<>();
            for (String[] pair : queryParams) {
                params.add(Json.obj("name", pair[0], "value", pair[1]));
            }
            return Json.obj(
                    "seq", (long) seq,
                    "method", method,
                    "rawPath", rawPath,
                    "rawQuery", rawQuery,
                    "queryParams", params,
                    "headers", new LinkedHashMap<String, Object>(headers),
                    "body", body,
                    "operationId", operationId,
                    "rejection", rejection,
                    "responseStatus", (long) responseStatus);
        }
    }

    // ----------------------------------------------------------------- route

    private static final class Route {
        final String operationId;
        final String method;
        final List<String> segments;
        final Map<String, String> requiredQuery;
        final int successStatus;

        Route(String operationId, String method, String fullPath,
              Map<String, String> requiredQuery, int successStatus) {
            this.operationId = operationId;
            this.method = method;
            this.segments = splitPath(fullPath);
            this.requiredQuery = requiredQuery;
            this.successStatus = successStatus;
        }
    }

    // ----------------------------------------------------------------- state

    private final List<Route> routes = new ArrayList<>();
    private final List<Recorded> log = Collections.synchronizedList(new ArrayList<>());

    private HttpServer server;
    private String baseUrl;
    private int unmatched;

    private String username = "administrator@vsphere.local";
    private String password = "VMw@re1!vcf90";
    private String scenario = "default";
    private String sessionToken = "unset";
    private String taskId = "unset";
    private List<String> statusScript = List.of("SUCCEEDED");
    private String failureMessage;
    private int pollIndex;

    public MockVcenter(Path contractFile) throws IOException {
        Map<String, Object> contract = Json.parseObject(Files.readString(contractFile));
        String basePath = (String) contract.get("basePath");
        if (basePath == null || basePath.isBlank()) {
            throw new IllegalStateException("contract.json is missing basePath");
        }
        List<Object> operations = Json.asArray(contract.get("operations"));
        if (operations == null || operations.isEmpty()) {
            throw new IllegalStateException("contract.json declares no operations");
        }
        for (Object entry : operations) {
            Map<String, Object> op = Json.asObject(entry);
            String operationId = (String) op.get("operationId");
            Map<String, Object> rawQuery = Json.asObject(op.get("requiredQuery"));
            Map<String, String> requiredQuery = new LinkedHashMap<>();
            if (rawQuery != null) {
                for (Map.Entry<String, Object> e : rawQuery.entrySet()) {
                    requiredQuery.put(e.getKey(), String.valueOf(e.getValue()));
                }
            }
            Number status = (Number) op.get("successStatus");
            routes.add(new Route(
                    operationId,
                    String.valueOf(op.get("method")),
                    basePath + op.get("path"),
                    requiredQuery,
                    status == null ? 200 : status.intValue()));
            if (!isHandled(operationId)) {
                throw new IllegalStateException(
                        "contract.json names operation '" + operationId + "' but the mock has no handler for it");
            }
        }
    }

    private static boolean isHandled(String operationId) {
        return operationId.equals("Cis.Session_create")
                || operationId.equals("Esx.Settings.Clusters.Software_apply$Task")
                || operationId.equals("Cis.Tasks_get");
    }

    // --------------------------------------------------------------- control

    public void start() throws IOException {
        // Use a literal IPv4 loopback address so the generated base URL is valid and independent
        // of host name/address-family preferences (an IPv6 URL would require brackets).
        InetAddress loopback = InetAddress.getByAddress(new byte[]{127, 0, 0, 1});
        server = HttpServer.create(new InetSocketAddress(loopback, 0), 0);
        // Single threaded on purpose: the request log then reflects true arrival order.
        server.setExecutor(Executors.newSingleThreadExecutor(r -> {
            Thread t = new Thread(r, "mock-vcenter");
            t.setDaemon(true);
            return t;
        }));
        server.createContext("/", this::handle);
        server.start();
        baseUrl = "http://127.0.0.1:" + server.getAddress().getPort();
    }

    public void stop() {
        if (server != null) {
            server.stop(0);
        }
    }

    /**
     * Clears the log and arms a fresh scenario.
     *
     * @param scenarioName    used to derive a scenario specific session token and task id, so a
     *                        client cannot pass by hard-coding values seen in an earlier scenario
     * @param statuses        the {@code Cis.Task.Status} values the task reports, one per poll;
     *                        the last one is repeated if the client polls beyond the script
     * @param failureMessage  default_message reported when the script ends in FAILED
     */
    public void reset(String scenarioName, List<String> statuses, String failureMessage) {
        this.scenario = scenarioName;
        this.statusScript = List.copyOf(statuses);
        this.failureMessage = failureMessage;
        this.pollIndex = 0;
        this.unmatched = 0;
        this.log.clear();
        int fingerprint = scenarioName.hashCode() & 0x7fffffff;
        this.sessionToken = String.format("vmw-sess-%s-%08x", scenarioName, fingerprint);
        this.taskId = String.format("52%08x-%04x-%04x:com.vmware.esx.settings.clusters.software",
                fingerprint, fingerprint & 0xffff, (fingerprint >> 8) & 0xffff);
    }

    public String baseUrl() {
        return baseUrl;
    }

    public String username() {
        return username;
    }

    public String password() {
        return password;
    }

    public String sessionToken() {
        return sessionToken;
    }

    public String taskId() {
        return taskId;
    }

    public int unmatchedCount() {
        return unmatched;
    }

    public List<Recorded> log() {
        synchronized (log) {
            return new ArrayList<>(log);
        }
    }

    /** Requests served as the given contract operation, in arrival order. */
    public List<Recorded> logFor(String operationId) {
        List<Recorded> out = new ArrayList<>();
        for (Recorded r : log()) {
            if (operationId.equals(r.operationId)) {
                out.add(r);
            }
        }
        return out;
    }

    public void writeLog(Path file) throws IOException {
        List<Object> entries = new ArrayList<>();
        for (Recorded r : log()) {
            entries.add(r.toJson());
        }
        Files.createDirectories(file.getParent());
        Files.writeString(file, Json.write(Json.obj("scenario", scenario, "requests", entries)));
    }

    // --------------------------------------------------------------- serving

    private void handle(HttpExchange exchange) throws IOException {
        Recorded rec = new Recorded();
        try {
            rec.method = exchange.getRequestMethod();
            rec.rawPath = exchange.getRequestURI().getRawPath();
            rec.decodedPath = decodePath(rec.rawPath);
            rec.rawQuery = exchange.getRequestURI().getRawQuery();
            rec.queryParams = parseQuery(rec.rawQuery);
            for (Map.Entry<String, List<String>> e : exchange.getRequestHeaders().entrySet()) {
                rec.headers.put(e.getKey().toLowerCase(Locale.ROOT), List.copyOf(e.getValue()));
            }
            try (InputStream in = exchange.getRequestBody()) {
                rec.body = new String(in.readAllBytes(), StandardCharsets.UTF_8);
            }
            synchronized (log) {
                rec.seq = log.size() + 1;
                log.add(rec);
            }
            dispatch(exchange, rec);
        } catch (RuntimeException e) {
            rec.rejection = "mock error: " + e;
            respond(exchange, rec, 500, Json.obj(
                    "error_type", "ERROR",
                    "messages", List.of(message("mock.internal", String.valueOf(e)))));
        } finally {
            exchange.close();
        }
    }

    private void dispatch(HttpExchange exchange, Recorded rec) throws IOException {
        Route matched = null;
        String queryMismatch = null;
        for (Route route : routes) {
            Map<String, String> pathParams = matchPath(route, rec.decodedPath);
            if (pathParams == null || !route.method.equalsIgnoreCase(rec.method)) {
                continue;
            }
            if (!queryMatches(route, rec)) {
                queryMismatch = route.operationId;
                continue;
            }
            rec.pathParams = pathParams;
            matched = route;
            break;
        }

        if (matched == null) {
            unmatched++;
            rec.rejection = queryMismatch != null
                    ? "path matched " + queryMismatch + " but its required query parameters were absent or wrong"
                    : "no operation in docs/contract.json covers this method and path";
            respond(exchange, rec, 404, Json.obj(
                    "error_type", "NOT_FOUND",
                    "messages", List.of(message(
                            "mock.not_in_contract",
                            "The mock serves only the operations named in docs/contract.json. "
                                    + rec.method + " " + rec.decodedPath + " is not one of them."))));
            return;
        }

        rec.operationId = matched.operationId;
        switch (matched.operationId) {
            case "Cis.Session_create" -> serveSessionCreate(exchange, rec, matched);
            case "Esx.Settings.Clusters.Software_apply$Task" -> serveApply(exchange, rec, matched);
            case "Cis.Tasks_get" -> serveTaskGet(exchange, rec, matched);
            default -> throw new IllegalStateException("unhandled operation " + matched.operationId);
        }
    }

    private void serveSessionCreate(HttpExchange exchange, Recorded rec, Route route) throws IOException {
        String authorization = rec.header("authorization");
        String expected = "Basic " + Base64.getEncoder().encodeToString(
                (username + ":" + password).getBytes(StandardCharsets.UTF_8));
        if (authorization == null || !authorization.equals(expected)) {
            rec.rejection = "basic_auth credentials missing or wrong";
            respond(exchange, rec, 401, unauthenticated("Session creation requires HTTP Basic credentials."));
            return;
        }
        respond(exchange, rec, route.successStatus, sessionToken);
    }

    private void serveApply(HttpExchange exchange, Recorded rec, Route route) throws IOException {
        if (!authorized(exchange, rec)) {
            return;
        }
        // The body is validated by WireVerifier, not here; the mock only needs it to be JSON so
        // that a malformed request is not mistaken for a passing one.
        if (!rec.body.isBlank()) {
            try {
                Json.parseObject(rec.body);
            } catch (RuntimeException e) {
                rec.rejection = "request body is not a JSON object: " + e.getMessage();
                respond(exchange, rec, 400, error("Request body is not a valid ApplySpec document."));
                return;
            }
        }
        pollIndex = 0;
        respond(exchange, rec, route.successStatus, taskId);
    }

    private void serveTaskGet(HttpExchange exchange, Recorded rec, Route route) throws IOException {
        if (!authorized(exchange, rec)) {
            return;
        }
        String requested = rec.pathParams.get("task");
        if (!taskId.equals(requested)) {
            rec.rejection = "unknown task id '" + requested + "'";
            respond(exchange, rec, 404, Json.obj(
                    "error_type", "NOT_FOUND",
                    "messages", List.of(message("com.vmware.cis.task.not_found",
                            "No task with identifier " + requested + "."))));
            return;
        }
        int step = Math.min(pollIndex, statusScript.size() - 1);
        pollIndex++;
        respond(exchange, rec, route.successStatus, taskInfo(statusScript.get(step), step));
    }

    private boolean authorized(HttpExchange exchange, Recorded rec) throws IOException {
        String token = rec.header("vmware-api-session-id");
        if (token == null || !token.equals(sessionToken)) {
            rec.rejection = token == null
                    ? "vmware-api-session-id header absent"
                    : "vmware-api-session-id header does not hold the token issued by Cis.Session_create";
            respond(exchange, rec, 401, unauthenticated("A valid vmware-api-session-id header is required."));
            return false;
        }
        return true;
    }

    // ------------------------------------------------------------- responses

    private Map<String, Object> taskInfo(String status, int step) {
        Map<String, Object> info = Json.obj(
                "description", message("com.vmware.esx.settings.clusters.software.apply",
                        "Applying the cluster software specification."),
                "service", "com.vmware.esx.settings.clusters.software",
                "operation", "apply",
                "status", status,
                "cancelable", Boolean.TRUE,
                "user", "VSPHERE.LOCAL\\Administrator");

        if (!status.equals("PENDING")) {
            info.put("start_time", "2026-08-12T09:14:02.000Z");
            long completed = Math.min(100L, 20L * (step + 1));
            info.put("progress", Json.obj(
                    "total", 100L,
                    "completed", status.equals("SUCCEEDED") ? 100L : completed,
                    "message", message("com.vmware.esx.settings.remediation.progress",
                            "Remediating hosts in the cluster.")));
        }
        if (status.equals("SUCCEEDED") || status.equals("FAILED")) {
            info.put("end_time", "2026-08-12T09:21:47.000Z");
        }
        if (status.equals("SUCCEEDED")) {
            info.put("result", Json.obj("status", "OK"));
        }
        if (status.equals("FAILED")) {
            info.put("error", Json.obj(
                    "error_type", "ERROR",
                    "messages", List.of(message(
                            "com.vmware.esx.settings.remediation_failed",
                            failureMessage == null ? "Remediation failed." : failureMessage))));
        }
        return info;
    }

    private static Map<String, Object> message(String id, String defaultMessage) {
        return Json.obj("id", id, "default_message", defaultMessage, "args", List.of());
    }

    private static Map<String, Object> unauthenticated(String text) {
        return Json.obj("error_type", "UNAUTHENTICATED",
                "messages", List.of(message("com.vmware.vapi.std.errors.unauthenticated", text)));
    }

    private static Map<String, Object> error(String text) {
        return Json.obj("error_type", "ERROR",
                "messages", List.of(message("com.vmware.vapi.std.errors.error", text)));
    }

    private void respond(HttpExchange exchange, Recorded rec, int status, Object jsonBody) throws IOException {
        byte[] payload = Json.write(jsonBody).getBytes(StandardCharsets.UTF_8);
        rec.responseStatus = status;
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, payload.length);
        exchange.getResponseBody().write(payload);
    }

    // ----------------------------------------------------------- path/query

    private static List<String> splitPath(String path) {
        List<String> out = new ArrayList<>();
        for (String segment : path.split("/")) {
            if (!segment.isEmpty()) {
                out.add(segment);
            }
        }
        return out;
    }

    /** Returns captured path parameters, or {@code null} when the path does not match. */
    private static Map<String, String> matchPath(Route route, String decodedPath) {
        List<String> actual = splitPath(decodedPath);
        if (actual.size() != route.segments.size()) {
            return null;
        }
        Map<String, String> params = new LinkedHashMap<>();
        for (int i = 0; i < actual.size(); i++) {
            String template = route.segments.get(i);
            if (template.startsWith("{") && template.endsWith("}")) {
                params.put(template.substring(1, template.length() - 1), actual.get(i));
            } else if (!template.equals(actual.get(i))) {
                return null;
            }
        }
        return params;
    }

    private static boolean queryMatches(Route route, Recorded rec) {
        for (Map.Entry<String, String> required : route.requiredQuery.entrySet()) {
            if (!rec.queryValues(required.getKey()).contains(required.getValue())) {
                return false;
            }
        }
        return true;
    }

    private static List<String[]> parseQuery(String rawQuery) {
        List<String[]> out = new ArrayList<>();
        if (rawQuery == null || rawQuery.isEmpty()) {
            return out;
        }
        for (String part : rawQuery.split("&", -1)) {
            int eq = part.indexOf('=');
            if (eq < 0) {
                out.add(new String[]{decode(part), ""});
            } else {
                out.add(new String[]{decode(part.substring(0, eq)), decode(part.substring(eq + 1))});
            }
        }
        return out;
    }

    /** Form decoding: {@code +} means space. Correct for query strings. */
    private static String decode(String value) {
        return URLDecoder.decode(value, StandardCharsets.UTF_8);
    }

    /**
     * Percent decoding for path segments, where {@code +} is a literal plus rather than a space.
     * A client is free to percent-encode reserved characters such as {@code :} inside a task id;
     * both spellings decode to the same path.
     */
    private static String decodePath(String value) {
        return URLDecoder.decode(value.replace("+", "%2B"), StandardCharsets.UTF_8);
    }
}
